#!/usr/bin/env python3

import importlib.util
import io
import tempfile
import unittest
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

    def make_fetch_cache(self, directory, opener):
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

    def test_fetch_blob_rejects_disallowed_url(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = self.make_fetch_cache(directory, object())
            with self.assertRaises(self.module.CacheError) as raised:
                cache.fetch_blob("store-cache/ditt/public/baseline/abc/index.html", "https://example.com/ditt/public/latest/index.html")
            self.assertEqual(raised.exception.code, "fetch-url-not-allowed")


if __name__ == "__main__":
    unittest.main()
