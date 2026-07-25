"""LR bridge UX layer — connect/disconnect, new-exports signal, health >24h."""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from features.sync import elo_stars, export_relation, lr_connect, lr_routes, lr_status


HASH_RAW = "r" * 32
HASH_EDIT = "e" * 32

CATALOG_DDL = """
PRAGMA foreign_keys=ON;
CREATE TABLE catalog_sources (
    id INTEGER PRIMARY KEY,
    path TEXT,
    display_name TEXT,
    online INTEGER DEFAULT 1
);
CREATE TABLE images (
    id INTEGER PRIMARY KEY,
    filename TEXT,
    filepath TEXT,
    content_hash TEXT UNIQUE,
    flag TEXT NOT NULL DEFAULT 'unflagged',
    elo REAL DEFAULT 1200,
    comparisons INTEGER DEFAULT 0,
    status TEXT DEFAULT 'kept',
    missing_at REAL,
    file_ext TEXT,
    date_taken TEXT,
    camera_model TEXT,
    source_id INTEGER,
    vc_of INTEGER
);
CREATE TABLE stacks (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,
    representative_image_id INTEGER,
    auto INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL DEFAULT 0
);
CREATE TABLE stack_members (
    stack_id INTEGER NOT NULL REFERENCES stacks(id) ON DELETE CASCADE,
    image_id INTEGER NOT NULL REFERENCES images(id) ON DELETE CASCADE,
    score REAL NOT NULL DEFAULT 0,
    added_at REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (stack_id, image_id)
);
"""


class LrConnectTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.modules = self.root / "Modules"
        self.repo = self.root / "repo"
        plugin_src = self.repo / "clients" / "lightroom" / "azimuth-sync.lrplugin"
        plugin_src.mkdir(parents=True)
        (plugin_src / "Info.lua").write_text("return {}\n", encoding="utf-8")
        (plugin_src / "AzimuthSyncCore.lua").write_text("-- core\n", encoding="utf-8")
        self.env = {
            "PHOTOARCHIVE_LR_MODULES_DIR": str(self.modules),
            "PHOTOARCHIVE_LR_FORCE_DETECT": "1",
            "APPDATA": str(self.root / "AppData"),
        }

    def tearDown(self):
        self.tempdir.cleanup()

    def test_connect_writes_plugin_and_config_idempotent(self):
        first = lr_connect.connect_plugin(
            satellite_url="http://127.0.0.1:8123",
            environ=self.env,
            platform_name="win32",
            repo_root=self.repo,
        )
        self.assertTrue(first["ok"])
        self.assertTrue(first["connected"])
        dest = Path(first["plugin_dir"])
        self.assertTrue(dest.is_dir())
        self.assertTrue((dest / "Info.lua").is_file())
        config = json.loads((dest / "satellite_url.json").read_text(encoding="utf-8"))
        self.assertEqual(config["satelliteUrl"], "http://127.0.0.1:8123")

        second = lr_connect.connect_plugin(
            satellite_url="http://127.0.0.1:8123",
            environ=self.env,
            platform_name="win32",
            repo_root=self.repo,
        )
        self.assertTrue(second["ok"])
        self.assertTrue((dest / "Info.lua").is_file())

    def test_disconnect_removes_plugin_idempotent(self):
        lr_connect.connect_plugin(
            satellite_url="http://127.0.0.1:8000",
            environ=self.env,
            platform_name="win32",
            repo_root=self.repo,
        )
        removed = lr_connect.disconnect_plugin(environ=self.env, platform_name="win32")
        self.assertTrue(removed["ok"])
        self.assertTrue(removed["removed"])
        self.assertFalse(Path(removed["plugin_dir"]).exists())
        again = lr_connect.disconnect_plugin(environ=self.env, platform_name="win32")
        self.assertTrue(again["ok"])
        self.assertFalse(again["removed"])

    def test_button_hidden_when_lr_not_detected(self):
        env = {**self.env, "PHOTOARCHIVE_LR_FORCE_DETECT": "0"}
        status = lr_connect.connect_status(environ=env, platform_name="win32", path_exists=lambda _p: False)
        self.assertFalse(status["show_button"])
        self.assertFalse(status["detected"])


class LrConnectRouteTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.modules = self.root / "Modules"
        self.repo = Path(__file__).resolve().parents[1]
        self.env_patch = {
            "PHOTOARCHIVE_LR_MODULES_DIR": str(self.modules),
            "PHOTOARCHIVE_LR_FORCE_DETECT": "1",
        }
        self._old_env = {key: os.environ.get(key) for key in self.env_patch}
        os.environ.update(self.env_patch)
        app = FastAPI()
        app.include_router(lr_routes.router)
        self.client = TestClient(app)

    async def asyncTearDown(self):
        for key, value in self._old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.tempdir.cleanup()

    async def test_connect_disconnect_endpoints(self):
        status = self.client.get("/api/lr/connect")
        self.assertEqual(status.status_code, 200)
        self.assertTrue(status.json()["show_button"])

        connected = self.client.post("/api/lr/connect", json={"satellite_url": "http://127.0.0.1:9001"})
        self.assertEqual(connected.status_code, 200)
        body = connected.json()
        self.assertTrue(body["ok"])
        self.assertTrue(Path(body["plugin_dir"]).is_dir())
        config = json.loads((Path(body["plugin_dir"]) / "satellite_url.json").read_text(encoding="utf-8"))
        self.assertEqual(config["satelliteUrl"], "http://127.0.0.1:9001")

        # Idempotent reconnect
        again = self.client.post("/api/lr/connect", json={})
        self.assertEqual(again.status_code, 200)

        removed = self.client.delete("/api/lr/connect")
        self.assertEqual(removed.status_code, 200)
        self.assertFalse(Path(body["plugin_dir"]).exists())
        removed_again = self.client.delete("/api/lr/connect")
        self.assertEqual(removed_again.status_code, 200)
        self.assertFalse(removed_again.json()["removed"])


class LrStatusUxTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tempdir.name, "ux.db")
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.executescript(CATALOG_DDL)
            conn.execute(
                "INSERT INTO images(id, filename, filepath, content_hash, file_ext, comparisons, elo) "
                "VALUES (1, 'raw.dng', '/raw.dng', ?, 'dng', 10, 1600)",
                (HASH_RAW,),
            )
            conn.execute(
                "INSERT INTO images(id, filename, filepath, content_hash, file_ext, comparisons, elo) "
                "VALUES (2, 'edit.jpg', '/edit.jpg', ?, 'jpg', 4, 1400)",
                (HASH_EDIT,),
            )
            conn.commit()
        # Reset exchange state between tests.
        with lr_status._lock:
            lr_status._state["last_delta_at"] = None
            lr_status._state["last_delta_direction"] = None

    async def asyncTearDown(self):
        self.tempdir.cleanup()

    async def test_new_exports_signal(self):
        linked = await export_relation.link_export(
            self.db_path,
            source_image_id=1,
            export_image_id=2,
        )
        self.assertTrue(linked["linked"])
        batch = await lr_status.new_export_batch(self.db_path, since=0)
        self.assertIsNotNone(batch)
        self.assertEqual(batch["count"], 1)
        self.assertEqual(batch["image_ids"], [2])
        self.assertTrue(batch["batch_id"])

        later = await lr_status.new_export_batch(self.db_path, since=time.time() + 10)
        self.assertIsNone(later)

    async def test_bridge_status_stays_lightweight(self):
        """Frequent sync heartbeats must not carry the ranked-photo snapshot."""

        status = await lr_status.bridge_status_payload(self.db_path)

        self.assertNotIn("shoot_context", status)
        self.assertEqual(
            set(status),
            {"last_delta_at", "age_hours", "stale", "health_line", "new_exports"},
        )

    async def test_health_line_after_24h(self):
        with lr_status._lock:
            lr_status._state["last_delta_at"] = time.time() - (25 * 3600)
            lr_status._state["last_delta_direction"] = "out"
        status = lr_status.delta_exchange_status()
        self.assertTrue(status["stale"])
        self.assertIsNotNone(status["health_line"])
        self.assertIn("Lightroom bridge hasn't synced since", status["health_line"])

        with lr_status._lock:
            lr_status._state["last_delta_at"] = time.time() - 60
        fresh = lr_status.delta_exchange_status()
        self.assertFalse(fresh["stale"])
        self.assertIsNone(fresh["health_line"])

    def test_whisper_bands(self):
        self.assertEqual(elo_stars.whisper_for_stars(5), "Top 2% of your ranked photos")
        self.assertEqual(elo_stars.whisper_for_stars(4), "Top 10% of your ranked photos")
        self.assertEqual(elo_stars.whisper_for_stars(3), "Top 30% of your ranked photos")
        self.assertIsNone(elo_stars.whisper_for_stars(0))


if __name__ == "__main__":
    unittest.main()
