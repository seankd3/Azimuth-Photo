"""Opening a photo, and the difference between away and lost."""

import os
import shutil
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from model import drives, photos


class OpenTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        schema = os.path.join(os.path.dirname(drives.__file__), "schema.sql")
        with open(schema, encoding="utf-8") as handle:
            self.conn.executescript(handle.read())
        self.conn.executescript(
            "CREATE TABLE images (id INTEGER PRIMARY KEY, tail TEXT, file_size INTEGER)"
        )
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.addCleanup(self.conn.close)

        self.working = self._drive("Working", is_record=False)
        self.archive = self._drive("Archive", is_record=True)

    def _drive(self, name, *, is_record):
        root = os.path.join(self.tmp, name)
        os.makedirs(root, exist_ok=True)
        return drives.attach(self.conn, root, label=name, is_record=is_record), root

    def _write(self, root, tail, body=b"photo-bytes"):
        path = os.path.join(root, tail.replace("/", os.sep))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(body)
        return path

    def _photo(self, tail, size):
        self.conn.execute("INSERT INTO images(tail, file_size) VALUES (?, ?)", (tail, size))
        self.conn.commit()
        return self.conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]

    TAIL = "Raws/Digital/2026/x.CR3"

    def test_it_opens_from_the_archive_when_that_is_the_only_copy(self):
        path = self._write(self.archive[1], self.TAIL)
        image = self._photo(self.TAIL, os.path.getsize(path))
        self.assertEqual(photos.open_photo(self.conn, image), path)
        self.assertEqual(photos.state(self.conn, image), "available")

    def test_the_working_disk_wins_when_both_drives_have_it(self):
        self._write(self.archive[1], self.TAIL)
        hot = self._write(self.working[1], self.TAIL)
        image = self._photo(self.TAIL, os.path.getsize(hot))
        self.assertEqual(photos.open_photo(self.conn, image), hot)

    def test_a_photo_moved_between_drives_keeps_opening(self):
        cold = self._write(self.archive[1], self.TAIL)
        image = self._photo(self.TAIL, os.path.getsize(cold))
        self.assertEqual(photos.open_photo(self.conn, image), cold)

        # Moved to the working disk. The catalog is not told, and does not need to be.
        hot = self._write(self.working[1], self.TAIL)
        os.remove(cold)
        self.assertEqual(photos.open_photo(self.conn, image), hot)

    def test_a_same_named_stranger_never_stands_in(self):
        self._write(self.archive[1], self.TAIL, body=b"a different photograph entirely")
        image = self._photo(self.TAIL, 11)  # the size the catalog recorded
        self.assertIsNone(photos.open_photo(self.conn, image))

    def test_an_unplugged_archive_makes_a_photo_away_not_lost(self):
        path = self._write(self.archive[1], self.TAIL)
        image = self._photo(self.TAIL, os.path.getsize(path))
        shutil.rmtree(self.archive[1])

        with patch.object(drives, "_candidate_roots", return_value=[]):
            self.assertIsNone(photos.open_photo(self.conn, image))
            self.assertEqual(photos.state(self.conn, image), "away")

    def test_lost_requires_every_drive_to_be_present_and_empty_handed(self):
        path = self._write(self.working[1], self.TAIL)
        image = self._photo(self.TAIL, os.path.getsize(path))
        os.remove(path)

        # Both drives attached, neither has it — only now is it lost.
        self.assertEqual(photos.state(self.conn, image), "lost")

    def test_a_photo_with_no_tail_is_not_openable(self):
        image = self._photo(None, 11)
        self.assertIsNone(photos.open_photo(self.conn, image))


if __name__ == "__main__":
    unittest.main()
