"""Synchronize Folder: the catalog follows the disk, in one pass."""

from core.catalog_path import catalog_path, use as catalog_path_use
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
        self.old_db_path = catalog_path()
        catalog_path_use(self.db_path)
        asyncio.run(db.init_db())

    def tearDown(self):
        catalog_path_use(self.old_db_path)
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


class ReconcileTests(LibraryFixture, unittest.TestCase):
    """The catalog keeps itself matching the folders, with no user action."""

    def _source(self) -> dict:
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                "INSERT OR IGNORE INTO catalog_sources(path, display_name) VALUES (?, ?)",
                (str(self.library), "Photos"),
            )
            conn.commit()
            row = conn.execute(
                "SELECT id, path, included FROM catalog_sources WHERE path = ?", (str(self.library),)
            ).fetchone()
        finally:
            conn.close()
        return {"id": row[0], "path": row[1], "included": row[2]}

    def _paths(self) -> set[str]:
        conn = sqlite3.connect(self.db_path)
        try:
            return {r[0] for r in conn.execute("SELECT filepath FROM images")}
        finally:
            conn.close()

    def test_a_file_dropped_into_the_library_joins_it(self):
        self.library.mkdir(parents=True, exist_ok=True)
        source = self._source()
        arrived = self._photo("Raws/Digital/2026/2026-05-01/new.jpg")

        result = asyncio.run(synchronize.reconcile_source(self.db_path, source))
        self.assertEqual(result["adopted"], 1, result)
        self.assertIn(str(arrived), self._paths())

        again = asyncio.run(synchronize.reconcile_source(self.db_path, source))
        self.assertFalse(again["applied"], "a settled library needs no second pass")

    def test_reconcile_once_skips_a_mirrored_source(self):
        self.library.mkdir(parents=True, exist_ok=True)
        self._source()
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                "INSERT OR IGNORE INTO catalog_sources(path, display_name) VALUES ('hub://', 'Hub library')"
            )
            conn.commit()
        finally:
            conn.close()
        self._photo("Edits/2026/2026-06-01/a.jpg")

        results = asyncio.run(synchronize.reconcile_once(self.db_path))
        self.assertEqual(len(results), 1, "only the real folder is reconciled")
        self.assertEqual(results[0]["adopted"], 1)


    def test_a_renamed_root_moves_the_source_rather_than_losing_the_photos(self):
        """The failure that cost a day, at the root level."""

        self.library.mkdir(parents=True, exist_ok=True)
        photos = [self._photo(f"Snapshots/2026/2026-01-0{n}/a.jpg") for n in range(1, 4)]
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                "INSERT INTO catalog_sources(path, display_name) VALUES (?, 'Snapshots')",
                (str(self.library / "Snapshots"),),
            )
            source_id = conn.execute(
                "SELECT id FROM catalog_sources WHERE path = ?",
                (str(self.library / "Snapshots"),),
            ).fetchone()[0]
            for path in photos:
                conn.execute(
                    "INSERT INTO images(source_id, filename, filepath, file_size, status) "
                    "VALUES (?, ?, ?, ?, 'kept')",
                    (source_id, path.name, str(path), path.stat().st_size),
                )
            conn.commit()
        finally:
            conn.close()

        os.rename(self.library / "Snapshots", self.library / "Personal Photos")
        asyncio.run(synchronize.reconcile_once(self.db_path))

        conn = sqlite3.connect(self.db_path)
        try:
            root = conn.execute(
                "SELECT path FROM catalog_sources WHERE id = ?", (source_id,)
            ).fetchone()[0]
            missing = conn.execute(
                "SELECT count(*) FROM images WHERE missing_at IS NOT NULL"
            ).fetchone()[0]
            live = conn.execute(
                "SELECT count(*) FROM images WHERE status = 'kept' AND missing_at IS NULL"
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertTrue(root.endswith("Personal Photos"), f"the source should follow: {root}")
        self.assertEqual(missing, 0, "a rename must not lose a single photo")
        self.assertEqual(live, 3)

    def test_an_unplugged_root_is_left_alone_rather_than_rebound(self):
        self.library.mkdir(parents=True, exist_ok=True)
        photos = [self._photo(f"Raws/2026/{n}.jpg") for n in range(3)]
        decoy = self.library / "Something Else"
        decoy.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                "INSERT INTO catalog_sources(path, display_name) VALUES (?, 'Raws')",
                (str(self.library / "Raws"),),
            )
            source_id = conn.execute(
                "SELECT id FROM catalog_sources WHERE path = ?", (str(self.library / "Raws"),)
            ).fetchone()[0]
            for path in photos:
                conn.execute(
                    "INSERT INTO images(source_id, filename, filepath, file_size, status) "
                    "VALUES (?, ?, ?, ?, 'kept')",
                    (source_id, path.name, str(path), path.stat().st_size),
                )
            conn.commit()
        finally:
            conn.close()

        shutil.rmtree(self.library / "Raws")
        asyncio.run(synchronize.reconcile_once(self.db_path))

        conn = sqlite3.connect(self.db_path)
        try:
            root = conn.execute(
                "SELECT path FROM catalog_sources WHERE id = ?", (source_id,)
            ).fetchone()[0]
            missing = conn.execute(
                "SELECT count(*) FROM images WHERE missing_at IS NOT NULL"
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertTrue(root.endswith("Raws"), "nothing matched, so the source must not move")
        self.assertEqual(missing, 0, "an absent root is not proof the photos are gone")


if __name__ == "__main__":
    unittest.main()
