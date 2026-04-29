#!/usr/bin/env python3

import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
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


ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*$")
SHA_PATTERN = re.compile(r"^[0-9a-fA-F]{7,40}$")
REPO_PATH_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*\.git$")
BLOB_KEY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/+-]*$")
SCRATCH_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$")


class CacheError(Exception):
    def __init__(self, status_code, code, message, details=None):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details or {}


def require_pattern(value, pattern, field_name):
    text = str(value or "").strip()
    if not pattern.fullmatch(text):
        raise CacheError(
            HTTPStatus.BAD_REQUEST,
            "invalid-request",
            f"Invalid value for {field_name}.",
            {"field": field_name},
        )
    return text


def normalize_repo_path(value):
    text = str(value or "").strip().replace("\\", "/")
    if text.startswith("/") or "//" in text or "/../" in f"/{text}/" or "/./" in f"/{text}/":
        raise CacheError(HTTPStatus.INTERNAL_SERVER_ERROR, "invalid-config", "Repository path is unsafe.")
    if not REPO_PATH_PATTERN.fullmatch(text):
        raise CacheError(HTTPStatus.INTERNAL_SERVER_ERROR, "invalid-config", "Repository path is invalid.")
    return text


def normalize_https_prefix(value, field_name):
    text = str(value or "").strip()
    parsed = urllib.parse.urlsplit(text)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password or parsed.fragment:
        raise CacheError(HTTPStatus.INTERNAL_SERVER_ERROR, "invalid-config", f"{field_name} must be a plain HTTPS URL prefix.")
    return text


def load_blob_fetch_basic_auth(path, allowed_url_prefixes):
    if not path:
        return []
    auth_path = Path(path)
    if not auth_path.is_file():
        raise CacheError(HTTPStatus.INTERNAL_SERVER_ERROR, "invalid-config", "Blob fetch auth file does not exist.")
    with auth_path.open("r", encoding="utf-8") as handle:
        loaded = json.load(handle)
    entries = loaded.get("entries") if isinstance(loaded, dict) else loaded
    if not isinstance(entries, list):
        raise CacheError(HTTPStatus.INTERNAL_SERVER_ERROR, "invalid-config", "Blob fetch auth file must contain an entries list.")
    normalized_entries = []
    for item in entries:
        if not isinstance(item, dict):
            raise CacheError(HTTPStatus.INTERNAL_SERVER_ERROR, "invalid-config", "Blob fetch auth entry must be an object.")
        prefix = normalize_https_prefix(item.get("url_prefix"), "blob fetch auth url_prefix")
        if not any(prefix.startswith(allowed) for allowed in allowed_url_prefixes):
            raise CacheError(HTTPStatus.INTERNAL_SERVER_ERROR, "invalid-config", "Blob fetch auth prefix is outside the fetch allowlist.")
        username = str(item.get("username") or "")
        password = str(item.get("password") or "")
        if not username or not password:
            raise CacheError(HTTPStatus.INTERNAL_SERVER_ERROR, "invalid-config", "Blob fetch auth entry is missing credentials.")
        token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
        normalized_entries.append({"url_prefix": prefix, "authorization": f"Basic {token}"})
    return sorted(normalized_entries, key=lambda entry: len(entry["url_prefix"]), reverse=True)


def run_git(args, cwd=None, timeout=900):
    command = ["git", *args]
    env = os.environ.copy()
    completed = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )
    if completed.returncode != 0:
        raise CacheError(
            HTTPStatus.BAD_GATEWAY,
            "git-command-failed",
            "Git cache command failed.",
            {"git_args": args[:2]},
        )
    return completed.stdout.strip()


def monotonic_ms():
    return int(time.monotonic() * 1000)


def elapsed_ms(started_ms):
    return max(0, monotonic_ms() - started_ms)


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        return None


class CacheConfig:
    def __init__(
        self,
        path,
        root,
        public_git_base_url,
        blob_root=None,
        blob_allowed_prefixes=None,
        blob_max_bytes=536870912,
        blob_fetch_allowed_url_prefixes=None,
        blob_fetch_basic_auth_file=None,
        blob_fetch_timeout_seconds=300,
        scratch_root=None,
        scratch_unc_root="",
        scratch_smb_username="",
        scratch_smb_credential_file=None,
        url_opener=None,
    ):
        self.path = Path(path)
        self.root = Path(root)
        self.public_git_base_url = public_git_base_url.rstrip("/")
        self.blob_root = Path(blob_root) if blob_root else self.root / "blobs"
        self.blob_allowed_prefixes = [prefix.strip().replace("\\", "/").strip("/") + "/" for prefix in (blob_allowed_prefixes or ["runner-cache/"])]
        self.blob_max_bytes = int(blob_max_bytes)
        self.blob_fetch_allowed_url_prefixes = [
            normalize_https_prefix(prefix, "BLOB_FETCH_ALLOWED_URL_PREFIXES")
            for prefix in (blob_fetch_allowed_url_prefixes or [])
            if prefix.strip()
        ]
        self.blob_fetch_basic_auth = load_blob_fetch_basic_auth(blob_fetch_basic_auth_file, self.blob_fetch_allowed_url_prefixes)
        self.blob_fetch_timeout_seconds = int(blob_fetch_timeout_seconds)
        self.scratch_root = Path(scratch_root) if scratch_root else self.root / "scratch"
        self.scratch_unc_root = str(scratch_unc_root or "").rstrip("\\/")
        self.scratch_smb_username = str(scratch_smb_username or "")
        self.scratch_smb_credential_file = Path(scratch_smb_credential_file) if scratch_smb_credential_file else None
        self.url_opener = url_opener or urllib.request.build_opener(NoRedirectHandler)
        self._lock = threading.Lock()
        self._mtime = None
        self._repos = None

    def repositories(self):
        current_mtime = self.path.stat().st_mtime
        with self._lock:
            if self._repos is not None and self._mtime == current_mtime:
                return self._repos
            with self.path.open("r", encoding="utf-8") as handle:
                loaded = json.load(handle)
            if loaded.get("version") != 1:
                raise CacheError(HTTPStatus.INTERNAL_SERVER_ERROR, "invalid-config", "Git cache config version is unsupported.")
            repos = {}
            for item in loaded.get("repositories", []):
                repo_id = require_pattern(item.get("id"), ID_PATTERN, "repositories.id")
                remote = str(item.get("remote") or "").strip()
                if not remote:
                    raise CacheError(HTTPStatus.INTERNAL_SERVER_ERROR, "invalid-config", "Repository remote is missing.")
                repo_path = normalize_repo_path(item.get("path") or f"{repo_id}.git")
                visibility = str(item.get("visibility") or "private").strip().lower()
                if visibility not in {"public", "private"}:
                    raise CacheError(HTTPStatus.INTERNAL_SERVER_ERROR, "invalid-config", "Repository visibility is invalid.")
                repos[repo_id] = {
                    "id": repo_id,
                    "remote": remote,
                    "path": repo_path,
                    "visibility": visibility,
                    "description": str(item.get("description") or "").strip(),
                }
            if not repos:
                raise CacheError(HTTPStatus.INTERNAL_SERVER_ERROR, "invalid-config", "Git cache config has no repositories.")
            self._repos = repos
            self._mtime = current_mtime
            return repos

    def repo(self, repo_id):
        repos = self.repositories()
        if repo_id not in repos:
            raise CacheError(HTTPStatus.BAD_REQUEST, "unknown-repository", "Requested repository is not configured.", {"id": repo_id})
        return repos[repo_id]

    def repo_dir(self, repo):
        repo_dir = (self.root / repo["path"]).resolve()
        root = self.root.resolve()
        try:
            repo_dir.relative_to(root)
        except ValueError as exc:
            raise CacheError(HTTPStatus.INTERNAL_SERVER_ERROR, "invalid-config", "Repository path escapes cache root.") from exc
        return repo_dir

    def public_url(self, repo):
        return f"{self.public_git_base_url}/{repo['path']}"

    def normalize_blob_key(self, key):
        text = str(key or "").strip().replace("\\", "/")
        if text.startswith("/") or text.endswith("/") or "//" in text:
            raise CacheError(HTTPStatus.BAD_REQUEST, "invalid-blob-key", "Blob cache key is invalid.")
        parts = text.split("/")
        if any(part in {"", ".", ".."} for part in parts):
            raise CacheError(HTTPStatus.BAD_REQUEST, "invalid-blob-key", "Blob cache key must not contain traversal segments.")
        if not BLOB_KEY_PATTERN.fullmatch(text):
            raise CacheError(HTTPStatus.BAD_REQUEST, "invalid-blob-key", "Blob cache key contains unsupported characters.")
        if not any(text.startswith(prefix) for prefix in self.blob_allowed_prefixes):
            raise CacheError(HTTPStatus.FORBIDDEN, "blob-key-not-allowed", "Blob cache key is outside the allowed prefixes.")
        return text

    def blob_path(self, key):
        normalized = self.normalize_blob_key(key)
        path = (self.blob_root / normalized).resolve()
        root = self.blob_root.resolve()
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise CacheError(HTTPStatus.BAD_REQUEST, "invalid-blob-key", "Blob cache key escapes cache root.") from exc
        return normalized, path

    def normalize_fetch_url(self, url):
        text = str(url or "").strip()
        parsed = urllib.parse.urlsplit(text)
        if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password or parsed.fragment:
            raise CacheError(HTTPStatus.BAD_REQUEST, "invalid-fetch-url", "Blob fetch URL must be a plain HTTPS URL.")
        if not self.blob_fetch_allowed_url_prefixes:
            raise CacheError(HTTPStatus.FORBIDDEN, "fetch-url-not-allowed", "Blob fetch is disabled.")
        if not any(text.startswith(prefix) for prefix in self.blob_fetch_allowed_url_prefixes):
            raise CacheError(HTTPStatus.FORBIDDEN, "fetch-url-not-allowed", "Blob fetch URL is outside the allowed prefixes.")
        return text

    def fetch_headers(self, url):
        headers = {"User-Agent": "proxmox-runner-git-cache/0.1"}
        for entry in self.blob_fetch_basic_auth:
            if url.startswith(entry["url_prefix"]):
                headers["Authorization"] = entry["authorization"]
                break
        return headers

    def scratch_path(self, scratch_id):
        normalized = require_pattern(scratch_id, SCRATCH_ID_PATTERN, "scratch.id")
        path = (self.scratch_root / normalized).resolve()
        root = self.scratch_root.resolve()
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise CacheError(HTTPStatus.BAD_REQUEST, "invalid-scratch-id", "Scratch id escapes scratch root.") from exc
        unc_path = None
        if self.scratch_unc_root:
            unc_path = self.scratch_unc_root + "\\" + normalized
        return normalized, path, unc_path

    def scratch_smb_auth(self):
        if not self.scratch_smb_username:
            return None
        if not self.scratch_smb_credential_file:
            raise CacheError(HTTPStatus.INTERNAL_SERVER_ERROR, "scratch-auth-missing", "Scratch SMB credential file is not configured.")
        try:
            credential = self.scratch_smb_credential_file.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise CacheError(HTTPStatus.INTERNAL_SERVER_ERROR, "scratch-auth-unreadable", "Scratch SMB credential file is not readable.") from exc
        if not credential:
            raise CacheError(HTTPStatus.INTERNAL_SERVER_ERROR, "scratch-auth-empty", "Scratch SMB credential is empty.")
        return {"username": self.scratch_smb_username, "credential": credential}


class GitObjectCache:
    def __init__(self, config):
        self.config = config
        self._locks = {}
        self._locks_lock = threading.Lock()

    def _lock_for(self, repo_id):
        with self._locks_lock:
            if repo_id not in self._locks:
                self._locks[repo_id] = threading.Lock()
            return self._locks[repo_id]

    def _ensure_repo(self, repo):
        repo_dir = self.config.repo_dir(repo)
        repo_dir.parent.mkdir(parents=True, exist_ok=True)
        if not repo_dir.exists():
            run_git(["init", "--bare", str(repo_dir)], timeout=120)
            run_git(["-C", str(repo_dir), "remote", "add", "origin", repo["remote"]], timeout=120)
        else:
            run_git(["-C", str(repo_dir), "remote", "set-url", "origin", repo["remote"]], timeout=120)
        return repo_dir

    def _has_commit(self, repo_dir, commit):
        completed = subprocess.run(
            ["git", "-C", str(repo_dir), "cat-file", "-e", f"{commit}^{{commit}}"],
            text=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return completed.returncode == 0

    def _resolve_commit(self, repo_dir, commit):
        return run_git(["-C", str(repo_dir), "rev-parse", f"{commit}^{{commit}}"], timeout=120)

    def fetch_commit(self, repo_id, commit):
        repo_id = require_pattern(repo_id, ID_PATTERN, "repositories.id")
        commit = require_pattern(commit, SHA_PATTERN, "repositories.commit").lower()
        repo = self.config.repo(repo_id)
        lock = self._lock_for(repo_id)
        started_ms = monotonic_ms()
        with lock:
            repo_dir = self._ensure_repo(repo)
            if not self._has_commit(repo_dir, commit):
                try:
                    run_git(["-C", str(repo_dir), "fetch", "origin", commit], timeout=1800)
                except CacheError:
                    run_git(
                        [
                            "-C",
                            str(repo_dir),
                            "fetch",
                            "--prune",
                            "origin",
                            "+refs/heads/*:refs/remotes/origin/*",
                            "+refs/tags/*:refs/tags/*",
                        ],
                        timeout=1800,
                    )
            if not self._has_commit(repo_dir, commit):
                run_git(["-C", str(repo_dir), "fetch", "origin", commit], timeout=1800)
            full_commit = self._resolve_commit(repo_dir, commit)
            cache_ref = f"refs/cache/{repo_id}/{full_commit}"
            run_git(["-C", str(repo_dir), "update-ref", cache_ref, full_commit], timeout=120)
            run_git(["-C", str(repo_dir), "update-server-info"], timeout=120)
            return {
                "id": repo_id,
                "commit": full_commit,
                "ref": cache_ref,
                "git_url": self.config.public_url(repo),
                "path": repo["path"],
                "visibility": repo["visibility"],
                "fetch_ms": elapsed_ms(started_ms),
            }

    def blob_metadata(self, key):
        normalized, path = self.config.blob_path(key)
        if not path.is_file():
            raise CacheError(HTTPStatus.NOT_FOUND, "blob-not-found", "Blob cache entry was not found.", {"key": normalized})
        digest = hashlib.sha256()
        size = 0
        with path.open("rb") as handle:
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                digest.update(chunk)
        return {"key": normalized, "path": path, "size": size, "sha256": digest.hexdigest()}

    def put_blob(self, key, source, content_length):
        normalized, path = self.config.blob_path(key)
        if content_length is None or content_length < 0:
            raise CacheError(HTTPStatus.LENGTH_REQUIRED, "missing-content-length", "Blob upload requires Content-Length.")
        if content_length > self.config.blob_max_bytes:
            raise CacheError(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                "blob-too-large",
                "Blob upload exceeds the configured size limit.",
                {"max_bytes": self.config.blob_max_bytes},
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_name(f".{path.name}.tmp.{os.getpid()}.{threading.get_ident()}")
        digest = hashlib.sha256()
        remaining = content_length
        written = 0
        try:
            with tmp_path.open("wb") as handle:
                while remaining > 0:
                    chunk = source.read(min(1024 * 1024, remaining))
                    if not chunk:
                        raise CacheError(HTTPStatus.BAD_REQUEST, "short-blob-upload", "Blob upload ended before Content-Length bytes.")
                    handle.write(chunk)
                    digest.update(chunk)
                    written += len(chunk)
                    remaining -= len(chunk)
            os.replace(tmp_path, path)
        finally:
            try:
                tmp_path.unlink()
            except FileNotFoundError:
                pass
        return {"key": normalized, "size": written, "sha256": digest.hexdigest()}

    def fetch_blob(self, key, url, refresh=False):
        normalized, path = self.config.blob_path(key)
        if path.is_file() and not refresh:
            metadata = self.blob_metadata(normalized)
            return {"cached": True, "key": normalized, "size": metadata["size"], "sha256": metadata["sha256"]}

        fetch_url = self.config.normalize_fetch_url(url)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_name(f".{path.name}.fetch.{os.getpid()}.{threading.get_ident()}")
        digest = hashlib.sha256()
        written = 0
        started_ms = monotonic_ms()
        try:
            request = urllib.request.Request(fetch_url, headers=self.config.fetch_headers(fetch_url))
            try:
                response_context = self.config.url_opener.open(request, timeout=self.config.blob_fetch_timeout_seconds)
            except urllib.error.HTTPError as exc:
                raise CacheError(
                    HTTPStatus.BAD_GATEWAY,
                    "blob-fetch-failed",
                    "Blob fetch returned an HTTP error.",
                    {"status": exc.code},
                ) from exc
            with response_context as response:
                status = getattr(response, "status", response.getcode())
                if status != 200:
                    raise CacheError(HTTPStatus.BAD_GATEWAY, "blob-fetch-failed", "Blob fetch returned a non-200 response.", {"status": status})
                content_length = response.headers.get("Content-Length")
                if content_length is not None and int(content_length) > self.config.blob_max_bytes:
                    raise CacheError(
                        HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                        "blob-too-large",
                        "Fetched blob exceeds the configured size limit.",
                        {"max_bytes": self.config.blob_max_bytes},
                    )
                with tmp_path.open("wb") as handle:
                    while True:
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        written += len(chunk)
                        if written > self.config.blob_max_bytes:
                            raise CacheError(
                                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                                "blob-too-large",
                                "Fetched blob exceeds the configured size limit.",
                                {"max_bytes": self.config.blob_max_bytes},
                            )
                        handle.write(chunk)
                        digest.update(chunk)
            os.replace(tmp_path, path)
        finally:
            try:
                tmp_path.unlink()
            except FileNotFoundError:
                pass
        return {"cached": False, "key": normalized, "size": written, "sha256": digest.hexdigest(), "fetch_ms": elapsed_ms(started_ms)}

    def create_scratch(self, scratch_id):
        normalized, path, unc_path = self.config.scratch_path(scratch_id)
        existed = path.exists()
        path.mkdir(parents=True, exist_ok=True, mode=0o770)
        path.chmod(0o770)
        result = {"id": normalized, "created": not existed, "exists": True, "unc_path": unc_path}
        auth = self.config.scratch_smb_auth()
        if auth:
            result["smb_auth"] = auth
        return result

    def delete_scratch(self, scratch_id):
        normalized, path, unc_path = self.config.scratch_path(scratch_id)
        existed = path.exists()
        if existed:
            shutil.rmtree(path)
        return {"id": normalized, "deleted": existed, "exists": False, "unc_path": unc_path}

    def scratch_metadata(self, scratch_id):
        normalized, path, unc_path = self.config.scratch_path(scratch_id)
        result = {"id": normalized, "exists": path.exists(), "unc_path": unc_path}
        auth = self.config.scratch_smb_auth()
        if auth:
            result["smb_auth"] = auth
        return result


class Handler(BaseHTTPRequestHandler):
    server_version = "proxmox-runner-git-cache/0.1"

    def log_message(self, format_string, *args):
        sys.stderr.write("%s - - [%s] %s\n" % (self.address_string(), self.log_date_time_string(), format_string % args))

    def _read_json(self):
        content_length = int(self.headers.get("Content-Length", "0"))
        if content_length <= 0:
            return {}
        raw = self.rfile.read(content_length)
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise CacheError(HTTPStatus.BAD_REQUEST, "invalid-json", "Request body must be valid JSON.") from exc

    def _write_json(self, status_code, payload):
        encoded = json.dumps(payload, sort_keys=True).encode("utf-8")
        self.send_response(int(status_code))
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _handle_exception(self, exc):
        if isinstance(exc, CacheError):
            self._write_json(
                exc.status_code,
                {"status": "error", "code": exc.code, "message": exc.message, "details": exc.details},
            )
            return
        traceback.print_exc()
        self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"status": "error", "code": "internal-error", "message": "Git cache internal error."})

    def do_GET(self):
        try:
            parsed_path = urllib.parse.urlsplit(self.path).path
            if self.path == "/healthz":
                repos = self.server.cache.config.repositories()
                self._write_json(HTTPStatus.OK, {"status": "ok", "repositories": sorted(repos)})
                return
            if parsed_path == "/api/v1/repos":
                repos = self.server.cache.config.repositories()
                self._write_json(
                    HTTPStatus.OK,
                    {
                        "status": "ok",
                        "repositories": [
                            {
                                "id": repo["id"],
                                "path": repo["path"],
                                "visibility": repo["visibility"],
                                "git_url": self.server.cache.config.public_url(repo),
                                "description": repo.get("description", ""),
                            }
                            for repo in repos.values()
                        ],
                    },
                )
                return
            if parsed_path.startswith("/api/v1/blob/"):
                key = urllib.parse.unquote(parsed_path[len("/api/v1/blob/") :])
                metadata = self.server.cache.blob_metadata(key)
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(metadata["size"]))
                self.send_header("X-Content-SHA256", metadata["sha256"])
                self.end_headers()
                with metadata["path"].open("rb") as handle:
                    while True:
                        chunk = handle.read(1024 * 1024)
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                return
            if parsed_path.startswith("/api/v1/scratch/"):
                scratch_id = urllib.parse.unquote(parsed_path[len("/api/v1/scratch/") :])
                metadata = self.server.cache.scratch_metadata(scratch_id)
                self._write_json(HTTPStatus.OK, {"status": "ok", **metadata})
                return
            self._write_json(HTTPStatus.NOT_FOUND, {"status": "error", "code": "not-found", "message": "Unknown endpoint."})
        except Exception as exc:
            self._handle_exception(exc)

    def do_HEAD(self):
        try:
            parsed_path = urllib.parse.urlsplit(self.path).path
            if parsed_path.startswith("/api/v1/blob/"):
                key = urllib.parse.unquote(parsed_path[len("/api/v1/blob/") :])
                metadata = self.server.cache.blob_metadata(key)
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(metadata["size"]))
                self.send_header("X-Content-SHA256", metadata["sha256"])
                self.end_headers()
                return
            self.send_response(HTTPStatus.NOT_FOUND)
            self.end_headers()
        except Exception as exc:
            self._handle_exception(exc)

    def do_POST(self):
        try:
            parsed_path = urllib.parse.urlsplit(self.path).path
            if parsed_path == "/api/v1/blob/fetch":
                body = self._read_json()
                result = self.server.cache.fetch_blob(body.get("key"), body.get("url"), bool(body.get("refresh", False)))
                self._write_json(HTTPStatus.OK, {"status": "ok", **result})
                return
            if parsed_path == "/api/v1/scratch/create":
                body = self._read_json()
                result = self.server.cache.create_scratch(body.get("id"))
                self._write_json(HTTPStatus.OK, {"status": "ok", **result})
                return
            if parsed_path == "/api/v1/scratch/delete":
                body = self._read_json()
                result = self.server.cache.delete_scratch(body.get("id"))
                self._write_json(HTTPStatus.OK, {"status": "ok", **result})
                return
            if parsed_path != "/api/v1/fetch":
                self._write_json(HTTPStatus.NOT_FOUND, {"status": "error", "code": "not-found", "message": "Unknown endpoint."})
                return
            body = self._read_json()
            repositories = body.get("repositories", [])
            if not isinstance(repositories, list) or not repositories:
                raise CacheError(HTTPStatus.BAD_REQUEST, "invalid-request", "repositories must be a non-empty JSON array.")
            if len(repositories) > 16:
                raise CacheError(HTTPStatus.BAD_REQUEST, "invalid-request", "repositories contains too many entries.")
            started_ms = monotonic_ms()
            results = []
            for item in repositories:
                if not isinstance(item, dict):
                    raise CacheError(HTTPStatus.BAD_REQUEST, "invalid-request", "repository entries must be objects.")
                results.append(self.server.cache.fetch_commit(item.get("id"), item.get("commit")))
            self._write_json(HTTPStatus.OK, {"status": "ok", "repositories": results, "total_ms": elapsed_ms(started_ms)})
        except Exception as exc:
            self._handle_exception(exc)

    def do_PUT(self):
        try:
            parsed_path = urllib.parse.urlsplit(self.path).path
            if not parsed_path.startswith("/api/v1/blob/"):
                self._write_json(HTTPStatus.NOT_FOUND, {"status": "error", "code": "not-found", "message": "Unknown endpoint."})
                return
            key = urllib.parse.unquote(parsed_path[len("/api/v1/blob/") :])
            content_length = self.headers.get("Content-Length")
            length = int(content_length) if content_length is not None else None
            result = self.server.cache.put_blob(key, self.rfile, length)
            self._write_json(HTTPStatus.OK, {"status": "ok", **result})
        except Exception as exc:
            self._handle_exception(exc)


class Server(ThreadingHTTPServer):
    def __init__(self, address, cache):
        super().__init__(address, Handler)
        self.cache = cache


def main():
    config_path = os.getenv("GIT_CACHE_CONFIG_PATH", "/etc/proxmox-runner-git-cache/repos.json")
    cache_root = os.getenv("GIT_CACHE_ROOT", "/var/lib/proxmox-runner-git-cache")
    blob_cache_root = os.getenv("BLOB_CACHE_ROOT", str(Path(cache_root) / "blobs"))
    blob_allowed_prefixes = [item for item in os.getenv("BLOB_CACHE_ALLOWED_PREFIXES", "runner-cache/").split(",") if item.strip()]
    blob_max_bytes = int(os.getenv("BLOB_CACHE_MAX_BYTES", "536870912"))
    blob_fetch_allowed_url_prefixes = [item for item in os.getenv("BLOB_FETCH_ALLOWED_URL_PREFIXES", "").split(",") if item.strip()]
    blob_fetch_basic_auth_file = os.getenv("BLOB_FETCH_BASIC_AUTH_FILE", "")
    blob_fetch_timeout_seconds = int(os.getenv("BLOB_FETCH_TIMEOUT_SECONDS", "300"))
    scratch_root = os.getenv("SCRATCH_ROOT", str(Path(cache_root) / "scratch"))
    scratch_unc_root = os.getenv("SCRATCH_UNC_ROOT", "")
    scratch_smb_username = os.getenv("SCRATCH_SMB_USERNAME", "")
    scratch_smb_credential_file = os.getenv("SCRATCH_SMB_CREDENTIAL_FILE", "")
    listen_host = os.getenv("GIT_CACHE_API_LISTEN_HOST", "127.0.0.1")
    listen_port = int(os.getenv("GIT_CACHE_API_PORT", "18082"))
    public_git_base_url = os.getenv("GIT_CACHE_PUBLIC_GIT_BASE_URL", "git://127.0.0.1")

    config = CacheConfig(
        config_path,
        cache_root,
        public_git_base_url,
        blob_cache_root,
        blob_allowed_prefixes,
        blob_max_bytes,
        blob_fetch_allowed_url_prefixes,
        blob_fetch_basic_auth_file,
        blob_fetch_timeout_seconds,
        scratch_root,
        scratch_unc_root,
        scratch_smb_username,
        scratch_smb_credential_file,
    )
    cache = GitObjectCache(config)
    server = Server((listen_host, listen_port), cache)
    print(f"proxmox runner git cache listening on {listen_host}:{listen_port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
