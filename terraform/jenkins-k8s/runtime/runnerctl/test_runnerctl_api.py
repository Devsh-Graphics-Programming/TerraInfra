import base64
import io
import json
import os
import threading
import tempfile
import unittest
import urllib.error
import zipfile
from unittest import mock
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
                "git_object_cache": {
                    "api_url": "http://10.254.254.254:18082",
                    "git_base_url": "git://10.254.254.254:9418",
                    "git_client_url": "http://10.254.254.254:18081/MinGit.zip",
                    "git_client_sha256": "04f937e1f0918b17b9be6f2294cb2bb66e96e1d9832d1c298e2de088a1d0e668",
                    "stores": [
                        {
                            "id": "nabla-media-public",
                            "repo": "nabla-media-public.git",
                            "scope": "public",
                            "description": "Shared media",
                        },
                        {
                            "id": "ditt-reference-scenes",
                            "repo": "ditt-reference-scenes.git",
                            "scope": "private",
                            "description": "Private DITT scenes",
                        },
                    ],
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

    def test_run_with_operation_lock_reports_timings(self):
        lock = threading.Lock()
        result = runnerctl_api.run_with_operation_lock(
            lock,
            lambda: {"result": "ok", "timings": {"work_ms": 1}},
        )

        self.assertEqual(result["result"], "ok")
        self.assertEqual(result["timings"]["work_ms"], 1)
        self.assertIn("operation_lock_wait_ms", result["timings"])
        self.assertIn("operation_lock_held_ms", result["timings"])

    def test_choose_free_vmid_skips_used_values(self):
        vmid = runnerctl_api.choose_free_vmid({"start": 2000, "end": 2002}, {2000, 2001})
        self.assertEqual(vmid, 2002)

    def test_normalize_store_prefix_accepts_dummy_prefix(self):
        self.assertEqual(runnerctl_api.normalize_store_prefix("ditt/dummy"), "ditt/dummy/")

    def test_store_allowed_prefixes_accepts_public_and_private_reports(self):
        env = {
            "RUNNERCTL_STORE_ALLOWED_PREFIXES": "ditt/dummy/,ditt/public/,ditt/private/",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            self.assertEqual(
                runnerctl_api.store_allowed_prefixes(),
                ["ditt/dummy/", "ditt/public/", "ditt/private/"],
            )
            runnerctl_api.require_store_prefix_allowed("ditt/public/smoke/latest/")
            runnerctl_api.require_store_prefix_allowed("ditt/private/smoke/latest/")

    def test_normalize_store_file_path_rejects_traversal(self):
        with self.assertRaises(runnerctl_api.RunnerCtlError) as raised:
            runnerctl_api.normalize_store_file_path("../index.html")
        self.assertEqual(raised.exception.code, "invalid-request")

    def test_build_s3_put_request_uses_virtual_host_style_url(self):
        request = runnerctl_api.build_s3_put_request(
            {
                "endpoint": "https://s3.fr-par.scw.cloud",
                "region": "fr-par",
                "bucket": "devsh-store-prod",
                "access_key": "test-access",
                "secret_key": "test-secret",
            },
            "ditt/dummy/index.html",
            b"hello",
            "text/html",
            "no-store",
            request_datetime=runnerctl_api.datetime.datetime(2026, 4, 25, 10, 0, 0, tzinfo=runnerctl_api.datetime.timezone.utc),
        )
        self.assertEqual(request.full_url, "https://devsh-store-prod.s3.fr-par.scw.cloud/ditt/dummy/index.html")
        self.assertIn("AWS4-HMAC-SHA256", request.headers["Authorization"])
        self.assertNotIn("test-secret", request.headers["Authorization"])

    def test_publish_store_bundle_uploads_prepared_files(self):
        class FakeResponse:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return False

        calls = []

        def fake_open(request, timeout):
            calls.append((request, timeout))
            return FakeResponse()

        env = {
            "RUNNERCTL_STORE_ALLOWED_PREFIXES": "ditt/dummy/",
            "RUNNERCTL_STORE_S3_BUCKET": "devsh-store-prod",
            "RUNNERCTL_STORE_AWS_ACCESS_KEY_ID": "test-access",
            "RUNNERCTL_STORE_AWS_SECRET_ACCESS_KEY": "test-secret",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            result = runnerctl_api.publish_store_bundle(
                {
                    "prefix": "ditt/dummy/",
                    "files": [
                        {
                            "path": "index.html",
                            "content_base64": base64.b64encode(b"hello").decode("ascii"),
                            "content_type": "text/html",
                        }
                    ],
                },
                opener=fake_open,
            )

        self.assertEqual(result["result"], "published")
        self.assertEqual(result["url"], "https://store.devsh.eu/ditt/dummy/")
        self.assertEqual(result["file_count"], 1)
        self.assertEqual(calls[0][0].full_url, "https://devsh-store-prod.s3.fr-par.scw.cloud/ditt/dummy/index.html")

    def test_publish_store_artifact_zip_downloads_jenkins_artifact(self):
        class FakeResponse:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return False

        class FakeJenkinsClient:
            def __init__(self, payload):
                self.payload = payload
                self.downloads = []

            def download_artifact(self, job, build_number, artifact_path, target_path, max_bytes):
                self.downloads.append((job, build_number, artifact_path, max_bytes))
                target_path.write_bytes(self.payload)
                return len(self.payload)

        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w") as archive:
            archive.writestr("index.html", b"hello")
            archive.writestr("css/report.css", b"body{}")
        jenkins = FakeJenkinsClient(zip_buffer.getvalue())
        calls = []

        def fake_open(request, timeout):
            calls.append((request, timeout))
            return FakeResponse()

        env = {
            "RUNNERCTL_STORE_ALLOWED_PREFIXES": "ditt/private/",
            "RUNNERCTL_STORE_S3_BUCKET": "devsh-store-prod",
            "RUNNERCTL_STORE_AWS_ACCESS_KEY_ID": "test-access",
            "RUNNERCTL_STORE_AWS_SECRET_ACCESS_KEY": "test-secret",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            result = runnerctl_api.publish_store_artifact_zip(
                {
                    "prefix": "ditt/private/latest/",
                    "job": "ci/ditt/ex40-scene-smoke",
                    "build": "42",
                    "artifact": "publish.zip",
                },
                jenkins,
                opener=fake_open,
            )

        self.assertEqual(result["result"], "published")
        self.assertEqual(result["file_count"], 2)
        self.assertEqual(result["bytes"], 11)
        self.assertEqual(jenkins.downloads[0][0], "ci/ditt/ex40-scene-smoke")
        self.assertEqual(jenkins.downloads[0][1], 42)
        uploaded_urls = [call[0].full_url for call in calls]
        self.assertIn("https://devsh-store-prod.s3.fr-par.scw.cloud/ditt/private/latest/index.html", uploaded_urls)
        self.assertIn("https://devsh-store-prod.s3.fr-par.scw.cloud/ditt/private/latest/css/report.css", uploaded_urls)

    def test_publish_store_artifact_zip_rejects_traversal_entry(self):
        class FakeJenkinsClient:
            def __init__(self, payload):
                self.payload = payload

            def download_artifact(self, job, build_number, artifact_path, target_path, max_bytes):
                target_path.write_bytes(self.payload)
                return len(self.payload)

        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w") as archive:
            archive.writestr("../index.html", b"bad")

        env = {
            "RUNNERCTL_STORE_ALLOWED_PREFIXES": "ditt/private/",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            with self.assertRaises(runnerctl_api.RunnerCtlError) as raised:
                runnerctl_api.publish_store_artifact_zip(
                    {
                        "prefix": "ditt/private/latest/",
                        "job": "ci/ditt/ex40-scene-smoke",
                        "build": "42",
                        "artifact": "publish.zip",
                    },
                    FakeJenkinsClient(zip_buffer.getvalue()),
                )

        self.assertEqual(raised.exception.code, "invalid-request")


class JenkinsApiClientTests(unittest.TestCase):
    def test_post_refreshes_crumb_and_retries_after_stale_403(self):
        class FakeResponse:
            def __init__(self, payload):
                self.payload = payload

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return False

            def read(self):
                return self.payload

        class FakeOpener:
            def __init__(self):
                self.calls = []
                self.crumbs = ["stale-crumb", "fresh-crumb"]
                self.post_attempts = 0

            def open(self, request, timeout):
                headers = {key.lower(): value for key, value in request.header_items()}
                self.calls.append((request.get_method(), request.full_url, headers, request.data, timeout))
                if request.full_url.endswith("/crumbIssuer/api/json"):
                    crumb = self.crumbs.pop(0)
                    payload = json.dumps({"crumbRequestField": "Jenkins-Crumb", "crumb": crumb}).encode("utf-8")
                    return FakeResponse(payload)
                if request.full_url.endswith("/scriptText"):
                    self.post_attempts += 1
                    if self.post_attempts == 1:
                        raise urllib.error.HTTPError(
                            request.full_url,
                            403,
                            "Forbidden",
                            {},
                            io.BytesIO(b"stale crumb"),
                        )
                    return FakeResponse(b"ok\n")
                raise AssertionError(f"unexpected request: {request.full_url}")

        opener = FakeOpener()
        client = runnerctl_api.JenkinsApiClient(
            "http://jenkins.example.invalid",
            "https://jenkins.example.invalid",
            "admin",
            "password",
            timeout_seconds=7,
        )
        client.opener = opener

        self.assertEqual(client.script_text("println('ok')"), "ok\n")

        script_calls = [call for call in opener.calls if call[1].endswith("/scriptText")]
        crumb_calls = [call for call in opener.calls if call[1].endswith("/crumbIssuer/api/json")]
        self.assertEqual(len(script_calls), 2)
        self.assertEqual(len(crumb_calls), 2)
        self.assertEqual(script_calls[0][2]["jenkins-crumb"], "stale-crumb")
        self.assertEqual(script_calls[1][2]["jenkins-crumb"], "fresh-crumb")
        self.assertEqual(script_calls[1][4], 7)


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
            self.assertEqual(record["git_object_cache"]["stores"][0]["git_url"], "git://10.254.254.254:9418/nabla-media-public.git")
            self.assertEqual(record["git_object_cache"]["git_client_sha256"], "04f937e1f0918b17b9be6f2294cb2bb66e96e1d9832d1c298e2de088a1d0e668")
            self.assertEqual(result["git_object_cache"]["stores"][1]["id"], "ditt-reference-scenes")
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
            self.assertEqual(record["git_object_cache"]["api_url"], "http://10.254.254.254:18082")
            self.assertEqual(record["git_object_cache"]["git_client_url"], "http://10.254.254.254:18081/MinGit.zip")

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
            self.assertIn("New-NetRoute", script)
            self.assertIn("curl.exe", script)
            self.assertIn("--resolve", script)
            self.assertIn("-Djdk.net.hosts.file=", script)
            self.assertIn("runnerctl: agentJarDownloadError attempt={0}", script)
            self.assertIn("-TimeoutSec 10", script)
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

    def test_janitor_removes_pretagged_orphan_runner_vm(self):
        with tempfile.TemporaryDirectory() as directory:
            lease_store = runnerctl_api.LeaseStore(Path(directory) / "leases.json")
            client = FakeProxmoxClient()
            client.vmids = {9002, 2000}
            client.vm_configs = {
                2000: {
                    "name": "runnerctl-hot-win-gpu-nvidia-99999999",
                    "tags": "gpu;nvidia;runnerctl;template;windows",
                }
            }
            registry = FakeProxmoxRegistry(client)
            result = runnerctl_api.run_janitor(registry, lease_store, SAMPLE_INVENTORY, {})
            self.assertEqual(result["cleaned_count"], 1)
            self.assertEqual(result["cleaned"][0]["reason"], "orphan-runner-vm")
            self.assertEqual(client.destroy_requests, [("pve-rtx-01", 2000)])


if __name__ == "__main__":
    unittest.main()
