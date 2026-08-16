"""Sweeping a drive, and the ways a sweep must refuse to act."""

import os
import shutil
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from model import copies, drives


class SweepTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        schema = os.path.join(os.path.dirname(drives.__file__), "schema.sql")
        with open(schema, encoding="utf-8") as handle:
            self.conn.executescript(handle.read())
        self.conn.execute("CREATE TABLE images (id INTEGER PRIMARY KEY, tail TEXT, vc_of INTEGER)")
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.addCleanup(self.conn.close)

        self.root = os.path.join(self.tmp, "Photos")
        os.makedirs(self.root)
        self.drive = drives.attach(self.conn, self.root, label="Archive", is_record=True)

    def _file(self, tail):
        path = os.path.join(self.root, tail.replace("/", os.sep))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(b"bytes")
        return path

    def _photo(self, tail):
        self.conn.execute("INSERT INTO images(tail) VALUES (?)", (tail,))
        self.conn.commit()
        return self.conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]

    def test_a_sweep_records_what_is_there(self):
        self._file("Raws/a.CR3")
        self._file("Raws/b.CR3")
        a = self._photo("Raws/a.CR3")
        self._photo("Raws/zzz.CR3")  # catalogued, not on this drive

        result = copies.sweep(self.conn, self.drive["uuid"])
        self.assertTrue(result["applied"])
        self.assertEqual(result["files_seen"], 2)
        self.assertEqual(result["copies_recorded"], 1)
        self.assertEqual(result["unknown_files"], 1)
        self.assertTrue(copies.is_backed_up(self.conn, a))

    def test_a_deleted_file_retires_its_copy_but_not_the_photo(self):
        path = self._file("Raws/a.CR3")
        a = self._photo("Raws/a.CR3")
        copies.sweep(self.conn, self.drive["uuid"])
        self.assertTrue(copies.is_backed_up(self.conn, a))

        os.remove(path)
        result = copies.sweep(self.conn, self.drive["uuid"])
        self.assertEqual(result["copies_retired"], 1)
        self.assertFalse(copies.is_backed_up(self.conn, a))
        # The photograph itself is untouched. Only the hint went.
        self.assertIsNotNone(self.conn.execute("SELECT 1 FROM images WHERE id = ?", (a,)).fetchone())

    def test_a_sweep_never_descends_into_trash(self):
        self._file(".trash/Raws/a.CR3")
        a = self._photo(".trash/Raws/a.CR3")
        result = copies.sweep(self.conn, self.drive["uuid"])
        self.assertEqual(result["files_seen"], 0)
        self.assertFalse(copies.is_backed_up(self.conn, a))

    def test_a_sweep_never_descends_into_astrophotography(self):
        self._file("Astrophotography/m31.CR3")
        self._file("Raws/a.CR3")
        result = copies.sweep(self.conn, self.drive["uuid"])
        self.assertEqual(result["files_seen"], 1)

    def test_an_incomplete_walk_changes_nothing(self):
        self._file("Raws/a.CR3")
        a = self._photo("Raws/a.CR3")
        copies.sweep(self.conn, self.drive["uuid"])

        os.remove(os.path.join(self.root, "Raws", "a.CR3"))
        with patch.object(copies, "walk_tails", return_value=(set(), False)):
            result = copies.sweep(self.conn, self.drive["uuid"])
        self.assertFalse(result["applied"])
        self.assertEqual(result["reason"], "walk incomplete")
        # The copy survives, because we never proved it was gone.
        self.assertTrue(copies.is_backed_up(self.conn, a))

    def test_a_drive_that_vanishes_mid_sweep_changes_nothing(self):
        self._file("Raws/a.CR3")
        a = self._photo("Raws/a.CR3")
        copies.sweep(self.conn, self.drive["uuid"])

        # The drive answers while it is being located, then is something else
        # by the time the pass re-reads the marker to check its own footing.
        with patch.object(
            copies.drives, "read_marker",
            side_effect=[self.drive["uuid"], "a-different-drive"],
        ):
            result = copies.sweep(self.conn, self.drive["uuid"])
        self.assertFalse(result["applied"])
        self.assertTrue(copies.is_backed_up(self.conn, a))

    def test_an_unattached_drive_is_not_an_error(self):
        shutil.rmtree(self.root)
        with patch.object(drives, "_candidate_roots", return_value=[]):
            result = copies.sweep(self.conn, self.drive["uuid"])
        self.assertFalse(result["applied"])
        self.assertEqual(result["reason"], "not attached")

    def test_moving_a_photo_between_folders_is_not_an_event(self):
        self._file("Raws/2026/a.CR3")
        a = self._photo("Raws/2026/a.CR3")
        copies.sweep(self.conn, self.drive["uuid"])

        # The owner reorganises. The catalog's tail is now stale, so the copy
        # retires -- and nothing anywhere reports a rename.
        os.makedirs(os.path.join(self.root, "Raws", "2026-07"), exist_ok=True)
        shutil.move(
            os.path.join(self.root, "Raws", "2026", "a.CR3"),
            os.path.join(self.root, "Raws", "2026-07", "a.CR3"),
        )
        result = copies.sweep(self.conn, self.drive["uuid"])
        self.assertTrue(result["applied"])
        self.assertEqual(result["copies_retired"], 1)
        self.assertEqual(result["unknown_files"], 1)
        self.assertIsNotNone(self.conn.execute("SELECT 1 FROM images WHERE id = ?", (a,)).fetchone())


if __name__ == "__main__":
    unittest.main()
