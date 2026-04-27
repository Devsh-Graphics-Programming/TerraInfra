#!/usr/bin/env python3

import base64
import datetime
import hashlib
import hmac
import http.cookiejar
import ipaddress
import json
import mimetypes
import os
import re
import shutil
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
import xml.etree.ElementTree as ElementTree
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*$")
LABEL_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_.-]*$")
SAFE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
ENV_NAME_PATTERN = re.compile(r"^[A-Z_][A-Z0-9_]*$")
LEASE_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
STORE_PATH_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/+-]*$")
GIT_CACHE_REPO_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*\.git$")
CONTENT_TYPE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]*/[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]*(; ?[A-Za-z0-9_.-]+=[A-Za-z0-9_.-]+)*$")
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


def optional_bool(value, default=False):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default


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


def run_with_operation_lock(lock, callback):
    lock_wait_started_ms = monotonic_ms()
    lock.acquire()
    lock_wait_ms = elapsed_ms(lock_wait_started_ms)
    held_started_ms = monotonic_ms()
    try:
        result = callback()
    finally:
        held_ms = elapsed_ms(held_started_ms)
        lock.release()
    if isinstance(result, dict):
        result["timings"] = merge_timings(
            result.get("timings"),
            {
                "operation_lock_wait_ms": lock_wait_ms,
                "operation_lock_held_ms": held_ms,
            },
        )
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


def require_public_url(value, field_name, allowed_schemes):
    text = str(value or "").strip()
    parsed = urllib.parse.urlparse(text)
    if parsed.scheme not in allowed_schemes or not parsed.netloc or parsed.username or parsed.password or parsed.fragment:
        raise RunnerCtlError(
            HTTPStatus.INTERNAL_SERVER_ERROR,
            "invalid-inventory",
            f"Invalid URL for {field_name}.",
            {"field": field_name},
        )
    return text.rstrip("/")


def normalize_git_cache_repo_path(value, field_name):
    text = str(value or "").strip().replace("\\", "/")
    if text.startswith("/") or text.endswith("/") or "//" in text:
        raise RunnerCtlError(
            HTTPStatus.INTERNAL_SERVER_ERROR,
            "invalid-inventory",
            f"{field_name} must be a relative Git repository path.",
            {"field": field_name},
        )
    parts = text.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise RunnerCtlError(
            HTTPStatus.INTERNAL_SERVER_ERROR,
            "invalid-inventory",
            f"{field_name} must not contain traversal segments.",
            {"field": field_name},
        )
    if not GIT_CACHE_REPO_PATTERN.fullmatch(text):
        raise RunnerCtlError(
            HTTPStatus.INTERNAL_SERVER_ERROR,
            "invalid-inventory",
            f"{field_name} must end with .git and contain only safe path characters.",
            {"field": field_name},
        )
    return text


def host_git_object_cache(host):
    cache = host.get("git_object_cache")
    if not cache:
        return None
    api_url = require_public_url(cache.get("api_url"), "git_object_cache.api_url", {"http", "https"})
    git_base_url = require_public_url(cache.get("git_base_url"), "git_object_cache.git_base_url", {"git", "http", "https", "ssh"})
    git_client_url = None
    if cache.get("git_client_url"):
        git_client_url = require_public_url(cache.get("git_client_url"), "git_object_cache.git_client_url", {"http", "https"})
    git_client_sha256 = str(cache.get("git_client_sha256") or "").strip().lower()
    if git_client_sha256 and not re.fullmatch(r"[0-9a-f]{64}", git_client_sha256):
        raise RunnerCtlError(
            HTTPStatus.INTERNAL_SERVER_ERROR,
            "invalid-inventory",
            "git_object_cache.git_client_sha256 must be a SHA-256 hex digest.",
        )
    stores = require_list(cache.get("stores", []), "git_object_cache.stores")
    if not stores:
        raise RunnerCtlError(HTTPStatus.INTERNAL_SERVER_ERROR, "invalid-inventory", "git_object_cache.stores must not be empty.")
    public_stores = []
    seen_ids = set()
    for item in stores:
        if not isinstance(item, dict):
            raise RunnerCtlError(HTTPStatus.INTERNAL_SERVER_ERROR, "invalid-inventory", "git_object_cache.stores entries must be objects.")
        store_id = require_pattern(item.get("id", ""), ID_PATTERN, "git_object_cache.stores.id")
        if store_id in seen_ids:
            raise RunnerCtlError(HTTPStatus.INTERNAL_SERVER_ERROR, "invalid-inventory", "git_object_cache store ids must be unique.")
        seen_ids.add(store_id)
        repo_path = normalize_git_cache_repo_path(item.get("repo", ""), "git_object_cache.stores.repo")
        scope = str(item.get("scope") or "private").strip().lower()
        if scope not in {"public", "private"}:
            raise RunnerCtlError(HTTPStatus.INTERNAL_SERVER_ERROR, "invalid-inventory", "git_object_cache store scope is invalid.")
        public_stores.append(
            {
                "id": store_id,
                "repo": repo_path,
                "scope": scope,
                "git_url": f"{git_base_url}/{repo_path}",
                "description": str(item.get("description") or "").strip(),
            }
        )
    result = {"api_url": api_url, "git_base_url": git_base_url, "stores": public_stores}
    if git_client_url:
        result["git_client_url"] = git_client_url
    if git_client_sha256:
        result["git_client_sha256"] = git_client_sha256
    return result


def normalize_store_prefix(value, field_name="prefix"):
    text = str(value or "").strip()
    if not text:
        raise RunnerCtlError(
            HTTPStatus.BAD_REQUEST,
            "invalid-request",
            f"{field_name} is required.",
            {"field": field_name},
        )
    text = text.replace("\\", "/")
    if text.startswith("/") or "//" in text:
        raise RunnerCtlError(
            HTTPStatus.BAD_REQUEST,
            "invalid-request",
            f"{field_name} must be a relative object prefix.",
            {"field": field_name},
        )
    if not text.endswith("/"):
        text += "/"
    parts = text.split("/")[:-1]
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise RunnerCtlError(
            HTTPStatus.BAD_REQUEST,
            "invalid-request",
            f"{field_name} must not contain empty or traversal segments.",
            {"field": field_name},
        )
    if not STORE_PATH_PATTERN.fullmatch(text):
        raise RunnerCtlError(
            HTTPStatus.BAD_REQUEST,
            "invalid-request",
            f"{field_name} contains unsupported characters.",
            {"field": field_name},
        )
    return text


def normalize_store_file_path(value, field_name="path"):
    text = str(value or "").strip()
    if not text:
        raise RunnerCtlError(
            HTTPStatus.BAD_REQUEST,
            "invalid-request",
            f"{field_name} is required.",
            {"field": field_name},
        )
    text = text.replace("\\", "/")
    if text.startswith("/") or text.endswith("/") or "//" in text:
        raise RunnerCtlError(
            HTTPStatus.BAD_REQUEST,
            "invalid-request",
            f"{field_name} must be a relative file path.",
            {"field": field_name},
        )
    parts = text.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise RunnerCtlError(
            HTTPStatus.BAD_REQUEST,
            "invalid-request",
            f"{field_name} must not contain empty or traversal segments.",
            {"field": field_name},
        )
    if not STORE_PATH_PATTERN.fullmatch(text):
        raise RunnerCtlError(
            HTTPStatus.BAD_REQUEST,
            "invalid-request",
            f"{field_name} contains unsupported characters.",
            {"field": field_name},
        )
    return text


def store_allowed_prefixes():
    raw_value = os.getenv("RUNNERCTL_STORE_ALLOWED_PREFIXES", "ditt/dummy/")
    prefixes = [normalize_store_prefix(item, "RUNNERCTL_STORE_ALLOWED_PREFIXES") for item in raw_value.split(",") if item.strip()]
    if not prefixes:
        raise RunnerCtlError(
            HTTPStatus.INTERNAL_SERVER_ERROR,
            "store-config-missing",
            "Store publish allowed prefixes are not configured.",
        )
    return prefixes


def require_store_prefix_allowed(prefix):
    allowed_prefixes = store_allowed_prefixes()
    if not any(prefix.startswith(allowed) for allowed in allowed_prefixes):
        raise RunnerCtlError(
            HTTPStatus.FORBIDDEN,
            "store-prefix-forbidden",
            "Store publish prefix is not allowed.",
            {"prefix": prefix, "allowed_prefixes": allowed_prefixes},
        )


def content_type_for_store_path(path, supplied):
    if supplied:
        text = str(supplied).strip()
        if CONTENT_TYPE_PATTERN.fullmatch(text):
            return text
        raise RunnerCtlError(
            HTTPStatus.BAD_REQUEST,
            "invalid-request",
            "Invalid content type.",
            {"path": path},
        )
    return mimetypes.guess_type(path)[0] or "application/octet-stream"


def store_public_url(prefix):
    public_base = os.getenv("RUNNERCTL_STORE_PUBLIC_BASE_URL", "https://store.devsh.eu/")
    if not public_base.endswith("/"):
        public_base += "/"
    return urllib.parse.urljoin(public_base, prefix)


def store_publish_max_bytes():
    raw_value = os.getenv("RUNNERCTL_STORE_MAX_UPLOAD_BYTES", str(64 * 1024 * 1024))
    try:
        return max(1, int(raw_value))
    except ValueError:
        return 64 * 1024 * 1024


def store_config_from_env():
    config = {
        "endpoint": os.getenv("RUNNERCTL_STORE_S3_ENDPOINT", "https://s3.fr-par.scw.cloud"),
        "region": os.getenv("RUNNERCTL_STORE_S3_REGION", "fr-par"),
        "bucket": os.getenv("RUNNERCTL_STORE_S3_BUCKET", ""),
        "access_key": os.getenv("RUNNERCTL_STORE_AWS_ACCESS_KEY_ID", os.getenv("AWS_ACCESS_KEY_ID", "")),
        "secret_key": os.getenv("RUNNERCTL_STORE_AWS_SECRET_ACCESS_KEY", os.getenv("AWS_SECRET_ACCESS_KEY", "")),
    }
    missing = [key for key in ("bucket", "access_key", "secret_key") if not config[key]]
    if missing:
        raise RunnerCtlError(
            HTTPStatus.SERVICE_UNAVAILABLE,
            "store-config-missing",
            "Store publish credentials are not configured.",
            {"missing": missing},
        )
    return config


def aws_sigv4_signing_key(secret_key, date_stamp, region, service):
    key_date = hmac.new(("AWS4" + secret_key).encode("utf-8"), date_stamp.encode("utf-8"), hashlib.sha256).digest()
    key_region = hmac.new(key_date, region.encode("utf-8"), hashlib.sha256).digest()
    key_service = hmac.new(key_region, service.encode("utf-8"), hashlib.sha256).digest()
    return hmac.new(key_service, b"aws4_request", hashlib.sha256).digest()


def s3_endpoint_host(config):
    parsed_endpoint = urllib.parse.urlparse(config["endpoint"])
    if parsed_endpoint.scheme != "https" or not parsed_endpoint.netloc:
        raise RunnerCtlError(
            HTTPStatus.INTERNAL_SERVER_ERROR,
            "store-config-invalid",
            "Store S3 endpoint must be an HTTPS URL.",
        )
    return parsed_endpoint.scheme, f"{config['bucket']}.{parsed_endpoint.netloc}"


def s3_canonical_query(query):
    if not query:
        return ""
    parts = []
    for name, value in sorted((str(name), str(value)) for name, value in query.items()):
        encoded_name = urllib.parse.quote(name, safe="-_.~")
        encoded_value = urllib.parse.quote(value, safe="-_.~")
        parts.append(f"{encoded_name}={encoded_value}")
    return "&".join(parts)


def build_s3_request(config, method, key="", query=None, content=b"", headers=None, request_datetime=None):
    scheme, host = s3_endpoint_host(config)
    canonical_uri = "/" + urllib.parse.quote(key, safe="/~") if key else "/"
    canonical_query = s3_canonical_query(query)
    url = f"{scheme}://{host}{canonical_uri}"
    if canonical_query:
        url += "?" + canonical_query
    now = request_datetime or datetime.datetime.now(datetime.timezone.utc)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = now.strftime("%Y%m%d")
    payload_hash = hashlib.sha256(content).hexdigest()
    header_values = {
        "host": host,
        "x-amz-content-sha256": payload_hash,
        "x-amz-date": amz_date,
    }
    for name, value in (headers or {}).items():
        header_values[str(name).lower()] = str(value)
    canonical_headers = "".join(f"{name}:{header_values[name].strip()}\n" for name in sorted(header_values))
    signed_headers = ";".join(sorted(header_values))
    canonical_request = "\n".join([method.upper(), canonical_uri, canonical_query, canonical_headers, signed_headers, payload_hash])
    credential_scope = f"{date_stamp}/{config['region']}/s3/aws4_request"
    string_to_sign = "\n".join(
        [
            "AWS4-HMAC-SHA256",
            amz_date,
            credential_scope,
            hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
        ]
    )
    signing_key = aws_sigv4_signing_key(config["secret_key"], date_stamp, config["region"], "s3")
    signature = hmac.new(signing_key, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
    authorization = (
        f"AWS4-HMAC-SHA256 Credential={config['access_key']}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )
    request_headers = {
        "Authorization": authorization,
        "Host": host,
        "X-Amz-Content-Sha256": payload_hash,
        "X-Amz-Date": amz_date,
    }
    for name, value in (headers or {}).items():
        request_headers[name] = value
    data = content if method.upper() not in {"GET", "HEAD"} else None
    return urllib.request.Request(url, data=data, headers=request_headers, method=method.upper())


def build_s3_put_request(config, key, content, content_type, cache_control, request_datetime=None):
    return build_s3_request(
        config,
        "PUT",
        key=key,
        content=content,
        headers={"Cache-Control": cache_control, "Content-Type": content_type},
        request_datetime=request_datetime,
    )


def build_s3_delete_request(config, key, request_datetime=None):
    return build_s3_request(config, "DELETE", key=key, request_datetime=request_datetime)


def build_s3_list_request(config, prefix, continuation_token=None, request_datetime=None):
    query = {"list-type": "2", "prefix": prefix}
    if continuation_token:
        query["continuation-token"] = continuation_token
    return build_s3_request(config, "GET", query=query, request_datetime=request_datetime)


class S3StorePublisher:
    def __init__(self, config, opener=urllib.request.urlopen):
        self.config = config
        self.opener = opener

    def put_object(self, key, content, content_type, cache_control):
        request = build_s3_put_request(self.config, key, content, content_type, cache_control)
        try:
            with self.opener(request, timeout=60) as response:
                status = getattr(response, "status", 200)
                if status < 200 or status >= 300:
                    raise RunnerCtlError(
                        HTTPStatus.BAD_GATEWAY,
                        "store-upload-failed",
                        "Store upload failed.",
                        {"key": key, "upstream_status": status},
                    )
        except urllib.error.HTTPError as exc:
            raise RunnerCtlError(
                HTTPStatus.BAD_GATEWAY,
                "store-upload-failed",
                "Store upload failed.",
                {"key": key, "upstream_status": exc.code},
            ) from exc
        except urllib.error.URLError as exc:
            raise RunnerCtlError(
                HTTPStatus.BAD_GATEWAY,
                "store-upload-failed",
                "Store upload request failed.",
                {"key": key},
            ) from exc

    def list_keys(self, prefix):
        keys = []
        token = None
        while True:
            request = build_s3_list_request(self.config, prefix, continuation_token=token)
            try:
                with self.opener(request, timeout=60) as response:
                    status = getattr(response, "status", 200)
                    if status < 200 or status >= 300:
                        raise RunnerCtlError(
                            HTTPStatus.BAD_GATEWAY,
                            "store-list-failed",
                            "Store object listing failed.",
                            {"prefix": prefix, "upstream_status": status},
                        )
                    payload = response.read()
            except urllib.error.HTTPError as exc:
                raise RunnerCtlError(
                    HTTPStatus.BAD_GATEWAY,
                    "store-list-failed",
                    "Store object listing failed.",
                    {"prefix": prefix, "upstream_status": exc.code},
                ) from exc
            except urllib.error.URLError as exc:
                raise RunnerCtlError(
                    HTTPStatus.BAD_GATEWAY,
                    "store-list-failed",
                    "Store object listing request failed.",
                    {"prefix": prefix},
                ) from exc
            try:
                root = ElementTree.fromstring(payload)
            except ElementTree.ParseError as exc:
                raise RunnerCtlError(
                    HTTPStatus.BAD_GATEWAY,
                    "store-list-invalid-response",
                    "Store object listing returned invalid XML.",
                    {"prefix": prefix},
                ) from exc
            namespace = ""
            if root.tag.startswith("{"):
                namespace = root.tag.split("}", 1)[0] + "}"
            for key_element in root.findall(f".//{namespace}Contents/{namespace}Key"):
                if key_element.text:
                    keys.append(key_element.text)
            truncated = (root.findtext(f"{namespace}IsTruncated") or "").strip().lower() == "true"
            token = root.findtext(f"{namespace}NextContinuationToken")
            if not truncated:
                return keys

    def delete_object(self, key):
        request = build_s3_delete_request(self.config, key)
        try:
            with self.opener(request, timeout=60) as response:
                status = getattr(response, "status", 204)
                if status < 200 or status >= 300:
                    raise RunnerCtlError(
                        HTTPStatus.BAD_GATEWAY,
                        "store-delete-failed",
                        "Store object delete failed.",
                        {"key": key, "upstream_status": status},
                    )
        except urllib.error.HTTPError as exc:
            raise RunnerCtlError(
                HTTPStatus.BAD_GATEWAY,
                "store-delete-failed",
                "Store object delete failed.",
                {"key": key, "upstream_status": exc.code},
            ) from exc
        except urllib.error.URLError as exc:
            raise RunnerCtlError(
                HTTPStatus.BAD_GATEWAY,
                "store-delete-failed",
                "Store object delete request failed.",
                {"key": key},
            ) from exc

    def prune_prefix(self, prefix, expected_keys):
        current_keys = set(self.list_keys(prefix))
        expected = set(expected_keys)
        delete_keys = sorted(key for key in current_keys if key.startswith(prefix) and key not in expected)
        max_deletes = require_int(
            os.getenv("RUNNERCTL_STORE_PRUNE_MAX_DELETE_OBJECTS", "5000"),
            "RUNNERCTL_STORE_PRUNE_MAX_DELETE_OBJECTS",
            minimum=1,
            maximum=100000,
        )
        if len(delete_keys) > max_deletes:
            raise RunnerCtlError(
                HTTPStatus.CONFLICT,
                "store-prune-too-large",
                "Store prune would delete too many objects.",
                {"prefix": prefix, "delete_count": len(delete_keys), "maximum": max_deletes},
            )
        for key in delete_keys:
            self.delete_object(key)
        return {"listed_count": len(current_keys), "deleted_count": len(delete_keys)}


def publish_store_bundle(request_data, opener=urllib.request.urlopen):
    prefix = normalize_store_prefix(request_data.get("prefix", ""))
    require_store_prefix_allowed(prefix)
    files = require_list(request_data.get("files", []), "files")
    if not files:
        raise RunnerCtlError(
            HTTPStatus.BAD_REQUEST,
            "invalid-request",
            "files must contain at least one file.",
            {"field": "files"},
        )
    if len(files) > 200:
        raise RunnerCtlError(
            HTTPStatus.BAD_REQUEST,
            "invalid-request",
            "files contains too many entries.",
            {"field": "files", "maximum": 200},
        )

    cache_control = str(request_data.get("cache_control") or "no-store").strip()
    if "\r" in cache_control or "\n" in cache_control:
        raise RunnerCtlError(
            HTTPStatus.BAD_REQUEST,
            "invalid-request",
            "cache_control contains unsupported characters.",
            {"field": "cache_control"},
        )

    max_bytes = store_publish_max_bytes()
    total_bytes = 0
    prepared_files = []
    seen_paths = set()
    for index, item in enumerate(files):
        if not isinstance(item, dict):
            raise RunnerCtlError(
                HTTPStatus.BAD_REQUEST,
                "invalid-request",
                "Each files entry must be an object.",
                {"index": index},
            )
        relative_path = normalize_store_file_path(item.get("path", ""), f"files[{index}].path")
        if relative_path in seen_paths:
            raise RunnerCtlError(
                HTTPStatus.BAD_REQUEST,
                "invalid-request",
                "Duplicate store file path.",
                {"path": relative_path},
            )
        seen_paths.add(relative_path)
        try:
            content = base64.b64decode(str(item.get("content_base64", "")), validate=True)
        except (ValueError, TypeError) as exc:
            raise RunnerCtlError(
                HTTPStatus.BAD_REQUEST,
                "invalid-request",
                "File content must be valid base64.",
                {"path": relative_path},
            ) from exc
        total_bytes += len(content)
        if total_bytes > max_bytes:
            raise RunnerCtlError(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                "store-payload-too-large",
                "Store publish payload is too large.",
                {"maximum_bytes": max_bytes},
            )
        prepared_files.append(
            {
                "key": prefix + relative_path,
                "path": relative_path,
                "content": content,
                "content_type": content_type_for_store_path(relative_path, item.get("content_type")),
            }
        )

    publisher = S3StorePublisher(store_config_from_env(), opener=opener)
    for item in prepared_files:
        publisher.put_object(item["key"], item["content"], item["content_type"], cache_control)

    return {
        "result": "published",
        "prefix": prefix,
        "url": store_public_url(prefix),
        "file_count": len(prepared_files),
        "bytes": total_bytes,
    }


def validate_store_cache_control(value):
    cache_control = str(value or "no-store").strip()
    if "\r" in cache_control or "\n" in cache_control:
        raise RunnerCtlError(
            HTTPStatus.BAD_REQUEST,
            "invalid-request",
            "cache_control contains unsupported characters.",
            {"field": "cache_control"},
        )
    return cache_control


def normalize_jenkins_job_path(value, field_name="job"):
    text = str(value or "").strip().replace("\\", "/")
    if not text or text.startswith("/") or text.endswith("/") or "//" in text or "/../" in f"/{text}/":
        raise RunnerCtlError(
            HTTPStatus.BAD_REQUEST,
            "invalid-request",
            f"Invalid value for {field_name}.",
            {"field": field_name},
        )
    segments = text.split("/")
    for segment in segments:
        require_pattern(segment, SAFE_NAME_PATTERN, field_name)
    return "/".join(segments)


def jenkins_job_build_path(job, build_number, artifact_path=None):
    path_parts = []
    for segment in normalize_jenkins_job_path(job).split("/"):
        path_parts.extend(["job", urllib.parse.quote(segment, safe="")])
    path_parts.append(str(build_number))
    if artifact_path is not None:
        path_parts.append("artifact")
        path_parts.extend(urllib.parse.quote(segment, safe="") for segment in artifact_path.split("/"))
    return "/" + "/".join(path_parts)


def store_zip_entries(zip_path, max_bytes):
    total_bytes = 0
    prepared_entries = []
    seen_paths = set()
    try:
        with zipfile.ZipFile(zip_path) as archive:
            entries = [entry for entry in archive.infolist() if not entry.is_dir()]
            if not entries:
                raise RunnerCtlError(
                    HTTPStatus.BAD_REQUEST,
                    "invalid-request",
                    "Artifact zip contains no files.",
                    {"field": "artifact"},
                )
            if len(entries) > 2000:
                raise RunnerCtlError(
                    HTTPStatus.BAD_REQUEST,
                    "invalid-request",
                    "Artifact zip contains too many files.",
                    {"field": "artifact", "maximum": 2000},
                )
            for index, entry in enumerate(entries):
                file_type = (entry.external_attr >> 16) & 0o170000
                if file_type == 0o120000:
                    raise RunnerCtlError(
                        HTTPStatus.BAD_REQUEST,
                        "invalid-request",
                        "Artifact zip must not contain symbolic links.",
                        {"path": entry.filename},
                    )
                relative_path = normalize_store_file_path(entry.filename.replace("\\", "/"), f"artifact[{index}].path")
                if relative_path in seen_paths:
                    raise RunnerCtlError(
                        HTTPStatus.BAD_REQUEST,
                        "invalid-request",
                        "Duplicate store file path.",
                        {"path": relative_path},
                    )
                seen_paths.add(relative_path)
                total_bytes += int(entry.file_size)
                if total_bytes > max_bytes:
                    raise RunnerCtlError(
                        HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                        "store-payload-too-large",
                        "Store publish artifact is too large.",
                        {"maximum_bytes": max_bytes},
                    )
                prepared_entries.append(
                    {
                        "zip_name": entry.filename,
                        "key": relative_path,
                        "path": relative_path,
                        "content_type": content_type_for_store_path(relative_path, None),
                    }
                )
    except zipfile.BadZipFile as exc:
        raise RunnerCtlError(
            HTTPStatus.BAD_REQUEST,
            "invalid-request",
            "Artifact must be a valid zip file.",
            {"field": "artifact"},
        ) from exc
    return prepared_entries, total_bytes


def prepare_store_files_from_zip(zip_path, max_bytes):
    prepared_entries, total_bytes = store_zip_entries(zip_path, max_bytes)
    prepared_files = []
    with zipfile.ZipFile(zip_path) as archive:
        for item in prepared_entries:
            prepared_files.append(
                {
                    "key": item["key"],
                    "path": item["path"],
                    "content": archive.read(item["zip_name"]),
                    "content_type": item["content_type"],
                }
            )
    return prepared_files, total_bytes


def extract_store_zip(zip_path, target_dir, entries):
    target_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as archive:
        for item in entries:
            target = target_dir / item["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.read(item["zip_name"]))
            info = archive.getinfo(item["zip_name"])
            timestamp = datetime.datetime(*info.date_time, tzinfo=datetime.timezone.utc).timestamp()
            os.utime(target, (timestamp, timestamp))


def prepare_report_dynamic_source(report_dir, dynamic_dir):
    static_extensions = {".css", ".html", ".js", ".mjs", ".wasm"}
    skipped_names = {"publishS3.py", "server.py"}
    dynamic_dir.mkdir(parents=True, exist_ok=True)
    file_count = 0
    for path in report_dir.rglob("*"):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        if path.suffix.lower() in static_extensions or path.name in skipped_names:
            continue
        relative = path.relative_to(report_dir)
        target = dynamic_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        file_count += 1
    return file_count


def report_publish_relative_paths(entries):
    skipped_names = {"publishS3.py", "server.py"}
    paths = []
    for item in entries:
        relative = item["path"]
        parts = relative.split("/")
        if "__pycache__" in parts or parts[-1] in skipped_names:
            continue
        paths.append(relative)
    return sorted(paths)


def store_report_manifest(prefix, job, build_number, artifact_path, relative_paths):
    return {
        "schema": 1,
        "prefix": prefix,
        "job": job,
        "build": build_number,
        "artifact": artifact_path,
        "generated_at": datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat(),
        "file_count": len(relative_paths),
        "files": relative_paths,
    }


def store_prune_allowed_prefixes():
    raw_value = os.getenv(
        "RUNNERCTL_STORE_PRUNE_ALLOWED_PREFIXES",
        "ditt/dummy/,ditt/public/latest/,ditt/private/latest/,ditt/public/smoke/latest/,ditt/private/smoke/latest/,ditt/compare/o1experimental-vs-o3/public/latest/,ditt/compare/o1experimental-vs-o3/private/latest/",
    )
    prefixes = [normalize_store_prefix(item, "RUNNERCTL_STORE_PRUNE_ALLOWED_PREFIXES") for item in raw_value.split(",") if item.strip()]
    if not prefixes:
        raise RunnerCtlError(
            HTTPStatus.INTERNAL_SERVER_ERROR,
            "store-config-missing",
            "Store prune allowed prefixes are not configured.",
        )
    return prefixes


def require_store_prune_prefix_allowed(prefix):
    allowed_prefixes = store_prune_allowed_prefixes()
    if not any(prefix.startswith(allowed) for allowed in allowed_prefixes):
        raise RunnerCtlError(
            HTTPStatus.FORBIDDEN,
            "store-prune-prefix-forbidden",
            "Store prune prefix is not allowed.",
            {"prefix": prefix, "allowed_prefixes": allowed_prefixes},
        )


def summarize_publish_s3_output(output):
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    uploaded = sum(1 for line in lines if line.startswith("uploaded "))
    skipped = sum(1 for line in lines if line.startswith("skip "))
    would_upload = sum(1 for line in lines if line.startswith("upload "))
    return {
        "uploaded_count": uploaded,
        "skipped_count": skipped,
        "would_upload_count": would_upload,
        "line_count": len(lines),
        "tail": lines[-20:],
    }


def publish_store_report_artifact(request_data, jenkins_client, opener=urllib.request.urlopen):
    prefix = normalize_store_prefix(request_data.get("prefix", ""))
    require_store_prefix_allowed(prefix)
    job = normalize_jenkins_job_path(request_data.get("job", ""))
    build_number = require_int(request_data.get("build", ""), "build", minimum=1)
    artifact_path = normalize_store_file_path(request_data.get("artifact", ""), "artifact")
    if not artifact_path.lower().endswith(".zip"):
        raise RunnerCtlError(
            HTTPStatus.BAD_REQUEST,
            "invalid-request",
            "artifact must point to a zip file.",
            {"field": "artifact"},
        )
    jobs = require_int(request_data.get("jobs", "8"), "jobs", minimum=1, maximum=32)
    max_bytes = store_publish_max_bytes()
    store_config = store_config_from_env()

    with tempfile.TemporaryDirectory(prefix="runnerctl-store-report-") as directory:
        root = Path(directory)
        zip_path = root / "artifact.zip"
        artifact_bytes = jenkins_client.download_artifact(job, build_number, artifact_path, zip_path, max_bytes)
        entries, total_bytes = store_zip_entries(zip_path, max_bytes)
        report_dir = root / "report"
        dynamic_dir = root / "dynamic"
        extract_store_zip(zip_path, report_dir, entries)
        dynamic_file_count = prepare_report_dynamic_source(report_dir, dynamic_dir)
        script_candidates = sorted(report_dir.rglob("publishS3.py"))
        if not script_candidates:
            raise RunnerCtlError(
                HTTPStatus.BAD_REQUEST,
                "invalid-request",
                "Report artifact does not contain publishS3.py.",
                {"artifact": artifact_path},
            )
        script_path = script_candidates[0]
        environment = os.environ.copy()
        environment["AWS_ACCESS_KEY_ID"] = store_config["access_key"]
        environment["AWS_SECRET_ACCESS_KEY"] = store_config["secret_key"]
        command = [
            sys.executable,
            str(script_path),
            "--source",
            str(dynamic_dir),
            "--bucket",
            store_config["bucket"],
            "--prefix",
            prefix.strip("/"),
            "--endpoint",
            store_config["endpoint"],
            "--region",
            store_config["region"],
            "--checksum",
            "--jobs",
            str(jobs),
        ]
        timeout_seconds = require_int(
            os.getenv("RUNNERCTL_STORE_REPORT_PUBLISH_TIMEOUT_SECONDS", "7200"),
            "RUNNERCTL_STORE_REPORT_PUBLISH_TIMEOUT_SECONDS",
            minimum=60,
            maximum=86400,
        )
        completed = subprocess.run(
            command,
            cwd=str(script_path.parent),
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout_seconds,
            check=False,
        )
        if completed.returncode != 0:
            raise RunnerCtlError(
                HTTPStatus.BAD_GATEWAY,
                "store-publish-failed",
                "publishS3.py failed while publishing the report.",
                {
                    "exit_code": completed.returncode,
                    "stderr_tail": completed.stderr.splitlines()[-20:],
                    "stdout_tail": completed.stdout.splitlines()[-20:],
                },
            )
        summary = summarize_publish_s3_output(completed.stdout)
        relative_paths = report_publish_relative_paths(entries)
        manifest_path = "publish-manifest.json"
        manifest_key = prefix + manifest_path
        expected_keys = [prefix + path for path in relative_paths] + [manifest_key]
        manifest = store_report_manifest(prefix, job, build_number, artifact_path, relative_paths)
        publisher = S3StorePublisher(store_config, opener=opener)
        publisher.put_object(
            manifest_key,
            json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8"),
            "application/json",
            "no-store",
        )
        prune_summary = {"listed_count": 0, "deleted_count": 0}
        if optional_bool(request_data.get("prune"), default=False):
            require_store_prune_prefix_allowed(prefix)
            prune_summary = publisher.prune_prefix(prefix, expected_keys)

    return {
        "result": "published",
        "prefix": prefix,
        "url": store_public_url(prefix),
        "file_count": len(entries),
        "published_file_count": len(relative_paths),
        "dynamic_file_count": dynamic_file_count,
        "bytes": total_bytes,
        "artifact_bytes": artifact_bytes,
        "artifact": artifact_path,
        "job": job,
        "build": build_number,
        "publisher": "publishS3.py",
        "manifest": manifest_path,
        "pruned_count": prune_summary["deleted_count"],
        "listed_count": prune_summary["listed_count"],
        **summary,
    }


def delete_jenkins_artifact(request_data, jenkins_client):
    job = normalize_jenkins_job_path(request_data.get("job", ""))
    build_number = require_int(request_data.get("build", ""), "build", minimum=1)
    artifact_path = normalize_store_file_path(request_data.get("artifact", ""), "artifact")
    if artifact_path != "publish.zip":
        raise RunnerCtlError(
            HTTPStatus.BAD_REQUEST,
            "invalid-request",
            "Only the transient publish.zip artifact can be deleted through runnerctl.",
            {"field": "artifact"},
        )
    result = jenkins_client.delete_artifact_file(job, build_number, artifact_path)
    return {
        "result": result["result"],
        "job": job,
        "build": build_number,
        "artifact": artifact_path,
        "bytes": result["bytes"],
    }


def publish_store_artifact_zip(request_data, jenkins_client, opener=urllib.request.urlopen):
    prefix = normalize_store_prefix(request_data.get("prefix", ""))
    require_store_prefix_allowed(prefix)
    job = normalize_jenkins_job_path(request_data.get("job", ""))
    build_number = require_int(request_data.get("build", ""), "build", minimum=1)
    artifact_path = normalize_store_file_path(request_data.get("artifact", ""), "artifact")
    if not artifact_path.lower().endswith(".zip"):
        raise RunnerCtlError(
            HTTPStatus.BAD_REQUEST,
            "invalid-request",
            "artifact must point to a zip file.",
            {"field": "artifact"},
        )
    cache_control = validate_store_cache_control(request_data.get("cache_control"))
    max_bytes = store_publish_max_bytes()

    with tempfile.TemporaryDirectory(prefix="runnerctl-store-publish-") as directory:
        zip_path = Path(directory) / "artifact.zip"
        artifact_bytes = jenkins_client.download_artifact(job, build_number, artifact_path, zip_path, max_bytes)
        prepared_entries, total_bytes = store_zip_entries(zip_path, max_bytes)
        publisher = S3StorePublisher(store_config_from_env(), opener=opener)
        with zipfile.ZipFile(zip_path) as archive:
            for item in prepared_entries:
                publisher.put_object(prefix + item["path"], archive.read(item["zip_name"]), item["content_type"], cache_control)

    return {
        "result": "published",
        "prefix": prefix,
        "url": store_public_url(prefix),
        "file_count": len(prepared_entries),
        "bytes": total_bytes,
        "artifact_bytes": artifact_bytes,
        "artifact": artifact_path,
        "job": job,
        "build": build_number,
    }


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
    git_cache = host_git_object_cache(host)
    if git_cache:
        candidate["git_object_cache"] = git_cache
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
        "git_object_cache": candidate.get("git_object_cache"),
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

    def _build_request(self, method, path, data=None, headers=None, use_crumb=True):
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

        return urllib.request.Request(self._url(path), data=body, headers=request_headers, method=method)

    def _open(self, method, path, data=None, headers=None, use_crumb=True):
        request = self._build_request(method, path, data=data, headers=headers, use_crumb=use_crumb)
        try:
            return self.opener.open(request, timeout=self.timeout_seconds)
        except urllib.error.HTTPError as exc:
            if exc.code == 403 and use_crumb and method.upper() not in {"GET", "HEAD"}:
                self._crumb = None
                self.cookie_jar.clear()
                retry_request = self._build_request(method, path, data=data, headers=headers, use_crumb=use_crumb)
                try:
                    return self.opener.open(retry_request, timeout=self.timeout_seconds)
                except urllib.error.HTTPError as retry_exc:
                    exc = retry_exc
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

    def _request(self, method, path, data=None, headers=None, use_crumb=True):
        with self._open(method, path, data=data, headers=headers, use_crumb=use_crumb) as response:
            return response.read().decode("utf-8", errors="replace")

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

    def download_file(self, path, target_path, max_bytes):
        target = Path(target_path)
        total_bytes = 0
        with self._open("GET", path, use_crumb=False) as response:
            with target.open("wb") as handle:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    total_bytes += len(chunk)
                    if total_bytes > max_bytes:
                        raise RunnerCtlError(
                            HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                            "store-payload-too-large",
                            "Jenkins artifact is too large.",
                            {"maximum_bytes": max_bytes},
                        )
                    handle.write(chunk)
        return total_bytes

    def download_artifact(self, job, build_number, artifact_path, target_path, max_bytes):
        path = jenkins_job_build_path(job, build_number, artifact_path)
        return self.download_file(path, target_path, max_bytes)

    def script_text(self, script):
        return self._request("POST", "/scriptText", data={"script": script})

    def delete_artifact_file(self, job, build_number, artifact_path):
        script = f"""
import jenkins.model.Jenkins

String jobName = {groovy_string(job)}
int buildNumber = {int(build_number)}
String artifactPath = {groovy_string(artifact_path)}

def item = Jenkins.get().getItemByFullName(jobName)
if (item == null) {{
  println("missing-job")
  return
}}
def run = item.getBuildByNumber(buildNumber)
if (run == null) {{
  println("missing-build")
  return
}}
File archiveRoot = run.getArtifactsDir()
File root = archiveRoot.getCanonicalFile()
File target = new File(archiveRoot, artifactPath).getCanonicalFile()
if (!target.toPath().startsWith(root.toPath())) {{
  println("invalid-path")
  return
}}
if (!target.exists()) {{
  println("missing")
  return
}}
if (!target.isFile()) {{
  println("not-file")
  return
}}
long bytes = target.length()
if (!target.delete()) {{
  println("delete-failed")
  return
}}
println("deleted " + bytes)
"""
        output = self.script_text(script)
        lines = [line.strip() for line in output.splitlines() if line.strip()]
        for line in reversed(lines):
            if line.startswith("deleted "):
                return {"result": "deleted", "bytes": int(line.split(" ", 1)[1])}
            if line in {"missing", "missing-job", "missing-build"}:
                return {"result": line, "bytes": 0}
            if line in {"invalid-path", "not-file", "delete-failed"}:
                raise RunnerCtlError(
                    HTTPStatus.BAD_GATEWAY,
                    "jenkins-artifact-delete-failed",
                    "Jenkins refused to delete the requested artifact.",
                    {"result": line, "job": job, "build": build_number, "artifact": artifact_path},
                )
        raise RunnerCtlError(
            HTTPStatus.BAD_GATEWAY,
            "jenkins-artifact-delete-failed",
            "Jenkins artifact cleanup returned an unexpected response.",
            {"job": job, "build": build_number, "artifact": artifact_path},
        )

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
        "gpu_exclusive": bool(resolved["policy"].get("gpu_exclusive", False)),
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
    if record.get("git_object_cache"):
        result["git_object_cache"] = record["git_object_cache"]
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
                    "git_object_cache": candidate.get("git_object_cache"),
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

            active_records = active_records_for_candidate(leases, resolved["runner_class"], candidate)
            warm_pool = resolved.get("warm_pool") or {}
            max_active = int(warm_pool.get("max_ready", 1 if resolved["policy"].get("gpu_exclusive") else 999999))
            if len(active_records) >= max_active:
                skipped.append(
                    {
                        "host_id": candidate["host_id"],
                        "reason": "capacity-exhausted",
                        "active": len(active_records),
                        "max_active": max_active,
                    }
                )
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
                    "git_object_cache": candidate.get("git_object_cache"),
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
$javaHostsFile = Join-Path $agentRoot 'java-hosts'
Write-Output ("runnerctl: jenkinsHost={{0}}; hostAliasIp={{1}}" -f $jenkinsHost, $(if ($hostAliasIp) {{ $hostAliasIp }} else {{ '<empty>' }}))
$hostAliasPresent = $false
function Test-RunnerctlHostsAlias {{
  param([string]$Path, [string]$Address, [string]$HostName)
  if (-not (Test-Path $Path)) {{ return $false }}
  foreach ($line in (Get-Content -Path $Path -ErrorAction Stop)) {{
    $entry = ($line -split '#', 2)[0].Trim()
    if (-not $entry) {{ continue }}
    $parts = $entry -split '\\s+'
    if ($parts.Count -lt 2) {{ continue }}
    if ($parts[0] -ne $Address) {{ continue }}
    if ($parts[1..($parts.Count - 1)] -contains $HostName) {{ return $true }}
  }}
  return $false
}}
if ($hostAliasIp -and $jenkinsHost) {{
  $candidateHostsPaths = @(
    (Join-Path $env:WINDIR 'System32/drivers/etc/hosts'),
    (Join-Path $env:WINDIR 'Sysnative/drivers/etc/hosts')
  ) | Select-Object -Unique
  Write-Output ("runnerctl: hostsPaths={{0}}" -f ($candidateHostsPaths -join ','))
  $hostEntry = "{{0}} {{1}} # runnerctl-jenkins" -f $hostAliasIp, $jenkinsHost
  $writtenHostsPaths = @()
  foreach ($hostsPath in $candidateHostsPaths) {{
    try {{
      $hostsParent = Split-Path $hostsPath -Parent
      if (-not (Test-Path $hostsParent)) {{
        Write-Output ("runnerctl: hostAliasWriteSkipped path={{0}} reason=parent-missing" -f $hostsPath)
        continue
      }}
      $existingHosts = @()
      if (Test-Path $hostsPath) {{
        $existingHosts = @(Get-Content -Path $hostsPath -ErrorAction Stop)
      }}
      $filteredHosts = @()
      foreach ($line in $existingHosts) {{
        $entry = ($line -split '#', 2)[0].Trim()
        $dropLine = $line -match '# runnerctl-jenkins'
        if ($entry) {{
          $parts = $entry -split '\\s+'
          if (($parts.Count -ge 2) -and ($parts[1..($parts.Count - 1)] -contains $jenkinsHost)) {{
            $dropLine = $true
          }}
        }}
        if (-not $dropLine) {{
          $filteredHosts += $line
        }}
      }}
      [System.IO.File]::WriteAllLines($hostsPath, [string[]]($filteredHosts + $hostEntry), [Text.Encoding]::ASCII)
      if (Test-RunnerctlHostsAlias -Path $hostsPath -Address $hostAliasIp -HostName $jenkinsHost) {{
        $writtenHostsPaths += $hostsPath
      }}
    }} catch {{
      Write-Output ("runnerctl: hostAliasWriteError path={{0}} message={{1}}" -f $hostsPath, $_.Exception.Message)
    }}
  }}
  $hostAliasPresent = $writtenHostsPaths.Count -gt 0
  Set-Content -Path $javaHostsFile -Value $hostEntry -Encoding ASCII
  $routeInterface = Get-NetIPInterface -AddressFamily IPv4 |
    Where-Object {{ $_.ConnectionState -eq 'Connected' -and $_.InterfaceAlias -notlike 'Loopback*' }} |
    Sort-Object InterfaceMetric |
    Select-Object -First 1
  if ($routeInterface) {{
    $routePrefix = $hostAliasIp + '/32'
    $existingRoute = Get-NetRoute -DestinationPrefix $routePrefix -InterfaceIndex $routeInterface.InterfaceIndex -ErrorAction SilentlyContinue
    if (-not $existingRoute) {{
      New-NetRoute -DestinationPrefix $routePrefix -InterfaceIndex $routeInterface.InterfaceIndex -NextHop '0.0.0.0' -PolicyStore ActiveStore -ErrorAction Stop | Out-Null
    }}
    Write-Output ("runnerctl: hostAliasRoute={{0}} interface={{1}}" -f $routePrefix, $routeInterface.InterfaceAlias)
  }} else {{
    Write-Output 'runnerctl: hostAliasRouteSkipped=no-connected-ipv4-interface'
  }}
  Clear-DnsClientCache -ErrorAction SilentlyContinue
  & ipconfig /flushdns | Out-Null
  Write-Output ("runnerctl: hostAliasWritten={{0}}; paths={{1}}" -f $hostEntry, ($writtenHostsPaths -join ','))
  Write-Output ("runnerctl: hostAliasPresent={{0}}" -f $hostAliasPresent)
  if (-not $hostAliasPresent) {{
    throw 'Jenkins host alias was not written to the hosts file.'
  }}
}}
$resolvedAddresses = @()
$lastDnsError = $null
for ($attempt = 1; $attempt -le 4; $attempt++) {{
  try {{
    $resolvedAddresses = @([System.Net.Dns]::GetHostAddresses($jenkinsHost) | ForEach-Object {{ $_.IPAddressToString }})
    if ($resolvedAddresses.Count -gt 0) {{ break }}
  }} catch {{
    $lastDnsError = $_.Exception.Message
  }}
  Clear-DnsClientCache -ErrorAction SilentlyContinue
  Start-Sleep -Seconds 1
}}
if ($resolvedAddresses.Count -gt 0) {{
  Write-Output ("runnerctl: dns={{0}}" -f ($resolvedAddresses -join ','))
}} else {{
  Write-Output ("runnerctl: dnsError={{0}}" -f $lastDnsError)
  if ($hostAliasPresent) {{
    Write-Output 'runnerctl: dnsFallback=hosts-file-present'
  }} else {{
    throw ("Jenkins hostname did not resolve inside the runner: {{0}}" -f $jenkinsHost)
  }}
}}
$agentJarUrl = $baseUrl.TrimEnd('/') + '/jnlpJars/agent.jar'
$lastDownloadError = $null
$downloaded = $false
if ($hostAliasIp -and $jenkinsHost) {{
  $curl = Get-Command curl.exe -ErrorAction SilentlyContinue
  if ($curl) {{
    try {{
      $baseUri = [Uri]$baseUrl
      $originPort = $baseUri.Port
      if ($baseUri.IsDefaultPort) {{
        if ($baseUri.Scheme -eq 'https') {{
          $originPort = 443
        }} elseif ($baseUri.Scheme -eq 'http') {{
          $originPort = 80
        }}
      }}
      $resolve = "{{0}}:{{1}}:{{2}}" -f $baseUri.Host, $originPort, $hostAliasIp
      & $curl.Source --fail --silent --show-error --location --connect-timeout 10 --max-time 30 --resolve $resolve --output $jar $agentJarUrl
      if ($LASTEXITCODE -eq 0 -and (Test-Path $jar)) {{
        $downloaded = $true
        Write-Output ("runnerctl: agentJarDownload=curl-resolve host={{0}} port={{1}}" -f $baseUri.Host, $originPort)
      }} else {{
        $lastDownloadError = "curl.exe failed with exit code $LASTEXITCODE"
        Write-Output ("runnerctl: agentJarDownloadError attempt=curl message={{0}}" -f $lastDownloadError)
      }}
    }} catch {{
      $lastDownloadError = $_.Exception.Message
      Write-Output ("runnerctl: agentJarDownloadError attempt=curl message={{0}}" -f $lastDownloadError)
    }}
  }} else {{
    Write-Output 'runnerctl: agentJarDownloadSkipped curl.exe not found'
  }}
}}
$downloadAttempts = 4
for ($attempt = 1; (-not $downloaded) -and $attempt -le $downloadAttempts; $attempt++) {{
  try {{
    Invoke-WebRequest -Uri $agentJarUrl -OutFile $jar -UseBasicParsing -TimeoutSec 10
    $lastDownloadError = $null
    $downloaded = $true
    break
  }} catch {{
    $lastDownloadError = $_.Exception.Message
    Write-Output ("runnerctl: agentJarDownloadError attempt={{0}} message={{1}}" -f $attempt, $lastDownloadError)
    if ($attempt -lt $downloadAttempts) {{
      Start-Sleep -Seconds 2
    }}
  }}
}}
if ($lastDownloadError) {{
  throw ("Failed to download Jenkins remoting jar: {{0}}" -f $lastDownloadError)
}}
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
$javaHostsLiteral = $(if ($hostAliasIp -and (Test-Path $javaHostsFile)) {{ $javaHostsFile.Replace("'", "''") }} else {{ "" }})
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
`$arguments = @()
if ('$javaHostsLiteral') {{
  `$arguments += '-Djdk.net.hosts.file=$javaHostsLiteral'
}}
`$arguments += @(
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
    if check_name == "git-client":
        run_guest_powershell_check(
            client,
            node,
            vmid,
            "$git = Get-Command git.exe -ErrorAction SilentlyContinue; "
            "if (-not $git) { exit 1 }; "
            "& $git.Source --version; exit $LASTEXITCODE",
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
    if record.get("git_object_cache"):
        result["git_object_cache"] = record["git_object_cache"]
    timings = merge_timings(prepare_result.get("timings"), record.get("timings"))
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
    janitor = inventory.get("janitor", {})
    pool_member_stale_after_minutes = int(janitor.get("stale_pool_member_after_minutes", 5))
    pool_member_stale_after_seconds = max(60, pool_member_stale_after_minutes * 60)
    if bool(record.get("pool_member", False)) and state in {"creating", "booting", "healthy"}:
        try:
            created_at = int(record.get("created_at", 0))
        except (TypeError, ValueError):
            created_at = 0
        if created_at > 0 and now - created_at >= pool_member_stale_after_seconds:
            return "stale-pool-member"
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
    janitor_started_ms = monotonic_ms()
    dry_run = request_bool(request_data, "dry_run", False)
    now = now_epoch()
    janitor = inventory.get("janitor", {})
    stale_after_minutes = int(janitor.get("stale_lease_after_minutes", 240))
    stale_after_seconds = max(60, stale_after_minutes * 60)
    cleaned = []
    skipped = []
    timings = {
        "lease_scan_ms": 0,
        "vm_lookup_ms": 0,
        "vm_status_ms": 0,
        "vm_config_ms": 0,
        "delete_jenkins_node_ms": 0,
        "destroy_vm_ms": 0,
    }

    lease_scan_started_ms = monotonic_ms()
    lease_items = sorted(lease_store.all().items())
    timings["lease_scan_ms"] = elapsed_ms(lease_scan_started_ms)

    for lease_id, record in lease_items:
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
        item_timings = {}
        vm_lookup_started_ms = monotonic_ms()
        active_vmids = client.qemu_vmids(node)
        item_timings["vm_lookup_ms"] = elapsed_ms(vm_lookup_started_ms)
        timings["vm_lookup_ms"] += item_timings["vm_lookup_ms"]
        vm_exists = vmid in active_vmids
        if vm_exists:
            required_tags = set(janitor.get("require_tags", []))
            vm_config_started_ms = monotonic_ms()
            vm_config = client.vm_config(node, vmid)
            item_timings["vm_config_ms"] = elapsed_ms(vm_config_started_ms)
            timings["vm_config_ms"] += item_timings["vm_config_ms"]
            actual_tags = set(str(vm_config.get("tags", "")).split(";"))
            missing_tags = sorted(required_tags - actual_tags)
            if missing_tags:
                skipped.append({"lease_id": lease_id, "reason": "missing-required-tags", "missing_tags": missing_tags, "timings": item_timings})
                continue
        agent_deleted = False
        destroyed = False
        if not dry_run:
            agent = record.get("jenkins_agent") or {}
            if jenkins_client is not None and agent.get("node_name"):
                delete_agent_started_ms = monotonic_ms()
                try:
                    agent_deleted = jenkins_client.delete_agent_node(agent["node_name"])
                except RunnerCtlError:
                    agent_deleted = False
                item_timings["delete_jenkins_node_ms"] = elapsed_ms(delete_agent_started_ms)
                timings["delete_jenkins_node_ms"] += item_timings["delete_jenkins_node_ms"]
            if vm_exists:
                destroy_started_ms = monotonic_ms()
                destroyed = client.safe_destroy(node, vmid)
                item_timings["destroy_vm_ms"] = elapsed_ms(destroy_started_ms)
                timings["destroy_vm_ms"] += item_timings["destroy_vm_ms"]
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
                "timings": item_timings,
            }
        )

    referenced_vmids_by_host = {}
    for _, record in lease_items:
        host_id = record.get("host_id")
        if not host_id:
            continue
        try:
            vmid = int(record.get("vmid"))
        except (TypeError, ValueError):
            continue
        referenced_vmids_by_host.setdefault(host_id, set()).add(vmid)

    required_tags = set(janitor.get("require_tags", []))
    range_ref = janitor.get("vmid_range", "runner")
    for host in inventory.get("proxmox", {}).get("hosts", []):
        if not bool(host.get("enabled", True)):
            continue
        host_id = host.get("id")
        node = require_pattern(host.get("node", ""), SAFE_NAME_PATTERN, "node")
        try:
            vmid_range = host_vmid_range(host, range_ref)
        except RunnerCtlError as exc:
            skipped.append({"host_id": host_id, "reason": exc.code})
            continue
        client = client_registry.client_for_host(inventory, host)
        vm_lookup_started_ms = monotonic_ms()
        active_vmids = client.qemu_vmids(node)
        vm_lookup_ms = elapsed_ms(vm_lookup_started_ms)
        timings["vm_lookup_ms"] += vm_lookup_ms
        referenced_vmids = referenced_vmids_by_host.get(host_id, set())
        orphan_vmids = sorted(
            vmid
            for vmid in active_vmids
            if vmid_range["start"] <= int(vmid) <= vmid_range["end"] and int(vmid) not in referenced_vmids
        )
        for vmid in orphan_vmids:
            item_timings = {"vm_lookup_ms": vm_lookup_ms}
            vm_config_started_ms = monotonic_ms()
            vm_config = client.vm_config(node, vmid)
            item_timings["vm_config_ms"] = elapsed_ms(vm_config_started_ms)
            timings["vm_config_ms"] += item_timings["vm_config_ms"]
            actual_tags = set(tag for tag in str(vm_config.get("tags", "")).split(";") if tag)
            missing_tags = sorted(required_tags - actual_tags)
            name = str(vm_config.get("name") or "")
            if not name:
                vm_status_started_ms = monotonic_ms()
                vm_status = client.vm_status(node, vmid)
                item_timings["vm_status_ms"] = elapsed_ms(vm_status_started_ms)
                timings["vm_status_ms"] += item_timings["vm_status_ms"]
                name = str(vm_status.get("name") or "")
            runnerctl_named_orphan = name.startswith("runnerctl-") and "runnerctl" in actual_tags
            if missing_tags and not runnerctl_named_orphan:
                skipped.append(
                    {
                        "host_id": host_id,
                        "node": node,
                        "vmid": vmid,
                        "reason": "orphan-missing-required-tags",
                        "missing_tags": missing_tags,
                        "timings": item_timings,
                    }
                )
                continue
            if not name.startswith("runnerctl-"):
                skipped.append(
                    {
                        "host_id": host_id,
                        "node": node,
                        "vmid": vmid,
                        "reason": "orphan-name-not-runnerctl",
                        "name": name,
                        "timings": item_timings,
                    }
                )
                continue
            template_value = str(vm_config.get("template", "")).strip().lower()
            if template_value in {"1", "true", "yes"}:
                skipped.append(
                    {
                        "host_id": host_id,
                        "node": node,
                        "vmid": vmid,
                        "reason": "orphan-template-vm",
                        "timings": item_timings,
                    }
                )
                continue
            destroyed = False
            if not dry_run:
                destroy_started_ms = monotonic_ms()
                destroyed = client.safe_destroy(node, vmid)
                item_timings["destroy_vm_ms"] = elapsed_ms(destroy_started_ms)
                timings["destroy_vm_ms"] += item_timings["destroy_vm_ms"]
            cleaned.append(
                {
                    "lease_id": None,
                    "runner_class": None,
                    "host_id": host_id,
                    "node": node,
                    "vmid": vmid,
                    "state": "orphan",
                    "reason": "orphan-runner-vm",
                    "destroyed_vm": destroyed,
                    "deleted_jenkins_node": False,
                    "timings": item_timings,
                }
            )

    timings["janitor_ms"] = elapsed_ms(janitor_started_ms)
    return {
        "result": "success",
        "dry_run": dry_run,
        "cleaned": cleaned,
        "skipped": skipped,
        "cleaned_count": len(cleaned),
        "skipped_count": len(skipped),
        "timings": timings,
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
        "git_object_cache": candidate.get("git_object_cache"),
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
                "pool_jenkins_agent_connect_ms": jenkins_agent_ms,
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
    refill_started_ms = monotonic_ms()
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
    return {"result": "success", "janitor": janitor_result, "pools": results, "timings": {"refill_ms": elapsed_ms(refill_started_ms)}}


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

                def lease_action():
                    return create_lease(self._clients(), self.server.lease_store, inventory, request_data)
                result = run_with_operation_lock(
                    self.server.operation_lock,
                    lease_action,
                )
                self._write_json(HTTPStatus.OK, {"status": "ok", **result})
                return
            if self.path == "/api/v1/agent/lease":
                inventory = self._inventory()

                def lease_agent_action():
                    return lease_jenkins_agent(
                        self._clients(),
                        self.server.lease_store,
                        inventory,
                        request_data,
                        self._jenkins(),
                    )
                result = run_with_operation_lock(
                    self.server.operation_lock,
                    lease_agent_action,
                )
                self._write_json(HTTPStatus.OK, {"status": "ok", **result})
                return
            if self.path == "/api/v1/prepare":
                inventory = self._inventory()

                def prepare_action():
                    return prepare_lease(self._clients(), self.server.lease_store, inventory, request_data)
                result = run_with_operation_lock(
                    self.server.operation_lock,
                    prepare_action,
                )
                self._write_json(HTTPStatus.OK, {"status": "ok", **result})
                return
            if self.path == "/api/v1/health":
                inventory = self._inventory()
                result = health_lease(self._clients(), self.server.lease_store, inventory, request_data)
                self._write_json(HTTPStatus.OK, {"status": "ok", **result})
                return
            if self.path == "/api/v1/release":
                inventory = self._inventory()

                def release_action():
                    lease_id = require_pattern(request_data.get("lease_id", ""), LEASE_ID_PATTERN, "lease_id")
                    record = self.server.lease_store.get(lease_id)
                    jenkins_client = self._jenkins() if record and record.get("jenkins_agent") else None
                    return release_lease(
                        self._clients(),
                        self.server.lease_store,
                        inventory,
                        request_data,
                        jenkins_client=jenkins_client,
                    )
                result = run_with_operation_lock(self.server.operation_lock, release_action)
                self._write_json(HTTPStatus.OK, {"status": "ok", **result})
                return
            if self.path == "/api/v1/pool/refill":
                inventory = self._inventory()

                def refill_action():
                    jenkins_client = self._jenkins() if hot_pool_preconnect_agents_enabled() else None
                    return refill_hot_pool(
                        self._clients(),
                        self.server.lease_store,
                        inventory,
                        request_data,
                        jenkins_client=jenkins_client,
                    )
                result = run_with_operation_lock(self.server.operation_lock, refill_action)
                self._write_json(HTTPStatus.OK, {"status": "ok", **result})
                return
            if self.path == "/api/v1/janitor/run":
                inventory = self._inventory()

                def janitor_action():
                    return run_janitor(
                        self._clients(),
                        self.server.lease_store,
                        inventory,
                        request_data,
                        jenkins_client=self._jenkins(),
                    )
                result = run_with_operation_lock(
                    self.server.operation_lock,
                    janitor_action,
                )
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
            if self.path == "/api/v1/store/publish":
                result = publish_store_bundle(request_data)
                self._write_json(HTTPStatus.OK, {"status": "ok", **result})
                return
            if self.path == "/api/v1/store/publish-artifact":
                result = publish_store_artifact_zip(request_data, self._jenkins())
                self._write_json(HTTPStatus.OK, {"status": "ok", **result})
                return
            if self.path == "/api/v1/store/publish-report-artifact":
                result = publish_store_report_artifact(request_data, self._jenkins())
                self._write_json(HTTPStatus.OK, {"status": "ok", **result})
                return
            if self.path == "/api/v1/jenkins/delete-artifact":
                result = delete_jenkins_artifact(request_data, self._jenkins())
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
