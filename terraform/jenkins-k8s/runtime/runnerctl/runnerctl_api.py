#!/usr/bin/env python3

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
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*$")
LABEL_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_.-]*$")
SAFE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
ENV_NAME_PATTERN = re.compile(r"^[A-Z_][A-Z0-9_]*$")
LEASE_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")


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
    }


def resolve_runner_context(inventory, runner_class, required_labels):
    class_id = require_pattern(runner_class, ID_PATTERN, "runner_class")
    labels = [require_pattern(label, LABEL_PATTERN, "required_labels") for label in required_labels]

    selected_class = find_by_id(inventory.get("runner_classes", []), class_id)
    if selected_class is None:
        raise RunnerCtlError(
            HTTPStatus.BAD_REQUEST,
            "unknown-runner-class",
            "Requested runner class is not configured.",
            {"runner_class": class_id},
        )

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


def resolve_lease_request(inventory, request_data):
    runner_class = request_data.get("runner_class", "")
    required_labels = require_list(request_data.get("required_labels", []), "required_labels")
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
                    raise RunnerCtlError(
                        HTTPStatus.BAD_GATEWAY,
                        "guest-command-failed",
                        "Guest command failed.",
                        {"node": node, "vmid": vmid, "exitcode": status.get("exitcode")},
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


def create_lease(client_registry, lease_store, inventory, request_data):
    resolved, candidates = resolve_lease_request(inventory, request_data)
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
                clone_created = True
                client.request(
                    "POST",
                    f"/nodes/{node}/qemu/{vmid}/config",
                    data={"tags": "runnerctl;lifecycle-ephemeral"},
                )

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
                    "connection": resolved["connection"],
                    "policy": {
                        "boot_timeout_minutes": int(resolved["policy"].get("boot_timeout_minutes", 10)),
                        "health_timeout_minutes": int(resolved["policy"].get("health_timeout_minutes", 15)),
                        "destroy_after_job": bool(resolved["policy"].get("destroy_after_job", True)),
                    },
                    "health_checks": resolved["health_checks"],
                }
                lease_store.put(lease_id, record)
            except Exception:
                if clone_created:
                    try:
                        safe_destroy(client, node, vmid)
                    except Exception:
                        pass
                raise

            return {
                "lease_id": lease_id,
                "runner_class": resolved["runner_class"],
                "labels": resolved["labels"],
                "host_id": candidate["host_id"],
                "node": node,
                "vmid": vmid,
                "clone_name": clone_name,
                "expires_at": expires_at,
                "connection": resolved["connection"],
            }
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
    lease_id = require_pattern(request_data.get("lease_id", ""), LEASE_ID_PATTERN, "lease_id")
    record = lease_store.get(lease_id)
    if record is None:
        raise RunnerCtlError(HTTPStatus.NOT_FOUND, "not-found", "Lease was not found.", {"lease_id": lease_id})
    host = host_by_id(inventory, record["host_id"])
    client = client_registry.client_for_host(inventory, host)
    node = require_pattern(record.get("node", ""), SAFE_NAME_PATTERN, "node")
    vmid = require_int(record.get("vmid", ""), "vmid", minimum=100, maximum=999999999)
    boot_timeout_seconds = int(record.get("policy", {}).get("boot_timeout_minutes", 10)) * 60

    lease_store.update(lease_id, {"state": "booting", "prepared_at": now_epoch()})
    client.start_vm(node, vmid)
    wait_for_guest_agent(client, node, vmid, boot_timeout_seconds)
    checks = {}
    for check_name in record.get("health_checks", []):
        try:
            checks[check_name] = run_named_health_check(client, node, vmid, check_name)
        except RunnerCtlError as exc:
            checks[check_name] = f"failed:{exc.code}"
    if "guest-agent" not in checks:
        checks["guest-agent"] = "passed"
    updated = lease_store.update(
        lease_id,
        {
            "state": "healthy",
            "healthy_at": now_epoch(),
            "last_health": checks,
        },
    )
    return {
        "lease_id": lease_id,
        "state": updated["state"],
        "host_id": record["host_id"],
        "node": node,
        "vmid": vmid,
        "health": updated["last_health"],
    }


def health_lease(client_registry, lease_store, inventory, request_data):
    lease_id = require_pattern(request_data.get("lease_id", ""), LEASE_ID_PATTERN, "lease_id")
    record = lease_store.get(lease_id)
    if record is None:
        raise RunnerCtlError(HTTPStatus.NOT_FOUND, "not-found", "Lease was not found.", {"lease_id": lease_id})
    host = host_by_id(inventory, record["host_id"])
    client = client_registry.client_for_host(inventory, host)
    node = require_pattern(record.get("node", ""), SAFE_NAME_PATTERN, "node")
    vmid = require_int(record.get("vmid", ""), "vmid", minimum=100, maximum=999999999)

    checks = {}
    requested_checks = record.get("health_checks", []) or ["guest-agent"]
    for check_name in requested_checks:
        try:
            checks[check_name] = run_named_health_check(client, node, vmid, check_name)
        except RunnerCtlError as exc:
            checks[check_name] = f"failed:{exc.code}"

    overall = "passed" if checks and all(value == "passed" for value in checks.values()) else "failed"
    lease_store.update(lease_id, {"last_health": checks, "last_health_at": now_epoch()})
    return {
        "lease_id": lease_id,
        "state": record.get("state"),
        "overall": overall,
        "checks": checks,
    }


def release_lease(client_registry, lease_store, inventory, request_data):
    lease_id = require_pattern(request_data.get("lease_id", ""), LEASE_ID_PATTERN, "lease_id")
    record = lease_store.get(lease_id)
    if record is None:
        return {"lease_id": lease_id, "result": "not-found"}

    host = host_by_id(inventory, record["host_id"])
    client = client_registry.client_for_host(inventory, host)
    node = require_pattern(record.get("node", ""), SAFE_NAME_PATTERN, "node")
    vmid = require_int(record.get("vmid", ""), "vmid", minimum=100, maximum=999999999)
    destroyed = client.safe_destroy(node, vmid)
    lease_store.delete(lease_id)
    return {
        "lease_id": lease_id,
        "result": "released",
        "host_id": record["host_id"],
        "node": node,
        "vmid": vmid,
        "destroyed_vm": destroyed,
    }


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
                    result = release_lease(self._clients(), self.server.lease_store, inventory, request_data)
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


def main():
    inventory_path = os.getenv("RUNNERCTL_INVENTORY_PATH", "/var/jenkins_runner/inventory.json")
    lease_store_path = os.getenv("RUNNERCTL_LEASE_STORE_PATH", "/var/jenkins_home/runnerctl/leases.json")
    listen_host = os.getenv("RUNNERCTL_LISTEN_HOST", "127.0.0.1")
    listen_port = int(os.getenv("RUNNERCTL_LISTEN_PORT", "18080"))

    inventory_store = InventoryStore(inventory_path)
    lease_store = LeaseStore(lease_store_path)
    proxmox_clients = ProxmoxClientRegistry()
    server = RunnerCtlServer((listen_host, listen_port), RunnerCtlHandler, inventory_store, lease_store, proxmox_clients)
    print(f"runnerctl listening on {listen_host}:{listen_port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
