"""Catalog and directory rebrand migrations."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from core import rebrand_migrate
from core.runtime_paths import resolve_runtime_paths


class RebrandMigrateTests(unittest.TestCase):
    def test_catalog_renames_old_to_new(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = Path(tmp) / "photoarchive.db"
            new = Path(tmp) / "azimuth.db"
            old.write_text("db", encoding="utf-8")
            (Path(tmp) / "photoarchive.db-wal").write_text("wal", encoding="utf-8")
            result = rebrand_migrate.migrate_catalog_db(new)
            self.assertEqual(Path(result), new)
            self.assertTrue(new.exists())
            self.assertFalse(old.exists())
            self.assertTrue((Path(tmp) / "azimuth.db-wal").exists())
            # second boot no-op
            result2 = rebrand_migrate.migrate_catalog_db(new)
            self.assertEqual(Path(result2), new)

    def test_catalog_keeps_old_when_rename_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = Path(tmp) / "photoarchive.db"
            new = Path(tmp) / "azimuth.db"
            old.write_text("db", encoding="utf-8")
            new.write_text("existing", encoding="utf-8")
            result = rebrand_migrate.migrate_catalog_db(new)
            self.assertEqual(Path(result), new)
            self.assertEqual(new.read_text(encoding="utf-8"), "existing")

    def test_resolve_prefers_existing_legacy_catalog_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            home.mkdir()
            data = home / ".local" / "share" / "photoarchive" / "catalog"
            data.mkdir(parents=True)
            (data / "photoarchive.db").write_text("x", encoding="utf-8")
            paths = resolve_runtime_paths(
                Path(tmp) / "web",
                {"HOME": str(home)},
                "linux",
                str(home),
            )
            self.assertTrue(paths.catalog_db.endswith("photoarchive.db") or "photoarchive" in paths.catalog_db or "azimuth" in paths.catalog_db)


if __name__ == "__main__":
    unittest.main()
