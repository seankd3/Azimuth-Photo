"""Backing a photo up, and every way it must refuse."""

import os
import shutil
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from model import backup, copies, drives


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        schema = os.path.join(os.path.dirname(drives.__file__), "schema.sql")
        with open(schema, encoding="utf-8") as handle:
            self.conn.executescript(handle.read())
        self.conn.execute(
            "CREATE TABLE images (id INTEGER PRIMARY KEY, tail TEXT, file_size INTEGER, vc_of INTEGER)"
        )
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.addCleanup(self.conn.close)

        self.hot_root = os.path.join(self.tmp, "Working")
        self.cold_root = os.path.join(self.tmp, "Archive")
        os.makedirs(self.hot_root)
        os.makedirs(self.cold_root)
        self.hot = drives.attach(self.conn, self.hot_root, label="Working", is_record=False)
        self.cold = drives.attach(self.conn, self.cold_root, label="Archive", is_record=True)

    TAIL = "Raws/Digital/2026/x.CR3"

    def _photo(self, tail=TAIL, body=b"the-photograph"):
        path = os.path.join(self.hot_root, tail.replace("/", os.sep))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(body)
        self.conn.execute(
            "INSERT INTO images(tail, file_size) VALUES (?, ?)", (tail, os.path.getsize(path))
        )
        image_id = self.conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
        copies.saw(self.conn, image_id, int(self.hot["id"]))
        self.conn.commit()
        return image_id, path

    def _cold_path(self, tail=TAIL):
        return os.path.join(self.cold_root, tail.replace("/", os.sep))

    def test_it_copies_verifies_and_records(self):
        image, source = self._photo()
        self.assertEqual(backup.back_up(self.conn, image), "copied")

        target = self._cold_path()
        self.assertTrue(os.path.exists(target))
        self.assertEqual(open(target, "rb").read(), open(source, "rb").read())
        self.assertTrue(copies.is_backed_up(self.conn, image))

    def test_the_source_is_never_touched(self):
        image, source = self._photo()
        backup.back_up(self.conn, image)
        self.assertTrue(os.path.exists(source))
        self.assertEqual(open(source, "rb").read(), b"the-photograph")

    def test_a_queue_of_one_becomes_a_queue_of_none(self):
        image, _ = self._photo()
        self.assertEqual([p["id"] for p in backup.unprotected(self.conn)], [image])
        backup.back_up_all(self.conn)
        self.assertEqual(backup.unprotected(self.conn), [])

    def test_it_refuses_when_a_different_file_holds_that_tail(self):
        image, _ = self._photo()
        target = self._cold_path()
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "wb") as handle:
            handle.write(b"a completely different photograph")

        self.assertEqual(backup.back_up(self.conn, image), "different file at that tail")
        # The stranger is left exactly as it was, and the photo stays unprotected.
        self.assertEqual(open(target, "rb").read(), b"a completely different photograph")
        self.assertFalse(copies.is_backed_up(self.conn, image))

    def test_an_identical_file_already_there_counts_as_protected(self):
        image, source = self._photo()
        target = self._cold_path()
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copy2(source, target)

        self.assertEqual(backup.back_up(self.conn, image), "already there")
        self.assertTrue(copies.is_backed_up(self.conn, image))

    def test_a_failed_verify_leaves_nothing_behind(self):
        image, _ = self._photo()
        real = backup.digest

        def wrong(path):
            return "0" * 32 if path.endswith(".copying") else real(path)

        with patch.object(backup, "digest", side_effect=wrong):
            self.assertEqual(backup.back_up(self.conn, image), "verify failed")

        self.assertFalse(os.path.exists(self._cold_path()))
        self.assertFalse(os.path.exists(self._cold_path() + ".copying"))
        self.assertFalse(copies.is_backed_up(self.conn, image))

    def test_no_record_drive_is_a_refusal_not_a_crash(self):
        image, _ = self._photo()
        shutil.rmtree(self.cold_root)
        with patch.object(drives, "_candidate_roots", return_value=[]):
            self.assertEqual(backup.back_up(self.conn, image), "no record drive attached")
        self.assertFalse(copies.is_backed_up(self.conn, image))

    def test_a_photo_already_on_the_archive_is_not_queued(self):
        image, _ = self._photo()
        copies.saw(self.conn, image, int(self.cold["id"]))
        self.conn.commit()
        self.assertEqual(backup.unprotected(self.conn), [])

    def test_dry_run_writes_nothing(self):
        image, _ = self._photo()
        result = backup.back_up_all(self.conn, dry_run=True)
        self.assertEqual(result["outcomes"], {"would copy": 1})
        self.assertFalse(os.path.exists(self._cold_path()))
        self.assertFalse(copies.is_backed_up(self.conn, image))


if __name__ == "__main__":
    unittest.main()
