#!/usr/bin/env python3

import json
import os
import re
import subprocess
import sys
import threading
import time
import traceback
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*$")
SHA_PATTERN = re.compile(r"^[0-9a-fA-F]{7,40}$")
REPO_PATH_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*\.git$")


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


class CacheConfig:
    def __init__(self, path, root, public_git_base_url):
        self.path = Path(path)
        self.root = Path(root)
        self.public_git_base_url = public_git_base_url.rstrip("/")
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
            if self.path == "/healthz":
                repos = self.server.cache.config.repositories()
                self._write_json(HTTPStatus.OK, {"status": "ok", "repositories": sorted(repos)})
                return
            if self.path == "/api/v1/repos":
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
            self._write_json(HTTPStatus.NOT_FOUND, {"status": "error", "code": "not-found", "message": "Unknown endpoint."})
        except Exception as exc:
            self._handle_exception(exc)

    def do_POST(self):
        try:
            if self.path != "/api/v1/fetch":
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


class Server(ThreadingHTTPServer):
    def __init__(self, address, cache):
        super().__init__(address, Handler)
        self.cache = cache


def main():
    config_path = os.getenv("GIT_CACHE_CONFIG_PATH", "/etc/proxmox-runner-git-cache/repos.json")
    cache_root = os.getenv("GIT_CACHE_ROOT", "/var/lib/proxmox-runner-git-cache")
    listen_host = os.getenv("GIT_CACHE_API_LISTEN_HOST", "127.0.0.1")
    listen_port = int(os.getenv("GIT_CACHE_API_PORT", "18082"))
    public_git_base_url = os.getenv("GIT_CACHE_PUBLIC_GIT_BASE_URL", "git://127.0.0.1")

    config = CacheConfig(config_path, cache_root, public_git_base_url)
    cache = GitObjectCache(config)
    server = Server((listen_host, listen_port), cache)
    print(f"proxmox runner git cache listening on {listen_host}:{listen_port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
