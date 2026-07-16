"""Pre-migration catalog backups: labeled snapshots, retention protection, hook."""

import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

import db as app_db
from data import connection as data_connection
from data import schema as data_schema
from features.system import backups


def _make_db(path: str, user_version: int) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE images (id INTEGER PRIMARY KEY)")
        conn.execute(f"PRAGMA user_version = {user_version}")
        conn.commit()
    finally:
        conn.close()


class MigrationBackupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "backups"
        self.root.mkdir()
        self.db = os.path.join(self.tmp.name, "photoarchive.db")
        _make_db(self.db, 20)
        self._patch = mock.patch.object(backups, "backup_root", return_value=self.root)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        self.tmp.cleanup()

    def _fake(self, when: datetime, label: str | None = None) -> str:
        name = backups._timestamp_name(when, label)
        (self.root / name).write_bytes(b"stub")
        return name

    def test_labeled_snapshot_roundtrips_and_lists(self):
        result = backups.create_snapshot(self.db, label=backups.PREMIGRATE_LABEL)
        self.assertTrue(result["ok"])
        self.assertIn(backups.PREMIGRATE_LABEL, result["name"])
        self.assertEqual(result["label"], backups.PREMIGRATE_LABEL)
        # Still discoverable by the time-machine listing (restorable).
        self.assertIn(result["name"], [b["name"] for b in backups.list_backups()])

    def test_backup_before_migration_creates_snapshot(self):
        result = backups.backup_before_migration(self.db, 20, backups.PREMIGRATE_KEEP + 20)
        self.assertIsNotNone(result)
        self.assertTrue(Path(result["path"]).exists())
        self.assertIn(backups.PREMIGRATE_LABEL, result["name"])

    def test_snapshot_copy_uses_fk_enabled_connection_helpers(self):
        destination = os.path.join(self.tmp.name, "copy.db")
        with mock.patch.object(
            data_connection,
            "open_sync",
            wraps=data_connection.open_sync,
        ) as open_sync:
            backups._sqlite_backup_to_path(self.db, destination)
        self.assertEqual(open_sync.call_count, 2)

    def test_backup_before_migration_raises_when_snapshot_fails(self):
        with self.assertRaises(FileNotFoundError):
            backups.backup_before_migration(self.db + ".nope", 20, 27)

    def test_retention_protects_premigrate_snapshots(self):
        old = datetime.now() - timedelta(days=120)  # far outside daily/weekly windows
        premig = self._fake(old, backups.PREMIGRATE_LABEL)
        plain = self._fake(old - timedelta(minutes=1))
        recent = self._fake(datetime.now())

        pruned = backups.apply_retention(self.root)

        self.assertIn(plain, pruned)
        self.assertNotIn(premig, pruned)
        self.assertNotIn(recent, pruned)
        self.assertTrue((self.root / premig).exists())
        self.assertFalse((self.root / plain).exists())

    def test_retention_caps_premigrate_backlog(self):
        base = datetime.now() - timedelta(days=200)
        names = [
            self._fake(base + timedelta(hours=i), backups.PREMIGRATE_LABEL)
            for i in range(backups.PREMIGRATE_KEEP + 3)
        ]
        backups.apply_retention(self.root)
        survivors = [n for n in names if (self.root / n).exists()]
        # Only the newest PREMIGRATE_KEEP are protected; older ones age out.
        self.assertEqual(len(survivors), backups.PREMIGRATE_KEEP)
        self.assertEqual(set(survivors), set(names[-backups.PREMIGRATE_KEEP:]))


class MigrationSafetyGateTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "photoarchive.db")
        _make_db(self.db, 20)

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def test_startup_refuses_migration_when_snapshot_returns_none(self):
        conn = await data_connection.open_async(self.db)
        try:
            with mock.patch.object(app_db, "DB_PATH", self.db), mock.patch.object(
                backups,
                "backup_before_migration",
                return_value=None,
            ):
                with self.assertRaisesRegex(RuntimeError, "backup"):
                    await app_db._backup_before_migration(conn)
        finally:
            await data_connection.close_async(conn, db_path=self.db)

    async def test_populated_version_zero_catalog_is_backed_up(self):
        version_zero_db = os.path.join(self.tmp.name, "version-zero.db")
        _make_db(version_zero_db, 0)
        with sqlite3.connect(version_zero_db) as conn:
            conn.execute("INSERT INTO images DEFAULT VALUES")
        conn = await data_connection.open_async(version_zero_db)
        try:
            with mock.patch.object(app_db, "DB_PATH", version_zero_db), mock.patch.object(
                backups,
                "backup_before_migration",
                return_value={"ok": True},
            ) as backup:
                await app_db._backup_before_migration(conn)
        finally:
            await data_connection.close_async(conn, db_path=version_zero_db)

        backup.assert_called_once_with(version_zero_db, 0, app_db.SCHEMA_VERSION)

    async def test_populated_version_zero_catalog_refuses_failed_backup(self):
        version_zero_db = os.path.join(self.tmp.name, "version-zero-failed.db")
        _make_db(version_zero_db, 0)
        with sqlite3.connect(version_zero_db) as conn:
            conn.execute("INSERT INTO images DEFAULT VALUES")
        with (
            mock.patch.object(app_db, "DB_PATH", version_zero_db),
            mock.patch.object(
                backups,
                "backup_before_migration",
                return_value=None,
            ),
            mock.patch.object(app_db, "_schema_is_current", return_value=False) as schema_probe,
        ):
            with self.assertRaisesRegex(RuntimeError, "backup"):
                await app_db.init_db()

        schema_probe.assert_not_awaited()

    async def test_stack_rebuild_creates_immediate_local_backup(self):
        stack_db = os.path.join(self.tmp.name, "legacy-stacks.db")
        conn = await data_connection.open_async(stack_db)
        try:
            await conn.executescript(
                """
                CREATE TABLE images (id INTEGER PRIMARY KEY);
                CREATE TABLE stacks (
                    id INTEGER PRIMARY KEY,
                    kind TEXT NOT NULL CHECK(kind IN ('burst','variant','crosssource','manual')),
                    representative_image_id INTEGER NOT NULL REFERENCES images(id),
                    auto INTEGER NOT NULL DEFAULT 1,
                    created_at REAL,
                    updated_at REAL
                );
                INSERT INTO images(id) VALUES (1);
                INSERT INTO stacks(id, kind, representative_image_id) VALUES (1, 'manual', 1);
                """
            )
            await data_schema.migrate_stack_kind_for_versions(conn)
        finally:
            await data_connection.close_async(conn, db_path=stack_db)

        backup_path = f"{stack_db}.pre-rebuild-stacks.bak"
        self.assertTrue(os.path.exists(backup_path))
        with sqlite3.connect(backup_path) as backup:
            table_sql = backup.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'stacks'"
            ).fetchone()[0]
        self.assertNotIn("'version'", table_sql)

    async def test_add_column_failure_aborts_schema_preparation(self):
        broken_db = os.path.join(self.tmp.name, "broken-column.db")
        conn = await data_connection.open_async(broken_db)
        try:
            await conn.execute("CREATE TABLE sample (id INTEGER PRIMARY KEY)")
            with self.assertRaises(Exception):
                await data_schema._add_columns_if_missing(
                    conn,
                    "sample",
                    (("broken", "TEXT DEFAULT ("),),
                )
        finally:
            await data_connection.close_async(conn, db_path=broken_db)


if __name__ == "__main__":
    unittest.main()
