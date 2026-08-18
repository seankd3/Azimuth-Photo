"""The core's refusals.

Not coverage. Every test here is either a bug this project already paid for
once, or a place where being wrong loses a photograph or a decision. Anything
that only restates what the code plainly says was deleted — a suite nobody
reads is a suite nobody runs.
"""

import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

import rank
import work
import model
from model import backup, cache, copies, decisions, drives, photos

TAIL = "Raws/Digital/2026/x.CR3"


class FreshCatalogTests(unittest.TestCase):
    def test_an_empty_file_is_the_whole_core(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "catalog.db")
            conn = model.connect(path)
            try:
                tables = {
                    row["name"]
                    for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    )
                }
                mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            finally:
                conn.close()

        self.assertEqual(tables, {"images", "drives", "copies", "decisions", "cache"})
        self.assertEqual(mode, "wal")


class CoreCase(unittest.TestCase):
    def setUp(self):
        self.conn = model.connect()
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
        self.assertIsNone(drives.root_of(self.conn, self.cold["uuid"]))
        drives.attach(self.conn, moved)
        self.assertEqual(drives.root_of(self.conn, self.cold["uuid"]), os.path.normpath(moved))
        # One row updated, not 144,000 paths rewritten.
        self.assertEqual(
            self.conn.execute("SELECT root FROM drives WHERE uuid = ?", (self.cold["uuid"],)).fetchone()["root"],
            os.path.normpath(moved),
        )

    def test_a_letter_reused_by_a_stranger_is_not_our_drive(self):
        shutil.rmtree(self.cold_root)
        os.makedirs(self.cold_root)  # same path, no marker: somebody else's disk
        self.assertIsNone(drives.root_of(self.conn, self.cold["uuid"]))

    def test_a_tail_is_posix_and_case_folded_but_keeps_the_disk_s_spelling(self):
        # Tails travel between drives; one carrying a backslash stops matching
        # the same photo on a drive that spells it with a slash.
        path = os.path.join(self.hot_root, "Raws", "Digital", "x.CR3")
        self.assertEqual(drives.tail_for(self.hot_root.upper(), path), "Raws/Digital/x.CR3")
        self.assertIsNone(drives.tail_for(self.cold_root, path))


class OpeningAPhoto(CoreCase):
    def test_a_copy_at_an_alternate_tail_can_be_opened(self):
        alternate = "Raws/2026/x-2.CR3"
        path = self.write(self.cold_root, alternate)
        image = self.photo(size=os.path.getsize(path))
        copies.saw(self.conn, image, int(self.cold["id"]), tail=alternate)
        self.conn.commit()
        self.assertEqual(photos.open_photo(self.conn, image), path)

    def test_the_working_disk_wins_and_the_archive_still_answers(self):
        cold = self.write(self.cold_root)
        image = self.photo(size=os.path.getsize(cold))
        self.assertEqual(photos.open_photo(self.conn, image), cold)
        hot = self.write(self.hot_root)
        self.assertEqual(photos.open_photo(self.conn, image), hot)

    def test_a_symlink_at_a_catalogued_tail_is_not_the_photograph(self):
        # A tail must resolve to a regular file on the drive it names. A
        # symlink points wherever it likes -- off the volume, at a file never
        # imported -- and os.stat follows it in silence, so the app would serve
        # bytes from a path no sweep ever walked.
        real = os.path.join(self.tmp, "elsewhere.CR3")
        with open(real, "wb") as handle:
            handle.write(b"the-photograph")
        link = os.path.join(self.cold_root, TAIL.replace("/", os.sep))
        os.makedirs(os.path.dirname(link), exist_ok=True)
        try:
            os.symlink(real, link)
        except (OSError, NotImplementedError):
            self.skipTest("this platform will not make symlinks unprivileged")
        self.assertIsNone(photos.open_photo(self.conn, self.photo(size=14)))

    def test_a_same_named_stranger_never_stands_in(self):
        self.write(self.cold_root, body=b"a different photograph entirely")
        self.assertIsNone(photos.open_photo(self.conn, self.photo(size=14)))

    def test_away_is_not_lost(self):
        # The sentence the whole design turns on. With the archive unplugged,
        # nothing can ever be declared missing -- no ratio, no override switch.
        path = self.write(self.cold_root)
        image = self.photo(size=os.path.getsize(path))
        shutil.rmtree(self.cold_root)
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


class SynchronizeRefuses(CoreCase):
    def test_an_away_drive_can_never_produce_a_removal_offer(self):
        # The sentence the whole design turns on, doing its job in the one
        # feature where being wrong deletes photographs. Lightroom's dialog
        # cannot make this distinction, which is why it can offer to remove
        # photographs that are simply on a disk you did not plug in.
        import synchronize

        path = self.write(self.cold_root)
        self.photo(size=os.path.getsize(path))
        shutil.rmtree(self.cold_root)

        found = synchronize.plan(self.conn, "")
        self.assertEqual(found["missing"], [])
        self.assertIn("away", found["note"])

    def test_a_walk_that_could_not_be_read_offers_nothing_either(self):
        # "I could not read the folder" and "the folder is empty" look
        # identical from here and are opposite facts.
        import synchronize

        self.write(self.cold_root)
        self.photo()
        with patch.object(copies, "walk_tails", return_value=(set(), False)):
            found = synchronize.plan(self.conn, "")
        self.assertEqual(found["missing"], [])


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
        self.assertEqual(backup.back_up(self.conn, image), "no record drive attached")

    def test_reclaim_frees_the_working_copy_and_never_the_archive_s(self):
        image, source = self._queued()
        backup.back_up(self.conn, image)
        self.assertEqual(backup.reclaim(self.conn, image), "freed")
        self.assertFalse(os.path.exists(source))
        self.assertTrue(os.path.exists(os.path.join(self.cold_root, TAIL.replace("/", os.sep))))

    def test_reclaim_refuses_on_a_stale_copy_row(self):
        # The row says the archive has it. The disk says otherwise, and the
        # disk is what a deletion has to answer to.
        image, source = self._queued()
        copies.saw(self.conn, image, int(self.cold["id"]))
        self.conn.commit()
        self.assertEqual(backup.reclaim(self.conn, image), "not archived")
        self.assertTrue(os.path.exists(source))

    def test_reclaim_refuses_when_the_two_copies_differ(self):
        image, source = self._queued()
        self.write(self.cold_root, body=b"not the same photograph at all")
        copies.saw(self.conn, image, int(self.cold["id"]))
        self.conn.commit()
        self.assertEqual(backup.reclaim(self.conn, image), "copies differ")
        self.assertTrue(os.path.exists(source))

    def test_reclaim_verifies_an_archive_copy_at_its_actual_tail(self):
        image, source = self._queued()
        alternate = "Raws/Digital/2026/x-2.CR3"
        self.write(self.cold_root, alternate)
        copies.saw(self.conn, image, int(self.cold["id"]), tail=alternate)
        self.conn.commit()
        self.assertEqual(backup.reclaim(self.conn, image), "freed")
        self.assertFalse(os.path.exists(source))
        self.assertTrue(os.path.exists(os.path.join(self.cold_root, alternate.replace("/", os.sep))))


class PuttingAFileDown(CoreCase):
    def test_put_writes_verifies_and_identifies(self):
        source = os.path.join(self.tmp, "card.CR3")
        with open(source, "wb") as handle:
            handle.write(b"straight-off-the-card")
        result = photos.put(self.conn, source, self.hot["uuid"], TAIL)
        self.assertEqual(result["outcome"], "written")
        self.assertEqual(open(result["path"], "rb").read(), b"straight-off-the-card")
        self.assertEqual(result["hash"], photos.content_hash(source))

    def test_put_never_overwrites_a_different_photograph(self):
        source = os.path.join(self.tmp, "card.CR3")
        with open(source, "wb") as handle:
            handle.write(b"straight-off-the-card")
        existing = self.write(self.hot_root, body=b"something already here")
        self.assertEqual(
            photos.put(self.conn, source, self.hot["uuid"], TAIL)["outcome"],
            "different file at that tail",
        )
        self.assertEqual(open(existing, "rb").read(), b"something already here")


class DecisionsSurvive(CoreCase):
    def test_your_decision_outweighs_later_imported_metadata(self):
        decisions.decide(self.conn, "abc", decisions.DEVELOP, {"crop": "mine"}, at=1, by="you")
        decisions.decide(self.conn, "abc", decisions.DEVELOP, {"crop": "file"}, at=2, by="file")
        self.assertEqual(decisions.latest(self.conn, "abc", decisions.DEVELOP), {"crop": "mine"})

    def test_partial_updates_preserve_fields_the_caller_did_not_send(self):
        decisions.decide(self.conn, "abc", decisions.DEVELOP, {"crop": 1, "masks": [2]})
        decisions.amend(self.conn, "abc", decisions.DEVELOP, {"crop": 3})
        self.assertEqual(
            decisions.latest(self.conn, "abc", decisions.DEVELOP),
            {"crop": 3, "masks": [2]},
        )

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
            compute=lambda source, hash, size=0: cache.Made(path=f"{source}@{size}", bytes=10),
            params=("size",),
        ))
        self.addCleanup(cache.unregister, "test_thumb")

    def test_a_recipe_refuses_anything_the_kind_did_not_declare(self):
        # This is "recipe is never a timestamp", made mechanical. Feeding
        # updated_at in is how reset-then-redo re-rendered identical pixels and
        # two machines never shared an entry.
        with self.assertRaises(ValueError):
            cache.canonical("test_thumb", {"size": 400, "updated_at": 1.0})

    def test_a_failure_is_recorded_once_with_why(self):
        cache.register(cache.Kind(name="boom", compute=lambda source, hash: 1 / 0))
        self.addCleanup(cache.unregister, "boom")
        self.assertIsNone(cache.make(self.conn, "hash1", "boom", "x.CR3"))
        stored = cache.get(self.conn, "hash1", "boom")
        self.assertEqual(stored["state"], cache.FAILED)
        self.assertIn("ZeroDivisionError", stored["note"])

    def test_a_machine_that_cannot_make_it_records_nothing(self):
        # "Not here" is a fact about this machine. Stored, it would poison the
        # entry for the helper that can make it.
        cache.register(cache.Kind(name="gpu", compute=lambda source, hash: cache.Made(), here=lambda: False))
        self.addCleanup(cache.unregister, "gpu")
        self.assertIsNone(cache.make(self.conn, "hash1", "gpu", "x.CR3"))
        self.assertIsNone(cache.get(self.conn, "hash1", "gpu"))

    def test_eviction_never_takes_what_it_cannot_remake_cheaply(self):
        cache.register(cache.Kind(name="embedding", compute=lambda source, hash: cache.Made(), evictable=False))
        self.addCleanup(cache.unregister, "embedding")
        cache.put(self.conn, "h1", "embedding", cache.Made(value=b"vector", bytes=3000))
        cache.put(self.conn, "h1", "test_thumb", cache.Made(path="/t.jpg", bytes=3000), {"size": 400})
        self.conn.commit()
        self.assertEqual(cache.evict(self.conn, 0), [("test_thumb", "/t.jpg")])
        self.assertIsNotNone(cache.get(self.conn, "h1", "embedding"))


class OwedIsAQuery(CoreCase):
    def setUp(self):
        super().setUp()
        cache.register(cache.Kind(name="thumb", compute=lambda source, hash: cache.Made(path="/t.jpg", bytes=1)))
        self.addCleanup(cache.unregister, "thumb")
        work.touched.__globals__["_last_touch"] = 0.0

    def _catalogued(self, tail=TAIL, *, hashed=True):
        path = self.write(self.hot_root, tail)
        self.conn.execute(
            "INSERT INTO images(tail, file_size, content_hash) VALUES (?, ?, ?)",
            (tail, os.path.getsize(path), tail if hashed else None),
        )
        self.conn.commit()
        return self.conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]

    def test_a_new_photo_is_owed_without_anything_enqueuing_it(self):
        image = self._catalogued()
        self.assertEqual([row["id"] for row in work.owed(self.conn, "thumb")], [image])

    def test_making_it_is_what_removes_it_from_the_queue(self):
        self._catalogued()
        row = work.owed(self.conn, "thumb")[0]
        cache.make(self.conn, row["hash"], "thumb", self.write(self.hot_root))
        self.assertEqual(work.owed(self.conn, "thumb"), [])

    def test_one_unreachable_photo_cannot_stall_the_queue(self):
        # This livelocked tile generation on the live library at 95 tiles with
        # 156,831 owed: the worker asked for one candidate, that candidate had
        # no tail so it could never be located, and the same row came back on
        # every pass forever.
        self.conn.execute(
            "INSERT INTO images(tail, file_size, content_hash, date_taken)"
            " VALUES (NULL, 1, 'no-tail', '2099-01-01')"
        )
        self.conn.commit()
        reachable = self._catalogued("Raws/reachable.CR3")
        self.assertNotIn("no-tail", [row["hash"] for row in work.owed(self.conn, "thumb")])
        self.assertEqual(work.step(self.conn, yield_to=lambda: False)["photo"], reachable)

    def test_a_failure_is_not_rediscovered_every_pass(self):
        cache.register(cache.Kind(name="boom", compute=lambda source, hash: 1 / 0))
        self.addCleanup(cache.unregister, "boom")
        self._catalogued()
        self.assertEqual(len(work.owed(self.conn, "boom")), 1)
        cache.make(self.conn, TAIL, "boom", "whatever")
        self.assertEqual(work.owed(self.conn, "boom"), [])

    def test_what_is_on_screen_is_served_first(self):
        self._catalogued("Raws/a.CR3")
        watching = self._catalogued("Raws/b.CR3")
        owed = work.owed(self.conn, "thumb", on_screen=[watching])
        self.assertEqual(owed[0]["id"], watching)

    def test_chores_keep_running_while_you_use_the_app_but_take_less_room(self):
        # Browsing was 23 ms with chores quiet and minutes with them running.
        # The fix is not to stop -- that idles the machine exactly when someone
        # is sitting at it -- but to take fewer workers and share nothing with
        # the request path.
        self._catalogued()
        work.touched()
        self.assertIsNotNone(work.step(self.conn))
        self.assertLess(work.workers(interactive=True), work.workers(interactive=False) + 1)
        self.assertGreaterEqual(work.workers(interactive=True), 1)

    def test_the_owner_can_stop_chores_and_it_survives_a_restart(self):
        # A preference is a decision, so it is a row in the log rather than a
        # flag -- a flag forgets itself exactly when someone who paused chores
        # to save battery would most mind them resuming.
        image = self._catalogued()
        work.set_paused(self.conn, True)
        self.assertIsNone(work.step(self.conn))
        self.assertTrue(work.paused(self.conn))
        work.set_paused(self.conn, False)
        self.assertIsNotNone(work.step(self.conn))

    def test_identity_is_owed_before_anything_keyed_on_it(self):
        image = self._catalogued(hashed=False)
        self.assertEqual(work.step(self.conn, yield_to=lambda: False), {"did": "identity", "photo": image})


class DecodingRefuses(unittest.TestCase):
    """Two properties carried over from the deleted thumbnail suite.

    Ported rather than dropped, because deleting a test file is how a guard
    goes quiet -- which happened once already today with the symlink refusal.
    """

    def test_raw_ness_is_decided_by_the_first_three_bytes(self):
        # This archive holds 1,306 files named .CR2 that are full-resolution
        # JPEGs. LibRaw refuses them as "not a raw file", so every branch taken
        # on the extension gets them wrong and they never get a tile.
        from photo import kind

        with tempfile.NamedTemporaryFile(suffix=".CR2", delete=False) as handle:
            handle.write(b"\xff\xd8\xff" + b"0" * 64)
            impostor = handle.name
        self.addCleanup(os.unlink, impostor)
        self.assertFalse(kind.is_raw(impostor))
        self.assertTrue(kind.is_raw(impostor, data=b"II*\x00"))

    def test_a_frame_too_big_to_afford_is_refused_not_clamped(self):
        # One 4.2 GB panorama charged at 768 MB because its weight was clamped
        # to the ceiling OOM-killed the service four times in an hour. A budget
        # that clamps has admitted a frame at a price it cannot pay.
        import render

        render._affordable(8192, 5464, render.GRID)  # a 45 MP frame at 400 px
        with self.assertRaises(render.TooBig):
            render._affordable(40000, 30000, 0)  # 1.2 Gpx at native size


class RankingIsDerived(CoreCase):
    def _round(self, winner, over, at):
        decisions.decide(self.conn, winner, decisions.COMPARE, {"over": list(over)}, at=at)

    def test_the_same_rounds_in_any_order_give_the_same_answer(self):
        # The whole reason strength is fitted rather than folded. Elo is
        # path-dependent by construction, and this archive proved what that
        # costs: replaying its own ledger matched the stored numbers exactly
        # for 41 comparisons and then diverged for good. A fit cannot do that.
        import random
        pairs = [(f"w{i}", [f"l{i}"]) for i in range(12)] + [("w0", ["w1"]), ("w2", ["w0"])]
        for at, (winner, over) in enumerate(pairs):
            self._round(winner, over, 1.0 + at)
        first = rank.strength(self.conn)

        shuffled = list(self.conn.execute(
            "SELECT subject, value FROM decisions WHERE family = 'compare'").fetchall())
        random.Random(11).shuffle(shuffled)

        class Reordered:
            def execute(self, *args, **kwargs):
                return shuffled

        again = rank.strength(Reordered())
        for photo, score in first.items():
            self.assertAlmostEqual(score, again[photo], places=9)

    def test_beating_eleven_says_more_than_beating_one(self):
        # Set size is not a mode, it is evidence: the model reads a round as
        # one softmax over whatever was on screen, so a grid and a duel are the
        # same statement at different strengths.
        self._round("grid", [f"other{i}" for i in range(11)], 1.0)
        self._round("duel", ["someone"], 2.0)
        scores = rank.strength(self.conn)
        self.assertGreater(scores["grid"], scores["duel"])

    def test_a_photo_that_only_ever_won_does_not_run_away(self):
        # Its likelihood is unbounded -- nothing in the data pulls it back --
        # so the prior is the only thing standing between one lucky frame and
        # the top of the library.
        for at in range(30):
            self._round("lucky", [f"other{at}"], 1.0 + at)
        scores = rank.strength(self.conn)
        self.assertLess(scores["lucky"], rank.BASE + 6 * rank.SPREAD)

    def test_your_verdict_outweighs_the_prediction_once_you_have_looked(self):
        # The guarantee the old MAX_DIRECT_COMPARISONS cap was trying to buy,
        # now a weight rather than a threshold: a photograph you have judged
        # many times is almost entirely its own strength, whatever the
        # direction thinks of it.
        import numpy as np
        import taste
        for at in range(40):
            self._round("often", [f"other{at}"], 1.0 + at)
        measured = rank.strength(self.conn)
        seen = rank.seen(self.conn)
        subjects = sorted(measured)
        vectors = np.eye(len(subjects), dtype=np.float64)[:, :8]
        out = taste.scores(measured, seen, subjects, vectors)
        drift = abs(out["often"] - measured["often"])
        self.assertLess(drift, abs(measured["often"] - rank.BASE) * 0.25)

    def test_a_photograph_you_never_judged_still_gets_a_score(self):
        # What the neighbour propagation could not do: a photograph far from
        # everything judged got nothing and sat at base forever. A direction is
        # a function of the vector, so coverage is total the moment it has one.
        import numpy as np
        import taste
        for i in range(20):
            self._round(f"win{i}", [f"lose{i}"], 1.0 + i)
        measured = rank.strength(self.conn)
        subjects = sorted(measured) + ["stranger"]
        rng = np.random.default_rng(3)
        vectors = rng.normal(size=(len(subjects), 6))
        vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
        out = taste.scores(measured, rank.seen(self.conn), subjects, vectors)
        self.assertIn("stranger", out)
        self.assertTrue(np.isfinite(out["stranger"]))

    def test_with_no_vectors_it_is_the_fit_not_a_failure(self):
        # Ranking works at zero embedding coverage and sharpens as they land.
        self._round("a", ["b"], 1.0)
        self.assertEqual(rank.ranking(self.conn), rank.strength(self.conn))


if __name__ == "__main__":
    unittest.main()
