"""Read-through and scripted one-pass satellite sync tests."""

from __future__ import annotations

import gzip
import asyncio
import io
import json
import os
import sqlite3
import struct
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

import numpy as np

from features.sync import readthrough
from features.media import routes as media_routes
import thumbnails
import field_sync


class _Paths:
    def __init__(self, root: Path):
        self.binary = root / "base.bin.gz"
        self.metadata = root / "base.json"
        self.preview = root / "base.jpg"


def _base_bytes() -> bytes:
    rgb = np.full((2, 3, 3), 32768, dtype="<u2")
    payload = b"PABASE1\0" + struct.pack("<II", 3, 2) + rgb.tobytes()
    return gzip.compress(payload)


class _HubHandler(BaseHTTPRequestHandler):
    base = _base_bytes()

    def do_GET(self):  # noqa: N802
        if self.path.endswith("/missing"):
            self.send_response(404)
            self.end_headers()
            return
        if self.path.endswith("/meta"):
            body = json.dumps({"camera": "stub"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        boundary = b"photoarchive-pabase1"
        body = b"".join((
            b"--" + boundary + b"\r\n"
            b"Content-Disposition: attachment; name=\"base\"; filename=\"base.bin.gz\"\r\n"
            b"Content-Type: application/octet-stream\r\nContent-Encoding: gzip\r\n\r\n",
            self.base,
            b"\r\n--" + boundary + b"\r\n"
            b"Content-Disposition: attachment; name=\"meta\"; filename=\"base.json\"\r\n"
            b"Content-Type: application/json\r\n\r\n",
            json.dumps({"camera": "stub"}).encode(),
            b"\r\n--" + boundary + b"--\r\n",
        ))
        self.send_response(200)
        self.send_header("Content-Type", f"multipart/mixed; boundary={boundary.decode()}")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format, *_args):
        pass


class ReadthroughTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.old_mode = os.environ.get("PHOTOARCHIVE_MODE")
        self.old_hub = os.environ.get("PHOTOARCHIVE_HUB_URL")
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _HubHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        os.environ["PHOTOARCHIVE_MODE"] = "satellite"
        os.environ["PHOTOARCHIVE_HUB_URL"] = f"http://127.0.0.1:{self.server.server_port}"
        self.db_path = str(Path(self.tempdir.name) / "catalog.db")
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("CREATE TABLE images (id INTEGER PRIMARY KEY, content_hash TEXT)")
            conn.execute("INSERT INTO images VALUES (1, ?)", ("a" * 32,))

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=1)
        if self.old_mode is None:
            os.environ.pop("PHOTOARCHIVE_MODE", None)
        else:
            os.environ["PHOTOARCHIVE_MODE"] = self.old_mode
        if self.old_hub is None:
            os.environ.pop("PHOTOARCHIVE_HUB_URL", None)
        else:
            os.environ["PHOTOARCHIVE_HUB_URL"] = self.old_hub
        self.tempdir.cleanup()

    def test_fetches_base_and_companion_metadata_from_stub_hub(self):
        paths = _Paths(Path(self.tempdir.name) / "cache")
        metadata = readthrough.fetch_base_cache_for_image(1, paths, db_path=self.db_path, source_path="/offline/raw.dng")
        self.assertEqual(metadata["camera"], "stub")
        self.assertEqual(metadata["source_path"], "/offline/raw.dng")
        self.assertTrue(paths.binary.exists())
        self.assertTrue(paths.metadata.exists())
        self.assertTrue(paths.preview.exists())

    def test_missing_hub_base_is_an_honest_error(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("UPDATE images SET content_hash = ? WHERE id = 1", ("b" * 32,))
        original = readthrough._request

        def missing(url, *, timeout):
            if url.endswith("/meta"):
                return original(url, timeout=timeout)
            raise readthrough.BaseReadthroughError("Hub has no cached Develop base for this photo")

        readthrough._request = missing
        try:
            with self.assertRaisesRegex(readthrough.BaseReadthroughError, "no cached"):
                readthrough.fetch_base_cache_for_image(1, _Paths(Path(self.tempdir.name) / "cache"), db_path=self.db_path, source_path="/offline/raw.dng")
        finally:
            readthrough._request = original

    def test_sm_thumbnail_uses_the_media_readthrough_and_caches_locally(self):
        calls = []

        async def request(method, url, *, body=None, headers=None):
            calls.append((method, url, body, headers))
            return 200, {"content-type": "image/jpeg"}, b"remote-sm-thumb"

        with mock.patch.object(media_routes, "_urllib_request", request), mock.patch.object(
            thumbnails, "_write_thumbnail_to_disk"
        ) as write_disk, mock.patch.object(thumbnails, "_memory_put") as memory_put:
            response = asyncio.run(
                media_routes._remote_media_response(
                    {"id": 41, "hub_image_id": 9},
                    "sm",
                )
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.body, b"remote-sm-thumb")
        self.assertEqual(calls[0][0:2], ("GET", f"{os.environ['PHOTOARCHIVE_HUB_URL']}/api/thumb/sm/9"))
        write_disk.assert_called_once()
        memory_put.assert_called_once()

    def test_cli_delegates_one_pass_to_the_satellite_worker(self):
        output = io.StringIO()
        with mock.patch.object(field_sync.sys, "stdout", output), mock.patch.object(
            field_sync, "_run_sync_pass", return_value={"hub": "http://stub-hub", "dry_run": True, "passes": 1}
        ) as run_pass:
            exit_code = field_sync.main(["--hub", "http://stub-hub/", "--dry-run"])
        self.assertEqual(exit_code, 0)
        run_pass.assert_called_once_with("http://stub-hub", dry_run=True)
        self.assertEqual(json.loads(output.getvalue()), {"dry_run": True, "hub": "http://stub-hub", "passes": 1})


if __name__ == "__main__":
    unittest.main()
