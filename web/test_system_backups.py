"""Contracts for catalog backups and integrity audits (TIMEMACHINE lane)."""

from __future__ import annotations

import gzip
import os
import sqlite3
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from features.system import backup_routes, backups


def _make_catalog(path: Path, *, files: list[tuple[str, bytes]] | None = None) -> Path:
    conn = sqlite3.connect(path)
    try:
        conn.executescript(
            """
            CREATE TABLE catalog_sources (
                id INTEGER PRIMARY KEY,
                path TEXT NOT NULL UNIQUE,
                display_name TEXT NOT NULL,
                included INTEGER NOT NULL DEFAULT 1,
                online INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE images (
                id INTEGER PRIMARY KEY,
                source_id INTEGER REFERENCES catalog_sources(id),
                filename TEXT NOT NULL,
                filepath TEXT NOT NULL UNIQUE,
                status TEXT DEFAULT 'kept',
                missing_at REAL DEFAULT NULL
            );
            """
        )
        conn.execute(
            "INSERT INTO catalog_sources (id, path, display_name, online) VALUES (1, '/tmp', 'tmp', 1)"
        )
        conn.execute(
            "INSERT INTO catalog_sources (id, path, display_name, online) VALUES (2, '/offline', 'off', 0)"
        )
        files = files or []
        for index, (filepath, payload) in enumerate(files, start=1):
            Path(filepath).parent.mkdir(parents=True, exist_ok=True)
            Path(filepath).write_bytes(payload)
            source_id = 1 if index % 2 else 2
            # First half online, but we assign explicitly below for clarity.
            conn.execute(
                "INSERT INTO images (id, source_id, filename, filepath, status) VALUES (?, ?, ?, ?, 'kept')",
                (index, 1, Path(filepath).name, filepath),
            )
        conn.commit()
    finally:
        conn.close()
    return path


class BackupUnitTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name) / "backups"
        self.root.mkdir()
        self.db_path = Path(self.tempdir.name) / "photoarchive.db"
        _make_catalog(self.db_path)
        self._root_patch = mock.patch.object(backups, "backup_root", return_value=self.root)
        self._root_patch.start()

    def tearDown(self):
        self._root_patch.stop()
        self.tempdir.cleanup()

    def test_snapshot_list_restore_roundtrip(self):
        created = backups.create_snapshot(str(self.db_path))
        self.assertTrue(created["ok"])
        self.assertTrue(created["name"].endswith(".db.gz"))
        self.assertGreater(created["bytes"], 0)
        self.assertTrue((self.root / created["name"]).is_file())

        listed = backups.list_backups()
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["name"], created["name"])

        restored = backups.restore_backup(str(self.db_path), created["name"])
        self.assertTrue(restored["ok"])
        self.assertFalse(restored["hot_swapped"])
        staging = Path(restored["staging_path"])
        self.assertTrue(staging.is_file())
        self.assertIn("was NOT replaced", restored["instructions"])

        # Staging is a valid sqlite db with our table.
        conn = sqlite3.connect(staging)
        try:
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        finally:
            conn.close()
        self.assertIn("images", tables)

        # Live db untouched (same inode content path still exists).
        self.assertTrue(self.db_path.is_file())

    def test_retention_keeps_daily_and_weekly(self):
        now = datetime(2026, 7, 10, 4, 0, 0)
        # 20 daily-ish backups spanning > 4 weeks.
        for days_ago in range(0, 40):
            when = now - timedelta(days=days_ago)
            name = f"photoarchive-{when.strftime('%Y%m%d-%H%M%S')}.db.gz"
            path = self.root / name
            with gzip.open(path, "wb") as gz:
                gz.write(b"sqlite-fake")
        pruned = backups.apply_retention(self.root)
        remaining = backups.list_backups()
        self.assertGreaterEqual(len(remaining), 7)
        self.assertLessEqual(len(remaining), 7 + 4)
        self.assertGreater(len(pruned), 0)

    def test_seconds_until_local_hour(self):
        now = datetime(2026, 7, 10, 3, 0, 0).astimezone()
        delay = backups.seconds_until_local_hour(4, now=now)
        self.assertAlmostEqual(delay, 3600.0, delta=2.0)
        later = datetime(2026, 7, 10, 5, 0, 0).astimezone()
        delay2 = backups.seconds_until_local_hour(4, now=later)
        self.assertGreater(delay2, 20 * 3600)


class IntegrityUnitTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.files_dir = Path(self.tempdir.name) / "files"
        self.files_dir.mkdir()
        payloads = []
        for i in range(5):
            path = self.files_dir / f"img-{i}.bin"
            payloads.append((str(path), f"payload-{i}".encode() * 64))
        self.db_path = Path(self.tempdir.name) / "photoarchive.db"
        _make_catalog(self.db_path, files=payloads)
        # Force image 5 onto offline source.
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("UPDATE images SET source_id = 2 WHERE id = 5")
            conn.commit()
        finally:
            conn.close()
        # Reset integrity state between tests.
        with backups._integrity_lock:
            backups._integrity_state.update(
                {
                    "state": "idle",
                    "started_at": None,
                    "finished_at": None,
                    "limit": None,
                    "checked": 0,
                    "recorded": 0,
                    "mismatches": 0,
                    "skipped_offline": 0,
                    "skipped_missing": 0,
                    "errors": 0,
                    "current_image_id": None,
                    "mismatch_ids": [],
                    "last_error": None,
                }
            )

    def tearDown(self):
        self.tempdir.cleanup()

    def test_integrity_scan_records_and_detects_bit_rot(self):
        status = backups.run_integrity_scan(str(self.db_path), limit=10, sleep_seconds=0)
        self.assertEqual(status["state"], "idle")
        self.assertEqual(status["checked"], 4)  # offline skipped
        self.assertEqual(status["skipped_offline"], 1)
        self.assertEqual(status["mismatches"], 0)

        # Corrupt an online original and rescan.
        Path(self.files_dir / "img-0.bin").write_bytes(b"corrupted-bytes")
        status2 = backups.run_integrity_scan(str(self.db_path), limit=10, sleep_seconds=0)
        self.assertGreaterEqual(status2["mismatches"], 1)
        self.assertIn(1, status2["mismatch_ids"])

        summary = backups.integrity_summary(str(self.db_path))
        self.assertTrue(summary["bit_rot"])
        self.assertIn("BIT ROT", summary["alert"])


class BackupRouteTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name) / "backups"
        self.root.mkdir()
        self.db_path = Path(self.tempdir.name) / "photoarchive.db"
        files_dir = Path(self.tempdir.name) / "files"
        files_dir.mkdir()
        payloads = [(str(files_dir / f"r{i}.bin"), f"route-{i}".encode() * 32) for i in range(3)]
        _make_catalog(self.db_path, files=payloads)

        self._root_patch = mock.patch.object(backups, "backup_root", return_value=self.root)
        self._root_patch.start()

        with backups._integrity_lock:
            backups._integrity_state.update(
                {
                    "state": "idle",
                    "started_at": None,
                    "finished_at": None,
                    "limit": None,
                    "checked": 0,
                    "recorded": 0,
                    "mismatches": 0,
                    "skipped_offline": 0,
                    "skipped_missing": 0,
                    "errors": 0,
                    "current_image_id": None,
                    "mismatch_ids": [],
                    "last_error": None,
                }
            )

        app = FastAPI()
        backup_routes.configure(db_path=lambda: str(self.db_path))
        app.include_router(backup_routes.router)
        self.client = TestClient(app)

    def tearDown(self):
        self._root_patch.stop()
        self.tempdir.cleanup()

    def test_backup_endpoints_roundtrip(self):
        now = self.client.post("/api/system/backup/now")
        self.assertEqual(now.status_code, 200, now.text)
        body = now.json()
        self.assertTrue(body["ok"])
        name = body["name"]

        listed = self.client.get("/api/system/backup/list")
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.json()["count"], 1)
        self.assertEqual(listed.json()["backups"][0]["name"], name)

        restored = self.client.post("/api/system/backup/restore", json={"name": name})
        self.assertEqual(restored.status_code, 200, restored.text)
        payload = restored.json()
        self.assertFalse(payload["hot_swapped"])
        self.assertTrue(Path(payload["staging_path"]).is_file())

    def test_integrity_endpoints(self):
        started = self.client.post("/api/system/integrity/scan", json={"limit": 3})
        self.assertEqual(started.status_code, 200, started.text)
        self.assertTrue(started.json()["ok"])

        # Wait for the background thread.
        deadline = time.time() + 5
        status = None
        while time.time() < deadline:
            status = self.client.get("/api/system/integrity/status")
            self.assertEqual(status.status_code, 200)
            scan = status.json()["scan"]
            if scan["state"] == "idle" and scan.get("finished_at"):
                break
            time.sleep(0.05)
        self.assertIsNotNone(status)
        self.assertEqual(status.json()["scan"]["state"], "idle")
        self.assertGreaterEqual(status.json()["checksummed"], 1)


if __name__ == "__main__":
    unittest.main()
