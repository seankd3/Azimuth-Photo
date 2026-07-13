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

import db
from core import background
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
            CREATE TABLE develop_settings (
                image_id INTEGER PRIMARY KEY,
                settings_json TEXT NOT NULL
            );
            CREATE TABLE cache_entries (
                image_id INTEGER PRIMARY KEY,
                path TEXT NOT NULL
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


def _catalog_dump(path: Path) -> str:
    conn = sqlite3.connect(path)
    try:
        return "\n".join(conn.iterdump())
    finally:
        conn.close()


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
        live_before = self.db_path.read_bytes()
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

        # Live db remains byte-for-byte untouched.
        self.assertEqual(self.db_path.read_bytes(), live_before)
        status = backups.restore_status(str(self.db_path))
        self.assertTrue(status["prepared"])
        self.assertEqual(status["name"], created["name"])
        with self.assertRaises(backups.RestoreStageExistsError):
            backups.restore_backup(str(self.db_path), created["name"])
        discarded = backups.discard_staged_restore(str(self.db_path))
        self.assertTrue(discarded["discarded"])
        self.assertFalse(discarded["prepared"])
        self.assertEqual(self.db_path.read_bytes(), live_before)

    def test_restore_rejects_invalid_catalog_and_cleans_temps(self):
        name = "photoarchive-20260710-120000.db.gz"
        with gzip.open(self.root / name, "wb") as gz:
            gz.write(b"not a sqlite database")

        with self.assertRaises(backups.RestoreValidationError):
            backups.restore_backup(str(self.db_path), name)

        self.assertFalse((self.db_path.parent / "photoarchive.restored.db").exists())
        self.assertFalse((self.db_path.parent / "photoarchive.restored.json").exists())
        self.assertFalse((self.db_path.parent / ".photoarchive.restored.db.tmp").exists())

    def test_restore_requires_photoarchive_tables(self):
        empty_db = Path(self.tempdir.name) / "empty.db"
        sqlite3.connect(empty_db).close()
        name = "photoarchive-20260710-121500.db.gz"
        with open(empty_db, "rb") as raw, gzip.open(self.root / name, "wb") as gz:
            gz.write(raw.read())

        with self.assertRaisesRegex(backups.RestoreValidationError, "required catalog tables"):
            backups.restore_backup(str(self.db_path), name)

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
        live_before = self.db_path.read_bytes()
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
        self.assertEqual(self.db_path.read_bytes(), live_before)

        status = self.client.get("/api/system/backup/restore-status")
        self.assertEqual(status.status_code, 200)
        self.assertTrue(status.json()["prepared"])
        self.assertEqual(status.json()["name"], name)

        conflict = self.client.post("/api/system/backup/restore", json={"name": name})
        self.assertEqual(conflict.status_code, 409)
        self.assertTrue(conflict.json()["restore"]["prepared"])
        self.assertNotIn("staging_path", conflict.json()["restore"])
        self.assertNotIn(str(self.root), conflict.text)

        discarded = self.client.delete("/api/system/backup/restore-staged")
        self.assertEqual(discarded.status_code, 200)
        self.assertTrue(discarded.json()["discarded"])
        self.assertFalse(discarded.json()["prepared"])
        self.assertEqual(self.db_path.read_bytes(), live_before)

    def test_restore_drill_recovers_catalog_content_after_live_db_corruption(self):
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("INSERT INTO develop_settings (image_id, settings_json) VALUES (1, '{\"crop\": 0.4}')")
            conn.execute("INSERT INTO cache_entries (image_id, path) VALUES (1, '/previews/1.jpg')")
            conn.commit()
        finally:
            conn.close()
        expected = _catalog_dump(self.db_path)

        snapshot = self.client.post("/api/system/backup/now")
        self.assertEqual(snapshot.status_code, 200, snapshot.text)
        self.db_path.write_bytes(b"not a sqlite catalog")

        restored = self.client.post("/api/system/backup/restore", json={"name": snapshot.json()["name"]})
        self.assertEqual(restored.status_code, 200, restored.text)
        staged = Path(restored.json()["staging_path"])

        self.assertEqual(_catalog_dump(staged), expected)
        self.assertEqual(self.db_path.read_bytes(), b"not a sqlite catalog")

    def test_restore_endpoint_reports_validation_and_storage_failures(self):
        missing = self.client.post(
            "/api/system/backup/restore",
            json={"name": "photoarchive-20260710-130000.db.gz"},
        )
        self.assertEqual(missing.status_code, 404)

        invalid = self.client.post("/api/system/backup/restore", json={"name": "../catalog.db"})
        self.assertEqual(invalid.status_code, 400)

        corrupt_name = "photoarchive-20260710-131500.db.gz"
        with gzip.open(self.root / corrupt_name, "wb") as gz:
            gz.write(b"not sqlite")
        corrupt = self.client.post("/api/system/backup/restore", json={"name": corrupt_name})
        self.assertEqual(corrupt.status_code, 422, corrupt.text)

        with mock.patch.object(
            backups,
            "restore_backup",
            side_effect=backups.RestoreStorageError("disk full"),
        ):
            storage = self.client.post("/api/system/backup/restore", json={"name": corrupt_name})
        self.assertEqual(storage.status_code, 507)

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


class CatalogRecoveryTests(unittest.TestCase):
    def test_corrupt_catalog_at_boot_is_reported_without_reinitializing_it(self):
        with tempfile.TemporaryDirectory() as tempdir:
            corrupt = Path(tempdir) / "photoarchive.db"
            corrupt.write_bytes(b"not a sqlite catalog")
            original_path = db.DB_PATH
            db.DB_PATH = str(corrupt)
            init_called = False

            async def init_db():
                nonlocal init_called
                init_called = True

            unused = lambda *_args, **_kwargs: None
            server = FastAPI()
            backup_routes.configure(db_path=lambda: db.DB_PATH)
            server.include_router(backup_routes.router)

            @server.on_event("startup")
            async def startup():
                await background.run_startup(
                    smoke_mode_enabled=lambda: False,
                    warm_templates=lambda: None,
                    thumbnails=mock.Mock(), settings=mock.Mock(), face_worker=mock.Mock(), caption_worker=mock.Mock(),
                    track_background_task=unused, init_db=init_db,
                    get_filter_options=unused, get_date_groups=unused, get_catalog_image_counts=unused,
                    get_stats=unused, get_ai_status_counts=unused, get_visible_orientation_pairing_pool_counts=unused,
                    get_catalog_summary=unused, cache_root=unused, build_ai_status=unused, build_cache_status=unused,
                    api_rankings=unused, api_folders=unused, api_map_markers=unused, api_date_groups=unused,
                    api_settings=unused, mosaic_next=unused, compare_next=unused,
                    default_visible_pairing_candidates=unused, warm_filtered_visible_ranked_candidates=unused,
                    get_visible_past_matchups=unused, classify_orientations_background=unused,
                    scan_metadata_background=unused, swiss_pair_window=1, filtered_swiss_pair_window=1,
                    filtered_mosaic_window=1, mosaic_explore_window=1, mosaic_diverse_window=1,
                    interaction_cache_warmup_delay_seconds=0,
                )

            try:
                with TestClient(server) as client:
                    status = client.get("/api/system/integrity/status")
                    self.assertEqual(status.status_code, 200, status.text)
                    catalog = status.json()["catalog"]
                    self.assertFalse(catalog["ok"])
                    self.assertEqual(catalog["state"], "corrupt")
                self.assertFalse(init_called)
                self.assertEqual(corrupt.read_bytes(), b"not a sqlite catalog")
            finally:
                db.DB_PATH = original_path


if __name__ == "__main__":
    unittest.main()
