import base64
import json
import tempfile
import unittest
from pathlib import Path

import runnerctl_api


SAMPLE_INVENTORY = {
    "version": 3,
    "defaults": {
        "backend": "proxmox",
        "clone_mode": "linked",
        "lease_ttl_minutes": 120,
        "api_credentials": {
            "token_id_env": "PROXMOX_RUNNER_API_TOKEN_ID",
            "token_secret_env": "PROXMOX_RUNNER_API_TOKEN_SECRET",
        },
    },
    "proxmox": {
        "hosts": [
            {
                "id": "example-rtx-node",
                "node": "pve-rtx-01",
                "enabled": True,
                "priority": 10,
                "labels": ["windows", "gpu", "nvidia", "vulkan", "runtime-only", "gpu-class-rtx-2070"],
                "api": {
                    "url": "https://127.0.0.1:18006/api2/json",
                    "token_id_env": "PROXMOX_RUNNER_API_TOKEN_ID",
                    "token_secret_env": "PROXMOX_RUNNER_API_TOKEN_SECRET",
                },
                "pools": {
                    "templates": "ci-images",
                    "runners": "ci-runners",
                },
                "storage": {
                    "runtime": "local-lvm",
                    "iso": "local",
                },
                "network": {
                    "bridge": "vmbr0",
                    "vlan_tag": 69,
                    "jenkins_host_alias_ip": "10.254.254.254",
                },
                "vmid_ranges": {
                    "runner": {
                        "start": 2000,
                        "end": 2002,
                    },
                },
                "smoke": {
                    "source_vmid": 9910,
                    "clone_vmid": 2910,
                },
                "gpu_devices": [
                    {
                        "id": "rtx-2070-0",
                        "vendor": "nvidia",
                        "pci_host": "0000:01:00.0",
                        "exclusive": True,
                        "labels": ["gpu", "nvidia", "gpu-class-rtx-2070"],
                    }
                ],
            }
        ]
    },
    "templates": [
        {
            "id": "windows-gpu-nvidia-stable",
            "channel": "windows-gpu-nvidia/stable",
            "builder": "proxmox-clone",
            "parent": "windows-base-stable",
            "guest_os": "windows11",
            "communicator": {"type": "winrm"},
            "placements": [
                {
                    "host": "example-rtx-node",
                    "vmid": 9002,
                    "gpu_device": "rtx-2070-0",
                }
            ],
        }
    ],
    "runner_classes": [
        {
            "id": "win-gpu-nvidia",
            "labels": ["windows", "gpu", "nvidia", "vulkan", "runtime-only", "gpu-class-rtx-2070"],
            "template": "windows-gpu-nvidia-stable",
            "connection": {"type": "winrm"},
            "host_selector": {
                "labels": ["windows", "gpu", "nvidia", "vulkan", "gpu-class-rtx-2070"],
            },
            "runtime": {
                "pool": "runners",
                "storage": "runtime",
                "vmid_range": "runner",
            },
            "warm_pool": {"min_ready": 1, "max_ready": 1},
            "policy": {
                "lease_ttl_minutes": 120,
                "boot_timeout_minutes": 10,
                "health_timeout_minutes": 15,
                "gpu_exclusive": True,
                "release_on_failure": True,
                "destroy_after_job": True,
            },
            "health_checks": ["guest-agent", "network-interfaces", "nvidia-smi", "vulkan-runtime", "workspace-ready"],
        }
    ],
    "janitor": {
        "backend": "proxmox",
        "pool": "runners",
        "vmid_range": "runner",
        "require_tags": ["runnerctl", "lifecycle-ephemeral"],
        "stale_lease_after_minutes": 240,
    },
}


class InventoryStoreTests(unittest.TestCase):
    def test_load_reads_json_inventory(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "inventory.json"
            path.write_text(json.dumps(SAMPLE_INVENTORY), encoding="utf-8")
            store = runnerctl_api.InventoryStore(path)
            loaded = store.load()
            self.assertEqual(loaded["version"], 3)
            self.assertEqual(loaded["runner_classes"][0]["id"], "win-gpu-nvidia")


class GuestOutputTests(unittest.TestCase):
    def test_compact_guest_output_redacts_secrets(self):
        text = "failed -secret " + ("a" * 64) + " <Obj>noise</Obj>"
        compact = runnerctl_api.compact_guest_output(text)
        self.assertIn("-secret <redacted>", compact)
        self.assertNotIn("a" * 64, compact)


class ResolveRunnerTests(unittest.TestCase):
    def test_resolve_runner_selects_class_by_labels_without_runner_class(self):
        resolved = runnerctl_api.resolve_runner(
            SAMPLE_INVENTORY,
            None,
            ["windows", "gpu", "nvidia"],
        )
        self.assertEqual(resolved["runner_class"], "win-gpu-nvidia")
        self.assertEqual(resolved["backend"]["host_id"], "example-rtx-node")

    def test_resolve_runner_returns_capability_based_placement(self):
        resolved = runnerctl_api.resolve_runner(
            SAMPLE_INVENTORY,
            "win-gpu-nvidia",
            ["windows", "gpu", "nvidia"],
        )
        self.assertEqual(resolved["runner_class"], "win-gpu-nvidia")
        self.assertEqual(resolved["template"]["channel"], "windows-gpu-nvidia/stable")
        self.assertEqual(resolved["backend"]["host_id"], "example-rtx-node")
        self.assertEqual(resolved["backend"]["node"], "pve-rtx-01")
        self.assertEqual(resolved["backend"]["template_vmid"], 9002)
        self.assertEqual(resolved["warm_pool"]["min_ready"], 1)

    def test_resolve_runner_rejects_missing_labels(self):
        with self.assertRaises(runnerctl_api.RunnerCtlError) as raised:
            runnerctl_api.resolve_runner(
                SAMPLE_INVENTORY,
                "win-gpu-nvidia",
                ["windows", "amd"],
            )
        self.assertEqual(raised.exception.code, "missing-labels")

    def test_resolve_runner_rejects_unknown_class(self):
        with self.assertRaises(runnerctl_api.RunnerCtlError) as raised:
            runnerctl_api.resolve_runner(
                SAMPLE_INVENTORY,
                "win-gpu-amd",
                ["windows"],
            )
        self.assertEqual(raised.exception.code, "unknown-runner-class")


class HelpersTests(unittest.TestCase):
    def test_require_int_accepts_bounds(self):
        self.assertEqual(runnerctl_api.require_int("69", "vlan_tag", minimum=1, maximum=4094), 69)

    def test_require_int_rejects_non_integer(self):
        with self.assertRaises(runnerctl_api.RunnerCtlError):
            runnerctl_api.require_int("abc", "vlan_tag", minimum=1)

    def test_choose_free_vmid_skips_used_values(self):
        vmid = runnerctl_api.choose_free_vmid({"start": 2000, "end": 2002}, {2000, 2001})
        self.assertEqual(vmid, 2002)


class LeaseStoreTests(unittest.TestCase):
    def test_put_get_delete_cycle(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "leases.json"
            store = runnerctl_api.LeaseStore(path)
            store.put("abc123" * 5 + "ab", {"vmid": 2000, "host_id": "example-rtx-node"})
            self.assertEqual(store.get("abc123" * 5 + "ab")["vmid"], 2000)
            deleted = store.delete("abc123" * 5 + "ab")
            self.assertEqual(deleted["host_id"], "example-rtx-node")
            self.assertIsNone(store.get("abc123" * 5 + "ab"))


class FakeProxmoxClient:
    def __init__(self):
        self.clone_requests = []
        self.config_requests = []
        self.destroy_requests = []
        self.start_requests = []
        self.agent_ping_requests = []
        self.guest_exec_requests = []
        self.tag_requests = []
        self.vmids = {9002}
        self.config_tags = "runnerctl;lifecycle-ephemeral;leased"
        self.vm_configs = {}

    def qemu_vmids(self, node):
        return set(self.vmids)

    def request(self, method, path, query=None, data=None):
        if method == "POST" and path.endswith("/clone"):
            self.clone_requests.append((path, data))
            self.vmids.add(int(data["newid"]))
            return "UPID:fake"
        if method == "POST" and path.endswith("/config"):
            self.config_requests.append((path, data))
            return "UPID:config"
        raise AssertionError(f"unexpected request {method} {path}")

    def wait_task(self, node, upid, timeout_seconds):
        return {"status": "stopped", "exitstatus": "OK"}

    def safe_destroy(self, node, vmid):
        self.destroy_requests.append((node, vmid))
        return True

    def start_vm(self, node, vmid):
        self.start_requests.append((node, vmid))
        return True

    def vm_status(self, node, vmid):
        return {"status": "running", "name": f"runnerctl-test-{vmid}"}

    def vm_config(self, node, vmid):
        config = {"name": f"runnerctl-test-{vmid}", "tags": self.config_tags}
        config.update(self.vm_configs.get(int(vmid), {}))
        return config

    def agent_ping(self, node, vmid):
        self.agent_ping_requests.append((node, vmid))
        return True

    def agent_network_get_interfaces(self, node, vmid):
        return [{"name": "Ethernet"}]

    def guest_exec(self, node, vmid, command, timeout_seconds):
        self.guest_exec_requests.append((node, vmid, command, timeout_seconds))
        return {"exitcode": 0}

    def set_tags(self, node, vmid, tags):
        self.tag_requests.append((node, vmid, tags))
        return True


class FailingLeaseStore(runnerctl_api.LeaseStore):
    def put(self, lease_id, record):
        raise OSError("lease store unavailable")


class FakeProxmoxRegistry:
    def __init__(self, client):
        self.client = client

    def client_for_host(self, inventory, host):
        return self.client


class FakeJenkinsClient:
    def __init__(self):
        self.public_url = "https://jenkins.example.invalid"
        self.created_nodes = []
        self.deleted_nodes = []
        self.online_nodes = []

    def create_agent_node(self, node_name, label_string, remote_fs):
        self.created_nodes.append((node_name, label_string, remote_fs))
        return True

    def agent_secret(self, node_name):
        return "s" * 64

    def wait_agent_online(self, node_name, timeout_seconds):
        self.online_nodes.append((node_name, timeout_seconds))
        return True

    def delete_agent_node(self, node_name):
        self.deleted_nodes.append(node_name)
        return True


class CreateLeaseTests(unittest.TestCase):
    def test_create_lease_clones_selected_template_on_capability_host(self):
        with tempfile.TemporaryDirectory() as directory:
            lease_store = runnerctl_api.LeaseStore(Path(directory) / "leases.json")
            client = FakeProxmoxClient()
            registry = FakeProxmoxRegistry(client)
            result = runnerctl_api.create_lease(
                registry,
                lease_store,
                SAMPLE_INVENTORY,
                {
                    "runner_class": "win-gpu-nvidia",
                    "required_labels": ["windows", "gpu", "nvidia"],
                },
            )
            self.assertEqual(result["host_id"], "example-rtx-node")
            self.assertEqual(result["node"], "pve-rtx-01")
            self.assertEqual(result["vmid"], 2000)
            self.assertEqual(client.clone_requests[0][1]["newid"], 2000)
            self.assertEqual(client.clone_requests[0][1]["pool"], "ci-runners")
            self.assertEqual(client.config_requests[0][1]["net0"], "e1000,bridge=vmbr0,tag=69,firewall=1")
            self.assertEqual(client.config_requests[1][1]["tags"], "runnerctl;lifecycle-ephemeral;leased")
            self.assertIn("timings", result)
            self.assertIn("clone_ms", result["timings"])
            record = lease_store.get(result["lease_id"])
            self.assertEqual(record["jenkins_host_alias_ip"], "10.254.254.254")
            self.assertIn("lease_total_ms", record["timings"])

    def test_create_lease_acquires_ready_hot_pool_member(self):
        with tempfile.TemporaryDirectory() as directory:
            lease_store = runnerctl_api.LeaseStore(Path(directory) / "leases.json")
            pool_id = "a" * 32
            lease_store.put(
                pool_id,
                {
                    "lease_id": pool_id,
                    "runner_class": "win-gpu-nvidia",
                    "labels": ["gpu", "gpu-class-rtx-2070", "nvidia", "runtime-only", "vulkan", "windows"],
                    "host_id": "example-rtx-node",
                    "node": "pve-rtx-01",
                    "vmid": 2000,
                    "template_vmid": 9002,
                    "clone_name": "runnerctl-hot-win-gpu-nvidia-aaaaaaaa",
                    "created_at": 1,
                    "ready_at": 2,
                    "expires_at": 9999999999,
                    "state": "ready",
                    "pool_member": True,
                    "allocation_mode": "hot-pool",
                    "connection": {"type": "winrm"},
                    "policy": {
                        "boot_timeout_minutes": 10,
                        "health_timeout_minutes": 15,
                        "destroy_after_job": True,
                    },
                    "health_checks": ["guest-agent"],
                },
            )
            client = FakeProxmoxClient()
            client.vmids = {9002, 2000}
            registry = FakeProxmoxRegistry(client)
            result = runnerctl_api.create_lease(
                registry,
                lease_store,
                SAMPLE_INVENTORY,
                {
                    "runner_class": "win-gpu-nvidia",
                    "required_labels": ["windows", "gpu", "nvidia"],
                },
            )
            self.assertEqual(result["allocation_mode"], "hot-pool")
            self.assertEqual(result["vmid"], 2000)
            self.assertIn("hot_pool_acquire_ms", result["timings"])
            self.assertEqual(client.clone_requests, [])
            self.assertEqual(client.tag_requests, [("pve-rtx-01", 2000, "runnerctl;lifecycle-ephemeral;leased")])
            record = lease_store.get(pool_id)
            self.assertEqual(record["state"], "leased")
            self.assertFalse(record["pool_member"])
            self.assertEqual(record["jenkins_host_alias_ip"], "10.254.254.254")

    def test_create_lease_rejects_second_exclusive_gpu_runner(self):
        with tempfile.TemporaryDirectory() as directory:
            lease_store = runnerctl_api.LeaseStore(Path(directory) / "leases.json")
            lease_store.put(
                "2" * 32,
                {
                    "lease_id": "2" * 32,
                    "runner_class": "win-gpu-nvidia",
                    "labels": ["gpu", "gpu-class-rtx-2070", "nvidia", "runtime-only", "vulkan", "windows"],
                    "host_id": "example-rtx-node",
                    "node": "pve-rtx-01",
                    "vmid": 2000,
                    "template_vmid": 9002,
                    "clone_name": "runnerctl-win-gpu-nvidia-22222222",
                    "created_at": 1,
                    "expires_at": 9999999999,
                    "state": "agent-online",
                    "pool_member": False,
                    "allocation_mode": "hot-pool",
                    "connection": {"type": "winrm"},
                    "policy": {
                        "boot_timeout_minutes": 10,
                        "health_timeout_minutes": 15,
                        "destroy_after_job": True,
                        "gpu_exclusive": True,
                    },
                    "health_checks": ["guest-agent"],
                },
            )
            client = FakeProxmoxClient()
            client.vmids = {9002, 2000}
            registry = FakeProxmoxRegistry(client)
            with self.assertRaises(runnerctl_api.RunnerCtlError) as raised:
                runnerctl_api.create_lease(
                    registry,
                    lease_store,
                    SAMPLE_INVENTORY,
                    {
                        "runner_class": "win-gpu-nvidia",
                        "required_labels": ["windows", "gpu", "nvidia"],
                    },
                )
            self.assertEqual(raised.exception.code, "no-placement")
            self.assertEqual(raised.exception.details["skipped"][0]["reason"], "capacity-exhausted")
            self.assertEqual(client.clone_requests, [])

    def test_lease_jenkins_agent_returns_unique_node_label(self):
        with tempfile.TemporaryDirectory() as directory:
            lease_store = runnerctl_api.LeaseStore(Path(directory) / "leases.json")
            client = FakeProxmoxClient()
            registry = FakeProxmoxRegistry(client)
            jenkins = FakeJenkinsClient()
            result = runnerctl_api.lease_jenkins_agent(
                registry,
                lease_store,
                SAMPLE_INVENTORY,
                {
                    "labels": ["windows", "gpu", "nvidia"],
                    "lease_ttl_minutes": "30",
                },
                jenkins,
            )
            self.assertEqual(result["runner_class"], "win-gpu-nvidia")
            self.assertEqual(result["allocation_mode"], "cold-clone")
            self.assertTrue(result["label"].startswith("runner-lease-"))
            self.assertEqual(result["label"], result["node_name"])
            self.assertEqual(jenkins.created_nodes[0][0], result["node_name"])
            self.assertIn("windows", jenkins.created_nodes[0][1].split())
            self.assertEqual(jenkins.online_nodes[0][0], result["node_name"])
            self.assertTrue(client.guest_exec_requests[-1][2][4])
            script = base64.b64decode(client.guest_exec_requests[-1][2][5]).decode("utf-16le")
            self.assertIn("10.254.254.254", script)
            self.assertIn("jenkins.example.invalid", script)
            self.assertIn("Register-ScheduledTask", script)
            self.assertIn("Clear-DnsClientCache", script)
            self.assertIn("Sysnative/drivers/etc/hosts", script)
            self.assertIn("Test-RunnerctlHostsAlias", script)
            self.assertIn('"{0} {1} # runnerctl-jenkins"', script)
            self.assertIn("runnerctl: hostAliasPresent={0}", script)
            self.assertIn("runnerctl: dnsFallback=hosts-file-present", script)
            self.assertIn("runnerctl: agentJarDownloadError attempt={0}", script)
            self.assertNotIn('"0 1 # runnerctl-jenkins"', script)
            self.assertNotIn("$entryPattern", script)
            self.assertIn("state={0}, last_result={1}", script)
            self.assertNotIn("state=0, last_result=1", script)
            self.assertIn("agent.stderr.tail", script)
            self.assertIn("Start-Process -FilePath", script)
            record = lease_store.get(result["lease_id"])
            self.assertEqual(record["state"], "agent-online")
            self.assertEqual(record["jenkins_agent"]["label"], result["label"])
            self.assertIn("agent_lease_total_ms", result["timings"])
            self.assertIn("jenkins_agent_connect_ms", result["timings"])

    def test_lease_jenkins_agent_reuses_preconnected_hot_pool_agent(self):
        with tempfile.TemporaryDirectory() as directory:
            lease_store = runnerctl_api.LeaseStore(Path(directory) / "leases.json")
            lease_id = "e" * 32
            last_health = {
                "guest-agent": "passed",
                "network-interfaces": "passed",
                "nvidia-smi": "passed",
                "vulkan-runtime": "passed",
                "workspace-ready": "passed",
            }
            node_name = "runner-lease-" + lease_id[:12]
            lease_store.put(
                lease_id,
                {
                    "lease_id": lease_id,
                    "runner_class": "win-gpu-nvidia",
                    "labels": ["gpu", "gpu-class-rtx-2070", "nvidia", "runtime-only", "vulkan", "windows"],
                    "host_id": "example-rtx-node",
                    "node": "pve-rtx-01",
                    "vmid": 2000,
                    "template_vmid": 9002,
                    "clone_name": "runnerctl-hot-win-gpu-nvidia-eeeeeeee",
                    "created_at": 1,
                    "ready_at": runnerctl_api.now_epoch(),
                    "last_health_at": runnerctl_api.now_epoch(),
                    "expires_at": 9999999999,
                    "state": "ready",
                    "pool_member": True,
                    "allocation_mode": "hot-pool",
                    "connection": {"type": "winrm"},
                    "policy": {
                        "boot_timeout_minutes": 10,
                        "health_timeout_minutes": 15,
                        "destroy_after_job": True,
                    },
                    "health_checks": list(last_health.keys()),
                    "last_health": last_health,
                    "jenkins_agent": {
                        "node_name": node_name,
                        "label": node_name,
                        "labels": ["runner-lease", node_name],
                        "work_dir": "C:\\runner\\jenkins-agent",
                        "online_at": runnerctl_api.now_epoch(),
                    },
                },
            )
            client = FakeProxmoxClient()
            client.vmids = {9002, 2000}
            registry = FakeProxmoxRegistry(client)
            jenkins = FakeJenkinsClient()
            result = runnerctl_api.lease_jenkins_agent(
                registry,
                lease_store,
                SAMPLE_INVENTORY,
                {
                    "labels": ["windows", "gpu", "nvidia"],
                },
                jenkins,
            )
            self.assertEqual(result["allocation_mode"], "hot-pool")
            self.assertEqual(result["label"], node_name)
            self.assertEqual(client.guest_exec_requests, [])
            self.assertEqual(jenkins.created_nodes, [])
            self.assertEqual(jenkins.online_nodes[0][0], node_name)
            record = lease_store.get(lease_id)
            self.assertEqual(record["state"], "agent-online")
            self.assertFalse(record["pool_member"])

    def test_prepare_lease_uses_fresh_hot_pool_health_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            lease_store = runnerctl_api.LeaseStore(Path(directory) / "leases.json")
            lease_id = "b" * 32
            last_health = {
                "guest-agent": "passed",
                "network-interfaces": "passed",
                "nvidia-smi": "passed",
                "vulkan-runtime": "passed",
                "workspace-ready": "passed",
            }
            lease_store.put(
                lease_id,
                {
                    "lease_id": lease_id,
                    "runner_class": "win-gpu-nvidia",
                    "labels": ["gpu", "gpu-class-rtx-2070", "nvidia", "runtime-only", "vulkan", "windows"],
                    "host_id": "example-rtx-node",
                    "node": "pve-rtx-01",
                    "vmid": 2000,
                    "template_vmid": 9002,
                    "clone_name": "runnerctl-hot-win-gpu-nvidia-bbbbbbbb",
                    "created_at": 1,
                    "ready_at": runnerctl_api.now_epoch(),
                    "last_health_at": runnerctl_api.now_epoch(),
                    "expires_at": 9999999999,
                    "state": "leased",
                    "pool_member": False,
                    "allocation_mode": "hot-pool",
                    "connection": {"type": "winrm"},
                    "policy": {
                        "boot_timeout_minutes": 10,
                        "health_timeout_minutes": 15,
                        "destroy_after_job": True,
                    },
                    "health_checks": list(last_health.keys()),
                    "last_health": last_health,
                },
            )
            client = FakeProxmoxClient()
            client.vmids = {9002, 2000}
            registry = FakeProxmoxRegistry(client)
            result = runnerctl_api.prepare_lease(
                registry,
                lease_store,
                SAMPLE_INVENTORY,
                {"lease_id": lease_id},
            )
            self.assertTrue(result["health_cached"])
            self.assertEqual(result["health"], last_health)
            self.assertEqual(client.start_requests, [])
            self.assertEqual(client.guest_exec_requests, [])
            self.assertEqual(client.agent_ping_requests, [("pve-rtx-01", 2000)])

    def test_health_lease_uses_fresh_hot_pool_health_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            lease_store = runnerctl_api.LeaseStore(Path(directory) / "leases.json")
            lease_id = "c" * 32
            last_health = {
                "guest-agent": "passed",
                "network-interfaces": "passed",
                "nvidia-smi": "passed",
                "vulkan-runtime": "passed",
                "workspace-ready": "passed",
            }
            lease_store.put(
                lease_id,
                {
                    "lease_id": lease_id,
                    "runner_class": "win-gpu-nvidia",
                    "labels": ["gpu", "gpu-class-rtx-2070", "nvidia", "runtime-only", "vulkan", "windows"],
                    "host_id": "example-rtx-node",
                    "node": "pve-rtx-01",
                    "vmid": 2000,
                    "template_vmid": 9002,
                    "clone_name": "runnerctl-hot-win-gpu-nvidia-cccccccc",
                    "created_at": 1,
                    "ready_at": runnerctl_api.now_epoch(),
                    "last_health_at": runnerctl_api.now_epoch(),
                    "expires_at": 9999999999,
                    "state": "healthy",
                    "pool_member": False,
                    "allocation_mode": "hot-pool",
                    "connection": {"type": "winrm"},
                    "policy": {
                        "boot_timeout_minutes": 10,
                        "health_timeout_minutes": 15,
                        "destroy_after_job": True,
                    },
                    "health_checks": list(last_health.keys()),
                    "last_health": last_health,
                },
            )
            client = FakeProxmoxClient()
            client.vmids = {9002, 2000}
            registry = FakeProxmoxRegistry(client)
            result = runnerctl_api.health_lease(
                registry,
                lease_store,
                SAMPLE_INVENTORY,
                {"lease_id": lease_id},
            )
            self.assertEqual(result["overall"], "passed")
            self.assertTrue(result["cached"])
            self.assertEqual(result["checks"], last_health)
            self.assertEqual(client.guest_exec_requests, [])
            self.assertEqual(client.agent_ping_requests, [("pve-rtx-01", 2000)])

    def test_release_lease_deletes_jenkins_agent_node(self):
        with tempfile.TemporaryDirectory() as directory:
            lease_store = runnerctl_api.LeaseStore(Path(directory) / "leases.json")
            lease_id = "d" * 32
            lease_store.put(
                lease_id,
                {
                    "lease_id": lease_id,
                    "runner_class": "win-gpu-nvidia",
                    "labels": ["gpu", "nvidia", "windows"],
                    "host_id": "example-rtx-node",
                    "node": "pve-rtx-01",
                    "vmid": 2000,
                    "template_vmid": 9002,
                    "clone_name": "runnerctl-win-gpu-nvidia-dddddddd",
                    "created_at": 1,
                    "expires_at": 9999999999,
                    "state": "agent-online",
                    "pool_member": False,
                    "allocation_mode": "hot-pool",
                    "connection": {"type": "winrm"},
                    "policy": {"destroy_after_job": True},
                    "health_checks": ["guest-agent"],
                    "jenkins_agent": {"node_name": "runner-lease-dddddddddddd", "label": "runner-lease-dddddddddddd"},
                },
            )
            client = FakeProxmoxClient()
            client.vmids = {9002, 2000}
            registry = FakeProxmoxRegistry(client)
            jenkins = FakeJenkinsClient()
            result = runnerctl_api.release_lease(
                registry,
                lease_store,
                SAMPLE_INVENTORY,
                {"lease_id": lease_id},
                jenkins_client=jenkins,
            )
            self.assertEqual(result["result"], "released")
            self.assertTrue(result["deleted_jenkins_node"])
            self.assertIn("release_ms", result["timings"])
            self.assertEqual(jenkins.deleted_nodes, ["runner-lease-dddddddddddd"])
            self.assertIsNone(lease_store.get(lease_id))

    def test_create_lease_cleans_up_clone_when_lease_store_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            lease_store = FailingLeaseStore(Path(directory) / "leases.json")
            client = FakeProxmoxClient()
            registry = FakeProxmoxRegistry(client)
            with self.assertRaises(OSError):
                runnerctl_api.create_lease(
                    registry,
                    lease_store,
                    SAMPLE_INVENTORY,
                    {
                        "runner_class": "win-gpu-nvidia",
                        "required_labels": ["windows", "gpu", "nvidia"],
                    },
                )
            self.assertEqual(client.destroy_requests, [("pve-rtx-01", 2000)])

    def test_janitor_removes_expired_runner_scoped_lease(self):
        with tempfile.TemporaryDirectory() as directory:
            lease_store = runnerctl_api.LeaseStore(Path(directory) / "leases.json")
            lease_id = "f" * 32
            node_name = "runner-lease-" + lease_id[:12]
            lease_store.put(
                lease_id,
                {
                    "lease_id": lease_id,
                    "runner_class": "win-gpu-nvidia",
                    "labels": ["gpu", "nvidia", "windows"],
                    "host_id": "example-rtx-node",
                    "node": "pve-rtx-01",
                    "vmid": 2000,
                    "template_vmid": 9002,
                    "clone_name": "runnerctl-win-gpu-nvidia-ffffffff",
                    "created_at": 1,
                    "expires_at": 2,
                    "state": "agent-online",
                    "pool_member": False,
                    "allocation_mode": "cold-clone",
                    "connection": {"type": "winrm"},
                    "policy": {"destroy_after_job": True},
                    "health_checks": ["guest-agent"],
                    "jenkins_agent": {"node_name": node_name, "label": node_name},
                },
            )
            client = FakeProxmoxClient()
            client.vmids = {9002, 2000}
            registry = FakeProxmoxRegistry(client)
            jenkins = FakeJenkinsClient()
            result = runnerctl_api.run_janitor(
                registry,
                lease_store,
                SAMPLE_INVENTORY,
                {},
                jenkins_client=jenkins,
            )
            self.assertEqual(result["cleaned_count"], 1)
            self.assertEqual(result["cleaned"][0]["reason"], "expired")
            self.assertIn("janitor_ms", result["timings"])
            self.assertIn("vm_lookup_ms", result["timings"])
            self.assertIn("destroy_vm_ms", result["timings"])
            self.assertIn("timings", result["cleaned"][0])
            self.assertEqual(client.destroy_requests, [("pve-rtx-01", 2000)])
            self.assertEqual(jenkins.deleted_nodes, [node_name])
            self.assertIsNone(lease_store.get(lease_id))

    def test_janitor_skips_expired_vm_without_required_tags(self):
        with tempfile.TemporaryDirectory() as directory:
            lease_store = runnerctl_api.LeaseStore(Path(directory) / "leases.json")
            lease_id = "1" * 32
            lease_store.put(
                lease_id,
                {
                    "lease_id": lease_id,
                    "runner_class": "win-gpu-nvidia",
                    "labels": ["gpu", "nvidia", "windows"],
                    "host_id": "example-rtx-node",
                    "node": "pve-rtx-01",
                    "vmid": 2000,
                    "template_vmid": 9002,
                    "clone_name": "runnerctl-win-gpu-nvidia-11111111",
                    "created_at": 1,
                    "expires_at": 2,
                    "state": "leased",
                    "pool_member": False,
                    "allocation_mode": "cold-clone",
                    "connection": {"type": "winrm"},
                    "policy": {"destroy_after_job": True},
                    "health_checks": ["guest-agent"],
                },
            )
            client = FakeProxmoxClient()
            client.vmids = {9002, 2000}
            client.config_tags = "unrelated"
            registry = FakeProxmoxRegistry(client)
            result = runnerctl_api.run_janitor(registry, lease_store, SAMPLE_INVENTORY, {})
            self.assertEqual(result["cleaned_count"], 0)
            self.assertEqual(result["skipped"][0]["reason"], "missing-required-tags")
            self.assertIn("janitor_ms", result["timings"])
            self.assertIn("timings", result["skipped"][0])
            self.assertEqual(client.destroy_requests, [])
            self.assertIsNotNone(lease_store.get(lease_id))

    def test_janitor_removes_stale_pool_member(self):
        with tempfile.TemporaryDirectory() as directory:
            lease_store = runnerctl_api.LeaseStore(Path(directory) / "leases.json")
            lease_id = "3" * 32
            lease_store.put(
                lease_id,
                {
                    "lease_id": lease_id,
                    "runner_class": "win-gpu-nvidia",
                    "labels": ["gpu", "nvidia", "windows"],
                    "host_id": "example-rtx-node",
                    "node": "pve-rtx-01",
                    "vmid": 2000,
                    "template_vmid": 9002,
                    "clone_name": "runnerctl-hot-win-gpu-nvidia-33333333",
                    "created_at": 1,
                    "expires_at": 9999999999,
                    "state": "creating",
                    "pool_member": True,
                    "allocation_mode": "hot-pool",
                    "connection": {"type": "winrm"},
                    "policy": {"destroy_after_job": True},
                    "health_checks": ["guest-agent"],
                },
            )
            client = FakeProxmoxClient()
            client.vmids = {9002, 2000}
            registry = FakeProxmoxRegistry(client)
            result = runnerctl_api.run_janitor(registry, lease_store, SAMPLE_INVENTORY, {})
            self.assertEqual(result["cleaned_count"], 1)
            self.assertEqual(result["cleaned"][0]["reason"], "stale-pool-member")
            self.assertEqual(client.destroy_requests, [("pve-rtx-01", 2000)])
            self.assertIsNone(lease_store.get(lease_id))

    def test_janitor_removes_orphan_runner_vm(self):
        with tempfile.TemporaryDirectory() as directory:
            lease_store = runnerctl_api.LeaseStore(Path(directory) / "leases.json")
            client = FakeProxmoxClient()
            client.vmids = {9002, 2000}
            client.vm_configs = {
                2000: {
                    "name": "runnerctl-hot-win-gpu-nvidia-33333333",
                    "tags": "runnerctl;lifecycle-ephemeral;hot-pool;creating",
                }
            }
            registry = FakeProxmoxRegistry(client)
            result = runnerctl_api.run_janitor(registry, lease_store, SAMPLE_INVENTORY, {})
            self.assertEqual(result["cleaned_count"], 1)
            self.assertEqual(result["cleaned"][0]["reason"], "orphan-runner-vm")
            self.assertEqual(result["cleaned"][0]["state"], "orphan")
            self.assertEqual(client.destroy_requests, [("pve-rtx-01", 2000)])

    def test_janitor_skips_orphan_vm_without_runner_name(self):
        with tempfile.TemporaryDirectory() as directory:
            lease_store = runnerctl_api.LeaseStore(Path(directory) / "leases.json")
            client = FakeProxmoxClient()
            client.vmids = {9002, 2000}
            client.vm_configs = {
                2000: {
                    "name": "manual-vm",
                    "tags": "runnerctl;lifecycle-ephemeral",
                }
            }
            registry = FakeProxmoxRegistry(client)
            result = runnerctl_api.run_janitor(registry, lease_store, SAMPLE_INVENTORY, {})
            self.assertEqual(result["cleaned_count"], 0)
            self.assertEqual(result["skipped"][0]["reason"], "orphan-name-not-runnerctl")
            self.assertEqual(client.destroy_requests, [])


if __name__ == "__main__":
    unittest.main()
