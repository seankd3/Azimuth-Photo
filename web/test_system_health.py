"""Contracts for /api/health/details System Health aggregation."""

from __future__ import annotations

import asyncio
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from features.system import health, health_routes


def _make_catalog(path: Path) -> Path:
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
            CREATE TABLE import_batches (
                id INTEGER PRIMARY KEY,
                name TEXT,
                status TEXT,
                destination_path TEXT,
                total_files INTEGER,
                imported_files INTEGER,
                skipped_files INTEGER,
                collision_count INTEGER,
                created_at REAL,
                completed_at REAL
            );
            """
        )
        conn.execute(
            "INSERT INTO catalog_sources (id, path, display_name, online) VALUES (1, '/tmp', 'tmp', 1)"
        )
        conn.commit()
    finally:
        conn.close()
    return path


class HealthAggregationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_path = _make_catalog(Path(self.tmp.name) / "catalog.db")
        health.configure(db_path=lambda: str(self.db_path))
        # Seed a cached quick_check result so collect_health does not re-probe.
        with mock.patch.object(
            health.backups,
            "catalog_health",
            return_value={"ok": True, "state": "ok", "checked_at": time.time()},
        ):
            pass

    def _payload(self, **patches):
        defaults = {
            "catalog_db": health._check(
                id="catalog_db", label="Catalog database", status="ok", detail="ok"
            ),
            "catalog_backup": health._check(
                id="catalog_backup",
                label="Catalog backup",
                status="ok",
                detail="ok",
                verified=True,
            ),
            "cloud_vault": health._check(
                id="cloud_vault",
                label="Cloud vault",
                status="ok",
                detail="Not configured",
                configured=False,
            ),
            "disk_library": health._check(
                id="disk_library", label="Library disk", status="ok", detail="ok"
            ),
            "disk_cache": health._check(
                id="disk_cache", label="Cache disk", status="ok", detail="ok"
            ),
            "memory": health._check(
                id="memory", label="Memory pressure", status="ok", detail="ok"
            ),
            "pregen": health._check(
                id="pregen", label="Preview pregen", status="ok", detail="idle"
            ),
            "workers": health._check(
                id="workers", label="Background workers", status="ok", detail="ok"
            ),
            "activity": health._check(
                id="activity", label="Import / sync activity", status="ok", detail="ok"
            ),
        }
        defaults.update(patches)
        with mock.patch.multiple(
            health,
            check_catalog_db=mock.Mock(return_value=defaults["catalog_db"]),
            check_catalog_backup=mock.Mock(return_value=defaults["catalog_backup"]),
            check_cloud_vault=mock.Mock(return_value=defaults["cloud_vault"]),
            check_disk_library=mock.Mock(return_value=defaults["disk_library"]),
            check_disk_cache=mock.Mock(return_value=defaults["disk_cache"]),
            check_memory=mock.Mock(return_value=defaults["memory"]),
            check_pregen=mock.Mock(return_value=defaults["pregen"]),
            check_workers=mock.Mock(return_value=defaults["workers"]),
            check_activity=mock.Mock(return_value=defaults["activity"]),
        ):
            return health.collect_health(db_path=str(self.db_path))

    def test_overall_ok_when_all_checks_ok(self):
        payload = self._payload()
        self.assertEqual(payload["overall"], "ok")
        self.assertEqual(len(payload["checks"]), 9)
        for check in payload["checks"]:
            self.assertIn(check["status"], {"ok", "warn", "bad"})
            self.assertIn("detail", check)
            self.assertIn("checked_at", check)

    def test_overall_warn_and_bad_from_any_check(self):
        warn = self._payload(
            memory=health._check(
                id="memory", label="Memory pressure", status="warn", detail="soft"
            )
        )
        self.assertEqual(warn["overall"], "warn")
        bad = self._payload(
            catalog_db=health._check(
                id="catalog_db", label="Catalog database", status="bad", detail="corrupt"
            ),
            memory=health._check(
                id="memory", label="Memory pressure", status="warn", detail="soft"
            ),
        )
        self.assertEqual(bad["overall"], "bad")

    def test_each_check_mocked_ok_warn_bad(self):
        ids = [
            "catalog_db",
            "catalog_backup",
            "cloud_vault",
            "disk_library",
            "disk_cache",
            "memory",
            "pregen",
            "workers",
            "activity",
        ]
        for check_id in ids:
            for status in ("ok", "warn", "bad"):
                patch = {
                    check_id: health._check(
                        id=check_id,
                        label=check_id,
                        status=status,
                        detail=f"{check_id}:{status}",
                    )
                }
                payload = self._payload(**patch)
                matched = next(item for item in payload["checks"] if item["id"] == check_id)
                self.assertEqual(matched["status"], status)
                expected_overall = "bad" if status == "bad" else ("warn" if status == "warn" else "ok")
                self.assertEqual(payload["overall"], expected_overall)

    def test_cloud_vault_absent_module_is_not_configured(self):
        with mock.patch.object(health, "_load_cloud_status", return_value=None):
            result = health.check_cloud_vault()
        self.assertEqual(result["status"], "ok")
        self.assertIn("not configured", result["detail"].lower())
        self.assertFalse(result.get("configured", True))

    def test_cloud_vault_empty_remote_is_not_configured(self):
        with mock.patch.object(
            health,
            "_load_cloud_status",
            return_value={
                "config": {"remote": ""},
                "state": "idle",
                "last_ok_at": None,
                "last_error": None,
            },
        ):
            result = health.check_cloud_vault()
        self.assertEqual(result["status"], "ok")
        self.assertIn("not configured", result["detail"].lower())
        self.assertFalse(result.get("configured", True))

    def test_catalog_backup_ages(self):
        now = time.time()
        recent = [
            {
                "name": "azimuth-20260719-010000.db.gz",
                "created_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now - 3600)),
                "bytes": 100,
            }
        ]
        with mock.patch.object(health.backups, "backup_run_status", return_value={"last_error": None}), mock.patch.object(
            health.backups, "list_backups", return_value=recent
        ):
            ok = health.check_catalog_backup(str(self.db_path))
        self.assertEqual(ok["status"], "ok")
        self.assertTrue(ok["verified"])

        stale = [
            {
                "name": "azimuth-20260101-010000.db.gz",
                "created_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now - 10 * 86400)),
                "bytes": 100,
            }
        ]
        with mock.patch.object(health.backups, "backup_run_status", return_value={"last_error": None}), mock.patch.object(
            health.backups, "list_backups", return_value=stale
        ):
            bad = health.check_catalog_backup(str(self.db_path))
        self.assertEqual(bad["status"], "bad")

    def test_memory_soft_and_hard(self):
        soft = SimpleNamespace(
            level="soft",
            pause_bulk=True,
            rss_bytes=4 * 1024**3,
            swap_bytes=3 * 1024**3,
            pressure_bytes=7 * 1024**3,
            signal="cgroup",
            soft_bytes=6 * 1024**3,
            hard_bytes=10 * 1024**3,
            resume_bytes=5 * 1024**3,
            unload_models=True,
            message="Paused: memory pressure",
        )
        hard = SimpleNamespace(
            level="hard",
            pause_bulk=True,
            rss_bytes=5 * 1024**3,
            swap_bytes=6 * 1024**3,
            pressure_bytes=11 * 1024**3,
            signal="cgroup",
            soft_bytes=6 * 1024**3,
            hard_bytes=10 * 1024**3,
            resume_bytes=5 * 1024**3,
            unload_models=True,
            message="Paused: memory pressure",
        )
        with mock.patch.object(health.memory_pressure, "evaluate_memory_pressure", return_value=soft):
            result = health.check_memory()
            self.assertEqual(result["status"], "warn")
            self.assertIn("swap", result["detail"])
            self.assertEqual(result["pressure_bytes"], 7 * 1024**3)
        with mock.patch.object(health.memory_pressure, "evaluate_memory_pressure", return_value=hard):
            result = health.check_memory()
            self.assertEqual(result["status"], "bad")
            self.assertIn("Hard pressure", result["detail"])


class HealthRouteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_path = _make_catalog(Path(self.tmp.name) / "catalog.db")
        app = FastAPI()
        health_routes.configure(db_path=lambda: str(self.db_path))
        app.include_router(health_routes.router)
        self.client = TestClient(app)

    def test_details_endpoint_returns_aggregation(self):
        with mock.patch.object(
            health,
            "collect_health",
            return_value={
                "overall": "ok",
                "checked_at": time.time(),
                "checks": [
                    health._check(
                        id="catalog_db",
                        label="Catalog database",
                        status="ok",
                        detail="ok",
                    )
                ],
            },
        ):
            response = self.client.get("/api/health/details")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["overall"], "ok")
        self.assertEqual(data["checks"][0]["id"], "catalog_db")

    def test_summary_endpoint_returns_only_fleet_signals(self):
        with mock.patch.object(
            health,
            "collect_health",
            return_value={
                "overall": "warn",
                "checked_at": 123.0,
                "checks": [
                    health._check(
                        id="catalog_db",
                        label="Catalog database",
                        status="ok",
                        detail="private catalog detail",
                    ),
                    health._check(
                        id="workers",
                        label="Background workers",
                        status="warn",
                        detail="private worker detail",
                    ),
                ],
            },
        ):
            response = self.client.get("/api/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "status": "warn",
                "checked_at": 123.0,
                "checks": {"catalog_db": "ok", "workers": "warn"},
            },
        )

    def test_summary_timeout_keeps_refresh_running_off_request(self):
        async def scenario():
            gate = asyncio.Event()

            async def slow_refresh():
                await gate.wait()
                return {"overall": "ok", "checked_at": 123.0, "checks": []}

            with mock.patch.object(health_routes, "_refresh_health", slow_refresh):
                started = time.perf_counter()
                result = await health_routes._health_snapshot(initial_wait_seconds=0.05)
                elapsed = time.perf_counter() - started
                task = health_routes._health_refresh_task
                self.assertEqual(result["overall"], "warn")
                self.assertTrue(result["status_stale"])
                self.assertLess(elapsed, 0.2)
                self.assertIsNotNone(task)
                self.assertFalse(task.cancelled())
                gate.set()
                await task

        asyncio.run(scenario())


class HealthAuthTests(unittest.TestCase):
    def test_owner_auth_required_on_live_app(self):
        # Import the live app so middleware is present; loopback is exempt, so
        # exercise a non-loopback client address.
        import app as app_module
        from core import owner_auth
        from features.auth import service as owner_service
        import settings
        import db
        import asyncio

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        settings_path = Path(tmp.name) / "settings.local.json"
        db_path = Path(tmp.name) / "auth.db"
        old_settings = settings.SETTINGS_PATH
        old_db = db.DB_PATH
        settings.SETTINGS_PATH = str(settings_path)
        settings._settings = None
        db.DB_PATH = str(db_path)
        owner_service.clear_unlock_failures()
        try:
            asyncio.run(owner_service.set_owner_key("health-panel-test-key"))
            client = TestClient(
                app_module.app,
                client=("10.9.8.7", 4444),
                follow_redirects=False,
            )
            response = client.get("/api/health/details")
            self.assertEqual(response.status_code, 401)
            self.assertIn("Owner authentication required", response.json().get("error", ""))
            self.assertNotIn("/api/health/details", owner_auth.PUBLIC_PATHS)
            # The summary is deliberately public: launchers and monitors probe
            # the bind address (tailnet IP in tailscale mode, never loopback),
            # and it carries no catalog specifics — only check ids and ok/warn.
            summary = client.get("/api/health")
            self.assertEqual(summary.status_code, 200)
            self.assertEqual(set(summary.json()), {"status", "checked_at", "checks"})
            self.assertIn("/api/health", owner_auth.PUBLIC_PATHS)
        finally:
            settings.SETTINGS_PATH = old_settings
            settings._settings = None
            db.DB_PATH = old_db
            owner_service.clear_unlock_failures()


if __name__ == "__main__":
    raise SystemExit(unittest.main())
