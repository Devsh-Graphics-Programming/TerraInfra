#!/usr/bin/env python3

import importlib.util
import io
import tempfile
import unittest
import urllib.error
from email.message import Message
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("proxmox-runner-git-cache.py")


def load_module():
    spec = importlib.util.spec_from_file_location("proxmox_runner_git_cache", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BlobCacheTests(unittest.TestCase):
    def setUp(self):
        self.module = load_module()

    def make_cache(self, directory):
        root = Path(directory) / "git"
        blob_root = root / "blobs"
        config = self.module.CacheConfig(
            Path(directory) / "repos.json",
            root,
            "git://127.0.0.1:9418",
            blob_root=blob_root,
            blob_allowed_prefixes=["ditt/ex40/lds/"],
            blob_max_bytes=1024,
        )
        return self.module.GitObjectCache(config)

    def make_fetch_cache(self, directory, opener, auth_file=None):
        root = Path(directory) / "git"
        blob_root = root / "blobs"
        config = self.module.CacheConfig(
            Path(directory) / "repos.json",
            root,
            "git://127.0.0.1:9418",
            blob_root=blob_root,
            blob_allowed_prefixes=["store-cache/"],
            blob_max_bytes=1024,
            blob_fetch_allowed_url_prefixes=["https://store.devsh.eu/ditt/"],
            blob_fetch_basic_auth_file=auth_file,
            url_opener=opener,
        )
        return self.module.GitObjectCache(config)

    def test_put_blob_writes_metadata_and_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = self.make_cache(directory)
            payload = b"cache-bytes"

            result = cache.put_blob("ditt/ex40/lds/v1/owen_sampler_buffer.bin", io.BytesIO(payload), len(payload))
            metadata = cache.blob_metadata("ditt/ex40/lds/v1/owen_sampler_buffer.bin")

            self.assertEqual(result["size"], len(payload))
            self.assertEqual(metadata["size"], len(payload))
            self.assertEqual(metadata["path"].read_bytes(), payload)
            self.assertEqual(result["sha256"], metadata["sha256"])

    def test_blob_key_rejects_traversal_and_wrong_prefix(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = self.make_cache(directory)
            for key in ["../x.bin", "ditt/ex40/lds/../x.bin", "other/ex40/lds/x.bin"]:
                with self.subTest(key=key):
                    with self.assertRaises(self.module.CacheError):
                        cache.config.blob_path(key)

    def test_put_blob_rejects_oversized_payload(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = self.make_cache(directory)
            with self.assertRaises(self.module.CacheError) as raised:
                cache.put_blob("ditt/ex40/lds/v1/owen_sampler_buffer.bin", io.BytesIO(b"x" * 2048), 2048)
            self.assertEqual(raised.exception.code, "blob-too-large")

    def test_blob_key_accepts_plus_signs(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = self.make_fetch_cache(directory, object())
            key, _ = cache.config.blob_path("store-cache/ditt/public/references/render_cube_x+/render_cube_x+.exr")
            self.assertEqual(key, "store-cache/ditt/public/references/render_cube_x+/render_cube_x+.exr")

    def test_fetch_blob_writes_allowed_https_url(self):
        class FakeResponse:
            status = 200

            def __init__(self, payload):
                self.payload = io.BytesIO(payload)
                self.headers = Message()
                self.headers["Content-Length"] = str(len(payload))

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def getcode(self):
                return self.status

            def read(self, size=-1):
                return self.payload.read(size)

        class FakeOpener:
            def __init__(self):
                self.urls = []

            def open(self, request, timeout):
                self.urls.append(request.full_url)
                return FakeResponse(b"report-bytes")

        with tempfile.TemporaryDirectory() as directory:
            opener = FakeOpener()
            cache = self.make_fetch_cache(directory, opener)

            result = cache.fetch_blob("store-cache/ditt/public/baseline/abc/index.html", "https://store.devsh.eu/ditt/public/latest/index.html")
            metadata = cache.blob_metadata("store-cache/ditt/public/baseline/abc/index.html")

            self.assertFalse(result["cached"])
            self.assertEqual(opener.urls, ["https://store.devsh.eu/ditt/public/latest/index.html"])
            self.assertEqual(metadata["path"].read_bytes(), b"report-bytes")

    def test_fetch_blob_adds_basic_auth_for_matching_prefix(self):
        class FakeResponse:
            status = 200

            def __init__(self):
                self.payload = io.BytesIO(b"private-report")
                self.headers = Message()
                self.headers["Content-Length"] = str(len(b"private-report"))

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def getcode(self):
                return self.status

            def read(self, size=-1):
                return self.payload.read(size)

        class FakeOpener:
            def __init__(self):
                self.headers = []

            def open(self, request, timeout):
                self.headers.append(dict(request.header_items()))
                return FakeResponse()

        with tempfile.TemporaryDirectory() as directory:
            auth_file = Path(directory) / "auth.json"
            auth_file.write_text(
                '{"entries":[{"url_prefix":"https://store.devsh.eu/ditt/private/","username":"store-user","password":"store-pass"}]}',
                encoding="utf-8",
            )
            opener = FakeOpener()
            cache = self.make_fetch_cache(directory, opener, auth_file)

            cache.fetch_blob("store-cache/ditt/private/baseline/abc/index.html", "https://store.devsh.eu/ditt/private/latest/index.html")

            self.assertEqual(opener.headers[0]["Authorization"], "Basic c3RvcmUtdXNlcjpzdG9yZS1wYXNz")

    def test_fetch_blob_maps_http_errors(self):
        class FailingOpener:
            def open(self, request, timeout):
                raise urllib.error.HTTPError(request.full_url, 401, "Unauthorized", {}, None)

        with tempfile.TemporaryDirectory() as directory:
            cache = self.make_fetch_cache(directory, FailingOpener())
            with self.assertRaises(self.module.CacheError) as raised:
                cache.fetch_blob("store-cache/ditt/private/baseline/abc/index.html", "https://store.devsh.eu/ditt/private/latest/index.html")
            self.assertEqual(raised.exception.code, "blob-fetch-failed")
            self.assertEqual(raised.exception.details["status"], 401)

    def test_fetch_blob_rejects_disallowed_url(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = self.make_fetch_cache(directory, object())
            with self.assertRaises(self.module.CacheError) as raised:
                cache.fetch_blob("store-cache/ditt/public/baseline/abc/index.html", "https://example.com/ditt/public/latest/index.html")
            self.assertEqual(raised.exception.code, "fetch-url-not-allowed")


class ScratchTests(unittest.TestCase):
    def setUp(self):
        self.module = load_module()

    def make_cache(self, directory):
        root = Path(directory) / "git"
        config = self.module.CacheConfig(
            Path(directory) / "repos.json",
            root,
            "git://127.0.0.1:9418",
            scratch_root=Path(directory) / "scratch",
            scratch_unc_root=r"\\10.254.254.254\runner-scratch",
        )
        return self.module.GitObjectCache(config)

    def test_scratch_create_metadata_and_delete_cycle(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = self.make_cache(directory)

            created = cache.create_scratch("gh-123-public")
            metadata = cache.scratch_metadata("gh-123-public")
            deleted = cache.delete_scratch("gh-123-public")

            self.assertTrue(created["created"])
            self.assertTrue(metadata["exists"])
            self.assertEqual(created["unc_path"], r"\\10.254.254.254\runner-scratch\gh-123-public")
            self.assertTrue(deleted["deleted"])
            self.assertFalse(cache.scratch_metadata("gh-123-public")["exists"])

    def test_scratch_rejects_traversal_and_unsupported_characters(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = self.make_cache(directory)
            for scratch_id in ["../x", "x/y", "x y", ""]:
                with self.subTest(scratch_id=scratch_id):
                    with self.assertRaises(self.module.CacheError):
                        cache.create_scratch(scratch_id)


if __name__ == "__main__":
    unittest.main()
