"""A photo's version only moves when a satellite would care.

Bumping `row_version` tells every satellite to re-download that row. Adding the
`relative_path` column rewrote 149,602 rows on the hub and therefore told a
laptop that almost the whole library had changed: it re-applied 150,000 rows to
find that 16 had genuinely moved, pegging three cores and starving the grid.
"""

import asyncio
import sqlite3
import tempfile
import unittest
from pathlib import Path

import db
from data.schema import EXPORTED_IMAGE_COLUMNS
from features.sync import mirror_export


class RowVersionScopeTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tempdir.name) / "catalog.db")
        self.old_db_path = db.DB_PATH
        db.DB_PATH = self.db_path
        asyncio.run(db.init_db())
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                "INSERT OR IGNORE INTO catalog_sources(path, display_name) VALUES ('/lib', 'Lib')"
            )
            source_id = conn.execute("SELECT id FROM catalog_sources WHERE path='/lib'").fetchone()[0]
            self.image_id = conn.execute(
                "INSERT INTO images(source_id, filename, filepath, status) VALUES (?, 'a.jpg', '/lib/a.jpg', 'kept')",
                (source_id,),
            ).lastrowid
            conn.commit()
        finally:
            conn.close()

    def tearDown(self):
        db.DB_PATH = self.old_db_path
        self.tempdir.cleanup()

    def _version(self) -> int:
        conn = sqlite3.connect(self.db_path)
        try:
            return int(conn.execute(
                "SELECT row_version FROM images WHERE id = ?", (self.image_id,)
            ).fetchone()[0])
        finally:
            conn.close()

    def _update(self, column: str, value) -> None:
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(f"UPDATE images SET {column} = ? WHERE id = ?", (value, self.image_id))
            conn.commit()
        finally:
            conn.close()

    def test_a_change_a_satellite_receives_bumps_the_version(self):
        for column, value in (("status", "maybe"), ("filepath", "/lib/moved.jpg"), ("flag", "picked")):
            before = self._version()
            self._update(column, value)
            self.assertGreater(self._version(), before, f"{column} is exported and must resync")

    def test_a_derived_column_no_satellite_reads_does_not(self):
        before = self._version()
        self._update("relative_path", "moved.jpg")
        self.assertEqual(self._version(), before,
                         "relative_path is local bookkeeping; rewriting it must not cost a resync")

    def test_other_local_bookkeeping_is_also_silent(self):
        for column, value in (("metadata_version", 7), ("aspect_ratio", 1.5), ("propagated_updates", 1)):
            before = self._version()
            self._update(column, value)
            self.assertEqual(self._version(), before, f"{column} is not exported")

    def test_the_trigger_covers_every_exported_image_column(self):
        """The guard: adding a field to the export must not silently stop syncing."""

        exported = {
            name.split(".", 1)[1]
            for name in mirror_export._IMAGE_COLUMNS.replace("\n", " ").split()
            if name.startswith("i.")
        }
        exported = {name.rstrip(",") for name in exported}
        # id identifies the row and row_version is the cursor itself.
        exported -= {"id", "row_version"}
        missing = exported - set(EXPORTED_IMAGE_COLUMNS)
        self.assertEqual(
            missing, set(),
            f"these columns are exported but would not bump row_version: {sorted(missing)}",
        )


if __name__ == "__main__":
    unittest.main()
