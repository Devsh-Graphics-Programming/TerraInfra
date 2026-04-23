import json
import tempfile
import unittest
from pathlib import Path

import runnerctl_api


SAMPLE_INVENTORY = {
    "version": 2,
    "defaults": {
        "backend": "proxmox",
        "lease_ttl_minutes": 120,
    },
    "templates": [
        {
            "id": "windows-gpu-nvidia-stable",
            "channel": "windows-gpu-nvidia/stable",
            "guest_os": "windows11",
            "backend": {
                "node": "node3",
                "pool": "ci-images",
                "storage": "local-lvm",
                "reverse_tunnel_port": 18006,
            },
        }
    ],
    "runner_classes": [
        {
            "id": "win-gpu-nvidia",
            "labels": ["windows", "gpu", "nvidia", "vulkan", "gpu-class-rtx-2070"],
            "template": "windows-gpu-nvidia-stable",
            "connection": {"type": "winrm"},
            "backend": {
                "target_node": "node3",
                "pool": "ci-runners",
                "storage": "local-lvm",
            },
            "warm_pool": {"min_ready": 1, "max_ready": 1},
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
            self.assertEqual(loaded["version"], 2)
            self.assertEqual(loaded["runner_classes"][0]["id"], "win-gpu-nvidia")


class ResolveRunnerTests(unittest.TestCase):
    def test_resolve_runner_returns_merged_backend(self):
        resolved = runnerctl_api.resolve_runner(
            SAMPLE_INVENTORY,
            "win-gpu-nvidia",
            ["windows", "gpu", "nvidia"],
        )
        self.assertEqual(resolved["runner_class"], "win-gpu-nvidia")
        self.assertEqual(resolved["template"]["channel"], "windows-gpu-nvidia/stable")
        self.assertEqual(resolved["backend"]["target_node"], "node3")
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


if __name__ == "__main__":
    unittest.main()
