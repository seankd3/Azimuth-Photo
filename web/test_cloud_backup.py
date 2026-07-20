"""Contracts for in-app Cloud Backup (rclone vault sync)."""

from __future__ import annotations

import os
import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

import settings
from features.backup import cloud, routes as cloud_routes


def _make_catalog(path: Path, *, rows: list[tuple] | None = None) -> Path:
    conn = sqlite3.connect(path)
    try:
        conn.executescript(
            """
            CREATE TABLE images (
                id INTEGER PRIMARY KEY,
                filename TEXT NOT NULL,
                filepath TEXT NOT NULL UNIQUE,
                status TEXT DEFAULT 'kept',
                flag TEXT DEFAULT NULL
            );
            """
        )
        for row in rows or []:
            conn.execute(
                "INSERT INTO images(filename, filepath, status, flag) VALUES (?, ?, ?, ?)",
                row,
            )
        conn.commit()
    finally:
        conn.close()
    return path


class ConfigValidationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.tree = Path(self.tmp.name) / "RAWS"
        self.tree.mkdir()
        cloud.reset_runner_for_tests()

    def test_validate_requires_remote_and_known_remote(self):
        with mock.patch.object(cloud, "rclone_available", return_value=True), mock.patch.object(
            cloud, "list_remotes", return_value=["gdrive", "localvault"]
        ):
            with self.assertRaises(ValueError) as missing:
                cloud.validate_config({"remote": "", "trees": [str(self.tree)]})
            self.assertIn("remote", str(missing.exception).lower())

            with self.assertRaises(ValueError) as unknown:
                cloud.validate_config({"remote": "missing", "trees": [str(self.tree)]})
            self.assertIn("not configured", str(unknown.exception))

            ok = cloud.validate_config(
                {
                    "remote": "gdrive",
                    "dest_prefix": "AzimuthVault",
                    "trees": [str(self.tree)],
                    "bwlimit": "08:00,2M",
                    "exclude_from_catalog": True,
                    "nightly_enabled": False,
                },
                remotes=["gdrive"],
            )
            self.assertEqual(ok["remote"], "gdrive")
            self.assertEqual(ok["dest_prefix"], "AzimuthVault")
            self.assertEqual(ok["trees"], [str(self.tree.resolve())])

    def test_validate_reports_unavailable_without_rclone(self):
        with mock.patch.object(cloud, "rclone_available", return_value=False):
            with self.assertRaises(ValueError) as ctx:
                cloud.validate_config({"remote": "gdrive", "trees": [str(self.tree)]})
            self.assertIn("unavailable", str(ctx.exception).lower())

    def test_require_trees_for_start(self):
        with mock.patch.object(cloud, "rclone_available", return_value=True), mock.patch.object(
            cloud, "list_remotes", return_value=["gdrive"]
        ):
            with self.assertRaises(ValueError) as ctx:
                cloud.validate_config({"remote": "gdrive", "trees": []}, require_trees=True)
            self.assertIn("tree", str(ctx.exception).lower())


class ExcludeGenerationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "Film Scans"
        self.root.mkdir()
        self.db = Path(self.tmp.name) / "catalog.db"
        _make_catalog(
            self.db,
            rows=[
                ("keep.jpg", str(self.root / "keep.jpg"), "kept", None),
                ("trash.jpg", str(self.root / "nested" / "trash.jpg"), "trashed", None),
                ("reject.jpg", str(self.root / "reject.jpg"), "kept", "rejected"),
                ("other.jpg", str(Path(self.tmp.name) / "other" / "x.jpg"), "trashed", None),
            ],
        )

    def test_relative_exclude_patterns_from_fixture_catalog(self):
        paths = cloud.catalog_excluded_filepaths(str(self.db), str(self.root))
        patterns = cloud.relative_exclude_patterns(paths, str(self.root))
        self.assertEqual(patterns, ["/nested/trash.jpg", "/reject.jpg"])

    def test_write_exclude_file(self):
        dest = Path(self.tmp.name) / "exclude.txt"
        count = cloud.write_exclude_file(str(self.db), str(self.root), dest)
        self.assertEqual(count, 2)
        self.assertEqual(dest.read_text(encoding="utf-8").splitlines(), ["/nested/trash.jpg", "/reject.jpg"])


class StatsParsingTests(unittest.TestCase):
    def test_parse_rclone_stats_line(self):
        line = "Transferred:   	  123.456 MiB / 1.234 GiB, 10%, 12.345 MiB/s, ETA 1m23s"
        parsed = cloud.parse_rclone_stats(line)
        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed["pct"], 10.0)
        self.assertEqual(parsed["speed"], "12.345 MiB/s")
        self.assertEqual(parsed["eta"], "1m23s")
        self.assertGreater(parsed["bytes_done"], 100 * 1024 * 1024)
        self.assertGreater(parsed["bytes_total"], parsed["bytes_done"])

    def test_parse_empty_transfer_line(self):
        line = "Transferred:   	          0 B / 0 B, -, 0 B/s, ETA -"
        parsed = cloud.parse_rclone_stats(line)
        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed["bytes_done"], 0)
        self.assertEqual(parsed["pct"], 0.0)
        self.assertEqual(parsed["speed"], "0 B/s")
        self.assertEqual(parsed["eta"], "")

    def test_non_stats_line_returns_none(self):
        self.assertIsNone(cloud.parse_rclone_stats("INFO  : something else"))


class RunnerStateMachineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.tree = Path(self.tmp.name) / "RAWS"
        self.tree.mkdir()
        (self.tree / "a.jpg").write_bytes(b"photo")
        self.db = Path(self.tmp.name) / "catalog.db"
        _make_catalog(self.db)
        self.status_dir = Path(self.tmp.name) / "state"
        self.status_dir.mkdir()
        self._old_path = settings.SETTINGS_PATH
        settings.SETTINGS_PATH = str(Path(self.tmp.name) / "settings.json")
        settings.reset_settings()
        self.addCleanup(self._restore_settings)
        from core import bulk_scheduler

        bulk_scheduler.reset_for_tests(
            desired_path=Path(self.tmp.name) / "bulk_desired.json"
        )
        self.addCleanup(bulk_scheduler.reset_for_tests)
        cloud.reset_runner_for_tests()
        self.addCleanup(cloud.reset_runner_for_tests)

    def _restore_settings(self):
        settings.SETTINGS_PATH = self._old_path
        settings.load_settings(force=True)

    def test_start_stop_and_already_running(self):
        config = {
            "remote": "localvault",
            "dest_prefix": "Vault",
            "trees": [str(self.tree)],
            "bwlimit": "off",
            "exclude_from_catalog": False,
            "nightly_enabled": False,
        }

        def fake_job(db_path, resolved, **_kwargs):
            del db_path, resolved
            cloud._set_runtime(state="running", message="working")
            deadline = time.time() + 2.0
            while time.time() < deadline:
                if cloud._stop_requested:
                    cloud._set_runtime(state="idle", message="paused", finished_at=time.time())
                    return
                time.sleep(0.05)
            cloud._set_runtime(state="idle", message="done", finished_at=time.time())

        with mock.patch.object(cloud, "rclone_available", return_value=True), mock.patch.object(
            cloud, "list_remotes", return_value=["localvault"]
        ), mock.patch.object(cloud, "_run_sync_job", side_effect=fake_job), mock.patch.object(
            cloud, "status_path", return_value=self.status_dir / "status.json"
        ):
            first = cloud.start_sync(str(self.db), config=config)
            self.assertEqual(first["state"], "running")

            with self.assertRaises(RuntimeError) as ctx:
                cloud.start_sync(str(self.db), config=config)
            self.assertIn("already running", str(ctx.exception).lower())

            stopped = cloud.stop_sync()
            self.assertIn(stopped["state"], {"stopping", "idle", "running"})

            deadline = time.time() + 2.0
            while time.time() < deadline:
                if cloud.status_payload()["state"] == "idle":
                    break
                time.sleep(0.05)
            self.assertEqual(cloud.status_payload()["state"], "idle")

    def test_scheduled_start_waits_while_previews_pending(self):
        from core import bulk_scheduler

        config = {
            "remote": "localvault",
            "dest_prefix": "Vault",
            "trees": [str(self.tree)],
            "bwlimit": "off",
            "exclude_from_catalog": False,
            "nightly_enabled": False,
        }
        started = threading.Event()

        def fake_job(db_path, resolved, *, wait_for_previews=False, override_warning=""):
            del db_path, resolved, override_warning
            if wait_for_previews:
                cloud._set_runtime(
                    state="waiting",
                    message=bulk_scheduler.VAULT_WAIT_MESSAGE,
                )
                deadline = time.time() + 2.0
                while time.time() < deadline and not cloud._stop_requested:
                    time.sleep(0.05)
                cloud._set_runtime(state="idle", message="paused", finished_at=time.time())
                return
            started.set()
            cloud._set_runtime(state="running", message="should-not-run")

        # Keep the temp desired path from setUp; only rewire the previews probe.
        bulk_scheduler.configure(previews_pending=lambda: True)

        with mock.patch.dict(os.environ, {"PHOTOARCHIVE_BULK_SEQUENCING": "1"}), mock.patch.object(
            cloud, "rclone_available", return_value=True
        ), mock.patch.object(cloud, "list_remotes", return_value=["localvault"]), mock.patch.object(
            cloud, "_run_sync_job", side_effect=fake_job
        ):
            payload = cloud.start_sync(str(self.db), config=config, manual_override=False)
            self.assertEqual(payload["state"], "waiting")
            self.assertIn("preview", payload["message"].lower())
            self.assertFalse(started.wait(0.3))
            cloud.stop_sync()

    def test_manual_override_runs_while_previews_pending(self):
        from core import bulk_scheduler

        config = {
            "remote": "localvault",
            "dest_prefix": "Vault",
            "trees": [str(self.tree)],
            "bwlimit": "off",
            "exclude_from_catalog": False,
            "nightly_enabled": False,
        }
        seen = {}

        def fake_job(db_path, resolved, *, wait_for_previews=False, override_warning=""):
            del db_path, resolved
            seen["wait"] = wait_for_previews
            seen["warning"] = override_warning
            cloud._set_runtime(state="running", message=override_warning or "running")
            time.sleep(0.1)
            cloud._set_runtime(state="idle", message="done", finished_at=time.time())

        # Keep the temp desired path; only rewire the previews probe.
        bulk_scheduler.configure(previews_pending=lambda: True)

        with mock.patch.dict(os.environ, {"PHOTOARCHIVE_BULK_SEQUENCING": "1"}), mock.patch.object(
            cloud, "rclone_available", return_value=True
        ), mock.patch.object(cloud, "list_remotes", return_value=["localvault"]), mock.patch.object(
            cloud, "_run_sync_job", side_effect=fake_job
        ):
            payload = cloud.start_sync(str(self.db), config=config, manual_override=True)
            self.assertEqual(payload["state"], "running")
            self.assertIn("share the disk", payload["message"])
            deadline = time.time() + 2.0
            while time.time() < deadline and "wait" not in seen:
                time.sleep(0.02)
            self.assertFalse(seen.get("wait"))
            self.assertIn("share the disk", seen.get("warning", ""))
            cloud.stop_sync()


class CloudBackupRouteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.tree = Path(self.tmp.name) / "Film"
        self.tree.mkdir()
        self.db = Path(self.tmp.name) / "catalog.db"
        _make_catalog(self.db)
        self.settings_path = Path(self.tmp.name) / "settings.json"
        cloud.reset_runner_for_tests()
        self.addCleanup(cloud.reset_runner_for_tests)

        self._old_path = settings.SETTINGS_PATH
        settings.SETTINGS_PATH = str(self.settings_path)
        settings.reset_settings()
        self.addCleanup(self._restore_settings)

        cloud_routes.configure(db_path=lambda: str(self.db))
        from core import bulk_scheduler

        bulk_scheduler.reset_for_tests(
            desired_path=Path(self.tmp.name) / "bulk_desired.json"
        )
        self.addCleanup(bulk_scheduler.reset_for_tests)
        app = FastAPI()
        app.include_router(cloud_routes.router)
        self.client = TestClient(app)

    def _restore_settings(self):
        settings.SETTINGS_PATH = self._old_path
        settings.load_settings(force=True)

    def test_config_save_and_status(self):
        with mock.patch.object(cloud, "rclone_available", return_value=True), mock.patch.object(
            cloud, "list_remotes", return_value=["gdrive"]
        ):
            saved = self.client.put(
                "/api/backup/cloud/config",
                json={
                    "remote": "gdrive",
                    "dest_prefix": "AzimuthVault",
                    "trees": [str(self.tree)],
                    "bwlimit": "07:00,3M 23:00,off",
                    "exclude_from_catalog": True,
                    "nightly_enabled": True,
                },
            )
            self.assertEqual(saved.status_code, 200, saved.text)
            body = saved.json()
            self.assertTrue(body["ok"])
            self.assertEqual(body["config"]["remote"], "gdrive")
            self.assertTrue(body["config"]["nightly_enabled"])

            status = self.client.get("/api/backup/cloud/status")
            self.assertEqual(status.status_code, 200)
            payload = status.json()
            self.assertTrue(payload["available"])
            self.assertEqual(payload["config"]["dest_prefix"], "AzimuthVault")
            self.assertEqual(payload["config"]["trees"], [str(self.tree.resolve())])

    def test_unavailable_without_rclone(self):
        with mock.patch.object(cloud, "rclone_available", return_value=False):
            status = self.client.get("/api/backup/cloud/status")
            self.assertEqual(status.status_code, 200)
            payload = status.json()
            self.assertFalse(payload["available"])
            self.assertEqual(payload["state"], "unavailable")

            start = self.client.post("/api/backup/cloud/start")
            self.assertEqual(start.status_code, 503)


class SettingsDefaultsTests(unittest.TestCase):
    def test_defaults_include_cloud_backup_keys(self):
        for key in (
            "cloud_backup_remote",
            "cloud_backup_dest_prefix",
            "cloud_backup_trees",
            "cloud_backup_bwlimit",
            "cloud_backup_exclude_from_catalog",
            "cloud_backup_nightly_enabled",
        ):
            self.assertIn(key, settings.DEFAULT_SETTINGS)


@unittest.skipUnless(cloud.rclone_available(), "rclone not installed")
class RealLocalRcloneSyncTests(unittest.TestCase):
    """Acceptance: one real sync against a local-type rclone remote."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.source = root / "RAWS"
        self.source.mkdir()
        (self.source / "keep.jpg").write_bytes(b"keep-bytes")
        (self.source / "trash.jpg").write_bytes(b"trash-bytes")
        self.dest_root = root / "cloud-dest"
        self.dest_root.mkdir()
        self.db = root / "catalog.db"
        _make_catalog(
            self.db,
            rows=[
                ("keep.jpg", str(self.source / "keep.jpg"), "kept", None),
                ("trash.jpg", str(self.source / "trash.jpg"), "trashed", None),
            ],
        )
        self.conf = root / "rclone.conf"
        self.conf.write_text("[cloudproof]\ntype = local\n", encoding="utf-8")
        self.status_dir = root / "state"
        self.status_dir.mkdir()
        self._old_rclone_config = os.environ.get("RCLONE_CONFIG")
        os.environ["RCLONE_CONFIG"] = str(self.conf)
        from core import bulk_scheduler

        bulk_scheduler.reset_for_tests(desired_path=root / "bulk_desired.json")
        self.addCleanup(bulk_scheduler.reset_for_tests)
        cloud.reset_runner_for_tests()
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        cloud.reset_runner_for_tests()
        if self._old_rclone_config is None:
            os.environ.pop("RCLONE_CONFIG", None)
        else:
            os.environ["RCLONE_CONFIG"] = self._old_rclone_config

    def test_real_small_sync_excludes_trashed(self):
        with mock.patch.object(cloud, "status_path", return_value=self.status_dir / "status.json"):
            remotes = cloud.list_remotes()
            self.assertIn("cloudproof", remotes)
            config = {
                "remote": "cloudproof",
                "dest_prefix": str(self.dest_root / "Vault"),
                "trees": [str(self.source)],
                "bwlimit": "off",
                "exclude_from_catalog": True,
                "nightly_enabled": False,
            }
            cloud.start_sync(str(self.db), config=config)
            deadline = time.time() + 30.0
            while time.time() < deadline:
                payload = cloud.status_payload()
                if payload["state"] in {"idle", "error"} and payload.get("finished_at"):
                    break
                time.sleep(0.1)
            payload = cloud.status_payload()
            self.assertEqual(payload["state"], "idle", payload)
            self.assertIsNone(payload.get("last_error"))
            synced = self.dest_root / "Vault" / "RAWS" / "keep.jpg"
            trashed = self.dest_root / "Vault" / "RAWS" / "trash.jpg"
            self.assertTrue(synced.is_file(), f"missing synced file under {self.dest_root}")
            self.assertEqual(synced.read_bytes(), b"keep-bytes")
            self.assertFalse(trashed.exists(), "trashed file should be excluded from vault")
            self.assertIsNotNone(payload.get("last_ok_at"))


if __name__ == "__main__":
    unittest.main()
