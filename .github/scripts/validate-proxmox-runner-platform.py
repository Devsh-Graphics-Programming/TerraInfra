#!/usr/bin/env python3
from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml


ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
LABEL_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]*$")
CHANNEL_RE = re.compile(r"^[a-z0-9][a-z0-9/._-]*$")
ENV_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")
SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def fail(message: str) -> None:
    raise SystemExit(message)


def require(condition: bool, message: str) -> None:
    if not condition:
        fail(message)


def require_id(value: object, field: str) -> str:
    require(isinstance(value, str) and bool(ID_RE.match(value)), f"{field} is invalid")
    return value


def require_label(value: object, field: str) -> str:
    require(isinstance(value, str) and bool(LABEL_RE.match(value)), f"{field} is invalid")
    return value


def require_env(value: object, field: str) -> None:
    require(isinstance(value, str) and bool(ENV_RE.match(value)), f"{field} must be an environment variable name")


def require_example(value: object, field: str) -> None:
    require(isinstance(value, str) and value.startswith("example-"), f"{field} must use example-* placeholder")


def require_safe_name(value: object, field: str) -> None:
    require(isinstance(value, str) and bool(SAFE_NAME_RE.match(value)), f"{field} is invalid")


def require_positive_int(value: object, field: str) -> None:
    require(isinstance(value, int) and value > 0, f"{field} must be a positive integer")


def require_port(value: object, field: str) -> None:
    require(isinstance(value, int) and 1 <= value <= 65535, f"{field} must be a TCP port")


def require_unique(items: list[str], field: str) -> None:
    require(len(set(items)) == len(items), f"{field} must be unique")


def main() -> None:
    if len(sys.argv) != 2:
        fail("usage: validate-proxmox-runner-platform.py <path>")

    path = Path(sys.argv[1])
    data = yaml.safe_load(path.read_text(encoding="utf-8"))

    require(data.get("version") == 3, "version must be 3")

    defaults = data.get("defaults")
    require(isinstance(defaults, dict), "defaults must be a map")
    require(defaults.get("backend") == "proxmox", "defaults backend must be proxmox")
    require(defaults.get("clone_mode") == "linked", "defaults clone_mode must be linked")
    default_ttl = defaults.get("lease_ttl_minutes")
    require(isinstance(default_ttl, int) and 5 <= default_ttl <= 720, "defaults lease_ttl_minutes must be 5..720")
    api_credentials = defaults.get("api_credentials") or {}
    require_env(api_credentials.get("token_id_env"), "defaults api_credentials token_id_env")
    require_env(api_credentials.get("token_secret_env"), "defaults api_credentials token_secret_env")

    proxmox = data.get("proxmox")
    require(isinstance(proxmox, dict), "proxmox must be a map")
    hosts = proxmox.get("hosts")
    require(isinstance(hosts, list) and hosts, "proxmox hosts must be a non-empty array")

    host_ids: set[str] = set()
    gpu_ids_by_host: dict[str, set[str]] = {}
    for host in hosts:
        require(isinstance(host, dict), "host entry must be a map")
        host_id = require_id(host.get("id"), "host id")
        require(host_id not in host_ids, "host ids must be unique")
        require_example(host_id, "host id")
        host_ids.add(host_id)

        require_example(host.get("node"), "host node")
        require(isinstance(host.get("enabled"), bool), "host enabled must be boolean")
        require_positive_int(host.get("priority"), "host priority")

        labels = host.get("labels")
        require(isinstance(labels, list) and labels, "host labels must be a non-empty array")
        require_unique(labels, "host labels")
        for label in labels:
            require_label(label, f"host label {label}")

        api = host.get("api") or {}
        require(isinstance(api, dict), "host api must be a map")
        require(isinstance(api.get("url"), str) and api["url"].startswith("https://127.0.0.1:"), "host api url must use local reverse tunnel")
        require_env(api.get("token_id_env"), "host api token_id_env")
        require_env(api.get("token_secret_env"), "host api token_secret_env")
        tunnel = api.get("tunnel") or {}
        require(tunnel.get("type") == "reverse-ssh", "host tunnel type must be reverse-ssh")
        require_example(tunnel.get("server_host"), "host tunnel server_host")
        require_port(tunnel.get("server_port"), "host tunnel server_port")
        require(tunnel.get("remote_bind_host") == "127.0.0.1", "host tunnel remote_bind_host must be loopback")
        require_port(tunnel.get("remote_bind_port"), "host tunnel remote_bind_port")

        pools = host.get("pools") or {}
        require_example(pools.get("templates"), "host pools templates")
        require_example(pools.get("runners"), "host pools runners")

        storage = host.get("storage") or {}
        require_example(storage.get("runtime"), "host storage runtime")
        require_example(storage.get("iso"), "host storage iso")

        network = host.get("network") or {}
        require_example(network.get("bridge"), "host network bridge")
        vlan_tag = network.get("vlan_tag")
        require(isinstance(vlan_tag, int) and 1 <= vlan_tag <= 4094, "host network vlan_tag must be 1..4094")

        runner_range = ((host.get("vmid_ranges") or {}).get("runner")) or {}
        range_start = runner_range.get("start")
        range_end = runner_range.get("end")
        require(isinstance(range_start, int) and range_start > 0, "host runner VMID range start must be positive")
        require(isinstance(range_end, int) and range_end > range_start, "host runner VMID range end must be greater than start")

        smoke = host.get("smoke") or {}
        require_positive_int(smoke.get("source_vmid"), "host smoke source_vmid")
        require_positive_int(smoke.get("clone_vmid"), "host smoke clone_vmid")
        require(smoke.get("source_vmid") != smoke.get("clone_vmid"), "host smoke VMIDs must differ")

        runtime_profile = host.get("runtime_profile") or {}
        require(runtime_profile.get("mode") == "runtime-only", "host runtime_profile mode must be runtime-only")
        components = runtime_profile.get("components")
        require(isinstance(components, list) and components, "host runtime components must be a non-empty array")
        for component in components:
            require_label(component, f"runtime component {component}")

        gpu_ids: set[str] = set()
        for gpu in host.get("gpu_devices", []):
            require(isinstance(gpu, dict), "gpu device must be a map")
            gpu_id = require_id(gpu.get("id"), "gpu device id")
            require(gpu_id not in gpu_ids, "gpu device ids must be unique per host")
            gpu_ids.add(gpu_id)
            require(gpu.get("vendor") == "nvidia", "example gpu vendor must be nvidia")
            require(isinstance(gpu.get("pci_host"), str) and re.match(r"^[0-9a-fA-F:.]+$", gpu["pci_host"]), "gpu pci_host is invalid")
            require(isinstance(gpu.get("exclusive"), bool), "gpu exclusive must be boolean")
            gpu_labels = gpu.get("labels")
            require(isinstance(gpu_labels, list) and gpu_labels, "gpu labels must be a non-empty array")
            for label in gpu_labels:
                require_label(label, f"gpu label {label}")
        gpu_ids_by_host[host_id] = gpu_ids

    templates = data.get("templates")
    require(isinstance(templates, list) and templates, "templates must be a non-empty array")

    template_ids: set[str] = set()
    for template in templates:
        require(isinstance(template, dict), "template entry must be a map")
        template_id = require_id(template.get("id"), "template id")
        require(template_id not in template_ids, "template ids must be unique")
        template_ids.add(template_id)

        channel = template.get("channel")
        require(isinstance(channel, str) and CHANNEL_RE.match(channel), "template channel is invalid")

        builder = template.get("builder")
        require(builder in {"proxmox-iso", "proxmox-clone"}, "template builder must be proxmox-iso or proxmox-clone")

        parent = template.get("parent")
        if builder == "proxmox-clone":
            require(isinstance(parent, str) and parent, "clone template parent must be set")
        else:
            require(parent is None, "iso template must not set parent")

        communicator = template.get("communicator") or {}
        require(communicator.get("type") == "winrm", "template communicator type must be winrm")

        placements = template.get("placements")
        require(isinstance(placements, list) and placements, "template placements must be a non-empty array")
        placed_hosts: set[str] = set()
        for placement in placements:
            require(isinstance(placement, dict), "template placement must be a map")
            placement_host = require_id(placement.get("host"), "template placement host")
            require(placement_host in host_ids, "template placement host must reference an existing host")
            require(placement_host not in placed_hosts, "template placement host must be unique per template")
            placed_hosts.add(placement_host)
            require_positive_int(placement.get("vmid"), "template placement vmid")
            gpu_device = placement.get("gpu_device")
            if gpu_device is not None:
                require(gpu_device in gpu_ids_by_host[placement_host], "template placement gpu_device must reference host gpu device")

    for template in templates:
        parent = template.get("parent")
        if parent is not None:
            require(parent in template_ids, "clone template parent must exist")

    runner_classes = data.get("runner_classes")
    require(isinstance(runner_classes, list) and runner_classes, "runner_classes must be a non-empty array")

    runner_ids: set[str] = set()
    for runner_class in runner_classes:
        require(isinstance(runner_class, dict), "runner class entry must be a map")
        runner_id = require_id(runner_class.get("id"), "runner class id")
        require(runner_id not in runner_ids, "runner class ids must be unique")
        runner_ids.add(runner_id)

        labels = runner_class.get("labels")
        require(isinstance(labels, list) and labels, "runner class labels must be a non-empty array")
        require_unique(labels, "runner class labels")
        for label in labels:
            require_label(label, f"runner class label {label}")
        require("runtime-only" in labels, "runner class must declare runtime-only")

        template_ref = runner_class.get("template")
        require(isinstance(template_ref, str) and template_ref in template_ids, "runner class template must reference an existing template")

        connection = runner_class.get("connection") or {}
        require(connection.get("type") == "winrm", "runner class connection type must be winrm")

        host_selector = runner_class.get("host_selector") or {}
        selector_labels = host_selector.get("labels")
        require(isinstance(selector_labels, list) and selector_labels, "runner class host_selector labels must be a non-empty array")
        for label in selector_labels:
            require_label(label, f"runner class host selector label {label}")

        runtime = runner_class.get("runtime") or {}
        require(runtime.get("pool") == "runners", "runner class runtime pool must reference host pool key")
        require(runtime.get("storage") == "runtime", "runner class runtime storage must reference host storage key")
        require(runtime.get("vmid_range") == "runner", "runner class runtime vmid_range must reference host range key")

        warm_pool = runner_class.get("warm_pool") or {}
        min_ready = warm_pool.get("min_ready")
        max_ready = warm_pool.get("max_ready")
        require(isinstance(min_ready, int) and min_ready >= 0, "warm_pool min_ready must be >= 0")
        require(isinstance(max_ready, int) and max_ready >= min_ready, "warm_pool max_ready must be >= min_ready")

        policy = runner_class.get("policy") or {}
        ttl = policy.get("lease_ttl_minutes")
        require(isinstance(ttl, int) and 5 <= ttl <= 720, "runner class lease_ttl_minutes must be 5..720")
        for field in ("boot_timeout_minutes", "health_timeout_minutes"):
            value = policy.get(field)
            require(isinstance(value, int) and 1 <= value <= 120, f"runner class {field} must be 1..120")
        for field in ("gpu_exclusive", "release_on_failure", "destroy_after_job"):
            require(isinstance(policy.get(field), bool), f"runner class {field} must be boolean")

        health_checks = runner_class.get("health_checks")
        require(isinstance(health_checks, list) and health_checks, "runner class health_checks must be a non-empty array")
        for check in health_checks:
            require_label(check, f"runner class health check {check}")

    janitor = data.get("janitor")
    require(isinstance(janitor, dict), "janitor must be a map")
    require(janitor.get("backend") == "proxmox", "janitor backend must be proxmox")
    require(janitor.get("pool") == "runners", "janitor pool must reference host pool key")
    require(janitor.get("vmid_range") == "runner", "janitor vmid_range must reference host range key")

    required_tags = janitor.get("require_tags")
    require(isinstance(required_tags, list) and required_tags, "janitor require_tags must be a non-empty array")
    for tag in required_tags:
        require_label(tag, f"janitor tag {tag}")

    stale_lease_after = janitor.get("stale_lease_after_minutes")
    require(isinstance(stale_lease_after, int) and 15 <= stale_lease_after <= 1440, "janitor stale_lease_after_minutes must be 15..1440")


if __name__ == "__main__":
    main()
