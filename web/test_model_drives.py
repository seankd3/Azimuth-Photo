"""A drive is named by its marker, not by its letter.

The test that matters is the last one: a drive that comes back somewhere else
is the same drive, and finding it costs one row rather than every path in the
library.
"""

import os
import shutil
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from model import drives


def _catalog():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    schema = os.path.join(os.path.dirname(drives.__file__), "schema.sql")
    with open(schema, encoding="utf-8") as handle:
        conn.executescript(handle.read())
    return conn


class DriveTests(unittest.TestCase):
    def setUp(self):
        self.conn = _catalog()
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.addCleanup(self.conn.close)

    def _root(self, name):
        path = os.path.join(self.tmp, name)
        os.makedirs(path, exist_ok=True)
        return path

    def test_attaching_a_root_names_it_and_leaves_a_marker(self):
        root = self._root("Pictures")
        drive = drives.attach(self.conn, root, label="Working", is_record=False)

        self.assertTrue(drive["uuid"])
        self.assertEqual(drive["label"], "Working")
        self.assertEqual(drive["is_record"], 0)
        self.assertEqual(drives.read_marker(root), drive["uuid"])

    def test_attaching_the_same_root_twice_is_the_same_drive(self):
        root = self._root("Pictures")
        first = drives.attach(self.conn, root)
        second = drives.attach(self.conn, root)

        self.assertEqual(first["uuid"], second["uuid"])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM drives").fetchone()[0], 1)

    def test_a_marker_is_never_stolen_by_another_drive(self):
        root = self._root("Pictures")
        drives.attach(self.conn, root)
        with self.assertRaises(ValueError):
            drives.write_marker(root, "some-other-drive")

    def test_a_path_is_a_drive_plus_a_tail(self):
        root = self._root("Photos")
        drive = drives.attach(self.conn, root, is_record=True)

        resolved = drives.path_for(self.conn, drive["uuid"], "Raws/Digital/2026/x.CR3")
        self.assertEqual(resolved, os.path.join(root, "Raws", "Digital", "2026", "x.CR3"))

    def test_an_absent_drive_is_offline_not_an_error(self):
        root = self._root("Photos")
        drive = drives.attach(self.conn, root)
        shutil.rmtree(root)

        with patch.object(drives, "_candidate_roots", return_value=[]):
            self.assertFalse(drives.online(self.conn, drive["uuid"]))
            self.assertIsNone(drives.path_for(self.conn, drive["uuid"], "Raws/x.CR3"))

    def test_a_drive_that_comes_back_on_a_different_letter_is_the_same_drive(self):
        old = self._root("E_Photos")
        drive = drives.attach(self.conn, old, is_record=True)
        uuid = drive["uuid"]

        # The volume reappears somewhere else entirely — a new letter, a new mount.
        new = os.path.join(self.tmp, "F_Photos")
        shutil.move(old, new)

        with patch.object(drives, "_candidate_roots", return_value=[self.tmp]):
            found = drives.root_of(self.conn, uuid)

        self.assertEqual(found, os.path.normpath(new))
        self.assertTrue(drives.online(self.conn, uuid))
        # The row followed the drive, and there is still only one of it.
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM drives").fetchone()[0], 1)
        self.assertEqual(
            drives.path_for(self.conn, uuid, "Raws/x.CR3"),
            os.path.join(new, "Raws", "x.CR3"),
        )

    def test_a_letter_reused_by_a_stranger_is_not_our_drive(self):
        ours = self._root("Ours")
        drive = drives.attach(self.conn, ours, is_record=True)
        uuid = drive["uuid"]

        # Something else now sits where our drive used to be.
        shutil.rmtree(ours)
        os.makedirs(ours)
        stranger = self._root("Stranger")
        drives.attach(self.conn, stranger)

        with patch.object(drives, "_candidate_roots", return_value=[self.tmp]):
            self.assertIsNone(drives.root_of(self.conn, uuid))


if __name__ == "__main__":
    unittest.main()
