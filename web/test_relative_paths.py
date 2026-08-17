"""Where a photo sits inside its source, stored so a root rename is one row.

The absolute `filepath` stays exactly as it was — every reader, index, ETag and
external contract still works. This adds the durable half beside it.
"""

from core.catalog_path import catalog_path, use as catalog_path_use
import asyncio
import sqlite3
import tempfile
import unittest
from pathlib import Path

import db
from data import connection
from data import schema as data_schema


class RelativePathTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.db_path = str(self.root / "catalog.db")
        self.old_db_path = catalog_path()
        catalog_path_use(self.db_path)
        asyncio.run(db.init_db())

    def tearDown(self):
        catalog_path_use(self.old_db_path)
        self.tempdir.cleanup()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _source(self, path: str, name: str) -> int:
        conn = self._connect()
        try:
            conn.execute(
                "INSERT OR IGNORE INTO catalog_sources(path, display_name) VALUES (?, ?)",
                (path, name),
            )
            conn.commit()
            return int(conn.execute(
                "SELECT id FROM catalog_sources WHERE path = ?", (path,)
            ).fetchone()["id"])
        finally:
            conn.close()

    def _image(self, source_id: int, filepath: str) -> int:
        conn = self._connect()
        try:
            image_id = conn.execute(
                "INSERT INTO images(source_id, filename, filepath, status) VALUES (?, ?, ?, 'kept')",
                (source_id, Path(filepath).name, filepath),
            ).lastrowid
            conn.commit()
            return int(image_id)
        finally:
            conn.close()

    def _backfill(self) -> int:
        async def run():
            # Its own connection, closed here: sharing the app's pooled one
            # across separate event loops leaves aiosqlite threads orphaned.
            conn = await connection.open_async(self.db_path)
            try:
                written = await data_schema.backfill_relative_paths(conn)
                await conn.commit()
                return written
            finally:
                await connection.close_async(conn, db_path=self.db_path)

        return asyncio.run(run())

    def test_the_column_and_its_uniqueness_exist(self):
        conn = self._connect()
        try:
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(images)")}
            self.assertIn("relative_path", columns)
            index = conn.execute(
                "SELECT sql FROM sqlite_master WHERE name = 'idx_images_source_relative_path'"
            ).fetchone()
            self.assertIsNotNone(index, "the uniqueness must exist, not just the column")
            self.assertIn("UNIQUE", index["sql"])
            self.assertIn("vc_of IS NULL", index["sql"], "virtual copies share a parent's file")
        finally:
            conn.close()

    def test_a_photo_gets_its_place_inside_its_source(self):
        source_id = self._source("/library/Raws", "Raws")
        image_id = self._image(source_id, "/library/Raws/Digital/2026/2026-01-02/a.CR3")

        self.assertEqual(self._backfill(), 1)
        conn = self._connect()
        try:
            row = conn.execute("SELECT filepath, relative_path FROM images WHERE id = ?", (image_id,)).fetchone()
        finally:
            conn.close()
        self.assertEqual(row["relative_path"], "Digital/2026/2026-01-02/a.CR3")
        self.assertEqual(row["filepath"], "/library/Raws/Digital/2026/2026-01-02/a.CR3",
                         "the absolute path is untouched")

    def test_a_path_outside_its_source_is_left_absent_rather_than_guessed(self):
        source_id = self._source("/library/Edits", "Edits")
        image_id = self._image(source_id, "/somewhere/else/stray.jpg")

        self._backfill()
        conn = self._connect()
        try:
            value = conn.execute("SELECT relative_path FROM images WHERE id = ?", (image_id,)).fetchone()["relative_path"]
        finally:
            conn.close()
        self.assertIsNone(value, "a value that cannot be derived must stay visibly absent")

    def test_a_mirrored_row_is_left_for_the_mirror_to_supply(self):
        source_id = self._source("hub://", "Hub library")
        image_id = self._image(source_id, "/mnt/expansion/Photos/Edits/2026/a.jpg")

        self._backfill()
        conn = self._connect()
        try:
            value = conn.execute("SELECT relative_path FROM images WHERE id = ?", (image_id,)).fetchone()["relative_path"]
        finally:
            conn.close()
        self.assertIsNone(value, "hub:// is not a place on this machine")

    def test_two_photos_cannot_occupy_the_same_place(self):
        source_id = self._source("/library/Snapshots", "Snapshots")
        self._image(source_id, "/library/Snapshots/2026/a.jpg")
        self._backfill()

        conn = self._connect()
        try:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO images(source_id, filename, filepath, relative_path, status) "
                    "VALUES (?, 'a.jpg', '/library/Snapshots/elsewhere/a.jpg', '2026/a.jpg', 'kept')",
                    (source_id,),
                )
                conn.commit()
        finally:
            conn.close()

    def test_the_same_place_in_two_sources_is_fine(self):
        edits = self._source("/library/Edits", "Edits")
        raws = self._source("/library/Raws", "Raws")
        self._image(edits, "/library/Edits/2026/a.jpg")
        self._image(raws, "/library/Raws/2026/a.jpg")

        self.assertEqual(self._backfill(), 2, "the same relative path under two roots is not a clash")

    def test_backfilling_twice_changes_nothing(self):
        source_id = self._source("/library/Edits", "Edits")
        self._image(source_id, "/library/Edits/2026/a.jpg")
        self.assertEqual(self._backfill(), 1)
        self.assertEqual(self._backfill(), 0, "already-derived rows are not rewritten")


if __name__ == "__main__":
    unittest.main()
