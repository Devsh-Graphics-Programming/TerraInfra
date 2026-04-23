#!/usr/bin/env python3
from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml


ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
LABEL_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]*$")
CHANNEL_RE = re.compile(r"^[a-z0-9][a-z0-9/._-]*$")


def fail(message: str) -> None:
    raise SystemExit(message)


def require(condition: bool, message: str) -> None:
    if not condition:
        fail(message)


def require_example(value: object, field: str) -> None:
    require(isinstance(value, str) and value.startswith("example-"), f"{field} must use example-* placeholder")


def require_positive_int(value: object, field: str) -> None:
    require(isinstance(value, int) and value > 0, f"{field} must be a positive integer")


def main() -> None:
    if len(sys.argv) != 2:
        fail("usage: validate-proxmox-runner-platform.py <path>")

    path = Path(sys.argv[1])
    data = yaml.safe_load(path.read_text(encoding="utf-8"))

    require(data.get("version") == 2, "version must be 2")

    defaults = data.get("defaults")
    require(isinstance(defaults, dict), "defaults must be a map")
    require(defaults.get("backend") == "proxmox", "defaults backend must be proxmox")
    require(defaults.get("clone_mode") == "linked", "defaults clone_mode must be linked")
    default_ttl = defaults.get("lease_ttl_minutes")
    require(isinstance(default_ttl, int) and 5 <= default_ttl <= 720, "defaults lease_ttl_minutes must be 5..720")

    templates = data.get("templates")
    require(isinstance(templates, list) and templates, "templates must be a non-empty array")

    template_ids: set[str] = set()
    for template in templates:
        require(isinstance(template, dict), "template entry must be a map")
        template_id = template.get("id")
        require(isinstance(template_id, str) and ID_RE.match(template_id), "template id is invalid")
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

        backend = template.get("backend") or {}
        require(backend.get("type") == "proxmox", "template backend type must be proxmox")
        require_example(backend.get("node"), "template backend node")
        require_example(backend.get("pool"), "template backend pool")
        require_example(backend.get("storage"), "template backend storage")
        require_positive_int(backend.get("template_vmid"), "template_vmid")

    for template in templates:
        parent = template.get("parent")
        if parent is not None:
            require(parent in template_ids, "clone template parent must exist")

    runner_classes = data.get("runner_classes")
    require(isinstance(runner_classes, list) and runner_classes, "runner_classes must be a non-empty array")

    runner_ids: set[str] = set()
    for runner_class in runner_classes:
        require(isinstance(runner_class, dict), "runner class entry must be a map")
        runner_id = runner_class.get("id")
        require(isinstance(runner_id, str) and ID_RE.match(runner_id), "runner class id is invalid")
        require(runner_id not in runner_ids, "runner class ids must be unique")
        runner_ids.add(runner_id)

        labels = runner_class.get("labels")
        require(isinstance(labels, list) and labels, "runner class labels must be a non-empty array")
        require(len(set(labels)) == len(labels), "runner class labels must be unique")
        for label in labels:
            require(isinstance(label, str) and LABEL_RE.match(label), f"runner class label is invalid: {label}")

        template_ref = runner_class.get("template")
        require(isinstance(template_ref, str) and template_ref in template_ids, "runner class template must reference an existing template")

        connection = runner_class.get("connection") or {}
        require(connection.get("type") == "winrm", "runner class connection type must be winrm")

        backend = runner_class.get("backend") or {}
        require(backend.get("type") == "proxmox", "runner class backend type must be proxmox")
        require_example(backend.get("target_node"), "runner class backend target_node")
        require_example(backend.get("pool"), "runner class backend pool")
        require_example(backend.get("storage"), "runner class backend storage")

        vmid_range = backend.get("vmid_range") or {}
        range_start = vmid_range.get("start")
        range_end = vmid_range.get("end")
        require(isinstance(range_start, int) and range_start > 0, "runner class vmid range start must be positive")
        require(isinstance(range_end, int) and range_end > range_start, "runner class vmid range end must be greater than start")

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
            require(isinstance(check, str) and re.match(r"^[a-z0-9][a-z0-9-]*$", check), f"runner class health check is invalid: {check}")

    janitor = data.get("janitor")
    require(isinstance(janitor, dict), "janitor must be a map")
    require(janitor.get("backend") == "proxmox", "janitor backend must be proxmox")
    require_example(janitor.get("pool"), "janitor pool")

    janitor_range = janitor.get("vmid_range") or {}
    janitor_start = janitor_range.get("start")
    janitor_end = janitor_range.get("end")
    require(isinstance(janitor_start, int) and janitor_start > 0, "janitor vmid range start must be positive")
    require(isinstance(janitor_end, int) and janitor_end > janitor_start, "janitor vmid range end must be greater than start")

    required_tags = janitor.get("require_tags")
    require(isinstance(required_tags, list) and required_tags, "janitor require_tags must be a non-empty array")
    for tag in required_tags:
        require(isinstance(tag, str) and "=" in tag, f"janitor tag is invalid: {tag}")

    stale_lease_after = janitor.get("stale_lease_after_minutes")
    require(isinstance(stale_lease_after, int) and 15 <= stale_lease_after <= 1440, "janitor stale_lease_after_minutes must be 15..1440")


if __name__ == "__main__":
    main()
