"""The core's refusals.

Not coverage. Every test here is either a bug this project already paid for
once, or a place where being wrong loses a photograph or a decision. Anything
that only restates what the code plainly says was deleted — a suite nobody
reads is a suite nobody runs.
"""

import os
from pathlib import Path
import shutil
import struct
import tempfile
import unittest
from unittest.mock import patch

import rank
import render
import work
import model
import tiles
import boot
import library as library_surface
import metadata as embedded_metadata
from PIL import Image
from model import backup, cache, copies, decisions, drives, photos, scope, sets
from photo import exif as raw_exif

TAIL = "Raws/Digital/2026/x.CR3"


class FreshCatalogTests(unittest.TestCase):
    def test_a_sweep_publishes_real_photos_one_bounded_batch_at_a_time(self):
        with tempfile.TemporaryDirectory() as directory:
            catalog = os.path.join(directory, "catalog.db")
            photo_root = os.path.join(directory, "Photos")
            os.makedirs(photo_root)
            for name, color in (("one.jpg", "navy"), ("two.jpg", "gold")):
                Image.new("RGB", (32, 24), color).save(
                    os.path.join(photo_root, name), "JPEG"
                )
            writer = model.connect(catalog)
            observer = model.connect(catalog)
            try:
                drive = drives.attach(writer, photo_root)
                visible = []
                result = copies.sweep(
                    writer,
                    drive["uuid"],
                    batch_size=1,
                    progress=lambda _status: visible.append(
                        observer.execute("SELECT COUNT(*) FROM images").fetchone()[0]
                    ),
                )
            finally:
                observer.close()
                writer.close()

        self.assertEqual(visible, [1, 2])
        self.assertEqual(result["photos_added"], 2)

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

    def test_a_fresh_drive_becomes_a_browseable_tiled_library(self):
        with tempfile.TemporaryDirectory() as directory:
            catalog = os.path.join(directory, "catalog.db")
            drive_root = os.path.join(directory, "Photos")
            tile_root = os.path.join(directory, "Tiles")
            source = os.path.join(drive_root, "Trips", "lake.jpg")
            os.makedirs(os.path.dirname(source))
            Image.new("RGB", (800, 600), "navy").save(source, "JPEG")

            conn = model.connect(catalog)
            try:
                drive = drives.attach(conn, drive_root, label="Photos")
                swept = copies.sweep(conn, drive["uuid"])
                listed = library_surface.photos(conn)
                store = tiles.Store(tile_root)

                identified = work.step(conn, (store.kind,), yield_to=lambda: False)
                made = work.step(conn, (store.kind,), yield_to=lambda: False)
                listed_after = library_surface.photos(conn)
                entry = cache.get(
                    conn,
                    listed_after[0]["hash"],
                    store.kind,
                    {"size": render.GRID, "rotate": 0},
                )
                with Image.open(entry["path"]) as tile:
                    tile_size = tile.size
                tile_path = entry["path"]
                freed = work.sweep_cache(conn, 0, (store.kind,))
                tile_survived = os.path.exists(tile_path)
            finally:
                conn.close()

        self.assertEqual(swept["photos_added"], 1)
        self.assertEqual(len(listed), 1)
        self.assertEqual(identified, {"did": "identity", "photo": listed[0]["id"]})
        self.assertEqual(made["did"], "tile")
        self.assertEqual(max(tile_size), render.GRID)
        self.assertTrue(tile_path.startswith(tile_root))
        self.assertEqual(freed, 1)
        self.assertFalse(tile_survived)

    def test_one_owned_worker_starts_once_and_releases_its_catalog(self):
        with tempfile.TemporaryDirectory() as directory:
            catalog = os.path.join(directory, "catalog.db")
            model.connect(catalog).close()
            chores = work.Chores(lambda: model.connect(catalog), ())

            self.assertTrue(chores.start())
            self.assertFalse(chores.start())
            self.assertTrue(chores.stop())
            self.assertFalse(chores.running)

    def test_the_v2_product_opens_browses_tiles_closes_and_reopens(self):
        with tempfile.TemporaryDirectory() as directory:
            catalog = os.path.join(directory, "Library", "catalog.db")
            tile_root = os.path.join(directory, "Cache", "tiles")
            photo_root = os.path.join(directory, "Photos")
            source = os.path.join(photo_root, "2026", "lake.jpg")
            os.makedirs(os.path.dirname(source))
            Image.new("RGB", (900, 600), "teal").save(source, "JPEG")

            product = boot.Library(catalog, tile_root)
            attached = product.attach(photo_root)
            swept = product.refresh(attached["uuid"])
            page = product.browse()
            body = product.tile(page[0]["id"])
            self.assertTrue(product.start())
            product.close()
            with self.assertRaises(RuntimeError):
                product.browse()

            with boot.Library(catalog, tile_root) as reopened:
                again = reopened.browse()
                cached = reopened.tile(again[0]["id"])

        self.assertEqual(swept["photos_added"], 1)
        self.assertEqual(len(page), 1)
        self.assertEqual(body, cached)
        self.assertTrue(body.startswith(b"\xff\xd8"))

    def test_embedded_metadata_is_cached_projected_and_overridden_by_your_date(self):
        with tempfile.TemporaryDirectory() as directory:
            catalog = os.path.join(directory, "catalog.db")
            tile_root = os.path.join(directory, "tiles")
            photo_root = os.path.join(directory, "Photos")
            source = os.path.join(photo_root, "portrait.jpg")
            os.makedirs(photo_root)
            exif = Image.Exif()
            exif[0x010F] = "Canon"
            exif[0x0110] = "Canon EOS R5"
            exif[0x0112] = 6
            exif[0x9003] = "2026:08:17 03:34:08"
            exif[0xA434] = "RF 50mm F1.2 L"
            Image.new("RGB", (900, 600), "maroon").save(source, "JPEG", exif=exif)

            with boot.Library(catalog, tile_root) as product:
                drive = product.attach(photo_root)
                product.refresh(drive["uuid"])
                photo = product.browse()[0]
                identified = work.step(product.conn, (embedded_metadata.KIND,))
                enriched = work.step(product.conn, (embedded_metadata.KIND,))
                answer = product.details(photo["id"])
                projected = product.browse()[0]
                corrected_date = product.set_date(photo["id"], "2020-01-02 04:05:06")
                with self.assertRaises(ValueError):
                    product.set_date(photo["id"], "2020-19-40")
                corrected = product.browse()[0]
                product.conn.execute(
                    "UPDATE images SET date_taken = NULL, camera_make = NULL, width = NULL"
                )
                product.conn.commit()

            with boot.Library(catalog, tile_root) as reopened:
                repaired = reopened.browse()[0]

        self.assertEqual((answer["width"], answer["height"]), (600, 900))
        self.assertEqual(identified["did"], "identity")
        self.assertEqual(enriched["did"], "metadata")
        self.assertEqual(answer["camera_make"], "Canon")
        self.assertEqual(answer["camera_model"], "EOS R5")
        self.assertEqual(answer["lens"], "RF 50mm F1.2 L")
        self.assertEqual(projected["date_taken"], "2026-08-17 03:34:08")
        self.assertEqual(corrected_date, "2020-01-02 04:05:06")
        self.assertEqual(corrected["date_taken"], "2020-01-02 04:05:06")
        self.assertEqual(repaired["date_taken"], "2020-01-02 04:05:06")
        self.assertEqual(repaired["width"], 600)

    def test_a_damaged_disposable_metadata_answer_cannot_stop_boot(self):
        with tempfile.TemporaryDirectory() as directory:
            catalog = os.path.join(directory, "catalog.db")
            digest = "f" * 64
            conn = model.connect(catalog)
            conn.execute(
                "INSERT INTO images(tail, content_hash) VALUES (?, ?)",
                ("Raws/frame.jpg", digest),
            )
            cache.put(
                conn,
                digest,
                embedded_metadata.KIND,
                cache.Made(value="not json", bytes=8),
            )
            conn.commit()
            conn.close()

            with boot.Library(catalog, os.path.join(directory, "tiles")) as product:
                repaired = cache.get(product.conn, digest, embedded_metadata.KIND)

        self.assertIsNone(repaired)


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

    def test_a_tail_can_never_escape_its_drive(self):
        for tail in ("../outside.CR3", "/outside.CR3", r"C:\outside.CR3", ""):
            with self.subTest(tail=tail), self.assertRaises(ValueError):
                drives.path_for(self.conn, self.hot["uuid"], tail)


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
        copies.saw(self.conn, image, int(self.cold["id"]))
        self.conn.commit()
        shutil.rmtree(self.cold_root)
        self.assertEqual(photos.state(self.conn, image), "away")

        # Only with every drive attached and empty-handed is it lost.
        os.makedirs(self.cold_root)
        drives.write_marker(self.cold_root, self.cold["uuid"])
        self.assertEqual(photos.state(self.conn, image), "lost")

    def test_an_unrelated_away_drive_does_not_hide_a_lost_copy(self):
        path = self.write(self.hot_root)
        image = self.photo(size=os.path.getsize(path))
        copies.saw(self.conn, image, int(self.hot["id"]))
        self.conn.commit()
        os.remove(path)
        shutil.rmtree(self.cold_root)

        self.assertEqual(photos.state(self.conn, image), "lost")


class SweepingRefuses(CoreCase):
    def test_a_fresh_sweep_is_a_browsable_library(self):
        photo = self.write(self.hot_root, "Raws/2026/new.jpg", b"jpeg bytes")
        self.write(self.hot_root, "Raws/2026/edit.xmp", b"sidecar")
        self.write(self.hot_root, "Raws/2026/empty.jpg", b"")

        result = copies.sweep(self.conn, self.hot["uuid"])

        self.assertEqual(result["photos_added"], 1)
        row = self.conn.execute("SELECT * FROM images").fetchone()
        self.assertEqual(row["tail"], "Raws/2026/new.jpg")
        self.assertEqual(row["file_size"], os.path.getsize(photo))
        self.assertEqual(row["file_modified_ns"], os.stat(photo).st_mtime_ns)
        self.assertEqual(len(copies.drives_holding(self.conn, row["id"])), 1)
        self.assertEqual(library_surface.photos(self.conn, sort="added")[0]["id"], row["id"])
        self.assertEqual(library_surface.folders(self.conn), [{"folder": "Raws/2026", "photos": 1}])

    def test_a_changed_file_is_reported_without_replacing_its_identity(self):
        path = self.write(self.hot_root, "Raws/2026/change.jpg", b"before")
        copies.sweep(self.conn, self.hot["uuid"])
        photo_id = self.conn.execute("SELECT id FROM images").fetchone()["id"]
        with open(path, "wb") as handle:
            handle.write(b"after and a different size")

        result = copies.sweep(self.conn, self.hot["uuid"])

        self.assertEqual(result["changed"], ["Raws/2026/change.jpg"])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM images").fetchone()[0], 1)
        self.assertIsNone(photos.open_photo(self.conn, photo_id))

    def test_a_known_alternate_copy_tail_is_not_admitted_as_a_second_photo(self):
        alternate = "Raws/2026/lake-2.jpg"
        path = self.write(self.cold_root, alternate, b"jpeg bytes")
        image = self.photo("Raws/2026/lake.jpg", size=os.path.getsize(path))
        copies.saw(self.conn, image, int(self.cold["id"]), tail=alternate)
        self.conn.commit()

        result = copies.sweep(self.conn, self.cold["uuid"])

        self.assertEqual(result["photos_added"], 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM images").fetchone()[0], 1)

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
        self.assertEqual(Path(target).read_bytes(), b"the-photograph")
        self.assertEqual(Path(source).read_bytes(), b"the-photograph")
        self.assertTrue(copies.is_backed_up(self.conn, image))

    def test_it_refuses_when_a_different_file_holds_that_tail(self):
        image, _ = self._queued()
        stranger = self.write(self.cold_root, body=b"a completely different photograph")
        self.assertEqual(backup.back_up(self.conn, image), "different file at that tail")
        self.assertEqual(Path(stranger).read_bytes(), b"a completely different photograph")
        self.assertFalse(copies.is_backed_up(self.conn, image))

    def test_a_failed_verify_leaves_nothing_behind(self):
        image, _ = self._queued()
        real = photos.same_bytes
        with patch.object(
            photos,
            "same_bytes",
            side_effect=lambda left, right: False if ".copying-" in left else real(left, right),
        ):
            self.assertEqual(backup.back_up(self.conn, image), "verify failed")
        target = os.path.join(self.cold_root, TAIL.replace("/", os.sep))
        self.assertFalse(os.path.exists(target))
        self.assertFalse(
            any(name.startswith("x.CR3.copying-") for name in os.listdir(os.path.dirname(target)))
        )
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

    def test_reclaim_refuses_a_symlink_that_only_points_back_to_the_source(self):
        image, source = self._queued()
        archived = os.path.join(self.cold_root, TAIL.replace("/", os.sep))
        os.makedirs(os.path.dirname(archived), exist_ok=True)
        try:
            os.symlink(source, archived)
        except (OSError, NotImplementedError):
            self.skipTest("this platform will not make symlinks unprivileged")
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

    def test_reclaim_rechecks_bytes_immediately_before_deletion(self):
        image, source = self._queued()
        backup.back_up(self.conn, image)
        with patch.object(photos, "same_bytes", side_effect=(True, False)):
            self.assertEqual(backup.reclaim(self.conn, image), "copies changed while verifying")
        self.assertTrue(os.path.exists(source))


class PuttingAFileDown(CoreCase):
    def test_identity_reads_past_the_old_eight_megabyte_prefix(self):
        first = os.path.join(self.tmp, "first.tif")
        second = os.path.join(self.tmp, "second.tif")
        prefix = b"0" * (8 * 1024 * 1024)
        with open(first, "wb") as handle:
            handle.write(prefix + b"A")
        with open(second, "wb") as handle:
            handle.write(prefix + b"B")
        self.assertNotEqual(photos.content_hash(first), photos.content_hash(second))

    def test_put_writes_verifies_and_identifies(self):
        source = os.path.join(self.tmp, "card.CR3")
        with open(source, "wb") as handle:
            handle.write(b"straight-off-the-card")
        result = photos.put(self.conn, source, self.hot["uuid"], TAIL)
        self.assertEqual(result["outcome"], "written")
        self.assertEqual(Path(result["path"]).read_bytes(), b"straight-off-the-card")
        self.assertEqual(result["hash"], photos.content_hash(source))
        self.assertIsInstance(result["id"], int)
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM images").fetchone()[0], 1
        )
        self.assertEqual(len(copies.drives_holding(self.conn, result["id"])), 1)

    def test_put_never_overwrites_a_different_photograph(self):
        source = os.path.join(self.tmp, "card.CR3")
        with open(source, "wb") as handle:
            handle.write(b"straight-off-the-card")
        existing = self.write(self.hot_root, body=b"something already here")
        self.assertEqual(
            photos.put(self.conn, source, self.hot["uuid"], TAIL)["outcome"],
            "different file at that tail",
        )
        self.assertEqual(Path(existing).read_bytes(), b"something already here")


class MovingAPhoto(CoreCase):
    def _on_working_drive(self):
        source = self.write(self.hot_root)
        image = self.photo(size=os.path.getsize(source))
        copies.saw(self.conn, image, int(self.hot["id"]))
        self.conn.commit()
        return image, source

    def test_move_proves_the_destination_before_removing_the_source(self):
        image, source = self._on_working_drive()
        new_tail = "Raws/Digital/2026/Filed/x.CR3"
        self.assertEqual(photos.move(self.conn, image, self.cold["uuid"], new_tail), "moved")
        target = os.path.join(self.cold_root, new_tail.replace("/", os.sep))
        self.assertFalse(os.path.exists(source))
        self.assertEqual(Path(target).read_bytes(), b"the-photograph")
        self.assertEqual(photos.open_photo(self.conn, image), target)

    def test_move_refuses_a_collision_before_changing_anything(self):
        image, source = self._on_working_drive()
        new_tail = "Raws/Digital/2026/Filed/x.CR3"
        stranger = self.write(self.cold_root, new_tail, b"somebody else")
        self.assertEqual(
            photos.move(self.conn, image, self.cold["uuid"], new_tail),
            "destination exists",
        )
        self.assertTrue(os.path.exists(source))
        self.assertEqual(Path(stranger).read_bytes(), b"somebody else")
        self.assertEqual(
            self.conn.execute("SELECT tail FROM images WHERE id = ?", (image,)).fetchone()["tail"],
            TAIL,
        )

    def test_move_remains_safe_on_a_drive_without_hardlinks(self):
        image, source = self._on_working_drive()
        new_tail = "Raws/Digital/2026/Filed/x.CR3"
        with patch.object(photos.os, "link", side_effect=OSError("unsupported")):
            self.assertEqual(photos.move(self.conn, image, self.cold["uuid"], new_tail), "moved")
        self.assertFalse(os.path.exists(source))

    def test_a_racing_collision_is_never_removed_as_ours(self):
        image, source = self._on_working_drive()
        new_tail = "Raws/Digital/2026/Filed/x.CR3"
        target = os.path.join(self.cold_root, new_tail.replace("/", os.sep))

        def race(_staging, _target):
            with open(target, "wb") as handle:
                handle.write(b"arrived during the move")
            raise FileExistsError(target)

        with patch.object(photos.os, "link", side_effect=race):
            self.assertTrue(
                photos.move(self.conn, image, self.cold["uuid"], new_tail).startswith("move failed:")
            )
        self.assertTrue(os.path.exists(source))
        self.assertEqual(Path(target).read_bytes(), b"arrived during the move")


class VersionFamilies(CoreCase):
    def test_group_reaches_the_original_and_every_descendant(self):
        original = self.photo("Raws/original.CR3")
        export = self.photo("Edits/export.jpg")
        second_export = self.photo("Edits/export-2.jpg")
        self.conn.execute("UPDATE images SET version_of = ? WHERE id = ?", (original, export))
        self.conn.execute("UPDATE images SET version_of = ? WHERE id = ?", (export, second_export))
        self.conn.commit()
        self.assertEqual(photos.group(self.conn, second_export), [original, export, second_export])


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


class SetsAreDecisions(CoreCase):
    FIRST = "a" * 64
    SECOND = "b" * 64

    def setUp(self):
        super().setUp()
        self.conn.executemany(
            "INSERT INTO images(tail, content_hash, stars) VALUES (?, ?, ?)",
            (("Raws/a.jpg", self.FIRST, 5), ("Raws/b.jpg", self.SECOND, 1)),
        )
        self.set_id = sets.create(self.conn, "Keepers", set_id="keepers")

    def test_membership_is_hash_stable_and_rename_moves_nothing(self):
        self.assertEqual(
            sets.add(self.conn, self.set_id, (self.FIRST, self.FIRST, self.SECOND)),
            2,
        )
        before = sets.members(self.conn, self.set_id)

        renamed = sets.rename(self.conn, self.set_id, "Portfolio")

        self.assertEqual(renamed["name"], "Portfolio")
        self.assertEqual(sets.members(self.conn, self.set_id), before)
        self.assertEqual(sets.all(self.conn)[0]["name"], "Portfolio")

    def test_one_set_scope_composes_with_every_other_scope(self):
        sets.add(self.conn, self.set_id, (self.FIRST, self.SECOND))
        narrowed = scope.all_of(scope.in_set(self.set_id), scope.starred(4))

        self.assertEqual(
            [row["hash"] for row in library_surface.photos(self.conn, scope=narrowed)],
            [self.FIRST],
        )

    def test_an_explicit_large_selection_is_still_one_sql_argument(self):
        selected = scope.ids(range(1, 40_001))
        self.assertEqual(len(selected.args), 1)
        self.assertEqual(len(library_surface.photos(self.conn, scope=selected)), 2)

    def test_star_scope_keeps_the_library_query_on_its_index(self):
        clause, args = scope.where(scope.starred(4))
        plan = " ".join(
            str(column)
            for row in self.conn.execute(
                f"EXPLAIN QUERY PLAN SELECT i.id FROM images i "
                f"WHERE {library_surface.IN_LIBRARY} AND ({clause}) "
                f"ORDER BY {library_surface.SORTS['stars']} LIMIT ?",
                (*args, 200),
            )
            for column in row
        )

        self.assertIn("idx_photos_stars", plan)

    def test_ambiguous_identifiers_and_non_photo_members_are_refused(self):
        with self.assertRaises(ValueError):
            sets.create(self.conn, "Bad", set_id="wild%card")
        with self.assertRaises(ValueError):
            sets.add(self.conn, self.set_id, (self.FIRST, "row-12"))
        self.assertEqual(sets.members(self.conn, self.set_id), [])
        with self.assertRaises(ValueError):
            scope.starred(6)

    def test_create_never_silently_redefines_an_existing_set(self):
        with self.assertRaises(ValueError):
            sets.create(self.conn, "Replacement", set_id=self.set_id)
        self.assertTrue(sets.forget(self.conn, self.set_id))
        with self.assertRaises(ValueError):
            sets.create(self.conn, "Reused", set_id=self.set_id)


class LibraryQueriesRefuse(CoreCase):
    def test_pagination_cannot_accidentally_request_the_whole_catalog(self):
        for limit, offset in ((0, 0), (501, 0), (20, -1)):
            with self.assertRaises(ValueError):
                library_surface.photos(self.conn, limit=limit, offset=offset)

    def test_reindex_updates_every_row_with_the_same_photo_identity(self):
        digest = "c" * 64
        self.conn.executemany(
            "INSERT INTO images(tail, content_hash) VALUES (?, ?)",
            (("Raws/one.jpg", digest), ("Raws/two.jpg", digest)),
        )
        decisions.decide(self.conn, digest, decisions.STAR, 5)

        rebuilt = library_surface.reindex(self.conn)

        stars = [row[0] for row in self.conn.execute("SELECT stars FROM images ORDER BY id")]
        self.assertEqual(stars, [5, 5])
        self.assertEqual(rebuilt[decisions.STAR], 2)

    def test_an_invalid_decision_cannot_partially_rebuild_the_read_index(self):
        first, second = "d" * 64, "e" * 64
        self.conn.executemany(
            "INSERT INTO images(tail, content_hash) VALUES (?, ?)",
            (("Raws/one.jpg", first), ("Raws/two.jpg", second)),
        )
        decisions.decide(self.conn, first, decisions.STATUS, "trashed")
        decisions.decide(self.conn, second, decisions.STAR, 9)

        with self.assertRaises(ValueError):
            library_surface.reindex(self.conn)

        statuses = [row[0] for row in self.conn.execute("SELECT status FROM images ORDER BY id")]
        self.assertEqual(statuses, ["kept", "kept"])

    def test_malformed_calendar_values_never_wrap_into_plausible_labels(self):
        self.assertEqual(library_surface.month_label("2026-00"), "2026-00")
        self.assertIsNone(library_surface.date_range("9999"))


class CacheRefuses(CoreCase):
    def setUp(self):
        super().setUp()
        self.kind = cache.Kind(
            name="test_thumb",
            compute=lambda source, hash, size=0: cache.Made(path=f"{source}@{size}", bytes=10),
            params=("size",),
        )

    def test_a_recipe_refuses_anything_the_kind_did_not_declare(self):
        # This is "recipe is never a timestamp", made mechanical. Feeding
        # updated_at in is how reset-then-redo re-rendered identical pixels and
        # two machines never shared an entry.
        with self.assertRaises(ValueError):
            cache.canonical(self.kind, {"size": 400, "updated_at": 1.0})

    def test_a_failure_is_recorded_once_with_why(self):
        boom = cache.Kind(name="boom", compute=lambda source, hash: 1 / 0)
        self.assertIsNone(cache.make(self.conn, "hash1", boom, "x.CR3"))
        stored = cache.get(self.conn, "hash1", boom)
        self.assertEqual(stored["state"], cache.FAILED)
        self.assertIn("ZeroDivisionError", stored["note"])

    def test_a_machine_that_cannot_make_it_records_nothing(self):
        # "Not here" is a fact about this machine. Stored, it would poison the
        # entry for the helper that can make it.
        gpu = cache.Kind(name="gpu", compute=lambda source, hash: cache.Made(), here=lambda: False)
        self.assertIsNone(cache.make(self.conn, "hash1", gpu, "x.CR3"))
        self.assertIsNone(cache.get(self.conn, "hash1", gpu))

    def test_eviction_never_takes_what_it_cannot_remake_cheaply(self):
        embedding = cache.Kind(name="embedding", compute=lambda source, hash: cache.Made(), evictable=False)
        cache.put(self.conn, "h1", embedding, cache.Made(value=b"vector", bytes=3000))
        cache.put(self.conn, "h1", self.kind, cache.Made(path="/t.jpg", bytes=3000), {"size": 400})
        self.conn.commit()
        self.assertEqual(cache.evict(self.conn, 0, (self.kind, embedding)), [("test_thumb", "/t.jpg")])
        self.assertIsNotNone(cache.get(self.conn, "h1", embedding))

    def test_a_projection_failure_becomes_one_recorded_answer(self):
        photo_id = self.photo()

        def break_after_writing(conn, projected_id, _entry):
            conn.execute("UPDATE images SET stars = 5 WHERE id = ?", (projected_id,))
            raise ZeroDivisionError("broken projection")

        projected = cache.Kind(
            name="projected",
            compute=lambda source, digest: cache.Made(value="answer"),
            project=break_after_writing,
        )
        cache.put(self.conn, "h1", projected, cache.Made(value="answer"))
        entry = cache.get(self.conn, "h1", projected)

        self.assertFalse(cache.project(self.conn, "h1", photo_id, projected, entry))
        failed = cache.get(self.conn, "h1", projected)
        self.assertEqual(failed["state"], cache.FAILED)
        self.assertIn("ProjectionError", failed["note"])
        self.assertEqual(
            self.conn.execute("SELECT stars FROM images WHERE id = ?", (photo_id,)).fetchone()[0],
            0,
        )


class OwedIsAQuery(CoreCase):
    def setUp(self):
        super().setUp()
        self.thumb = cache.Kind(
            name="thumb",
            compute=lambda source, hash: cache.Made(path="/t.jpg", bytes=1),
        )

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
        self.assertEqual([row["id"] for row in work.owed(self.conn, self.thumb)], [image])

    def test_making_it_is_what_removes_it_from_the_queue(self):
        self._catalogued()
        row = work.owed(self.conn, self.thumb)[0]
        cache.make(self.conn, row["hash"], self.thumb, self.write(self.hot_root))
        self.assertEqual(work.owed(self.conn, self.thumb), [])

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
        self.assertNotIn("no-tail", [row["hash"] for row in work.owed(self.conn, self.thumb)])
        self.assertEqual(work.step(self.conn, (self.thumb,), yield_to=lambda: False)["photo"], reachable)

    def test_a_failure_is_not_rediscovered_every_pass(self):
        boom = cache.Kind(name="boom", compute=lambda source, hash: 1 / 0)
        self._catalogued()
        self.assertEqual(len(work.owed(self.conn, boom)), 1)
        cache.make(self.conn, TAIL, boom, "whatever")
        self.assertEqual(work.owed(self.conn, boom), [])

    def test_what_is_on_screen_is_served_first(self):
        self._catalogued("Raws/a.CR3")
        watching = self._catalogued("Raws/b.CR3")
        owed = work.owed(self.conn, self.thumb, on_screen=[watching])
        self.assertEqual(owed[0]["id"], watching)

    def test_the_owner_can_stop_chores_and_it_survives_a_restart(self):
        # A preference is a decision, so it is a row in the log rather than a
        # flag -- a flag forgets itself exactly when someone who paused chores
        # to save battery would most mind them resuming.
        self._catalogued()
        work.set_paused(self.conn, True)
        self.assertIsNone(work.step(self.conn, (self.thumb,)))
        self.assertTrue(work.paused(self.conn))
        work.set_paused(self.conn, False)
        self.assertIsNotNone(work.step(self.conn, (self.thumb,)))

    def test_a_caller_can_yield_before_starting_an_item(self):
        self._catalogued()
        self.assertIsNone(work.step(self.conn, (self.thumb,), yield_to=lambda: True))

    def test_identity_is_owed_before_anything_keyed_on_it(self):
        image = self._catalogued(hashed=False)
        self.assertEqual(
            work.step(self.conn, (self.thumb,), yield_to=lambda: False),
            {"did": "identity", "photo": image},
        )


class DecodingRefuses(unittest.TestCase):
    """Two properties carried over from the deleted thumbnail suite.

    Ported rather than dropped, because deleting a test file is how a guard
    goes quiet -- which happened once already today with the symlink refusal.
    """

    def test_a_bounded_raw_header_read_finds_camera_date_and_lens(self):
        make, camera = b"Canon\0", b"Canon R5\0"
        taken, lens = b"2026:08:17 03:34:08\0", b"RF 50mm\0"
        ifd0, values = 8, 50
        exif_ifd = values + len(make) + len(camera)
        exif_values = exif_ifd + 30
        data = bytearray(exif_values + len(taken) + len(lens))
        struct.pack_into("<2sHI", data, 0, b"II", 42, ifd0)
        struct.pack_into("<H", data, ifd0, 3)
        struct.pack_into("<HHII", data, 10, 0x010F, 2, len(make), values)
        struct.pack_into("<HHII", data, 22, 0x0110, 2, len(camera), values + len(make))
        struct.pack_into("<HHII", data, 34, 0x8769, 4, 1, exif_ifd)
        data[values:values + len(make)] = make
        data[values + len(make):exif_ifd] = camera
        struct.pack_into("<H", data, exif_ifd, 2)
        struct.pack_into("<HHII", data, exif_ifd + 2, 0x9003, 2, len(taken), exif_values)
        struct.pack_into(
            "<HHII", data, exif_ifd + 14, 0xA434, 2, len(lens), exif_values + len(taken)
        )
        data[exif_values:exif_values + len(taken)] = taken
        data[exif_values + len(taken):] = lens

        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "frame.dng")
            with open(path, "wb") as handle:
                handle.write(data)
            found = raw_exif.read(path)

        self.assertEqual(found["make"], "Canon")
        self.assertEqual(found["model"], "Canon R5")
        self.assertEqual(found["date_taken"], "2026:08:17 03:34:08")
        self.assertEqual(found["lens"], "RF 50mm")

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
