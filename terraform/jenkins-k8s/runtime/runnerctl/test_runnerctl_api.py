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


class ResolveRunnerTests(unittest.TestCase):
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
        self.tag_requests = []
        self.vmids = {9002}

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

    def vm_status(self, node, vmid):
        return {"status": "running"}

    def agent_ping(self, node, vmid):
        return True

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
            self.assertEqual(client.config_requests[0][1]["tags"], "runnerctl;lifecycle-ephemeral;leased")

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
            self.assertEqual(client.clone_requests, [])
            self.assertEqual(client.tag_requests, [("pve-rtx-01", 2000, "runnerctl;lifecycle-ephemeral;leased")])
            record = lease_store.get(pool_id)
            self.assertEqual(record["state"], "leased")
            self.assertFalse(record["pool_member"])

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


if __name__ == "__main__":
    unittest.main()
