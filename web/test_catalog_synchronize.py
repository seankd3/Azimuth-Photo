"""Synchronize Folder: the catalog follows the disk, in one pass."""

import asyncio
import os
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

import db
from features.catalog import synchronize

# A real, tiny JPEG. The scanner reads headers, so the bytes have to parse.
JPEG = bytes.fromhex(
    "ffd8ffe000104a46494600010100000100010000ffdb004300ffffffffffffffffffffffff"
    "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
    "ffffffffffffffffffffffffffffffffffffffffffffffffffffffc2000b080001000101011"
    "100ffc40014000100000000000000000000000000000009ffda0008010100013f10"
)


class LibraryFixture:
    """A disposable catalog over a real little folder tree."""

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.library = self.root / "Photos"
        self.db_path = str(self.root / "catalog.db")
        self.old_db_path = db.DB_PATH
        db.DB_PATH = self.db_path
        asyncio.run(db.init_db())

    def tearDown(self):
        db.DB_PATH = self.old_db_path
        self.tempdir.cleanup()

    def _photo(self, relative: str, payload: bytes = JPEG) -> Path:
        path = self.library / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        return path

    def _catalog(self, *paths: Path) -> dict[str, int]:
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                "INSERT OR IGNORE INTO catalog_sources(path, display_name) VALUES (?, ?)",
                (str(self.library), "Photos"),
            )
            source_id = conn.execute(
                "SELECT id FROM catalog_sources WHERE path = ?", (str(self.library),)
            ).fetchone()[0]
            ids = {}
            for path in paths:
                ids[str(path)] = conn.execute(
                    "INSERT INTO images(source_id, filename, filepath, file_size, status) "
                    "VALUES (?, ?, ?, ?, 'kept')",
                    (source_id, path.name, str(path), path.stat().st_size),
                ).lastrowid
            conn.commit()
            return ids
        finally:
            conn.close()

    def _survey(self) -> synchronize.Plan:
        return asyncio.run(synchronize.survey(self.db_path, str(self.library)))


class SynchronizeFolderTests(LibraryFixture, unittest.TestCase):
    def test_a_renamed_folder_reads_as_moved_not_lost(self):
        """The failure that cost a day: a rename must not look like deletion."""

        first = self._photo("Personal Photos/2026/a.jpg")
        second = self._photo("Personal Photos/2026/b.jpg")
        self._catalog(first, second)

        os.rename(self.library / "Personal Photos", self.library / "Snapshots")

        plan = self._survey()
        self.assertEqual(len(plan.moved), 2, plan.as_payload())
        self.assertEqual(plan.gone, [], "a renamed folder is not lost photos")
        self.assertEqual(plan.added, [], "a renamed folder is not new photos")
        for _image_id, old, new in plan.moved:
            self.assertIn("Personal Photos", old)
            self.assertIn("Snapshots", new)

        result = asyncio.run(synchronize.apply(self.db_path, plan))
        self.assertTrue(result["applied"])

        conn = sqlite3.connect(self.db_path)
        try:
            rows = conn.execute("SELECT filepath, missing_at FROM images ORDER BY filepath").fetchall()
        finally:
            conn.close()
        for filepath, missing_at in rows:
            self.assertIn("Snapshots", filepath)
            self.assertIsNone(missing_at, "a moved photo is present, not missing")

        self.assertTrue(self._survey().is_empty, "a second pass has nothing left to do")

    def test_a_deleted_file_is_gone_and_a_new_file_is_added(self):
        kept = self._photo("Edits/2026/keep.jpg")
        removed = self._photo("Edits/2026/remove.jpg")
        self._catalog(kept, removed)
        removed.unlink()
        self._photo("Edits/2026/arrived.jpg")

        plan = self._survey()
        self.assertEqual([os.path.basename(p) for p in plan.added], ["arrived.jpg"])
        self.assertEqual(len(plan.gone), 1)
        self.assertEqual(plan.moved, [])
        self.assertEqual(plan.unchanged, 1)

        asyncio.run(synchronize.apply(self.db_path, plan))
        conn = sqlite3.connect(self.db_path)
        try:
            missing = conn.execute(
                "SELECT count(*) FROM images WHERE missing_at IS NOT NULL"
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(missing, 1)

    def test_an_unplugged_drive_is_refused_rather_than_marked_missing(self):
        photos = [self._photo(f"Raws/2026/{index}.jpg") for index in range(12)]
        self._catalog(*photos)
        shutil.rmtree(self.library / "Raws")
        (self.library / "Raws").mkdir(parents=True)

        plan = self._survey()
        self.assertEqual(len(plan.gone), 12)
        with self.assertRaises(Exception) as caught:
            asyncio.run(synchronize.apply(self.db_path, plan))
        self.assertIn("unavailable storage", str(caught.exception))

    def test_a_folder_already_matching_the_catalog_needs_no_work(self):
        photos = [self._photo(f"Snapshots/2026/{index}.jpg") for index in range(3)]
        self._catalog(*photos)
        plan = self._survey()
        self.assertTrue(plan.is_empty)
        self.assertEqual(plan.unchanged, 3)
        result = asyncio.run(synchronize.apply(self.db_path, plan))
        self.assertFalse(result["applied"])


class DirectoryFastPathTests(LibraryFixture, unittest.TestCase):
    """A second pass must not re-read directories that did not change."""

    def _apply(self) -> synchronize.Plan:
        plan = self._survey()
        asyncio.run(synchronize.apply(self.db_path, plan))
        return plan

    def test_an_unchanged_tree_is_not_read_twice(self):
        for day in range(4):
            self._photo(f"Edits/2026/2026-01-0{day}/a.jpg")
        self._catalog(*sorted(self.library.rglob("*.jpg")))

        first = self._apply()
        self.assertGreater(first.directories_read, 0, "the first pass must read everything")
        self.assertEqual(first.directories_read, first.directories_seen)

        second = self._survey()
        self.assertEqual(second.directories_seen, first.directories_seen)
        self.assertEqual(second.directories_read, 0, "nothing changed, so nothing needs listing")
        self.assertTrue(second.is_empty)
        self.assertEqual(second.unchanged, 4, "the catalog answers for untouched directories")

    def test_a_change_is_still_seen_after_the_tree_was_remembered(self):
        kept = self._photo("Snapshots/2026/2026-02-01/kept.jpg")
        self._catalog(kept)
        self._apply()

        self._photo("Snapshots/2026/2026-02-01/arrived.jpg")
        plan = self._survey()
        self.assertEqual(plan.directories_read, 1, "only the directory that moved is listed")
        self.assertEqual([os.path.basename(p) for p in plan.added], ["arrived.jpg"])

    def test_full_ignores_what_was_remembered(self):
        self._catalog(self._photo("Raws/2026/2026-03-01/a.jpg"))
        first = self._apply()
        self.assertEqual(self._survey().directories_read, 0)

        forced = asyncio.run(synchronize.survey(self.db_path, str(self.library), full=True))
        self.assertEqual(forced.directories_read, first.directories_seen)
        self.assertTrue(forced.is_empty)

    def test_a_recreated_directory_is_not_mistaken_for_the_old_one(self):
        first_photo = self._photo("Edits/2026/2026-04-01/one.jpg")
        self._catalog(first_photo)
        self._apply()

        shutil.rmtree(self.library / "Edits" / "2026" / "2026-04-01")
        plan = self._survey()
        asyncio.run(synchronize.apply(self.db_path, plan))
        self.assertEqual(len(plan.gone), 1)

        self._photo("Edits/2026/2026-04-01/two.jpg")
        again = self._survey()
        self.assertEqual([os.path.basename(p) for p in again.added], ["two.jpg"])


if __name__ == "__main__":
    unittest.main()
