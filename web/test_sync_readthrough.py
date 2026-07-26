"""Read-through and scripted one-pass satellite sync tests."""

from __future__ import annotations

import gzip
import asyncio
import io
import json
import os
import sqlite3
from contextlib import closing
import struct
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

import numpy as np

from data import connection as data_connection
from features.sync import readthrough
from features.media import routes as media_routes
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
        boundary = b"azimuth-pabase1"
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
        self.old_mode = os.environ.get("AZIMUTH_MODE")
        self.old_hub = os.environ.get("AZIMUTH_HUB_URL")
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _HubHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        os.environ["AZIMUTH_MODE"] = "satellite"
        os.environ["AZIMUTH_HUB_URL"] = f"http://127.0.0.1:{self.server.server_port}"
        self.db_path = str(Path(self.tempdir.name) / "catalog.db")
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            conn.execute("CREATE TABLE images (id INTEGER PRIMARY KEY, content_hash TEXT)")
            conn.execute("INSERT INTO images VALUES (1, ?)", ("a" * 32,))

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=1)
        if self.old_mode is None:
            os.environ.pop("AZIMUTH_MODE", None)
        else:
            os.environ["AZIMUTH_MODE"] = self.old_mode
        if self.old_hub is None:
            os.environ.pop("AZIMUTH_HUB_URL", None)
        else:
            os.environ["AZIMUTH_HUB_URL"] = self.old_hub
        self.tempdir.cleanup()

    def test_fetches_base_and_companion_metadata_from_stub_hub(self):
        paths = _Paths(Path(self.tempdir.name) / "cache")
        metadata = readthrough.fetch_base_cache_for_image(
            1, paths, db_path=self.db_path, source_path="/offline/raw.dng", blocking=True
        )
        self.assertEqual(metadata["camera"], "stub")
        self.assertEqual(metadata["source_path"], "/offline/raw.dng")
        self.assertTrue(paths.binary.exists())
        self.assertTrue(paths.metadata.exists())
        self.assertTrue(paths.preview.exists())

    def test_content_hash_lookup_uses_fk_enabled_connection_helper(self):
        with mock.patch.object(
            data_connection,
            "open_sync",
            wraps=data_connection.open_sync,
        ) as open_sync:
            self.assertEqual(readthrough._content_hash_for_image(1, self.db_path), "a" * 32)
        open_sync.assert_called_once_with(self.db_path)

    def test_missing_hub_base_is_an_honest_error(self):
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            conn.execute("UPDATE images SET content_hash = ? WHERE id = 1", ("b" * 32,))
        original = readthrough._request

        def missing(url, *, timeout):
            if url.endswith("/meta"):
                return original(url, timeout=timeout)
            raise readthrough.BaseReadthroughError("Hub has no cached Develop base for this photo")

        readthrough._request = missing
        try:
            with self.assertRaisesRegex(readthrough.BaseReadthroughError, "no cached"):
                readthrough.fetch_base_cache_for_image(
                    1,
                    _Paths(Path(self.tempdir.name) / "cache"),
                    db_path=self.db_path,
                    source_path="/offline/raw.dng",
                    blocking=True,
                )
        finally:
            readthrough._request = original

    def test_nonblocking_miss_returns_immediately_and_warms_in_background(self):
        paths = _Paths(Path(self.tempdir.name) / "pending")
        started = threading.Event()
        release = threading.Event()
        original = readthrough._materialize_from_hub

        def slow_materialize(*args, **kwargs):
            started.set()
            release.wait(timeout=2)
            return original(*args, **kwargs)

        with mock.patch.object(readthrough, "_materialize_from_hub", side_effect=slow_materialize):
            t0 = __import__("time").perf_counter()
            result = readthrough.fetch_base_cache_for_image(
                1, paths, db_path=self.db_path, source_path="/offline/raw.dng"
            )
            elapsed = __import__("time").perf_counter() - t0
        self.assertIsNone(result)
        self.assertLessEqual(elapsed, 0.25)
        self.assertTrue(started.wait(timeout=1))
        release.set()

    def test_nonblocking_base_warm_queue_is_bounded(self):
        paths = _Paths(Path(self.tempdir.name) / "bounded")
        readthrough._warm_inflight.add(99)
        try:
            with mock.patch.object(readthrough, "_BASE_WARM_MAX_INFLIGHT", 1), mock.patch.object(
                readthrough, "_materialize_from_hub"
            ) as materialize:
                readthrough._schedule_base_warm(
                    2,
                    paths,
                    db_path=self.db_path,
                    source_path="/offline/raw.dng",
                )
            materialize.assert_not_called()
            self.assertNotIn(2, readthrough._warm_inflight)
        finally:
            readthrough._warm_inflight.discard(99)

    def test_sm_thumbnail_miss_returns_pending_without_foreground_hub_await(self):
        with mock.patch.object(media_routes, "_schedule_remote_media_prefetch") as enqueue:
            response = asyncio.run(
                media_routes._remote_media_response(
                    {"id": 41, "hub_image_id": 9},
                    "sm",
                )
            )

        self.assertEqual(response.status_code, 204)
        self.assertEqual(media_routes._REMOTE_MEDIA_FOREGROUND_TIMEOUT_SECONDS, 0.0)
        enqueue.assert_called_once()
        self.assertEqual(enqueue.call_args.args[1], "sm")

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
