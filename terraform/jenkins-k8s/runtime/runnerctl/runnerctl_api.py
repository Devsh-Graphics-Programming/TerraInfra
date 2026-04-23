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
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


RUNNER_CLASS_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*$")
LABEL_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_.-]*$")
SAFE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


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


def merge_dicts(base, override):
    result = {}
    for source in (base or {}, override or {}):
        for key, value in source.items():
            if isinstance(value, dict) and isinstance(result.get(key), dict):
                result[key] = merge_dicts(result[key], value)
            else:
                result[key] = value
    return result


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
            if loaded.get("version") != 2:
                raise RunnerCtlError(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    "invalid-inventory",
                    "Runner platform inventory version is unsupported.",
                )
            self._data = loaded
            self._mtime = current_mtime
            return loaded


def resolve_runner(inventory, runner_class, required_labels):
    class_id = require_pattern(runner_class, RUNNER_CLASS_PATTERN, "runner_class")
    labels = [require_pattern(label, LABEL_PATTERN, "required_labels") for label in required_labels]

    selected_class = None
    for item in inventory.get("runner_classes", []):
        if item.get("id") == class_id:
            selected_class = item
            break
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
    selected_template = None
    for item in inventory.get("templates", []):
        if item.get("id") == template_id:
            selected_template = item
            break
    if selected_template is None:
        raise RunnerCtlError(
            HTTPStatus.INTERNAL_SERVER_ERROR,
            "invalid-inventory",
            "Runner class points to a missing template.",
            {"runner_class": class_id, "template": template_id},
        )

    defaults = inventory.get("defaults", {})
    resolved = {
        "runner_class": class_id,
        "labels": sorted(class_labels),
        "template": {
            "id": selected_template["id"],
            "channel": selected_template["channel"],
            "guest_os": selected_template.get("guest_os"),
        },
        "connection": merge_dicts(defaults.get("communicator"), selected_class.get("connection")),
        "backend": merge_dicts(defaults, merge_dicts(selected_template.get("backend"), selected_class.get("backend"))),
        "policy": merge_dicts(defaults, selected_class.get("policy")),
        "warm_pool": selected_class.get("warm_pool", {}),
    }
    return resolved


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
            body = urllib.parse.urlencode(data).encode("utf-8")
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

    def safe_destroy(self, node, vmid):
        if vmid not in self.qemu_vmids(node):
            return False
        upid = self.request(
            "DELETE",
            f"/nodes/{node}/qemu/{vmid}",
            query={"destroy-unreferenced-disks": 1, "purge": 1},
        )
        if upid:
            self.wait_task(node, str(upid), 180)
        return True


def build_service_state(inventory):
    return {
        "inventory_version": inventory.get("version"),
        "template_count": len(inventory.get("templates", [])),
        "runner_class_count": len(inventory.get("runner_classes", [])),
    }


def run_api_smoke(client, request_data):
    node = require_pattern(request_data.get("node", ""), SAFE_NAME_PATTERN, "node")
    template_pool = require_pattern(request_data.get("template_pool", ""), SAFE_NAME_PATTERN, "template_pool")
    runtime_pool = require_pattern(request_data.get("runtime_pool", ""), SAFE_NAME_PATTERN, "runtime_pool")
    storage = require_pattern(request_data.get("storage", ""), SAFE_NAME_PATTERN, "storage")

    version = client.request("GET", "/version")
    node_names = [entry.get("node") for entry in client.request("GET", "/cluster/resources", query={"type": "node"}) or []]
    if node not in node_names:
        raise RunnerCtlError(
            HTTPStatus.BAD_REQUEST,
            "unknown-node",
            "Configured Proxmox node is not visible through the API.",
            {"node": node},
        )

    pools = [entry.get("poolid") for entry in client.request("GET", "/pools") or []]
    missing_pools = [pool for pool in (template_pool, runtime_pool) if pool not in pools]
    if missing_pools:
        raise RunnerCtlError(
            HTTPStatus.BAD_REQUEST,
            "missing-pool",
            "Required Proxmox pool is missing.",
            {"missing_pools": missing_pools},
        )

    storages = [entry.get("storage") for entry in client.request("GET", f"/nodes/{node}/storage") or []]
    if storage not in storages:
        raise RunnerCtlError(
            HTTPStatus.BAD_REQUEST,
            "missing-storage",
            "Required Proxmox storage is missing on the selected node.",
            {"node": node, "storage": storage},
        )

    vm_count = len(client.qemu_vmids(node))
    return {
        "result": "success",
        "version": version.get("version"),
        "node": node,
        "template_pool": template_pool,
        "runtime_pool": runtime_pool,
        "storage": storage,
        "visible_vm_count": vm_count,
    }


def run_warm_smoke(client, request_data):
    node = require_pattern(request_data.get("node", ""), SAFE_NAME_PATTERN, "node")
    template_pool = require_pattern(request_data.get("template_pool", ""), SAFE_NAME_PATTERN, "template_pool")
    runtime_pool = require_pattern(request_data.get("runtime_pool", ""), SAFE_NAME_PATTERN, "runtime_pool")
    storage = require_pattern(request_data.get("storage", ""), SAFE_NAME_PATTERN, "storage")
    bridge = require_pattern(request_data.get("bridge", ""), SAFE_NAME_PATTERN, "bridge")
    vlan_tag = require_int(request_data.get("vlan_tag", ""), "vlan_tag", minimum=1, maximum=4094)
    source_vmid = require_int(request_data.get("source_vmid", ""), "source_vmid", minimum=100, maximum=999999999)
    clone_vmid = require_int(request_data.get("clone_vmid", ""), "clone_vmid", minimum=100, maximum=999999999)
    if source_vmid == clone_vmid:
        raise RunnerCtlError(
            HTTPStatus.BAD_REQUEST,
            "invalid-request",
            "source_vmid and clone_vmid must differ.",
        )

    client.safe_destroy(node, clone_vmid)
    client.safe_destroy(node, source_vmid)

    try:
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
                "scsi0": f"{storage}:1",
                "net0": f"virtio,bridge={bridge},tag={vlan_tag},firewall=1",
                "pool": template_pool,
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
                "pool": runtime_pool,
                "full": 0,
                "description": "Temporary runnerctl warm-path smoke clone",
            },
        )
        client.wait_task(node, str(clone_upid), 180)

        start_upid = client.request("POST", f"/nodes/{node}/qemu/{clone_vmid}/status/start")
        client.wait_task(node, str(start_upid), 120)
        running_state = client.request("GET", f"/nodes/{node}/qemu/{clone_vmid}/status/current")
        if running_state.get("status") != "running":
            raise RunnerCtlError(
                HTTPStatus.BAD_GATEWAY,
                "warm-smoke-start-failed",
                "Scratch clone did not reach running state.",
                {"node": node, "clone_vmid": clone_vmid},
            )

        stop_upid = client.request(
            "POST",
            f"/nodes/{node}/qemu/{clone_vmid}/status/stop",
            data={"timeout": 120},
        )
        client.wait_task(node, str(stop_upid), 180)
        stopped_state = client.request("GET", f"/nodes/{node}/qemu/{clone_vmid}/status/current")
        if stopped_state.get("status") != "stopped":
            raise RunnerCtlError(
                HTTPStatus.BAD_GATEWAY,
                "warm-smoke-stop-failed",
                "Scratch clone did not reach stopped state after stop request.",
                {"node": node, "clone_vmid": clone_vmid},
            )

        return {
            "result": "success",
            "node": node,
            "source_vmid": source_vmid,
            "clone_vmid": clone_vmid,
        }
    finally:
        client.safe_destroy(node, clone_vmid)
        client.safe_destroy(node, source_vmid)


class RunnerCtlHandler(BaseHTTPRequestHandler):
    server_version = "runnerctl/0.1"

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

    def _client(self):
        return self.server.proxmox_client

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
                        "runner_classes": [item.get("id") for item in inventory.get("runner_classes", [])],
                        "templates": [item.get("id") for item in inventory.get("templates", [])],
                    },
                )
                return
            self._write_json(HTTPStatus.NOT_FOUND, {"status": "error", "code": "not-found", "message": "Unknown endpoint."})
        except Exception as exc:  # noqa: BLE001
            self._handle_exception(exc)

    def do_POST(self):
        try:
            request_data = self._read_json()
            if self.path == "/api/v1/resolve":
                inventory = self._inventory()
                runner_class = request_data.get("runner_class", "")
                required_labels = request_data.get("required_labels", [])
                if not isinstance(required_labels, list):
                    raise RunnerCtlError(
                        HTTPStatus.BAD_REQUEST,
                        "invalid-request",
                        "required_labels must be a JSON array.",
                        {"field": "required_labels"},
                    )
                resolved = resolve_runner(inventory, runner_class, required_labels)
                self._write_json(HTTPStatus.OK, {"status": "ok", **resolved})
                return
            if self.path == "/api/v1/proxmox/smoke":
                result = run_api_smoke(self._client(), request_data)
                self._write_json(HTTPStatus.OK, {"status": "ok", **result})
                return
            if self.path == "/api/v1/proxmox/smoke/warm":
                result = run_warm_smoke(self._client(), request_data)
                self._write_json(HTTPStatus.OK, {"status": "ok", **result})
                return
            self._write_json(HTTPStatus.NOT_FOUND, {"status": "error", "code": "not-found", "message": "Unknown endpoint."})
        except Exception as exc:  # noqa: BLE001
            self._handle_exception(exc)


class RunnerCtlServer(ThreadingHTTPServer):
    def __init__(self, address, handler_cls, inventory_store, proxmox_client):
        super().__init__(address, handler_cls)
        self.inventory_store = inventory_store
        self.proxmox_client = proxmox_client


def main():
    inventory_path = os.getenv("RUNNERCTL_INVENTORY_PATH", "/var/jenkins_runner/inventory.json")
    listen_host = os.getenv("RUNNERCTL_LISTEN_HOST", "127.0.0.1")
    listen_port = int(os.getenv("RUNNERCTL_LISTEN_PORT", "18080"))

    inventory_store = InventoryStore(inventory_path)
    proxmox_client = ProxmoxApiClient(
        os.getenv("PROXMOX_RUNNER_API_URL", ""),
        os.getenv("PROXMOX_RUNNER_API_TOKEN_ID", ""),
        os.getenv("PROXMOX_RUNNER_API_TOKEN_SECRET", ""),
    )
    server = RunnerCtlServer((listen_host, listen_port), RunnerCtlHandler, inventory_store, proxmox_client)
    print(f"runnerctl listening on {listen_host}:{listen_port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
