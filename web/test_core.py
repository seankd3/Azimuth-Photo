"""The core's refusals.

Not coverage. Every test here is either a bug this project already paid for
once, or a place where being wrong loses a photograph or a decision. Anything
that only restates what the code plainly says was deleted — a suite nobody
reads is a suite nobody runs.
"""

import os
import shutil
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from model import backup, cache, copies, decisions, drives, photos

TAIL = "Raws/Digital/2026/x.CR3"


class CoreCase(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        with open(os.path.join(os.path.dirname(drives.__file__), "schema.sql"), encoding="utf-8") as handle:
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

    def write(self, root, tail=TAIL, body=b"the-photograph"):
        path = os.path.join(root, tail.replace("/", os.sep))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(body)
        return path

    def photo(self, tail=TAIL, size=None):
        self.conn.execute("INSERT INTO images(tail, file_size) VALUES (?, ?)", (tail, size))
        self.conn.commit()
        return self.conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]


class DrivesAreNotLetters(CoreCase):
    def test_a_marker_is_never_stolen(self):
        # Two drives answering to one uuid makes every copy row ambiguous, and
        # the failure looks like a photo that moved on its own.
        with self.assertRaises(ValueError):
            drives.write_marker(self.cold_root, "some-other-drive")

    def test_a_drive_back_on_a_different_letter_is_the_same_drive(self):
        moved = os.path.join(self.tmp, "Elsewhere")
        shutil.move(self.cold_root, moved)
        with patch.object(drives, "_candidate_roots", return_value=[self.tmp]):
            self.assertEqual(drives.root_of(self.conn, self.cold["uuid"]), os.path.normpath(moved))
        # One row updated, not 144,000 paths rewritten.
        self.assertEqual(
            self.conn.execute("SELECT root FROM drives WHERE uuid = ?", (self.cold["uuid"],)).fetchone()["root"],
            os.path.normpath(moved),
        )

    def test_a_letter_reused_by_a_stranger_is_not_our_drive(self):
        shutil.rmtree(self.cold_root)
        os.makedirs(self.cold_root)  # same path, no marker: somebody else's disk
        with patch.object(drives, "_candidate_roots", return_value=[]):
            self.assertIsNone(drives.root_of(self.conn, self.cold["uuid"]))

    def test_a_tail_is_posix_and_case_folded_but_keeps_the_disk_s_spelling(self):
        # Tails travel between drives; one carrying a backslash stops matching
        # the same photo on a drive that spells it with a slash.
        path = os.path.join(self.hot_root, "Raws", "Digital", "x.CR3")
        self.assertEqual(drives.tail_for(self.hot_root.upper(), path), "Raws/Digital/x.CR3")
        self.assertIsNone(drives.tail_for(self.cold_root, path))


class OpeningAPhoto(CoreCase):
    def test_the_working_disk_wins_and_the_archive_still_answers(self):
        cold = self.write(self.cold_root)
        image = self.photo(size=os.path.getsize(cold))
        self.assertEqual(photos.open_photo(self.conn, image), cold)
        hot = self.write(self.hot_root)
        self.assertEqual(photos.open_photo(self.conn, image), hot)

    def test_a_same_named_stranger_never_stands_in(self):
        self.write(self.cold_root, body=b"a different photograph entirely")
        self.assertIsNone(photos.open_photo(self.conn, self.photo(size=14)))

    def test_away_is_not_lost(self):
        # The sentence the whole design turns on. With the archive unplugged,
        # nothing can ever be declared missing -- no ratio, no override switch.
        path = self.write(self.cold_root)
        image = self.photo(size=os.path.getsize(path))
        shutil.rmtree(self.cold_root)
        with patch.object(drives, "_candidate_roots", return_value=[]):
            self.assertEqual(photos.state(self.conn, image), "away")

        # Only with every drive attached and empty-handed is it lost.
        os.makedirs(self.cold_root)
        drives.write_marker(self.cold_root, self.cold["uuid"])
        self.assertEqual(photos.state(self.conn, image), "lost")


class SweepingRefuses(CoreCase):
    def test_an_incomplete_walk_changes_nothing(self):
        # "I could not read the folder" and "the folder is empty" are the same
        # sight from inside a scan and opposite facts.
        self.write(self.cold_root)
        image = self.photo()
        copies.saw(self.conn, image, int(self.cold["id"]))
        self.conn.commit()
        with patch.object(copies, "walk_tails", return_value=(set(), False)):
            self.assertFalse(copies.sweep(self.conn, self.cold["uuid"])["applied"])
        self.assertTrue(copies.drives_holding(self.conn, image))

    def test_a_drive_that_vanishes_mid_sweep_changes_nothing(self):
        self.write(self.cold_root)
        image = self.photo()
        copies.saw(self.conn, image, int(self.cold["id"]))
        self.conn.commit()
        real = copies.walk_tails

        def pull_the_plug(root):
            seen = real(root)
            os.remove(os.path.join(root, drives.MARKER_NAME))
            return seen

        with patch.object(copies, "walk_tails", side_effect=pull_the_plug):
            self.assertFalse(copies.sweep(self.conn, self.cold["uuid"])["applied"])
        self.assertTrue(copies.drives_holding(self.conn, image))

    def test_a_sweep_never_resurrects_the_trash_or_reads_astrophotography(self):
        # Without .trash in the skip list, "a file at a known tail gets a copy
        # row" quietly un-trashes every photo ever thrown away.
        self.write(self.cold_root, ".trash/gone.CR3")
        self.write(self.cold_root, "Astrophotography/m31.CR3")
        thrown = self.photo(".trash/gone.CR3")
        fenced = self.photo("Astrophotography/m31.CR3")
        copies.sweep(self.conn, self.cold["uuid"])
        self.assertEqual(copies.drives_holding(self.conn, thrown), [])
        self.assertEqual(copies.drives_holding(self.conn, fenced), [])

    def test_moving_a_photo_between_folders_is_not_an_event(self):
        # Move detection stopped needing code: a sweep sees the new tail, the
        # catalog's tail is what it is, and nothing had to notice a rename.
        self.write(self.cold_root, "Raws/2026/moved.CR3")
        image = self.photo("Raws/2026/moved.CR3")
        copies.sweep(self.conn, self.cold["uuid"])
        self.assertTrue(copies.drives_holding(self.conn, image))


class BackupRefuses(CoreCase):
    def _queued(self):
        source = self.write(self.hot_root)
        image = self.photo(size=os.path.getsize(source))
        copies.saw(self.conn, image, int(self.hot["id"]))
        self.conn.commit()
        return image, source

    def test_it_copies_verifies_and_never_touches_the_source(self):
        image, source = self._queued()
        self.assertEqual(backup.back_up(self.conn, image), "copied")
        target = os.path.join(self.cold_root, TAIL.replace("/", os.sep))
        self.assertEqual(open(target, "rb").read(), b"the-photograph")
        self.assertEqual(open(source, "rb").read(), b"the-photograph")
        self.assertTrue(copies.is_backed_up(self.conn, image))

    def test_it_refuses_when_a_different_file_holds_that_tail(self):
        image, _ = self._queued()
        stranger = self.write(self.cold_root, body=b"a completely different photograph")
        self.assertEqual(backup.back_up(self.conn, image), "different file at that tail")
        self.assertEqual(open(stranger, "rb").read(), b"a completely different photograph")
        self.assertFalse(copies.is_backed_up(self.conn, image))

    def test_a_failed_verify_leaves_nothing_behind(self):
        image, _ = self._queued()
        real = backup.digest
        with patch.object(backup, "digest", side_effect=lambda p: "0" * 32 if p.endswith(".copying") else real(p)):
            self.assertEqual(backup.back_up(self.conn, image), "verify failed")
        target = os.path.join(self.cold_root, TAIL.replace("/", os.sep))
        self.assertFalse(os.path.exists(target))
        self.assertFalse(os.path.exists(target + ".copying"))
        self.assertFalse(copies.is_backed_up(self.conn, image))

    def test_no_record_drive_is_a_refusal_not_a_crash(self):
        image, _ = self._queued()
        shutil.rmtree(self.cold_root)
        with patch.object(drives, "_candidate_roots", return_value=[]):
            self.assertEqual(backup.back_up(self.conn, image), "no record drive attached")


class DecisionsSurvive(CoreCase):
    def test_two_decisions_in_one_instant_keep_their_order(self):
        # A millisecond clock hands out the same timestamp twice under a fast
        # keyboard; `at` alone would make the answer a coin toss.
        decisions.decide(self.conn, "abc", decisions.STATUS, "maybe", at=7.0)
        decisions.decide(self.conn, "abc", decisions.STATUS, "kept", at=7.0)
        self.assertEqual(decisions.latest(self.conn, "abc", decisions.STATUS), "kept")

    def test_undo_appends_so_the_log_never_lies(self):
        decisions.decide(self.conn, "abc", decisions.STATUS, "kept", at=1.0)
        decisions.decide(self.conn, "abc", decisions.STATUS, "trashed", at=2.0)
        self.assertEqual(decisions.undo(self.conn, "abc", decisions.STATUS), "kept")
        self.assertEqual(len(decisions.history(self.conn, "abc")), 3)

    def test_a_subject_is_any_stable_identity(self):
        # What killed the all-zeros fake content hash a collection needed in
        # order to be a row it could decide about.
        decisions.decide(self.conn, "Raws/Digital/2026", decisions.NAME, "Iceland")
        self.assertEqual(decisions.latest(self.conn, "Raws/Digital/2026", decisions.NAME), "Iceland")


class CacheRefuses(CoreCase):
    def setUp(self):
        super().setUp()
        self.kind = cache.register(cache.Kind(
            name="test_thumb",
            compute=lambda source, size=0: cache.Made(path=f"{source}@{size}", bytes=10),
            params=("size",),
        ))
        self.addCleanup(cache.kinds().pop, "test_thumb", None)

    def test_a_recipe_refuses_anything_the_kind_did_not_declare(self):
        # This is "recipe is never a timestamp", made mechanical. Feeding
        # updated_at in is how reset-then-redo re-rendered identical pixels and
        # two machines never shared an entry.
        with self.assertRaises(ValueError):
            cache.canonical("test_thumb", {"size": 400, "updated_at": 1.0})

    def test_a_failure_is_recorded_once_with_why(self):
        cache.register(cache.Kind(name="boom", compute=lambda source: 1 / 0))
        self.addCleanup(cache.kinds().pop, "boom", None)
        self.assertIsNone(cache.make(self.conn, "hash1", "boom", "x.CR3"))
        stored = cache.get(self.conn, "hash1", "boom")
        self.assertEqual(stored["state"], cache.FAILED)
        self.assertIn("ZeroDivisionError", stored["note"])

    def test_a_machine_that_cannot_make_it_records_nothing(self):
        # "Not here" is a fact about this machine. Stored, it would poison the
        # entry for the helper that can make it.
        cache.register(cache.Kind(name="gpu", compute=lambda source: cache.Made(), here=lambda: False))
        self.addCleanup(cache.kinds().pop, "gpu", None)
        self.assertIsNone(cache.make(self.conn, "hash1", "gpu", "x.CR3"))
        self.assertIsNone(cache.get(self.conn, "hash1", "gpu"))

    def test_eviction_never_takes_what_it_cannot_remake_cheaply(self):
        cache.register(cache.Kind(name="embedding", compute=lambda source: cache.Made(), evictable=False))
        self.addCleanup(cache.kinds().pop, "embedding", None)
        cache.put(self.conn, "h1", "embedding", cache.Made(value=b"vector", bytes=3000))
        cache.put(self.conn, "h1", "test_thumb", cache.Made(path="/t.jpg", bytes=3000), {"size": 400})
        self.conn.commit()
        self.assertEqual(cache.evict(self.conn, 0), ["/t.jpg"])
        self.assertIsNotNone(cache.get(self.conn, "h1", "embedding"))


if __name__ == "__main__":
    unittest.main()
