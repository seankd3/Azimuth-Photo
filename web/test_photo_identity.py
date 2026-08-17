"""Every photo gets an identity, newest first, without a bookmark.

The backfill used to be a route nobody called, ordered oldest-first, driven by
an `id > mark` cursor. Each of those is a separate way to leave a photo without
a content hash, and a photo without one cannot be the subject of any derived
artifact — no thumbnail key, no embedding, no caption, no face vector.
"""

from __future__ import annotations
from core.catalog_path import catalog_path, use as catalog_path_use

import asyncio
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

import db
from data import connection, schema
from photo import identity


class IdentityBackfillTests(unittest.TestCase):
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

    def add(self, name: str, *, date_taken: str, on_disk: bool = True, **columns) -> int:
        path = self.root / name
        if on_disk:
            Image.new("RGB", (4, 3), (len(name) * 7 % 256, 40, 90)).save(path, format="JPEG")
        row = {"filename": name, "filepath": str(path), "date_taken": date_taken, "status": "kept"}
        row.update(columns)
        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.execute(
                f"INSERT INTO images({','.join(row)}) VALUES ({','.join('?' * len(row))})",
                tuple(row.values()),
            )
            conn.commit()
            return int(cursor.lastrowid)
        finally:
            conn.close()

    def hashes(self) -> dict[str, str | None]:
        conn = sqlite3.connect(self.db_path)
        try:
            return {name: value for name, value in conn.execute("SELECT filename, content_hash FROM images")}
        finally:
            conn.close()

    def test_the_newest_photo_is_given_an_identity_first(self):
        self.add("old.jpg", date_taken="2019-01-01 10:00:00")
        self.add("new.jpg", date_taken="2026-08-04 10:00:00")
        with mock.patch.object(identity, "BATCH", 1):
            hashed, considered = asyncio.run(identity.fill_one_batch(self.db_path))
        self.assertEqual((hashed, considered), (1, 1))
        by_name = self.hashes()
        self.assertIsNotNone(by_name["new.jpg"], "the photo that just came off the card comes first")
        self.assertIsNone(by_name["old.jpg"])

    def test_it_advances_without_a_bookmark(self):
        for index in range(5):
            self.add(f"p{index}.jpg", date_taken=f"202{index}-01-01 10:00:00")
        # No cursor is threaded between calls. Hashing a photo removes it from
        # its own candidate set, which is the only thing that makes progress.
        with mock.patch.object(identity, "BATCH", 2):
            for _ in range(3):
                asyncio.run(identity.fill_one_batch(self.db_path))
        self.assertTrue(all(self.hashes().values()), "every photo ended up with an identity")

    def test_an_unreadable_photo_does_not_stall_the_ones_behind_it(self):
        self.add("offline.jpg", date_taken="2026-08-04 10:00:00", on_disk=False)
        self.add("readable.jpg", date_taken="2019-01-01 10:00:00")
        with mock.patch.object(identity, "BATCH", 1):
            # The head of the queue cannot be read: nothing hashed, but a row was
            # considered, which is what tells the worker to step over it.
            self.assertEqual(asyncio.run(identity.fill_one_batch(self.db_path)), (0, 1))
            self.assertEqual(asyncio.run(identity.fill_one_batch(self.db_path, skip=1)), (1, 1))
        self.assertIsNotNone(self.hashes()["readable.jpg"])

    def test_a_photo_out_of_the_library_is_not_owed_an_identity(self):
        self.add("trashed.jpg", date_taken="2026-08-04 10:00:00", status="trashed")
        self.add("gone.jpg", date_taken="2026-08-03 10:00:00", missing_at="2026-08-01 00:00:00")
        self.assertEqual(asyncio.run(identity.fill_one_batch(self.db_path)), (0, 0))

    def test_the_backlog_query_reads_its_index(self):
        """The index is named in the query, so drift fails here rather than in prod.

        Left to the planner this query costs 25 ms against 1.7 ms on a 150k-row
        catalog, because SQLite prefers `idx_images_missing_date_source` plus a
        temp B-tree for the sort until someone runs ANALYZE -- and nothing does.
        """

        conn = sqlite3.connect(self.db_path)
        try:
            plan = " ".join(row[-1] for row in conn.execute("EXPLAIN QUERY PLAN " + identity._NEEDS_IDENTITY, (100, 0)))
        finally:
            conn.close()
        self.assertIn("idx_images_needs_identity", plan)
        self.assertNotIn("TEMP B-TREE", plan, "the index must satisfy the ORDER BY, not just the filter")

    def test_an_empty_string_is_not_an_identity(self):
        self.add("legacy.jpg", date_taken="2026-08-04 10:00:00", content_hash="")
        self.assertEqual(
            asyncio.run(identity.fill_one_batch(self.db_path)),
            (0, 0),
            "an empty hash hides the photo from the backfill that would fix it",
        )

        async def normalize():
            conn = await connection.open_async(self.db_path)
            try:
                await schema.normalize_legacy_image_state(conn)
                await conn.commit()
            finally:
                await connection.close_async(conn, db_path=self.db_path)

        asyncio.run(normalize())
        self.assertEqual(asyncio.run(identity.fill_one_batch(self.db_path)), (1, 1))
        self.assertTrue(self.hashes()["legacy.jpg"])


if __name__ == "__main__":
    unittest.main()
