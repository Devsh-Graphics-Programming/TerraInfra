#!/usr/bin/env python3

import base64
import http.cookiejar
import ipaddress
import json
import os
import re
import ssl
import sys
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
import uuid
import xml.etree.ElementTree as ElementTree
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*$")
LABEL_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_.-]*$")
SAFE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
ENV_NAME_PATTERN = re.compile(r"^[A-Z_][A-Z0-9_]*$")
LEASE_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
ACTIVE_RUNNER_STATES = {"creating", "ready", "leased", "booting", "healthy", "agent-online"}
READY_POOL_STATES = {"ready"}
HOT_POOL_TAGS_READY = "runnerctl;lifecycle-ephemeral;hot-pool;ready"
HOT_POOL_TAGS_CREATING = "runnerctl;lifecycle-ephemeral;hot-pool;creating"
LEASED_TAGS = "runnerctl;lifecycle-ephemeral;leased"


class RunnerCtlError(Exception):
    def __init__(self, status_code, code, message, details=None):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details or {}


def require_pattern(value, pattern, field_name):
    text = str(value).strip()
    if not pattern.fullmatch(text):
        raise RunnerCtlError(
            HTTPStatus.BAD_REQUEST,
            "invalid-request",
            f"Invalid value for {field_name}.",
            {"field": field_name},
        )
    return text


def require_int(value, field_name, minimum=None, maximum=None):
    text = str(value).strip()
    if not text.isdigit():
        raise RunnerCtlError(
            HTTPStatus.BAD_REQUEST,
            "invalid-request",
            f"Invalid numeric value for {field_name}.",
            {"field": field_name},
        )
    parsed = int(text)
    if minimum is not None and parsed < minimum:
        raise RunnerCtlError(
            HTTPStatus.BAD_REQUEST,
            "invalid-request",
            f"{field_name} must be at least {minimum}.",
            {"field": field_name},
        )
    if maximum is not None and parsed > maximum:
        raise RunnerCtlError(
            HTTPStatus.BAD_REQUEST,
            "invalid-request",
            f"{field_name} must be at most {maximum}.",
            {"field": field_name},
        )
    return parsed


def require_list(value, field_name):
    if not isinstance(value, list):
        raise RunnerCtlError(
            HTTPStatus.BAD_REQUEST,
            "invalid-request",
            f"{field_name} must be a JSON array.",
            {"field": field_name},
        )
    return value


def merge_dicts(base, override):
    result = {}
    for source in (base or {}, override or {}):
        for key, value in source.items():
            if isinstance(value, dict) and isinstance(result.get(key), dict):
                result[key] = merge_dicts(result[key], value)
            else:
                result[key] = value
    return result


def now_epoch():
    return int(time.time())


def monotonic_ms():
    return int(time.monotonic() * 1000)


def elapsed_ms(start_ms):
    return max(0, monotonic_ms() - start_ms)


def merge_timings(*items):
    merged = {}
    for item in items:
        if isinstance(item, dict):
            merged.update(item)
    return merged


class InventoryStore:
    def __init__(self, path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._mtime = None
        self._data = None

    def load(self):
        current_mtime = self.path.stat().st_mtime
        with self._lock:
            if self._data is not None and self._mtime == current_mtime:
                return self._data
            with self.path.open("r", encoding="utf-8") as handle:
                loaded = json.load(handle)
            if loaded.get("version") != 3:
                raise RunnerCtlError(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    "invalid-inventory",
                    "Runner platform inventory version is unsupported.",
                    {"expected_version": 3, "actual_version": loaded.get("version")},
                )
            validate_inventory_shape(loaded)
            self._data = loaded
            self._mtime = current_mtime
            return loaded


class LeaseStore:
    def __init__(self, path):
        self.path = Path(path)
        self._lock = threading.Lock()

    def _read(self):
        if not self.path.exists():
            return {}
        with self.path.open("r", encoding="utf-8") as handle:
            return json.load(handle)

    def _write(self, leases):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.path.with_suffix(".tmp")
        with temp_path.open("w", encoding="utf-8") as handle:
            json.dump(leases, handle, sort_keys=True)
        temp_path.replace(self.path)

    def all(self):
        with self._lock:
            return self._read()

    def get(self, lease_id):
        with self._lock:
            return self._read().get(lease_id)

    def put(self, lease_id, record):
        with self._lock:
            leases = self._read()
            leases[lease_id] = record
            self._write(leases)

    def update(self, lease_id, updates):
        with self._lock:
            leases = self._read()
            record = leases.get(lease_id)
            if record is None:
                return None
            record.update(updates)
            leases[lease_id] = record
            self._write(leases)
            return record

    def delete(self, lease_id):
        with self._lock:
            leases = self._read()
            record = leases.pop(lease_id, None)
            self._write(leases)
            return record


def validate_inventory_shape(inventory):
    if not isinstance(inventory.get("proxmox"), dict):
        raise RunnerCtlError(HTTPStatus.INTERNAL_SERVER_ERROR, "invalid-inventory", "Inventory is missing proxmox config.")
    if not isinstance(inventory["proxmox"].get("hosts"), list) or not inventory["proxmox"]["hosts"]:
        raise RunnerCtlError(HTTPStatus.INTERNAL_SERVER_ERROR, "invalid-inventory", "Inventory must define at least one Proxmox host.")
    if not isinstance(inventory.get("templates"), list) or not inventory["templates"]:
        raise RunnerCtlError(HTTPStatus.INTERNAL_SERVER_ERROR, "invalid-inventory", "Inventory must define at least one template.")
    if not isinstance(inventory.get("runner_classes"), list) or not inventory["runner_classes"]:
        raise RunnerCtlError(HTTPStatus.INTERNAL_SERVER_ERROR, "invalid-inventory", "Inventory must define at least one runner class.")


def find_by_id(items, item_id):
    for item in items:
        if item.get("id") == item_id:
            return item
    return None


def groovy_string(value):
    return json.dumps(str(value))


def powershell_string(value):
    return "'" + str(value).replace("'", "''") + "'"


def compact_guest_output(value, limit=1600):
    text = str(value or "").replace("_x000D__x000A_", "\n")
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"(?i)(-secret\s+)[^\s]+", r"\1<redacted>", text)
    text = re.sub(r"\b[A-Za-z0-9+/=_-]{48,}\b", "<redacted>", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > limit:
        return text[:limit] + "..."
    return text


def guest_exec_output_summary(status):
    parts = []
    for key in ("err-data", "out-data"):
        summary = compact_guest_output(status.get(key))
        if summary:
            parts.append(f"{key}: {summary}")
    return " | ".join(parts)


def optional_ipv4_address(value, field_name):
    if value is None or str(value).strip() == "":
        return None
    text = str(value).strip()
    try:
        parsed = ipaddress.ip_address(text)
    except ValueError as exc:
        raise RunnerCtlError(
            HTTPStatus.INTERNAL_SERVER_ERROR,
            "invalid-inventory",
            f"Invalid IPv4 address for {field_name}.",
            {"field": field_name},
        ) from exc
    if parsed.version != 4:
        raise RunnerCtlError(
            HTTPStatus.INTERNAL_SERVER_ERROR,
            "invalid-inventory",
            f"{field_name} must be an IPv4 address.",
            {"field": field_name},
        )
    return str(parsed)


def jenkins_public_hostname(public_url):
    parsed = urllib.parse.urlparse(public_url)
    if not parsed.hostname:
        raise RunnerCtlError(
            HTTPStatus.INTERNAL_SERVER_ERROR,
            "invalid-jenkins-url",
            "Jenkins public URL must include a hostname.",
            {},
        )
    return parsed.hostname


def parse_required_labels(value, field_name="required_labels"):
    labels = [require_pattern(label, LABEL_PATTERN, field_name) for label in require_list(value, field_name)]
    if not labels:
        raise RunnerCtlError(
            HTTPStatus.BAD_REQUEST,
            "invalid-request",
            f"{field_name} must contain at least one label.",
            {"field": field_name},
        )
    if len(set(labels)) != len(labels):
        raise RunnerCtlError(
            HTTPStatus.BAD_REQUEST,
            "invalid-request",
            f"{field_name} must not contain duplicate labels.",
            {"field": field_name},
        )
    return labels


def inventory_hosts(inventory):
    return inventory.get("proxmox", {}).get("hosts", [])


def host_by_id(inventory, host_id):
    host = find_by_id(inventory_hosts(inventory), host_id)
    if host is None:
        raise RunnerCtlError(
            HTTPStatus.INTERNAL_SERVER_ERROR,
            "invalid-inventory",
            "Lease points to a Proxmox host that is no longer configured.",
            {"host_id": host_id},
        )
    return host


def template_placement_for_host(template, host_id):
    for placement in template.get("placements", []):
        if placement.get("host") == host_id:
            return placement
    return None


def host_labels(host):
    labels = set(host.get("labels", []))
    for gpu in host.get("gpu_devices", []):
        labels.update(gpu.get("labels", []))
        vendor = gpu.get("vendor")
        if vendor:
            labels.add(str(vendor).lower())
            labels.add(f"gpu-vendor-{str(vendor).lower()}")
    return labels


def host_is_schedulable(host):
    return bool(host.get("enabled", True)) and not bool(host.get("draining", False))


def named_host_value(host, group_name, value_ref, field_name):
    values = host.get(group_name, {})
    if not isinstance(values, dict):
        raise RunnerCtlError(
            HTTPStatus.INTERNAL_SERVER_ERROR,
            "invalid-inventory",
            f"Host {group_name} must be a map.",
            {"host_id": host.get("id"), "field": group_name},
        )
    if value_ref in values:
        return values[value_ref]
    if value_ref:
        return value_ref
    raise RunnerCtlError(
        HTTPStatus.INTERNAL_SERVER_ERROR,
        "invalid-inventory",
        f"Host field {field_name} could not be resolved.",
        {"host_id": host.get("id"), "field": field_name},
    )


def host_vmid_range(host, range_ref):
    ranges = host.get("vmid_ranges", {})
    if range_ref not in ranges:
        raise RunnerCtlError(
            HTTPStatus.INTERNAL_SERVER_ERROR,
            "invalid-inventory",
            "Host VMID range is missing.",
            {"host_id": host.get("id"), "vmid_range": range_ref},
        )
    vmid_range = ranges[range_ref]
    start = require_int(vmid_range.get("start", ""), "vmid_range.start", minimum=100, maximum=999999999)
    end = require_int(vmid_range.get("end", ""), "vmid_range.end", minimum=start, maximum=999999999)
    return {"start": start, "end": end}


def smoke_vmids(host):
    smoke = host.get("smoke", {})
    return {
        "source_vmid": require_int(smoke.get("source_vmid", ""), "smoke.source_vmid", minimum=100, maximum=999999999),
        "clone_vmid": require_int(smoke.get("clone_vmid", ""), "smoke.clone_vmid", minimum=100, maximum=999999999),
    }


def build_candidate(host, template, runner_class):
    placement = template_placement_for_host(template, host["id"])
    if placement is None:
        return None

    runtime = runner_class.get("runtime", {})
    pool_ref = runtime.get("pool", "runners")
    storage_ref = runtime.get("storage", "runtime")
    vmid_range_ref = runtime.get("vmid_range", "runner")
    network = host.get("network", {})

    template_vmid = require_int(placement.get("vmid", ""), "template_vmid", minimum=100, maximum=999999999)
    candidate = {
        "host_id": require_pattern(host.get("id", ""), ID_PATTERN, "host.id"),
        "node": require_pattern(host.get("node", ""), SAFE_NAME_PATTERN, "host.node"),
        "priority": int(host.get("priority", 100)),
        "template_vmid": template_vmid,
        "pool": require_pattern(named_host_value(host, "pools", pool_ref, "runtime.pool"), SAFE_NAME_PATTERN, "pool"),
        "storage": require_pattern(named_host_value(host, "storage", storage_ref, "runtime.storage"), SAFE_NAME_PATTERN, "storage"),
        "vmid_range": host_vmid_range(host, vmid_range_ref),
        "bridge": require_pattern(network.get("bridge", ""), SAFE_NAME_PATTERN, "network.bridge"),
        "vlan_tag": require_int(network.get("vlan_tag", ""), "network.vlan_tag", minimum=1, maximum=4094),
    }
    host_alias_ip = optional_ipv4_address(network.get("jenkins_host_alias_ip"), "network.jenkins_host_alias_ip")
    if host_alias_ip:
        candidate["jenkins_host_alias_ip"] = host_alias_ip
    if placement.get("gpu_device"):
        candidate["gpu_device"] = placement["gpu_device"]
    return candidate


def public_candidate(candidate):
    return {
        "host_id": candidate["host_id"],
        "node": candidate["node"],
        "template_vmid": candidate["template_vmid"],
        "pool": candidate["pool"],
        "storage": candidate["storage"],
        "vmid_range": candidate["vmid_range"],
        "bridge": candidate["bridge"],
        "vlan_tag": candidate["vlan_tag"],
        "jenkins_host_alias_ip": candidate.get("jenkins_host_alias_ip"),
    }


def runtime_network_config(candidate):
    return f"e1000,bridge={candidate['bridge']},tag={candidate['vlan_tag']},firewall=1"


def configure_runtime_network(client, node, vmid, candidate):
    client.request("POST", f"/nodes/{node}/qemu/{vmid}/config", data={"net0": runtime_network_config(candidate)})


def select_runner_class(inventory, runner_class, labels):
    requested_class = str(runner_class or "").strip()
    if requested_class:
        class_id = require_pattern(requested_class, ID_PATTERN, "runner_class")
        selected_class = find_by_id(inventory.get("runner_classes", []), class_id)
        if selected_class is None:
            raise RunnerCtlError(
                HTTPStatus.BAD_REQUEST,
                "unknown-runner-class",
                "Requested runner class is not configured.",
                {"runner_class": class_id},
            )
        return class_id, selected_class

    requested_labels = set(labels)
    matches = []
    for candidate in inventory.get("runner_classes", []):
        class_id = require_pattern(candidate.get("id", ""), ID_PATTERN, "runner_class")
        class_labels = set(candidate.get("labels", []))
        if requested_labels.issubset(class_labels):
            matches.append((len(class_labels), class_id, candidate))

    if not matches:
        raise RunnerCtlError(
            HTTPStatus.BAD_REQUEST,
            "no-runner-class",
            "No configured runner class satisfies the requested labels.",
            {"required_labels": sorted(requested_labels)},
        )

    matches.sort(key=lambda item: (item[0], item[1]))
    return matches[0][1], matches[0][2]


def resolve_runner_context(inventory, runner_class, required_labels):
    labels = parse_required_labels(required_labels)
    class_id, selected_class = select_runner_class(inventory, runner_class, labels)

    class_labels = set(selected_class.get("labels", []))
    missing_labels = [label for label in labels if label not in class_labels]
    if missing_labels:
        raise RunnerCtlError(
            HTTPStatus.BAD_REQUEST,
            "missing-labels",
            "Requested labels are not satisfied by the configured runner class.",
            {"runner_class": class_id, "missing_labels": missing_labels},
        )

    template_id = selected_class.get("template")
    selected_template = find_by_id(inventory.get("templates", []), template_id)
    if selected_template is None:
        raise RunnerCtlError(
            HTTPStatus.INTERNAL_SERVER_ERROR,
            "invalid-inventory",
            "Runner class points to a missing template.",
            {"runner_class": class_id, "template": template_id},
        )

    selector = selected_class.get("host_selector", {})
    selector_labels = set(selector.get("labels") or selected_class.get("labels", []))
    candidates = []
    skipped = []
    for host in inventory_hosts(inventory):
        host_id = host.get("id")
        if not host_is_schedulable(host):
            skipped.append({"host_id": host_id, "reason": "not-schedulable"})
            continue
        missing_host_labels = sorted(selector_labels - host_labels(host))
        if missing_host_labels:
            skipped.append({"host_id": host_id, "reason": "missing-labels", "missing_labels": missing_host_labels})
            continue
        candidate = build_candidate(host, selected_template, selected_class)
        if candidate is None:
            skipped.append({"host_id": host_id, "reason": "template-not-placed"})
            continue
        candidates.append(candidate)

    candidates.sort(key=lambda item: (item["priority"], item["host_id"]))
    if not candidates:
        raise RunnerCtlError(
            HTTPStatus.CONFLICT,
            "no-placement",
            "No Proxmox host satisfies the requested runner class.",
            {"runner_class": class_id, "skipped": skipped},
        )

    defaults = inventory.get("defaults", {})
    policy = merge_dicts(defaults, selected_class.get("policy"))
    resolved = {
        "runner_class": class_id,
        "labels": sorted(class_labels),
        "template": {
            "id": selected_template["id"],
            "channel": selected_template["channel"],
            "guest_os": selected_template.get("guest_os"),
        },
        "connection": merge_dicts(defaults.get("communicator"), selected_class.get("connection")),
        "placement": {
            "strategy": selected_class.get("placement_strategy", "first-available"),
            "candidates": [public_candidate(candidate) for candidate in candidates],
            "skipped": skipped,
        },
        "backend": public_candidate(candidates[0]),
        "policy": policy,
        "warm_pool": selected_class.get("warm_pool", {}),
        "health_checks": selected_class.get("health_checks", []),
    }
    return resolved, candidates


def resolve_runner(inventory, runner_class, required_labels):
    resolved, _ = resolve_runner_context(inventory, runner_class, required_labels)
    return resolved


def request_labels(request_data):
    if "required_labels" in request_data:
        return request_data.get("required_labels", [])
    return request_data.get("labels", [])


def resolve_lease_request(inventory, request_data):
    runner_class = request_data.get("runner_class")
    required_labels = request_labels(request_data)
    resolved, candidates = resolve_runner_context(inventory, runner_class, required_labels)
    ttl_minutes = request_data.get("lease_ttl_minutes")
    if ttl_minutes is None:
        ttl_minutes = int(resolved["policy"].get("lease_ttl_minutes", 120))
    else:
        ttl_minutes = require_int(ttl_minutes, "lease_ttl_minutes", minimum=5, maximum=720)
    resolved["policy"]["lease_ttl_minutes"] = ttl_minutes
    return resolved, candidates


class ProxmoxApiClient:
    def __init__(self, base_url, token_id, token_secret, timeout_seconds=30):
        if not base_url or not token_id or not token_secret:
            raise RunnerCtlError(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                "missing-backend-auth",
                "Proxmox API credentials are not configured for runnerctl.",
            )
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.headers = {
            "Authorization": f"PVEAPIToken={token_id}={token_secret}",
        }
        self.ssl_context = ssl._create_unverified_context()

    def _build_url(self, path, query=None):
        if query:
            encoded = urllib.parse.urlencode(query)
            return f"{self.base_url}{path}?{encoded}"
        return f"{self.base_url}{path}"

    def request(self, method, path, query=None, data=None):
        body = None
        headers = dict(self.headers)
        if data:
            body = urllib.parse.urlencode(data, doseq=True).encode("utf-8")
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        request = urllib.request.Request(
            self._build_url(path, query=query),
            data=body,
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(
                request,
                timeout=self.timeout_seconds,
                context=self.ssl_context,
            ) as response:
                payload = response.read().decode("utf-8").strip()
        except urllib.error.HTTPError as exc:
            raise RunnerCtlError(
                HTTPStatus.BAD_GATEWAY,
                "proxmox-http-error",
                "Proxmox API returned an unexpected response.",
                {"path": path, "upstream_status": exc.code},
            ) from exc
        except OSError as exc:
            raise RunnerCtlError(
                HTTPStatus.BAD_GATEWAY,
                "proxmox-network-error",
                "Runnerctl could not reach the Proxmox API.",
                {"path": path},
            ) from exc

        if not payload:
            return None
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise RunnerCtlError(
                HTTPStatus.BAD_GATEWAY,
                "proxmox-invalid-json",
                "Proxmox API returned invalid JSON.",
                {"path": path},
            ) from exc
        return parsed.get("data")

    def wait_task(self, node, upid, timeout_seconds):
        deadline = time.time() + timeout_seconds
        encoded_upid = urllib.parse.quote(upid, safe="")
        while time.time() < deadline:
            task = self.request("GET", f"/nodes/{node}/tasks/{encoded_upid}/status")
            if task.get("status") == "stopped":
                if task.get("exitstatus") != "OK":
                    raise RunnerCtlError(
                        HTTPStatus.BAD_GATEWAY,
                        "proxmox-task-failed",
                        "Proxmox background task failed.",
                        {"node": node, "upid": upid, "exitstatus": task.get("exitstatus")},
                    )
                return task
            time.sleep(1.0)
        raise RunnerCtlError(
            HTTPStatus.BAD_GATEWAY,
            "proxmox-task-timeout",
            "Timed out while waiting for a Proxmox task to finish.",
            {"node": node, "upid": upid},
        )

    def qemu_vmids(self, node):
        return {int(entry["vmid"]) for entry in self.request("GET", f"/nodes/{node}/qemu") or []}

    def vm_status(self, node, vmid):
        return self.request("GET", f"/nodes/{node}/qemu/{vmid}/status/current") or {}

    def vm_config(self, node, vmid):
        return self.request("GET", f"/nodes/{node}/qemu/{vmid}/config") or {}

    def start_vm(self, node, vmid):
        status = self.vm_status(node, vmid)
        if status.get("status") == "running":
            return False
        upid = self.request("POST", f"/nodes/{node}/qemu/{vmid}/status/start")
        if upid:
            self.wait_task(node, str(upid), 180)
        return True

    def stop_vm(self, node, vmid):
        status = self.vm_status(node, vmid)
        if status.get("status") != "running":
            return False
        upid = self.request("POST", f"/nodes/{node}/qemu/{vmid}/status/stop", data={"timeout": 120})
        if upid:
            self.wait_task(node, str(upid), 180)
        return True

    def agent_ping(self, node, vmid):
        self.request("POST", f"/nodes/{node}/qemu/{vmid}/agent/ping")
        return True

    def set_tags(self, node, vmid, tags):
        self.request("POST", f"/nodes/{node}/qemu/{vmid}/config", data={"tags": tags})
        return True

    def agent_network_get_interfaces(self, node, vmid):
        return self.request("GET", f"/nodes/{node}/qemu/{vmid}/agent/network-get-interfaces") or []

    def guest_exec(self, node, vmid, command, timeout_seconds):
        started = self.request(
            "POST",
            f"/nodes/{node}/qemu/{vmid}/agent/exec",
            data={"command": command},
        )
        pid = int(started["pid"])
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            status = self.request(
                "GET",
                f"/nodes/{node}/qemu/{vmid}/agent/exec-status",
                query={"pid": pid},
            )
            if status.get("exited"):
                if int(status.get("exitcode", 0)) != 0:
                    output_summary = guest_exec_output_summary(status)
                    message = "Guest command failed."
                    if output_summary:
                        message = f"{message} {output_summary}"
                    raise RunnerCtlError(
                        HTTPStatus.BAD_GATEWAY,
                        "guest-command-failed",
                        message,
                        {
                            "node": node,
                            "vmid": vmid,
                            "exitcode": status.get("exitcode"),
                            "output": output_summary,
                        },
                    )
                return status
            time.sleep(1.0)
        raise RunnerCtlError(
            HTTPStatus.BAD_GATEWAY,
            "guest-command-timeout",
            "Timed out while waiting for a guest command.",
            {"node": node, "vmid": vmid, "pid": pid},
        )

    def safe_destroy(self, node, vmid):
        if vmid not in self.qemu_vmids(node):
            return False
        self.stop_vm(node, vmid)
        upid = self.request(
            "DELETE",
            f"/nodes/{node}/qemu/{vmid}",
            query={"destroy-unreferenced-disks": 1, "purge": 1},
        )
        if upid:
            self.wait_task(node, str(upid), 180)
        return True


class ProxmoxClientRegistry:
    def __init__(self, env=None):
        self.env = env or os.environ
        self._lock = threading.Lock()
        self._clients = {}

    def _env(self, name, field_name, required=True):
        if not name:
            if required:
                raise RunnerCtlError(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    "invalid-inventory",
                    f"Environment variable name for {field_name} is missing.",
                )
            return ""
        require_pattern(name, ENV_NAME_PATTERN, field_name)
        value = self.env.get(name, "")
        if required and not value:
            raise RunnerCtlError(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                "missing-backend-auth",
                "Required Proxmox API environment variable is not set.",
                {"env": name, "field": field_name},
            )
        return value

    def client_for_host(self, inventory, host):
        defaults = inventory.get("defaults", {}).get("api_credentials", {})
        api = host.get("api", {})
        url = api.get("url")
        if not url:
            url = self._env(api.get("url_env") or defaults.get("url_env") or "PROXMOX_RUNNER_API_URL", "api.url")
        token_id = self._env(
            api.get("token_id_env") or defaults.get("token_id_env") or "PROXMOX_RUNNER_API_TOKEN_ID",
            "api.token_id_env",
        )
        token_secret = self._env(
            api.get("token_secret_env") or defaults.get("token_secret_env") or "PROXMOX_RUNNER_API_TOKEN_SECRET",
            "api.token_secret_env",
        )
        cache_key = host.get("id")
        with self._lock:
            client = self._clients.get(cache_key)
            if client is None or client.base_url != url.rstrip("/"):
                client = ProxmoxApiClient(url, token_id, token_secret)
                self._clients[cache_key] = client
            return client


class JenkinsApiClient:
    def __init__(self, internal_url, public_url, username, password, timeout_seconds=30):
        if not internal_url or not public_url or not username or not password:
            raise RunnerCtlError(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                "missing-jenkins-auth",
                "Jenkins API credentials are not configured for runnerctl.",
            )
        self.internal_url = internal_url.rstrip("/")
        self.public_url = public_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.cookie_jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cookie_jar))
        self.basic_auth = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
        self._crumb = None

    @classmethod
    def from_env(cls, env=None):
        env = env or os.environ
        internal_url = env.get("RUNNERCTL_JENKINS_INTERNAL_URL", "http://127.0.0.1:8080")
        public_url = env.get("RUNNERCTL_JENKINS_PUBLIC_URL", "https://jenkins.devsh.eu")
        user_env = env.get("RUNNERCTL_JENKINS_USER_ENV", "JENKINS_ADMIN_ID")
        password_env = env.get("RUNNERCTL_JENKINS_PASSWORD_ENV", "JENKINS_ADMIN_PASSWORD")
        require_pattern(user_env, ENV_NAME_PATTERN, "RUNNERCTL_JENKINS_USER_ENV")
        require_pattern(password_env, ENV_NAME_PATTERN, "RUNNERCTL_JENKINS_PASSWORD_ENV")
        username = env.get(user_env, "")
        password = env.get(password_env, "")
        timeout_seconds = int(env.get("RUNNERCTL_JENKINS_TIMEOUT_SECONDS", "30"))
        return cls(internal_url, public_url, username, password, timeout_seconds=timeout_seconds)

    def _url(self, path):
        if path.startswith("http://") or path.startswith("https://"):
            return path
        if not path.startswith("/"):
            path = "/" + path
        return f"{self.internal_url}{path}"

    def _request(self, method, path, data=None, headers=None, use_crumb=True):
        body = None
        request_headers = {
            "Authorization": f"Basic {self.basic_auth}",
        }
        if headers:
            request_headers.update(headers)
        if data is not None:
            if isinstance(data, bytes):
                body = data
            else:
                body = urllib.parse.urlencode(data, doseq=True).encode("utf-8")
                request_headers.setdefault("Content-Type", "application/x-www-form-urlencoded")
        if use_crumb and method.upper() not in {"GET", "HEAD"}:
            crumb = self.crumb()
            if crumb is not None:
                crumb_field, crumb_value = crumb
                request_headers[crumb_field] = crumb_value

        request = urllib.request.Request(self._url(path), data=body, headers=request_headers, method=method)
        try:
            with self.opener.open(request, timeout=self.timeout_seconds) as response:
                return response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            raise RunnerCtlError(
                HTTPStatus.BAD_GATEWAY,
                "jenkins-http-error",
                "Jenkins API returned an unexpected response.",
                {"path": path, "upstream_status": exc.code},
            ) from exc
        except OSError as exc:
            raise RunnerCtlError(
                HTTPStatus.BAD_GATEWAY,
                "jenkins-network-error",
                "Runnerctl could not reach Jenkins.",
                {"path": path},
            ) from exc

    def crumb(self):
        if self._crumb is not None:
            return self._crumb
        try:
            payload = self._request("GET", "/crumbIssuer/api/json", use_crumb=False)
            parsed = json.loads(payload)
            self._crumb = (parsed["crumbRequestField"], parsed["crumb"])
            return self._crumb
        except RunnerCtlError as exc:
            if exc.details.get("upstream_status") == 404:
                return None
            raise
        except (KeyError, json.JSONDecodeError) as exc:
            raise RunnerCtlError(
                HTTPStatus.BAD_GATEWAY,
                "jenkins-invalid-crumb",
                "Jenkins crumb issuer returned an invalid response.",
            ) from exc

    def json(self, path):
        payload = self._request("GET", path)
        try:
            return json.loads(payload)
        except json.JSONDecodeError as exc:
            raise RunnerCtlError(
                HTTPStatus.BAD_GATEWAY,
                "jenkins-invalid-json",
                "Jenkins API returned invalid JSON.",
                {"path": path},
            ) from exc

    def text(self, path):
        return self._request("GET", path)

    def script_text(self, script):
        return self._request("POST", "/scriptText", data={"script": script})

    def create_agent_node(self, node_name, label_string, remote_fs):
        script = f"""
import hudson.model.Node
import hudson.slaves.DumbSlave
import hudson.slaves.JNLPLauncher
import hudson.slaves.RetentionStrategy
import jenkins.model.Jenkins

String name = {groovy_string(node_name)}
String labels = {groovy_string(label_string)}
String remoteFs = {groovy_string(remote_fs)}
def instance = Jenkins.get()
def existing = instance.getNode(name)
if (existing != null) {{
  instance.removeNode(existing)
}}
def launcher = new JNLPLauncher()
def node = new DumbSlave(name, "Temporary runner lease " + name, remoteFs, "1", Node.Mode.EXCLUSIVE, labels, launcher, RetentionStrategy.INSTANCE, [])
instance.addNode(node)
println(name)
"""
        output = self.script_text(script)
        return node_name in output.splitlines()

    def delete_agent_node(self, node_name):
        script = f"""
import jenkins.model.Jenkins

String name = {groovy_string(node_name)}
def instance = Jenkins.get()
def node = instance.getNode(name)
if (node != null) {{
  instance.removeNode(node)
  println("deleted")
}} else {{
  println("missing")
}}
"""
        output = self.script_text(script)
        return "deleted" in output.splitlines()

    def agent_secret(self, node_name):
        encoded_name = urllib.parse.quote(node_name, safe="")
        payload = self.text(f"/computer/{encoded_name}/jenkins-agent.jnlp")
        try:
            root = ElementTree.fromstring(payload)
        except ElementTree.ParseError as exc:
            raise RunnerCtlError(
                HTTPStatus.BAD_GATEWAY,
                "jenkins-invalid-jnlp",
                "Jenkins returned an invalid inbound agent descriptor.",
                {"node_name": node_name},
            ) from exc
        arguments = []
        for element in root.iter():
            if element.tag.endswith("argument") and element.text:
                arguments.append(element.text.strip())
        for argument in arguments:
            if argument != node_name and len(argument) >= 20:
                return argument
        raise RunnerCtlError(
            HTTPStatus.BAD_GATEWAY,
            "jenkins-agent-secret-missing",
            "Jenkins did not return an inbound agent secret.",
            {"node_name": node_name},
        )

    def wait_agent_online(self, node_name, timeout_seconds):
        encoded_name = urllib.parse.quote(node_name, safe="")
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            try:
                status = self.json(f"/computer/{encoded_name}/api/json?tree=offline,temporarilyOffline")
                if status.get("offline") is False and status.get("temporarilyOffline") is False:
                    return True
            except RunnerCtlError as exc:
                if exc.details.get("upstream_status") != 404:
                    raise
            time.sleep(1.0)
        raise RunnerCtlError(
            HTTPStatus.BAD_GATEWAY,
            "jenkins-agent-timeout",
            "Timed out while waiting for the Jenkins runner node to come online.",
            {"node_name": node_name},
        )


def build_service_state(inventory):
    return {
        "inventory_version": inventory.get("version"),
        "host_count": len(inventory_hosts(inventory)),
        "template_count": len(inventory.get("templates", [])),
        "runner_class_count": len(inventory.get("runner_classes", [])),
    }


def choose_free_vmid(vmid_range, used_vmids):
    start = int(vmid_range["start"])
    end = int(vmid_range["end"])
    for vmid in range(start, end + 1):
        if vmid not in used_vmids:
            return vmid
    raise RunnerCtlError(
        HTTPStatus.CONFLICT,
        "no-capacity",
        "No free VMID is available in the configured runner range.",
        {"vmid_range": {"start": start, "end": end}},
    )


def build_clone_name(runner_class, lease_id):
    return f"runnerctl-{runner_class}-{lease_id[:8]}"


def build_policy_record(resolved):
    return {
        "boot_timeout_minutes": int(resolved["policy"].get("boot_timeout_minutes", 10)),
        "health_timeout_minutes": int(resolved["policy"].get("health_timeout_minutes", 15)),
        "destroy_after_job": bool(resolved["policy"].get("destroy_after_job", True)),
    }


def record_matches_candidate(record, runner_class, candidate):
    return (
        record.get("runner_class") == runner_class
        and record.get("host_id") == candidate["host_id"]
        and int(record.get("template_vmid", -1)) == int(candidate["template_vmid"])
    )


def active_records_for_candidate(leases, runner_class, candidate):
    records = []
    for lease_id, record in leases.items():
        if record_matches_candidate(record, runner_class, candidate) and record.get("state") in ACTIVE_RUNNER_STATES:
            records.append((lease_id, record))
    return records


def public_lease_result(record, allocation_mode, timings=None):
    result = {
        "lease_id": record["lease_id"],
        "runner_class": record["runner_class"],
        "labels": record["labels"],
        "host_id": record["host_id"],
        "node": record["node"],
        "vmid": record["vmid"],
        "clone_name": record["clone_name"],
        "expires_at": record["expires_at"],
        "connection": record["connection"],
        "allocation_mode": allocation_mode,
        "ready": record.get("state") in {"ready", "leased", "healthy"} and allocation_mode == "hot-pool",
    }
    timing_data = merge_timings(record.get("timings"), timings)
    if timing_data:
        result["timings"] = timing_data
    return result


def run_health_checks(client, node, vmid, requested_checks):
    checks = {}
    for check_name in requested_checks:
        try:
            checks[check_name] = run_named_health_check(client, node, vmid, check_name)
        except RunnerCtlError as exc:
            checks[check_name] = f"failed:{exc.code}"
    if "guest-agent" not in checks:
        checks["guest-agent"] = "passed"
    return checks


def health_checks_passed(checks):
    return bool(checks) and all(value == "passed" for value in checks.values())


def hot_pool_health_cache_ttl_seconds():
    raw_value = os.getenv("RUNNERCTL_HOT_POOL_HEALTH_CACHE_TTL_SECONDS", "300")
    try:
        return max(0, int(raw_value))
    except ValueError:
        return 300


def hot_pool_preconnect_agents_enabled():
    return os.getenv("RUNNERCTL_HOT_POOL_PRECONNECT_AGENTS", "false").lower() == "true"


def preconnected_agent_wait_seconds():
    raw_value = os.getenv("RUNNERCTL_PRECONNECTED_AGENT_WAIT_SECONDS", "8")
    try:
        return max(1, int(raw_value))
    except ValueError:
        return 8


def cached_health_for_record(record, requested_checks):
    if record.get("allocation_mode") != "hot-pool":
        return None
    ttl_seconds = hot_pool_health_cache_ttl_seconds()
    if ttl_seconds <= 0:
        return None
    last_health = record.get("last_health")
    if not isinstance(last_health, dict):
        return None
    try:
        last_health_at = int(record.get("last_health_at", 0))
    except (TypeError, ValueError):
        return None
    if now_epoch() - last_health_at > ttl_seconds:
        return None
    checks = requested_checks or ["guest-agent"]
    for check_name in checks:
        if last_health.get(check_name) != "passed":
            return None
    return dict(last_health)


def health_cache_age_seconds(record):
    try:
        last_health_at = int(record.get("last_health_at", 0))
    except (TypeError, ValueError):
        return None
    if last_health_at <= 0:
        return None
    return max(0, now_epoch() - last_health_at)


def acquire_ready_pool_member(client_registry, lease_store, inventory, resolved, candidates):
    acquire_started_ms = monotonic_ms()
    leases = lease_store.all()
    now = now_epoch()
    for candidate in candidates:
        ready_records = [
            (lease_id, record)
            for lease_id, record in active_records_for_candidate(leases, resolved["runner_class"], candidate)
            if record.get("state") in READY_POOL_STATES and bool(record.get("pool_member", False))
        ]
        ready_records.sort(key=lambda item: int(item[1].get("ready_at", item[1].get("created_at", 0))))
        for lease_id, record in ready_records:
            host = host_by_id(inventory, candidate["host_id"])
            node = candidate["node"]
            vmid = require_int(record.get("vmid", ""), "vmid", minimum=100, maximum=999999999)
            client = client_registry.client_for_host(inventory, host)
            try:
                if vmid not in client.qemu_vmids(node):
                    lease_store.delete(lease_id)
                    continue
                if client.vm_status(node, vmid).get("status") != "running":
                    lease_store.delete(lease_id)
                    client.safe_destroy(node, vmid)
                    continue
                client.agent_ping(node, vmid)
                client.set_tags(node, vmid, LEASED_TAGS)
            except RunnerCtlError:
                lease_store.delete(lease_id)
                try:
                    client.safe_destroy(node, vmid)
                except RunnerCtlError:
                    pass
                continue

            expires_at = now + (int(resolved["policy"]["lease_ttl_minutes"]) * 60)
            updated = lease_store.update(
                lease_id,
                {
                    "state": "leased",
                    "pool_member": False,
                    "allocation_mode": "hot-pool",
                    "leased_at": now,
                    "expires_at": expires_at,
                    "policy": build_policy_record(resolved),
                    "health_checks": resolved["health_checks"],
                    "jenkins_host_alias_ip": candidate.get("jenkins_host_alias_ip"),
                },
            )
            timings = merge_timings(
                record.get("timings"),
                {
                    "allocation_ms": elapsed_ms(acquire_started_ms),
                    "hot_pool_acquire_ms": elapsed_ms(acquire_started_ms),
                    "health_cache_age_seconds": health_cache_age_seconds(record),
                },
            )
            updated = lease_store.update(lease_id, {"timings": timings})
            return public_lease_result(updated, "hot-pool", timings)
    return None


def create_lease(client_registry, lease_store, inventory, request_data):
    lease_started_ms = monotonic_ms()
    resolved, candidates = resolve_lease_request(inventory, request_data)
    ready_result = acquire_ready_pool_member(client_registry, lease_store, inventory, resolved, candidates)
    if ready_result is not None:
        ready_timings = merge_timings(
            ready_result.get("timings"),
            {"lease_total_ms": elapsed_ms(lease_started_ms), "allocation_mode": "hot-pool"},
        )
        ready_result["timings"] = ready_timings
        return ready_result

    skipped = []
    leases = lease_store.all()

    for candidate in candidates:
        host = host_by_id(inventory, candidate["host_id"])
        node = candidate["node"]
        client = client_registry.client_for_host(inventory, host)
        try:
            active_vmids = client.qemu_vmids(node)
            template_vmid = candidate["template_vmid"]
            if template_vmid not in active_vmids:
                skipped.append({"host_id": candidate["host_id"], "reason": "template-missing"})
                continue

            used_vmids = set(active_vmids)
            for record in leases.values():
                if record.get("host_id") == candidate["host_id"] and record.get("vmid") is not None:
                    used_vmids.add(int(record["vmid"]))

            vmid = choose_free_vmid(candidate["vmid_range"], used_vmids)
            lease_id = uuid.uuid4().hex
            clone_name = build_clone_name(resolved["runner_class"], lease_id)
            description = f"RunnerCtl lease {lease_id} for {resolved['runner_class']}"

            clone_created = False
            try:
                clone_started_ms = monotonic_ms()
                clone_upid = client.request(
                    "POST",
                    f"/nodes/{node}/qemu/{template_vmid}/clone",
                    data={
                        "newid": vmid,
                        "name": clone_name,
                        "target": node,
                        "pool": candidate["pool"],
                        "full": 0,
                        "description": description,
                    },
                )
                client.wait_task(node, str(clone_upid), 180)
                clone_ms = elapsed_ms(clone_started_ms)
                clone_created = True
                configure_started_ms = monotonic_ms()
                configure_runtime_network(client, node, vmid, candidate)
                client.request(
                    "POST",
                    f"/nodes/{node}/qemu/{vmid}/config",
                    data={"tags": LEASED_TAGS},
                )
                configure_ms = elapsed_ms(configure_started_ms)

                created_at = now_epoch()
                expires_at = created_at + (int(resolved["policy"]["lease_ttl_minutes"]) * 60)
                record = {
                    "lease_id": lease_id,
                    "runner_class": resolved["runner_class"],
                    "labels": resolved["labels"],
                    "host_id": candidate["host_id"],
                    "node": node,
                    "vmid": vmid,
                    "template_vmid": template_vmid,
                    "clone_name": clone_name,
                    "created_at": created_at,
                    "expires_at": expires_at,
                    "state": "leased",
                    "pool_member": False,
                    "allocation_mode": "cold-clone",
                    "connection": resolved["connection"],
                    "policy": build_policy_record(resolved),
                    "health_checks": resolved["health_checks"],
                    "jenkins_host_alias_ip": candidate.get("jenkins_host_alias_ip"),
                    "timings": {
                        "clone_ms": clone_ms,
                        "configure_ms": configure_ms,
                        "allocation_ms": elapsed_ms(lease_started_ms),
                        "lease_total_ms": elapsed_ms(lease_started_ms),
                        "allocation_mode": "cold-clone",
                    },
                }
                lease_store.put(lease_id, record)
            except Exception:
                if clone_created:
                    try:
                        client.safe_destroy(node, vmid)
                    except Exception:
                        pass
                raise

            return public_lease_result(record, "cold-clone")
        except RunnerCtlError as exc:
            skipped.append({"host_id": candidate["host_id"], "reason": exc.code, "details": exc.details})

    raise RunnerCtlError(
        HTTPStatus.CONFLICT,
        "no-placement",
        "No Proxmox host could create the requested runner lease.",
        {"runner_class": resolved["runner_class"], "skipped": skipped},
    )


def wait_for_guest_agent(client, node, vmid, timeout_seconds):
    deadline = time.time() + timeout_seconds
    last_error = None
    while time.time() < deadline:
        try:
            client.agent_ping(node, vmid)
            return True
        except RunnerCtlError as exc:
            last_error = exc
            time.sleep(5.0)
    details = {"node": node, "vmid": vmid}
    if last_error is not None:
        details["last_error"] = last_error.code
    raise RunnerCtlError(
        HTTPStatus.BAD_GATEWAY,
        "guest-agent-timeout",
        "Timed out while waiting for QEMU Guest Agent.",
        details,
    )


def run_guest_powershell_check(client, node, vmid, script, timeout_seconds=60):
    command = [
        "powershell",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-Command",
        script,
    ]
    return client.guest_exec(node, vmid, command, timeout_seconds)


def powershell_encoded_command(script):
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    return [
        "powershell",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-EncodedCommand",
        encoded,
    ]


def build_jenkins_agent_node_name(lease_id):
    return f"runner-lease-{lease_id[:12]}"


def build_jenkins_agent_label(record, node_name, include_capability_labels=True):
    labels = set(record.get("labels", [])) if include_capability_labels else set()
    labels.add("runner-lease")
    labels.add(node_name)
    return " ".join(sorted(labels))


def start_jenkins_remoting_agent(
    client,
    node,
    vmid,
    jenkins_client,
    node_name,
    secret,
    work_dir,
    host_alias_ip=None,
    timeout_seconds=60,
):
    public_host = jenkins_public_hostname(jenkins_client.public_url)
    script = f"""
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$agentRoot = {powershell_string(work_dir)}
New-Item -ItemType Directory -Force -Path $agentRoot | Out-Null
$jar = Join-Path $agentRoot 'agent.jar'
$baseUrl = {powershell_string(jenkins_client.public_url)}
$jenkinsHost = {powershell_string(public_host)}
$hostAliasIp = {powershell_string(host_alias_ip or "")}
Write-Output ("runnerctl: jenkinsHost={{0}}; hostAliasIp={{1}}" -f $jenkinsHost, $(if ($hostAliasIp) {{ $hostAliasIp }} else {{ '<empty>' }}))
if ($hostAliasIp -and $jenkinsHost) {{
  $hostsPath = Join-Path $env:WINDIR 'System32/drivers/etc/hosts'
  $sysnativeHostsPath = Join-Path $env:WINDIR 'Sysnative/drivers/etc/hosts'
  if (Test-Path (Split-Path $sysnativeHostsPath -Parent)) {{
    $hostsPath = $sysnativeHostsPath
  }}
  Write-Output ("runnerctl: hostsPath={{0}}" -f $hostsPath)
  $escapedHost = [Regex]::Escape($jenkinsHost)
  $escapedIp = [Regex]::Escape($hostAliasIp)
  $entryPattern = '^\\s*' + $escapedIp + '\\s+' + $escapedHost + '(\\s|$)'
  $hostPattern = '^\\s*\\d{{1,3}}(\\.\\d{{1,3}}){{3}}\\s+' + $escapedHost + '(\\s|$)'
  $existingHosts = @()
  if (Test-Path $hostsPath) {{
    $existingHosts = @(Get-Content -Path $hostsPath -ErrorAction Stop)
  }}
  $filteredHosts = @($existingHosts | Where-Object {{ ($_ -notmatch $hostPattern) -and ($_ -notmatch '# runnerctl-jenkins') }})
  [System.IO.File]::WriteAllLines($hostsPath, [string[]]$filteredHosts, [Text.Encoding]::ASCII)
  Add-Content -Path $hostsPath -Value ("{{0}} {{1}} # runnerctl-jenkins" -f $hostAliasIp, $jenkinsHost) -Encoding ASCII
  Clear-DnsClientCache -ErrorAction SilentlyContinue
  & ipconfig /flushdns | Out-Null
  Write-Output ("runnerctl: hostAliasWritten={{0}}" -f $entryPattern)
}}
try {{
  $resolvedAddresses = [System.Net.Dns]::GetHostAddresses($jenkinsHost) | ForEach-Object {{ $_.IPAddressToString }}
  Write-Output ("runnerctl: dns={{0}}" -f ($resolvedAddresses -join ','))
}} catch {{
  Write-Output ("runnerctl: dnsError={{0}}" -f $_.Exception.Message)
}}
Invoke-WebRequest -Uri ($baseUrl.TrimEnd('/') + '/jnlpJars/agent.jar') -OutFile $jar -UseBasicParsing
$javaExe = $null
$javaCommand = Get-Command java.exe -ErrorAction SilentlyContinue
if ($javaCommand) {{
  $javaExe = $javaCommand.Source
}}
if (-not $javaExe) {{
  $searchRoots = @($env:ProgramFiles)
  $programFilesX86 = [Environment]::GetEnvironmentVariable('ProgramFiles(x86)')
  if ($programFilesX86) {{
    $searchRoots += $programFilesX86
  }}
  $javaExe = Get-ChildItem -Path $searchRoots -Recurse -Filter java.exe -ErrorAction SilentlyContinue |
    Where-Object {{ $_.FullName -match '\\\\bin\\\\java\\.exe$' }} |
    Select-Object -ExpandProperty FullName -First 1
}}
if (-not $javaExe) {{
  throw 'java.exe was not found on the runner.'
}}
$stdout = Join-Path $agentRoot 'agent.stdout.log'
$stderr = Join-Path $agentRoot 'agent.stderr.log'
$javaLiteral = $javaExe.Replace("'", "''")
$agentRootLiteral = $agentRoot.Replace("'", "''")
$baseUrlLiteral = ($baseUrl.TrimEnd('/') + '/').Replace("'", "''")
$secretLiteral = {powershell_string(secret)}.Replace("'", "''")
$nodeNameLiteral = {powershell_string(node_name)}.Replace("'", "''")
$launcher = Join-Path $agentRoot 'start-agent.ps1'
$launcherContent = @"
`$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
`$agentRoot = '$agentRootLiteral'
`$jar = Join-Path `$agentRoot 'agent.jar'
`$stdout = Join-Path `$agentRoot 'agent.stdout.log'
`$stderr = Join-Path `$agentRoot 'agent.stderr.log'
`$arguments = @(
  '-jar', `$jar,
  '-url', '$baseUrlLiteral',
  '-secret', '$secretLiteral',
  '-name', '$nodeNameLiteral',
  '-webSocket',
  '-workDir', `$agentRoot
)
try {{
  `$process = Start-Process -FilePath '$javaLiteral' -ArgumentList `$arguments -RedirectStandardOutput `$stdout -RedirectStandardError `$stderr -NoNewWindow -Wait -PassThru
  exit `$process.ExitCode
}} catch {{
  `$message = `$_ | Out-String
  Add-Content -Path `$stderr -Value `$message -Encoding UTF8
  exit 1
}}
"@
Set-Content -Path $launcher -Value $launcherContent -Encoding UTF8
$taskName = {powershell_string("runnerctl-" + node_name)}
Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue | Out-Null
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument ('-NoProfile -ExecutionPolicy Bypass -File "{{0}}"' -f $launcher)
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(5)
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -User 'SYSTEM' -RunLevel Highest -Force | Out-Null
Start-ScheduledTask -TaskName $taskName
Start-Sleep -Seconds 3
$task = Get-ScheduledTask -TaskName $taskName
$taskInfo = Get-ScheduledTaskInfo -TaskName $taskName
if ($task.State -ne 'Running') {{
  if (Test-Path $stdout) {{
    Write-Output 'runnerctl: agent.stdout.tail:'
    Get-Content -Path $stdout -Tail 30 | Write-Output
  }}
  if (Test-Path $stderr) {{
    Write-Output 'runnerctl: agent.stderr.tail:'
    Get-Content -Path $stderr -Tail 30 | Write-Output
  }}
  throw ('Jenkins remoting task is not running. state={{0}}, last_result={{1}}.' -f $task.State, $taskInfo.LastTaskResult)
}}
"""
    return client.guest_exec(node, vmid, powershell_encoded_command(script), timeout_seconds)


def attach_jenkins_agent_to_record(
    client,
    host,
    record,
    jenkins_client,
    work_dir,
    timeout_seconds,
    include_capability_labels=True,
):
    node = require_pattern(record.get("node", ""), SAFE_NAME_PATTERN, "node")
    vmid = require_int(record.get("vmid", ""), "vmid", minimum=100, maximum=999999999)
    node_name = build_jenkins_agent_node_name(record["lease_id"])
    label_string = build_jenkins_agent_label(record, node_name, include_capability_labels=include_capability_labels)
    created_node = False
    try:
        if not jenkins_client.create_agent_node(node_name, label_string, work_dir):
            raise RunnerCtlError(
                HTTPStatus.BAD_GATEWAY,
                "jenkins-node-create-failed",
                "Jenkins did not confirm runner node creation.",
                {"node_name": node_name},
            )
        created_node = True
        secret = jenkins_client.agent_secret(node_name)
        network = host.get("network") or {}
        host_alias_ip = optional_ipv4_address(
            record.get("jenkins_host_alias_ip") or network.get("jenkins_host_alias_ip"),
            "network.jenkins_host_alias_ip",
        )
        start_jenkins_remoting_agent(
            client,
            node,
            vmid,
            jenkins_client,
            node_name,
            secret,
            work_dir,
            host_alias_ip=host_alias_ip,
        )
        jenkins_client.wait_agent_online(node_name, timeout_seconds)
        return {
            "node_name": node_name,
            "label": node_name,
            "labels": label_string.split(),
            "work_dir": work_dir,
            "online_at": now_epoch(),
        }
    except Exception:
        if created_node:
            try:
                jenkins_client.delete_agent_node(node_name)
            except Exception:
                pass
        raise


def run_named_health_check(client, node, vmid, check_name):
    if check_name == "guest-agent":
        client.agent_ping(node, vmid)
        return "passed"
    if check_name == "network-interfaces":
        interfaces = client.agent_network_get_interfaces(node, vmid)
        return "passed" if interfaces else "failed"
    if check_name == "workspace-ready":
        run_guest_powershell_check(
            client,
            node,
            vmid,
            "New-Item -ItemType Directory -Force -Path 'C:\\runner\\work' | Out-Null; if (Test-Path 'C:\\runner\\work') { exit 0 } exit 1",
        )
        return "passed"
    if check_name == "vulkan-runtime":
        run_guest_powershell_check(
            client,
            node,
            vmid,
            "if (Test-Path \"$env:WINDIR\\System32\\vulkan-1.dll\") { exit 0 } exit 1",
        )
        return "passed"
    if check_name == "nvidia-smi":
        run_guest_powershell_check(
            client,
            node,
            vmid,
            "$paths = @(\"$env:ProgramFiles\\NVIDIA Corporation\\NVSMI\\nvidia-smi.exe\", 'nvidia-smi.exe'); "
            "$tool = $paths | Where-Object { Get-Command $_ -ErrorAction SilentlyContinue } | Select-Object -First 1; "
            "if (-not $tool) { exit 1 }; & $tool; exit $LASTEXITCODE",
            timeout_seconds=120,
        )
        return "passed"
    return "not-implemented"


def prepare_lease(client_registry, lease_store, inventory, request_data):
    prepare_started_ms = monotonic_ms()
    lease_id = require_pattern(request_data.get("lease_id", ""), LEASE_ID_PATTERN, "lease_id")
    record = lease_store.get(lease_id)
    if record is None:
        raise RunnerCtlError(HTTPStatus.NOT_FOUND, "not-found", "Lease was not found.", {"lease_id": lease_id})
    host = host_by_id(inventory, record["host_id"])
    client = client_registry.client_for_host(inventory, host)
    node = require_pattern(record.get("node", ""), SAFE_NAME_PATTERN, "node")
    vmid = require_int(record.get("vmid", ""), "vmid", minimum=100, maximum=999999999)
    boot_timeout_seconds = int(record.get("policy", {}).get("boot_timeout_minutes", 10)) * 60
    requested_checks = record.get("health_checks", [])

    cached_health = cached_health_for_record(record, requested_checks)
    if cached_health is not None:
        guard_started_ms = monotonic_ms()
        client.agent_ping(node, vmid)
        guard_ms = elapsed_ms(guard_started_ms)
        prepared_at = now_epoch()
        timings = merge_timings(
            record.get("timings"),
            {
                "prepare_ms": elapsed_ms(prepare_started_ms),
                "guest_agent_guard_ms": guard_ms,
                "health_source": "hot-pool-cache",
            },
        )
        updated = lease_store.update(
            lease_id,
            {
                "state": "healthy",
                "prepared_at": prepared_at,
                "healthy_at": prepared_at,
                "last_health": cached_health,
                "last_health_source": "hot-pool-cache",
                "timings": timings,
            },
        )
        return {
            "lease_id": lease_id,
            "state": updated["state"],
            "host_id": record["host_id"],
            "node": node,
            "vmid": vmid,
            "allocation_mode": record.get("allocation_mode", "cold-clone"),
            "health": updated["last_health"],
            "health_cached": True,
            "health_cache_age_seconds": health_cache_age_seconds(record),
            "last_health_at": record.get("last_health_at"),
            "timings": timings,
        }

    lease_store.update(lease_id, {"state": "booting", "prepared_at": now_epoch()})
    start_started_ms = monotonic_ms()
    client.start_vm(node, vmid)
    start_vm_ms = elapsed_ms(start_started_ms)
    guest_agent_started_ms = monotonic_ms()
    wait_for_guest_agent(client, node, vmid, boot_timeout_seconds)
    guest_agent_wait_ms = elapsed_ms(guest_agent_started_ms)
    health_started_ms = monotonic_ms()
    checks = run_health_checks(client, node, vmid, requested_checks)
    health_check_ms = elapsed_ms(health_started_ms)
    last_health_at = now_epoch()
    timings = merge_timings(
        record.get("timings"),
        {
            "start_vm_ms": start_vm_ms,
            "guest_agent_wait_ms": guest_agent_wait_ms,
            "health_check_ms": health_check_ms,
            "prepare_ms": elapsed_ms(prepare_started_ms),
            "health_source": "live",
        },
    )
    updated = lease_store.update(
        lease_id,
        {
            "state": "healthy",
            "healthy_at": last_health_at,
            "last_health": checks,
            "last_health_at": last_health_at,
            "last_health_source": "live",
            "timings": timings,
        },
    )
    return {
        "lease_id": lease_id,
        "state": updated["state"],
        "host_id": record["host_id"],
        "node": node,
        "vmid": vmid,
        "allocation_mode": record.get("allocation_mode", "cold-clone"),
        "health": updated["last_health"],
        "health_cached": False,
        "last_health_at": updated.get("last_health_at"),
        "timings": timings,
    }


def health_lease(client_registry, lease_store, inventory, request_data):
    health_started_ms = monotonic_ms()
    lease_id = require_pattern(request_data.get("lease_id", ""), LEASE_ID_PATTERN, "lease_id")
    record = lease_store.get(lease_id)
    if record is None:
        raise RunnerCtlError(HTTPStatus.NOT_FOUND, "not-found", "Lease was not found.", {"lease_id": lease_id})
    host = host_by_id(inventory, record["host_id"])
    client = client_registry.client_for_host(inventory, host)
    node = require_pattern(record.get("node", ""), SAFE_NAME_PATTERN, "node")
    vmid = require_int(record.get("vmid", ""), "vmid", minimum=100, maximum=999999999)

    requested_checks = record.get("health_checks", []) or ["guest-agent"]
    cached_health = cached_health_for_record(record, requested_checks)
    if cached_health is not None:
        guard_started_ms = monotonic_ms()
        client.agent_ping(node, vmid)
        guard_ms = elapsed_ms(guard_started_ms)
        overall = "passed" if health_checks_passed(cached_health) else "failed"
        timings = merge_timings(
            record.get("timings"),
            {
                "health_ms": elapsed_ms(health_started_ms),
                "health_guard_ms": guard_ms,
                "health_source": "hot-pool-cache",
            },
        )
        lease_store.update(lease_id, {"last_health": cached_health, "last_health_source": "hot-pool-cache", "timings": timings})
        return {
            "lease_id": lease_id,
            "state": record.get("state"),
            "overall": overall,
            "checks": cached_health,
            "cached": True,
            "health_cache_age_seconds": health_cache_age_seconds(record),
            "last_health_at": record.get("last_health_at"),
            "timings": timings,
        }

    checks = run_health_checks(client, node, vmid, requested_checks)

    overall = "passed" if health_checks_passed(checks) else "failed"
    last_health_at = now_epoch()
    timings = merge_timings(
        record.get("timings"),
        {
            "health_ms": elapsed_ms(health_started_ms),
            "health_source": "live",
        },
    )
    lease_store.update(lease_id, {"last_health": checks, "last_health_at": last_health_at, "last_health_source": "live", "timings": timings})
    return {
        "lease_id": lease_id,
        "state": record.get("state"),
        "overall": overall,
        "checks": checks,
        "cached": False,
        "last_health_at": last_health_at,
        "timings": timings,
    }


def public_agent_lease_result(record, prepare_result):
    agent = record.get("jenkins_agent", {})
    result = {
        "lease_id": record["lease_id"],
        "runner_class": record["runner_class"],
        "labels": record["labels"],
        "label": agent["label"],
        "node_name": agent["node_name"],
        "agent_online": True,
        "host_id": record["host_id"],
        "node": record["node"],
        "vmid": record["vmid"],
        "template_vmid": record["template_vmid"],
        "clone_name": record["clone_name"],
        "allocation_mode": record.get("allocation_mode", "cold-clone"),
        "health": prepare_result.get("health", {}),
        "health_cached": bool(prepare_result.get("health_cached", False)),
    }
    timings = merge_timings(record.get("timings"), prepare_result.get("timings"))
    if timings:
        result["timings"] = timings
    return result


def lease_jenkins_agent(client_registry, lease_store, inventory, request_data, jenkins_client):
    lease_agent_started_ms = monotonic_ms()
    lease_id = None
    node_name = None
    try:
        lease_request = {
            "required_labels": request_labels(request_data),
        }
        if request_data.get("runner_class"):
            lease_request["runner_class"] = request_data["runner_class"]
        if request_data.get("lease_ttl_minutes") is not None:
            lease_request["lease_ttl_minutes"] = request_data["lease_ttl_minutes"]

        allocator_started_ms = monotonic_ms()
        lease_result = create_lease(client_registry, lease_store, inventory, lease_request)
        allocator_ms = elapsed_ms(allocator_started_ms)
        lease_id = lease_result["lease_id"]
        prepare_started_ms = monotonic_ms()
        prepare_result = prepare_lease(client_registry, lease_store, inventory, {"lease_id": lease_id})
        prepare_ms = elapsed_ms(prepare_started_ms)
        record = lease_store.get(lease_id)
        if record is None:
            raise RunnerCtlError(HTTPStatus.NOT_FOUND, "not-found", "Lease was not found.", {"lease_id": lease_id})

        host = host_by_id(inventory, record["host_id"])
        client = client_registry.client_for_host(inventory, host)
        work_dir = os.getenv("RUNNERCTL_JENKINS_AGENT_WORK_DIR", "C:\\runner\\jenkins-agent")
        agent_timeout_seconds = int(os.getenv("RUNNERCTL_JENKINS_AGENT_TIMEOUT_SECONDS", "120"))
        existing_agent = record.get("jenkins_agent") or {}
        agent_started_ms = monotonic_ms()
        if existing_agent.get("node_name"):
            node_name = existing_agent["node_name"]
            try:
                jenkins_client.wait_agent_online(node_name, preconnected_agent_wait_seconds())
                agent = dict(existing_agent)
                agent.setdefault("label", node_name)
                agent.setdefault("labels", [node_name])
                agent.setdefault("work_dir", work_dir)
                agent["online_at"] = now_epoch()
            except RunnerCtlError:
                try:
                    jenkins_client.delete_agent_node(node_name)
                except Exception:
                    pass
                agent = attach_jenkins_agent_to_record(
                    client,
                    host,
                    record,
                    jenkins_client,
                    work_dir,
                    agent_timeout_seconds,
                    include_capability_labels=True,
                )
        else:
            agent = attach_jenkins_agent_to_record(
                client,
                host,
                record,
                jenkins_client,
                work_dir,
                agent_timeout_seconds,
                include_capability_labels=True,
            )
        node_name = agent["node_name"]
        agent_connect_ms = elapsed_ms(agent_started_ms)
        timings = merge_timings(
            record.get("timings"),
            lease_result.get("timings"),
            prepare_result.get("timings"),
            {
                "allocator_api_ms": allocator_ms,
                "prepare_api_ms": prepare_ms,
                "jenkins_agent_connect_ms": agent_connect_ms,
                "agent_lease_total_ms": elapsed_ms(lease_agent_started_ms),
            },
        )

        updated = lease_store.update(
            lease_id,
            {
                "state": "agent-online",
                "jenkins_agent": agent,
                "timings": timings,
            },
        )
        return public_agent_lease_result(updated, prepare_result)
    except Exception:
        if node_name:
            try:
                jenkins_client.delete_agent_node(node_name)
            except Exception:
                pass
        if lease_id:
            try:
                release_lease(client_registry, lease_store, inventory, {"lease_id": lease_id}, jenkins_client=jenkins_client)
            except Exception:
                pass
        raise


def release_lease(client_registry, lease_store, inventory, request_data, jenkins_client=None):
    release_started_ms = monotonic_ms()
    lease_id = require_pattern(request_data.get("lease_id", ""), LEASE_ID_PATTERN, "lease_id")
    record = lease_store.get(lease_id)
    if record is None:
        return {"lease_id": lease_id, "result": "not-found"}

    host = host_by_id(inventory, record["host_id"])
    client = client_registry.client_for_host(inventory, host)
    node = require_pattern(record.get("node", ""), SAFE_NAME_PATTERN, "node")
    vmid = require_int(record.get("vmid", ""), "vmid", minimum=100, maximum=999999999)
    agent_deleted = False
    agent = record.get("jenkins_agent") or {}
    delete_agent_ms = 0
    if jenkins_client is not None and agent.get("node_name"):
        delete_agent_started_ms = monotonic_ms()
        try:
            agent_deleted = jenkins_client.delete_agent_node(agent["node_name"])
        except RunnerCtlError:
            agent_deleted = False
        delete_agent_ms = elapsed_ms(delete_agent_started_ms)
    destroy_started_ms = monotonic_ms()
    destroyed = client.safe_destroy(node, vmid)
    destroy_vm_ms = elapsed_ms(destroy_started_ms)
    lease_store.delete(lease_id)
    return {
        "lease_id": lease_id,
        "result": "released",
        "host_id": record["host_id"],
        "node": node,
        "vmid": vmid,
        "destroyed_vm": destroyed,
        "deleted_jenkins_node": agent_deleted,
        "timings": {
            "delete_jenkins_node_ms": delete_agent_ms,
            "destroy_vm_ms": destroy_vm_ms,
            "release_ms": elapsed_ms(release_started_ms),
        },
    }


def request_bool(request_data, field_name, default=False):
    value = request_data.get(field_name, default)
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def lease_record_janitor_reason(inventory, record, now, stale_after_seconds):
    lease_id = str(record.get("lease_id", ""))
    if not LEASE_ID_PATTERN.fullmatch(lease_id):
        return None
    state = record.get("state")
    if state not in ACTIVE_RUNNER_STATES:
        return None
    if not str(record.get("clone_name", "")).startswith("runnerctl-"):
        return None
    host = host_by_id(inventory, record.get("host_id"))
    if record.get("node") != host.get("node"):
        return None
    vmid = require_int(record.get("vmid", ""), "vmid", minimum=100, maximum=999999999)
    janitor = inventory.get("janitor", {})
    range_ref = janitor.get("vmid_range", "runner")
    vmid_range = host_vmid_range(host, range_ref)
    if vmid < vmid_range["start"] or vmid > vmid_range["end"]:
        return None
    try:
        expires_at = int(record.get("expires_at", 0))
    except (TypeError, ValueError):
        expires_at = 0
    if expires_at > 0 and now >= expires_at:
        return "expired"
    if state == "ready":
        return None
    timestamps = []
    for key in ("leased_at", "prepared_at", "healthy_at", "created_at"):
        try:
            value = int(record.get(key, 0))
        except (TypeError, ValueError):
            value = 0
        if value > 0:
            timestamps.append(value)
    reference_at = max(timestamps) if timestamps else 0
    if reference_at > 0 and now - reference_at >= stale_after_seconds:
        return "stale"
    return None


def run_janitor(client_registry, lease_store, inventory, request_data, jenkins_client=None):
    dry_run = request_bool(request_data, "dry_run", False)
    now = now_epoch()
    janitor = inventory.get("janitor", {})
    stale_after_minutes = int(janitor.get("stale_lease_after_minutes", 240))
    stale_after_seconds = max(60, stale_after_minutes * 60)
    cleaned = []
    skipped = []

    for lease_id, record in sorted(lease_store.all().items()):
        try:
            reason = lease_record_janitor_reason(inventory, record, now, stale_after_seconds)
        except RunnerCtlError as exc:
            skipped.append({"lease_id": lease_id, "reason": exc.code})
            continue
        if reason is None:
            continue

        host = host_by_id(inventory, record["host_id"])
        client = client_registry.client_for_host(inventory, host)
        node = require_pattern(record.get("node", ""), SAFE_NAME_PATTERN, "node")
        vmid = require_int(record.get("vmid", ""), "vmid", minimum=100, maximum=999999999)
        active_vmids = client.qemu_vmids(node)
        vm_exists = vmid in active_vmids
        if vm_exists:
            required_tags = set(janitor.get("require_tags", []))
            vm_config = client.vm_config(node, vmid)
            actual_tags = set(str(vm_config.get("tags", "")).split(";"))
            missing_tags = sorted(required_tags - actual_tags)
            if missing_tags:
                skipped.append({"lease_id": lease_id, "reason": "missing-required-tags", "missing_tags": missing_tags})
                continue
        agent_deleted = False
        destroyed = False
        if not dry_run:
            agent = record.get("jenkins_agent") or {}
            if jenkins_client is not None and agent.get("node_name"):
                try:
                    agent_deleted = jenkins_client.delete_agent_node(agent["node_name"])
                except RunnerCtlError:
                    agent_deleted = False
            if vm_exists:
                destroyed = client.safe_destroy(node, vmid)
            lease_store.delete(lease_id)
        cleaned.append(
            {
                "lease_id": lease_id,
                "runner_class": record.get("runner_class"),
                "host_id": record.get("host_id"),
                "node": node,
                "vmid": vmid,
                "state": record.get("state"),
                "reason": reason,
                "destroyed_vm": destroyed,
                "deleted_jenkins_node": agent_deleted,
            }
        )

    return {
        "result": "success",
        "dry_run": dry_run,
        "cleaned": cleaned,
        "skipped": skipped,
        "cleaned_count": len(cleaned),
        "skipped_count": len(skipped),
    }


def build_ready_pool_member(client_registry, lease_store, inventory, resolved, candidate, jenkins_client=None):
    pool_started_ms = monotonic_ms()
    host = host_by_id(inventory, candidate["host_id"])
    node = candidate["node"]
    client = client_registry.client_for_host(inventory, host)
    active_vmids = client.qemu_vmids(node)
    template_vmid = candidate["template_vmid"]
    if template_vmid not in active_vmids:
        raise RunnerCtlError(
            HTTPStatus.CONFLICT,
            "template-missing",
            "Template VM is missing on the selected Proxmox host.",
            {"host_id": candidate["host_id"], "node": node, "template_vmid": template_vmid},
        )

    leases = lease_store.all()
    used_vmids = set(active_vmids)
    for record in leases.values():
        if record.get("host_id") == candidate["host_id"] and record.get("vmid") is not None:
            used_vmids.add(int(record["vmid"]))

    pool_id = uuid.uuid4().hex
    vmid = choose_free_vmid(candidate["vmid_range"], used_vmids)
    clone_name = build_clone_name(f"hot-{resolved['runner_class']}", pool_id)
    created_at = now_epoch()
    record = {
        "lease_id": pool_id,
        "runner_class": resolved["runner_class"],
        "labels": resolved["labels"],
        "host_id": candidate["host_id"],
        "node": node,
        "vmid": vmid,
        "template_vmid": template_vmid,
        "clone_name": clone_name,
        "created_at": created_at,
        "expires_at": created_at + (int(resolved["policy"].get("lease_ttl_minutes", 120)) * 60),
        "state": "creating",
        "pool_member": True,
        "allocation_mode": "hot-pool",
        "connection": resolved["connection"],
        "policy": build_policy_record(resolved),
        "health_checks": resolved["health_checks"],
        "jenkins_host_alias_ip": candidate.get("jenkins_host_alias_ip"),
    }
    lease_store.put(pool_id, record)

    preconnected_node_name = None
    try:
        clone_started_ms = monotonic_ms()
        clone_upid = client.request(
            "POST",
            f"/nodes/{node}/qemu/{template_vmid}/clone",
            data={
                "newid": vmid,
                "name": clone_name,
                "target": node,
                "pool": candidate["pool"],
                "full": 0,
                "description": f"RunnerCtl hot pool member {pool_id} for {resolved['runner_class']}",
            },
        )
        client.wait_task(node, str(clone_upid), 180)
        clone_ms = elapsed_ms(clone_started_ms)
        configure_started_ms = monotonic_ms()
        configure_runtime_network(client, node, vmid, candidate)
        client.set_tags(node, vmid, HOT_POOL_TAGS_CREATING)
        configure_ms = elapsed_ms(configure_started_ms)
        start_started_ms = monotonic_ms()
        client.start_vm(node, vmid)
        start_vm_ms = elapsed_ms(start_started_ms)
        guest_agent_started_ms = monotonic_ms()
        wait_for_guest_agent(client, node, vmid, int(resolved["policy"].get("boot_timeout_minutes", 10)) * 60)
        guest_agent_wait_ms = elapsed_ms(guest_agent_started_ms)
        health_started_ms = monotonic_ms()
        checks = run_health_checks(client, node, vmid, resolved["health_checks"])
        health_check_ms = elapsed_ms(health_started_ms)
        if not health_checks_passed(checks):
            raise RunnerCtlError(
                HTTPStatus.BAD_GATEWAY,
                "hot-pool-health-failed",
                "Hot pool VM failed health checks.",
                {"host_id": candidate["host_id"], "node": node, "vmid": vmid, "checks": checks},
            )
        jenkins_agent = None
        jenkins_agent_ms = 0
        if hot_pool_preconnect_agents_enabled() and jenkins_client is not None:
            work_dir = os.getenv("RUNNERCTL_JENKINS_AGENT_WORK_DIR", "C:\\runner\\jenkins-agent")
            agent_timeout_seconds = int(os.getenv("RUNNERCTL_JENKINS_AGENT_TIMEOUT_SECONDS", "120"))
            latest_record = lease_store.get(pool_id) or record
            jenkins_agent_started_ms = monotonic_ms()
            jenkins_agent = attach_jenkins_agent_to_record(
                client,
                host,
                latest_record,
                jenkins_client,
                work_dir,
                agent_timeout_seconds,
                include_capability_labels=False,
            )
            jenkins_agent_ms = elapsed_ms(jenkins_agent_started_ms)
            preconnected_node_name = jenkins_agent["node_name"]
        ready_at = now_epoch()
        client.set_tags(node, vmid, HOT_POOL_TAGS_READY)
        ready_update = {
            "state": "ready",
            "ready_at": ready_at,
            "last_health": checks,
            "last_health_at": ready_at,
            "timings": {
                "clone_ms": clone_ms,
                "configure_ms": configure_ms,
                "start_vm_ms": start_vm_ms,
                "guest_agent_wait_ms": guest_agent_wait_ms,
                "health_check_ms": health_check_ms,
                "jenkins_agent_connect_ms": jenkins_agent_ms,
                "pool_member_ready_ms": elapsed_ms(pool_started_ms),
            },
        }
        if jenkins_agent:
            ready_update["jenkins_agent"] = jenkins_agent
        updated = lease_store.update(pool_id, ready_update)
        return {
            "lease_id": pool_id,
            "runner_class": updated["runner_class"],
            "host_id": updated["host_id"],
            "node": updated["node"],
            "vmid": updated["vmid"],
            "clone_name": updated["clone_name"],
            "state": updated["state"],
            "health": updated["last_health"],
            "timings": updated.get("timings", {}),
        }
    except Exception:
        if preconnected_node_name and jenkins_client is not None:
            try:
                jenkins_client.delete_agent_node(preconnected_node_name)
            except Exception:
                pass
        try:
            client.safe_destroy(node, vmid)
        finally:
            lease_store.delete(pool_id)
        raise


def resolve_pool_targets(inventory, request_data):
    requested_class = request_data.get("runner_class")
    if requested_class:
        requested_class = require_pattern(requested_class, ID_PATTERN, "runner_class")
    requested_labels = request_data.get("required_labels")
    if requested_labels is None:
        requested_labels = request_data.get("labels")
    if not requested_class and requested_labels is not None:
        resolved, candidates = resolve_runner_context(inventory, None, requested_labels)
        return [(resolved, candidates)]

    targets = []
    for runner_class in inventory.get("runner_classes", []):
        class_id = require_pattern(runner_class.get("id", ""), ID_PATTERN, "runner_class")
        if requested_class and requested_class != class_id:
            continue
        labels = requested_labels
        if labels is None:
            labels = runner_class.get("labels", [])
        resolved, candidates = resolve_runner_context(inventory, class_id, labels)
        targets.append((resolved, candidates))
    if requested_class and not targets:
        raise RunnerCtlError(
            HTTPStatus.BAD_REQUEST,
            "unknown-runner-class",
            "Requested runner class is not configured.",
            {"runner_class": requested_class},
        )
    return targets


def refill_hot_pool(client_registry, lease_store, inventory, request_data, jenkins_client=None):
    janitor_result = run_janitor(client_registry, lease_store, inventory, {}, jenkins_client=jenkins_client)
    targets = resolve_pool_targets(inventory, request_data)
    results = []
    for resolved, candidates in targets:
        warm_pool = resolved.get("warm_pool") or {}
        min_ready = int(warm_pool.get("min_ready", 0))
        max_ready = int(warm_pool.get("max_ready", min_ready))
        if min_ready <= 0 or max_ready <= 0:
            results.append({"runner_class": resolved["runner_class"], "enabled": False})
            continue

        class_created = []
        class_status = []
        for candidate in candidates:
            while True:
                leases = lease_store.all()
                host = host_by_id(inventory, candidate["host_id"])
                client = client_registry.client_for_host(inventory, host)
                active_vmids = client.qemu_vmids(candidate["node"])
                for lease_id, record in active_records_for_candidate(leases, resolved["runner_class"], candidate):
                    vmid = int(record.get("vmid", -1))
                    if vmid not in active_vmids:
                        lease_store.delete(lease_id)
                leases = lease_store.all()
                active = active_records_for_candidate(leases, resolved["runner_class"], candidate)
                ready = [record for _, record in active if record.get("state") == "ready" and record.get("pool_member")]
                active_count = len(active)
                ready_count = len(ready)
                class_status.append(
                    {
                        "host_id": candidate["host_id"],
                        "node": candidate["node"],
                        "ready": ready_count,
                        "active": active_count,
                        "min_ready": min_ready,
                        "max_ready": max_ready,
                    }
                )
                if ready_count >= min_ready or active_count >= max_ready:
                    break
                class_created.append(
                    build_ready_pool_member(
                        client_registry,
                        lease_store,
                        inventory,
                        resolved,
                        candidate,
                        jenkins_client=jenkins_client,
                    )
                )

        results.append(
            {
                "runner_class": resolved["runner_class"],
                "enabled": True,
                "created": class_created,
                "status": class_status,
            }
        )
    return {"result": "success", "janitor": janitor_result, "pools": results}


def hot_pool_status(lease_store, inventory):
    leases = lease_store.all()
    now = now_epoch()
    pools = []
    for runner_class in inventory.get("runner_classes", []):
        class_id = require_pattern(runner_class.get("id", ""), ID_PATTERN, "runner_class")
        records = [record for record in leases.values() if record.get("runner_class") == class_id]
        counts = {}
        members = []
        for record in records:
            state = record.get("state", "unknown")
            counts[state] = counts.get(state, 0) + 1
            members.append(
                {
                    "lease_id": record.get("lease_id"),
                    "host_id": record.get("host_id"),
                    "node": record.get("node"),
                    "vmid": record.get("vmid"),
                    "state": state,
                    "pool_member": bool(record.get("pool_member", False)),
                    "allocation_mode": record.get("allocation_mode", "cold-clone"),
                    "clone_name": record.get("clone_name"),
                    "created_at": record.get("created_at"),
                    "ready_at": record.get("ready_at"),
                    "leased_at": record.get("leased_at"),
                    "expires_at": record.get("expires_at"),
                    "age_seconds": max(0, now - int(record.get("created_at", now) or now)),
                    "expires_in_seconds": int(record.get("expires_at", now) or now) - now,
                    "last_health_at": record.get("last_health_at"),
                    "health_cache_age_seconds": health_cache_age_seconds(record),
                    "has_jenkins_agent": bool((record.get("jenkins_agent") or {}).get("node_name")),
                    "timings": record.get("timings", {}),
                }
            )
        pools.append(
            {
                "runner_class": class_id,
                "warm_pool": runner_class.get("warm_pool", {}),
                "counts": counts,
                "members": sorted(members, key=lambda item: (str(item["host_id"]), int(item["vmid"] or 0))),
            }
        )
    return {"result": "success", "pools": pools}


def run_api_smoke(client_registry, inventory, request_data):
    requested_host_id = request_data.get("host_id")
    hosts = inventory_hosts(inventory)
    if requested_host_id:
        requested_host_id = require_pattern(requested_host_id, ID_PATTERN, "host_id")
        hosts = [host_by_id(inventory, requested_host_id)]

    results = []
    for host in hosts:
        host_id = require_pattern(host.get("id", ""), ID_PATTERN, "host.id")
        node = require_pattern(host.get("node", ""), SAFE_NAME_PATTERN, "host.node")
        client = client_registry.client_for_host(inventory, host)
        version = client.request("GET", "/version")
        node_names = [entry.get("node") for entry in client.request("GET", "/cluster/resources", query={"type": "node"}) or []]
        if node not in node_names:
            raise RunnerCtlError(
                HTTPStatus.BAD_REQUEST,
                "unknown-node",
                "Configured Proxmox node is not visible through the API.",
                {"host_id": host_id, "node": node},
            )

        expected_pools = list((host.get("pools") or {}).values())
        pools = [entry.get("poolid") for entry in client.request("GET", "/pools") or []]
        missing_pools = [pool for pool in expected_pools if pool not in pools]
        if missing_pools:
            raise RunnerCtlError(
                HTTPStatus.BAD_REQUEST,
                "missing-pool",
                "Required Proxmox pool is missing.",
                {"host_id": host_id, "missing_pools": sorted(set(missing_pools))},
            )

        expected_storage = list((host.get("storage") or {}).values())
        storages = [entry.get("storage") for entry in client.request("GET", f"/nodes/{node}/storage") or []]
        missing_storage = [storage for storage in expected_storage if storage not in storages]
        if missing_storage:
            raise RunnerCtlError(
                HTTPStatus.BAD_REQUEST,
                "missing-storage",
                "Required Proxmox storage is missing on the selected node.",
                {"host_id": host_id, "node": node, "missing_storage": sorted(set(missing_storage))},
            )

        vm_count = len(client.qemu_vmids(node))
        results.append(
            {
                "host_id": host_id,
                "node": node,
                "version": version.get("version"),
                "visible_vm_count": vm_count,
            }
        )

    return {"result": "success", "hosts": results}


def run_warm_smoke(client_registry, inventory, request_data):
    runner_class = request_data.get("runner_class", "")
    required_labels = require_list(request_data.get("required_labels", []), "required_labels")
    _, candidates = resolve_runner_context(inventory, runner_class, required_labels)
    last_error = None

    for candidate in candidates:
        host = host_by_id(inventory, candidate["host_id"])
        node = candidate["node"]
        client = client_registry.client_for_host(inventory, host)
        vmids = smoke_vmids(host)
        source_vmid = vmids["source_vmid"]
        clone_vmid = vmids["clone_vmid"]
        if source_vmid == clone_vmid:
            raise RunnerCtlError(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                "invalid-inventory",
                "smoke.source_vmid and smoke.clone_vmid must differ.",
                {"host_id": candidate["host_id"]},
            )

        try:
            client.safe_destroy(node, clone_vmid)
            client.safe_destroy(node, source_vmid)

            create_upid = client.request(
                "POST",
                f"/nodes/{node}/qemu",
                data={
                    "vmid": source_vmid,
                    "name": f"runnerctl-smoke-src-{source_vmid}",
                    "ostype": "l26",
                    "memory": 1024,
                    "cores": 1,
                    "sockets": 1,
                    "machine": "q35",
                    "bios": "seabios",
                    "scsihw": "virtio-scsi-single",
                    "scsi0": f"{candidate['storage']}:1",
                    "net0": f"virtio,bridge={candidate['bridge']},tag={candidate['vlan_tag']},firewall=1",
                    "pool": host.get("pools", {}).get("templates", candidate["pool"]),
                    "tags": "runnerctl;smoke;template-source",
                },
            )
            client.wait_task(node, str(create_upid), 180)

            template_upid = client.request("POST", f"/nodes/{node}/qemu/{source_vmid}/template")
            client.wait_task(node, str(template_upid), 180)

            clone_upid = client.request(
                "POST",
                f"/nodes/{node}/qemu/{source_vmid}/clone",
                data={
                    "newid": clone_vmid,
                    "name": f"runnerctl-smoke-warm-{clone_vmid}",
                    "target": node,
                    "pool": candidate["pool"],
                    "full": 0,
                    "description": "Temporary runnerctl warm-path smoke clone",
                },
            )
            client.wait_task(node, str(clone_upid), 180)

            client.start_vm(node, clone_vmid)
            running_state = client.vm_status(node, clone_vmid)
            if running_state.get("status") != "running":
                raise RunnerCtlError(
                    HTTPStatus.BAD_GATEWAY,
                    "warm-smoke-start-failed",
                    "Scratch clone did not reach running state.",
                    {"host_id": candidate["host_id"], "node": node, "clone_vmid": clone_vmid},
                )

            client.stop_vm(node, clone_vmid)
            stopped_state = client.vm_status(node, clone_vmid)
            if stopped_state.get("status") != "stopped":
                raise RunnerCtlError(
                    HTTPStatus.BAD_GATEWAY,
                    "warm-smoke-stop-failed",
                    "Scratch clone did not reach stopped state after stop request.",
                    {"host_id": candidate["host_id"], "node": node, "clone_vmid": clone_vmid},
                )

            return {
                "result": "success",
                "host_id": candidate["host_id"],
                "node": node,
                "source_vmid": source_vmid,
                "clone_vmid": clone_vmid,
            }
        except RunnerCtlError as exc:
            last_error = exc
        finally:
            client.safe_destroy(node, clone_vmid)
            client.safe_destroy(node, source_vmid)

    if last_error is not None:
        raise last_error
    raise RunnerCtlError(HTTPStatus.CONFLICT, "no-placement", "No Proxmox host was available for warm smoke.")


class RunnerCtlHandler(BaseHTTPRequestHandler):
    server_version = "runnerctl/0.2"

    def log_message(self, format_string, *args):
        sys.stderr.write("%s - - [%s] %s\n" % (self.address_string(), self.log_date_time_string(), format_string % args))

    def _read_json(self):
        content_length = int(self.headers.get("Content-Length", "0"))
        if content_length <= 0:
            return {}
        body = self.rfile.read(content_length)
        try:
            return json.loads(body.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise RunnerCtlError(
                HTTPStatus.BAD_REQUEST,
                "invalid-json",
                "Request body must be valid JSON.",
            ) from exc

    def _write_json(self, status_code, payload):
        encoded = json.dumps(payload, sort_keys=True).encode("utf-8")
        self.send_response(int(status_code))
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _inventory(self):
        return self.server.inventory_store.load()

    def _clients(self):
        return self.server.proxmox_clients

    def _jenkins(self):
        return self.server.jenkins_client()

    def _handle_exception(self, exc):
        if isinstance(exc, RunnerCtlError):
            self._write_json(
                exc.status_code,
                {"status": "error", "code": exc.code, "message": exc.message, "details": exc.details},
            )
            return
        traceback.print_exc()
        self._write_json(
            HTTPStatus.INTERNAL_SERVER_ERROR,
            {"status": "error", "code": "internal-error", "message": "Runnerctl hit an unexpected internal error."},
        )

    def do_GET(self):
        try:
            if self.path == "/healthz":
                inventory = self._inventory()
                self._write_json(HTTPStatus.OK, {"status": "ok", **build_service_state(inventory)})
                return
            if self.path == "/api/v1/inventory":
                inventory = self._inventory()
                self._write_json(
                    HTTPStatus.OK,
                    {
                        "status": "ok",
                        "version": inventory.get("version"),
                        "hosts": [item.get("id") for item in inventory_hosts(inventory)],
                        "runner_classes": [item.get("id") for item in inventory.get("runner_classes", [])],
                        "templates": [item.get("id") for item in inventory.get("templates", [])],
                    },
                )
                return
            if self.path == "/api/v1/pool":
                inventory = self._inventory()
                self._write_json(HTTPStatus.OK, {"status": "ok", **hot_pool_status(self.server.lease_store, inventory)})
                return
            if self.path.startswith("/api/v1/leases/"):
                lease_id = self.path.rsplit("/", 1)[-1]
                lease_id = require_pattern(lease_id, LEASE_ID_PATTERN, "lease_id")
                record = self.server.lease_store.get(lease_id)
                if record is None:
                    self._write_json(
                        HTTPStatus.NOT_FOUND,
                        {"status": "error", "code": "not-found", "message": "Lease was not found."},
                    )
                    return
                self._write_json(HTTPStatus.OK, {"status": "ok", **record})
                return
            self._write_json(HTTPStatus.NOT_FOUND, {"status": "error", "code": "not-found", "message": "Unknown endpoint."})
        except Exception as exc:  # noqa: BLE001
            self._handle_exception(exc)

    def do_POST(self):
        try:
            request_data = self._read_json()
            if self.path == "/api/v1/resolve":
                inventory = self._inventory()
                resolved, _ = resolve_lease_request(inventory, request_data)
                self._write_json(HTTPStatus.OK, {"status": "ok", **resolved})
                return
            if self.path == "/api/v1/lease":
                inventory = self._inventory()
                with self.server.operation_lock:
                    result = create_lease(self._clients(), self.server.lease_store, inventory, request_data)
                self._write_json(HTTPStatus.OK, {"status": "ok", **result})
                return
            if self.path == "/api/v1/agent/lease":
                inventory = self._inventory()
                with self.server.operation_lock:
                    result = lease_jenkins_agent(self._clients(), self.server.lease_store, inventory, request_data, self._jenkins())
                self._write_json(HTTPStatus.OK, {"status": "ok", **result})
                return
            if self.path == "/api/v1/prepare":
                inventory = self._inventory()
                with self.server.operation_lock:
                    result = prepare_lease(self._clients(), self.server.lease_store, inventory, request_data)
                self._write_json(HTTPStatus.OK, {"status": "ok", **result})
                return
            if self.path == "/api/v1/health":
                inventory = self._inventory()
                result = health_lease(self._clients(), self.server.lease_store, inventory, request_data)
                self._write_json(HTTPStatus.OK, {"status": "ok", **result})
                return
            if self.path == "/api/v1/release":
                inventory = self._inventory()
                with self.server.operation_lock:
                    lease_id = require_pattern(request_data.get("lease_id", ""), LEASE_ID_PATTERN, "lease_id")
                    record = self.server.lease_store.get(lease_id)
                    jenkins_client = self._jenkins() if record and record.get("jenkins_agent") else None
                    result = release_lease(
                        self._clients(),
                        self.server.lease_store,
                        inventory,
                        request_data,
                        jenkins_client=jenkins_client,
                    )
                self._write_json(HTTPStatus.OK, {"status": "ok", **result})
                return
            if self.path == "/api/v1/pool/refill":
                inventory = self._inventory()
                with self.server.operation_lock:
                    jenkins_client = self._jenkins() if hot_pool_preconnect_agents_enabled() else None
                    result = refill_hot_pool(
                        self._clients(),
                        self.server.lease_store,
                        inventory,
                        request_data,
                        jenkins_client=jenkins_client,
                    )
                self._write_json(HTTPStatus.OK, {"status": "ok", **result})
                return
            if self.path == "/api/v1/janitor/run":
                inventory = self._inventory()
                with self.server.operation_lock:
                    result = run_janitor(self._clients(), self.server.lease_store, inventory, request_data, jenkins_client=self._jenkins())
                self._write_json(HTTPStatus.OK, {"status": "ok", **result})
                return
            if self.path == "/api/v1/proxmox/smoke":
                inventory = self._inventory()
                result = run_api_smoke(self._clients(), inventory, request_data)
                self._write_json(HTTPStatus.OK, {"status": "ok", **result})
                return
            if self.path == "/api/v1/proxmox/smoke/warm":
                inventory = self._inventory()
                result = run_warm_smoke(self._clients(), inventory, request_data)
                self._write_json(HTTPStatus.OK, {"status": "ok", **result})
                return
            self._write_json(HTTPStatus.NOT_FOUND, {"status": "error", "code": "not-found", "message": "Unknown endpoint."})
        except Exception as exc:  # noqa: BLE001
            self._handle_exception(exc)


class RunnerCtlServer(ThreadingHTTPServer):
    def __init__(self, address, handler_cls, inventory_store, lease_store, proxmox_clients):
        super().__init__(address, handler_cls)
        self.inventory_store = inventory_store
        self.lease_store = lease_store
        self.proxmox_clients = proxmox_clients
        self.operation_lock = threading.Lock()
        self._jenkins_client = None
        self._jenkins_lock = threading.Lock()

    def jenkins_client(self):
        with self._jenkins_lock:
            if self._jenkins_client is None:
                self._jenkins_client = JenkinsApiClient.from_env()
            return self._jenkins_client


def hot_pool_reconciler_loop(server, interval_seconds, initial_delay_seconds):
    time.sleep(initial_delay_seconds)
    while True:
        try:
            inventory = server.inventory_store.load()
            with server.operation_lock:
                jenkins_client = server.jenkins_client() if hot_pool_preconnect_agents_enabled() else None
                refill_hot_pool(server.proxmox_clients, server.lease_store, inventory, {}, jenkins_client=jenkins_client)
        except Exception:  # noqa: BLE001
            traceback.print_exc()
        time.sleep(interval_seconds)


def main():
    inventory_path = os.getenv("RUNNERCTL_INVENTORY_PATH", "/var/jenkins_runner/inventory.json")
    lease_store_path = os.getenv("RUNNERCTL_LEASE_STORE_PATH", "/var/jenkins_home/runnerctl/leases.json")
    listen_host = os.getenv("RUNNERCTL_LISTEN_HOST", "127.0.0.1")
    listen_port = int(os.getenv("RUNNERCTL_LISTEN_PORT", "18080"))
    hot_pool_enabled = os.getenv("RUNNERCTL_HOT_POOL_ENABLED", "false").lower() == "true"
    hot_pool_interval = int(os.getenv("RUNNERCTL_HOT_POOL_RECONCILE_INTERVAL_SECONDS", "60"))
    hot_pool_initial_delay = int(os.getenv("RUNNERCTL_HOT_POOL_INITIAL_DELAY_SECONDS", "10"))

    inventory_store = InventoryStore(inventory_path)
    lease_store = LeaseStore(lease_store_path)
    proxmox_clients = ProxmoxClientRegistry()
    server = RunnerCtlServer((listen_host, listen_port), RunnerCtlHandler, inventory_store, lease_store, proxmox_clients)
    if hot_pool_enabled:
        threading.Thread(
            target=hot_pool_reconciler_loop,
            args=(server, hot_pool_interval, hot_pool_initial_delay),
            daemon=True,
        ).start()
    print(f"runnerctl listening on {listen_host}:{listen_port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
