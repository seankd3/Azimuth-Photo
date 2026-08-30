"""The core's refusals.

Not coverage. Every test here is either a bug this project already paid for
once, or a place where being wrong loses a photograph or a decision. Anything
that only restates what the code plainly says was deleted — a suite nobody
reads is a suite nobody runs.
"""

import io
import json
import hashlib
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
from model.scope import EVERYTHING
from model import backup, cache, copies, cull, decisions, drives, intake, photos, scope, sets, trash
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

                identified = work.step(conn, store.kinds, yield_to=lambda: False)
                made = work.step(conn, store.kinds, yield_to=lambda: False)
                recorded = work.step(conn, store.kinds, yield_to=lambda: False)
                listed_after = library_surface.photos(conn, renditions=store.renditions)
                grid_entry = cache.get(conn, listed_after[0]["hash"], store.grid)
                loupe_entry = cache.get(conn, listed_after[0]["hash"], store.loupe)
                with Image.open(grid_entry["path"]) as tile:
                    tile_size = tile.size
                with Image.open(loupe_entry["path"]) as tile:
                    loupe_size = tile.size
                freed = work.sweep_cache(conn, 0, store.kinds)
                grid_survived = os.path.exists(grid_entry["path"])
                loupe_survived = os.path.exists(loupe_entry["path"])
            finally:
                conn.close()

        self.assertEqual(swept["photos_added"], 1)
        self.assertEqual(len(listed), 1)
        self.assertEqual(identified, {"did": "identity", "photo": listed[0]["id"]})
        # The grid kind runs first and one decode publishes both files; the
        # loupe kind then finds its file and only records it.
        self.assertEqual(made["did"], "grid")
        self.assertEqual(recorded["did"], "loupe")
        self.assertEqual(max(tile_size), min(render.GRID, 800))
        self.assertEqual(max(loupe_size), 800)  # never larger than the photograph
        self.assertEqual(listed_after[0]["tile"], grid_entry["path"])
        self.assertEqual(listed_after[0]["loupe"], loupe_entry["path"])
        self.assertTrue(grid_entry["path"].startswith(tile_root))
        # A ceiling of zero removes what may be removed: the loupe, never the grid.
        self.assertEqual(freed, 1)
        self.assertTrue(grid_survived)
        self.assertFalse(loupe_survived)

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
            # Nothing is decoded on the interactive lane: the row says there is
            # no tile yet, and the worker makes it.
            while work.step(product.conn, (*product.tiles.kinds,), yield_to=lambda: False):
                pass
            page_after = product.browse()
            self.assertTrue(product.start())
            product.close()
            with self.assertRaises(RuntimeError):
                product.browse()

            with boot.Library(catalog, tile_root) as reopened:
                again = reopened.browse()
            tile_file = Path(again[0]["tile"].removeprefix("file:///"))
            body = tile_file.read_bytes()

        self.assertEqual(swept["photos_added"], 1)
        self.assertEqual(len(page), 1)
        self.assertIsNone(page[0]["tile"])
        self.assertTrue(page_after[0]["tile"].startswith("file:///"))
        self.assertEqual(again[0]["tile"], page_after[0]["tile"])
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


class BringingPhotographsIn(CoreCase):
    """Import is `put` per file plus one rule, and these are its promises."""

    def card(self, files: dict[str, bytes]) -> str:
        root = os.path.join(self.tmp, "Card")
        for rel, body in files.items():
            path = os.path.join(root, "DCIM", "100CANON", rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as handle:
                handle.write(body)
        return root

    def jpeg(self, taken: str = "2026:05:26 18:29:56", seed: str = "a") -> bytes:
        exif = Image.Exif()
        exif[0x9003] = taken
        out = io.BytesIO()
        shade = (ord(seed[0]) * 7) % 256
        Image.new("RGB", (32, 24), (shade, 255 - shade, 90)).save(out, "JPEG", exif=exif)
        return out.getvalue()

    def test_the_destination_rule_is_pure_and_refuses_to_guess(self):
        self.assertEqual(intake.destination(intake.RAWS, "2026-05-26 18:29:56", "IMG_0001.CR3"),
                         "Raws/Digital/2026/2026-05-26/IMG_0001.CR3")
        self.assertEqual(intake.destination(intake.FILM, "2026-05-26", "scan01.tif", roll="Portra 400 #3"),
                         "Raws/Film Scans/2026/2026-05-26/Portra 400 #3/scan01.tif")
        self.assertEqual(intake.destination(intake.SNAPSHOTS, "2026-05-26", "IMG_9.HEIC"), "Snapshots/2026/2026-05-26/IMG_9.HEIC")
        self.assertEqual(intake.destination(intake.EDITS, "2026-05-26", "a/b:c.jpg"), "Edits/2026/2026-05-26/a-b-c.jpg")
        with self.assertRaises(ValueError):
            intake.destination(intake.RAWS, None, "x.jpg")
        with self.assertRaises(ValueError):
            intake.destination(intake.FILM, "2026-05-26", "x.tif")
        self.assertEqual(intake.guess_kind(self.card({}), ["IMG_0001.CR3"]), intake.RAWS)
        self.assertIsNone(intake.guess_kind(os.path.join(self.tmp, "Photos"), ["a.jpg", "b.jpg"]))

    def test_an_export_is_the_photograph_rendered(self):
        # A full-resolution JPEG of what the library shows, wearing the
        # capture facts — never the original leaving through a side door.
        import exports

        body = self.jpeg(seed="x")
        src = self.write(self.hot_root, "Raws/2026-05-26/out.jpg", body)
        said = photos.put(self.conn, src, self.hot["uuid"], "Raws/2026-05-26/out.jpg")
        self.conn.execute(
            "UPDATE images SET date_taken = '2026-05-26 18:29:56' WHERE id = ?",
            (said["id"],))
        out = os.path.join(self.tmp, "exported")
        os.makedirs(out)

        self.assertEqual(exports.jpeg(self.conn, said["id"], out), "exported")
        from PIL import Image as Pillow

        target = os.path.join(out, "out.jpg")
        with Pillow.open(target) as made:
            self.assertEqual(made.format, "JPEG")
            self.assertGreater(made.width, 0)
            self.assertEqual(made.getexif().get(0x9003) or made.getexif().get_ifd(0x8769).get(0x9003),
                             "2026:05:26 18:29:56")
        # A second export steps aside rather than overwriting.
        self.assertEqual(exports.jpeg(self.conn, said["id"], out), "exported")
        self.assertTrue(os.path.exists(os.path.join(out, "out-2.jpg")))
        # A bounded long edge and a rename are honoured.
        self.assertEqual(
            exports.jpeg(self.conn, said["id"], out, long_edge=64, stem="client-001"),
            "exported")
        with Pillow.open(os.path.join(out, "client-001.jpg")) as small:
            self.assertLessEqual(max(small.size), 64)
        # A photograph on no drive is said, not failed.
        self.conn.execute("DELETE FROM copies")
        self.conn.execute("UPDATE images SET tail = 'Raws/ghost.jpg' WHERE id = ?", (said["id"],))
        self.assertEqual(exports.jpeg(self.conn, said["id"], out), "missing")

    def test_a_renamed_import_is_still_suspected_by_its_capture_second(self):
        # The import renames files to the date scheme, so a name is exactly
        # the thing a previous import did not keep — matching on name alone
        # meant a re-inserted card came back fully checked and the owner
        # scrolled through every already-culled day unchecking it.
        held = self.jpeg(seed="r")
        already = self.write(
            self.hot_root, "Raws/Digital/2026/2026-05-26/20260526-999999.JPG", held)
        photos.put(self.conn, already, self.hot["uuid"],
                   "Raws/Digital/2026/2026-05-26/20260526-999999.JPG")
        # The metadata worker projects the capture date after the copy lands.
        self.conn.execute(
            "UPDATE images SET date_taken = '2026-05-26 18:29:56'"
            " WHERE tail = 'Raws/Digital/2026/2026-05-26/20260526-999999.JPG'")
        card = self.card({"IMG_0042.JPG": held})

        staged = intake.scan(self.conn, card)
        self.assertTrue(staged[0]["suspect"],
                        "same capture second and size is the same photograph wearing a new name")

    def test_bring_verifies_suffixes_skips_by_identity_and_clears_only_after(self):
        one = self.jpeg(seed="a")
        two = self.jpeg(seed="b")   # same name as `one` in another card folder, different bytes
        held = self.jpeg(seed="h")
        card = self.card({"IMG_0001.JPG": one, "IMG_0003.JPG": held})
        twin_dir = os.path.join(card, "DCIM", "101CANON")
        os.makedirs(twin_dir)
        with open(os.path.join(twin_dir, "IMG_0001.JPG"), "wb") as handle:
            handle.write(two)
        # IMG_0003 is already in the library, under another name in another folder
        already = self.write(self.hot_root, "Snapshots/2026/2026-05-26/elsewhere.jpg", held)
        photos.put(self.conn, already, self.hot["uuid"], "Snapshots/2026/2026-05-26/elsewhere.jpg")

        staged = intake.scan(self.conn, card)
        self.assertEqual([c["key"] for c in staged],
                         ["DCIM/100CANON/IMG_0001.JPG", "DCIM/100CANON/IMG_0003.JPG", "DCIM/101CANON/IMG_0001.JPG"])
        self.assertTrue(all(c["taken"].startswith("2026-05-26") for c in staged))
        self.assertFalse(staged[1]["suspect"], "a suspicion needs the same name and size; identity decides at the copy")
        result = intake.bring(self.conn, self.hot["uuid"], intake.RAWS, staged, clear_source=True)

        self.assertEqual(result["skipped"], 1, "the photograph the library already holds is skipped by identity")
        self.assertEqual(result["brought"], 2)
        self.assertEqual(result["cleared"], 3, "verified and skipped sources are cleared; nothing else")
        tails = sorted(row[0] for row in self.conn.execute("SELECT tail FROM images"))
        self.assertEqual(tails, [
            "Raws/Digital/2026/2026-05-26/IMG_0001-2.JPG",  # the twin landed beside, never over
            "Raws/Digital/2026/2026-05-26/IMG_0001.JPG",
            "Snapshots/2026/2026-05-26/elsewhere.jpg",
        ])
        for tail in tails:
            self.assertTrue(os.path.isfile(os.path.join(self.hot_root, tail.replace("/", os.sep))))
        self.assertEqual(sorted(os.listdir(os.path.join(card, "DCIM", "100CANON"))), [])
        self.assertEqual(sorted(os.listdir(twin_dir)), [])

        # a second run over the same (now empty) card is a no-op; over a re-insert it skips everything
        again = intake.bring(self.conn, self.hot["uuid"], intake.RAWS, intake.scan(self.conn, card))
        self.assertEqual(again["total"], 0)

    def test_bring_stops_between_files_and_resumes_by_running_again(self):
        card = self.card({f"IMG_{n:04d}.JPG": self.jpeg(seed=chr(97 + n)) for n in range(4)})
        staged = intake.scan(self.conn, card)
        asked = {"n": 0}

        def stop_after_two() -> bool:
            asked["n"] += 1
            return asked["n"] > 2

        first = intake.bring(self.conn, self.hot["uuid"], intake.RAWS, staged, clear_source=True, stop=stop_after_two)
        self.assertTrue(first["stopped"])
        self.assertEqual(first["brought"], 2)
        self.assertEqual(len(os.listdir(os.path.join(card, "DCIM", "100CANON"))), 2, "unbrought sources are untouched")

        second = intake.bring(self.conn, self.hot["uuid"], intake.RAWS, intake.scan(self.conn, card), clear_source=True)
        self.assertEqual(second["brought"], 2)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM images").fetchone()[0], 4)
        self.assertEqual(os.listdir(os.path.join(card, "DCIM", "100CANON")), [])

    def test_film_scans_land_by_roll_under_the_lab_folder_date(self):
        lab = os.path.join(self.tmp, "Lab_2026-06-02_Order42")
        roll_a = os.path.join(lab, "Roll A")
        roll_b = os.path.join(lab, "Roll B")
        os.makedirs(roll_a); os.makedirs(roll_b)
        # the lab wrote the stock into one scan's description
        exif = Image.Exif(); exif[0x010E] = "Kodak Portra 400, Noritsu HS-1800"
        Image.new("RGB", (40, 30), (200, 180, 150)).save(os.path.join(roll_a, "000001.jpg"), "JPEG", exif=exif)
        Image.new("RGB", (40, 30), (120, 130, 90)).save(os.path.join(roll_a, "000002.jpg"), "JPEG")
        Image.new("RGB", (40, 30), (30, 60, 90)).save(os.path.join(roll_b, "000001.jpg"), "JPEG")

        staged = intake.scan(self.conn, lab)
        rolls = intake.rolls(staged)
        self.assertEqual(list(rolls), ["Roll A", "Roll B"])
        self.assertIn("Portra 400", rolls["Roll A"]["name"])
        self.assertEqual(rolls["Roll B"]["name"], "2")
        self.assertEqual({c["folder_date"] for c in staged}, {"2026-06-02"})
        rolls["Roll B"]["name"] = "Tri-X"  # the owner renames one

        result = intake.bring(self.conn, self.hot["uuid"], intake.FILM, staged,
                              rolls_by_group={g: r["name"] for g, r in rolls.items()})

        self.assertEqual(result["brought"], 3)
        tails = sorted(row[0] for row in self.conn.execute("SELECT tail FROM images"))
        self.assertEqual(tails[0], f"Raws/Film Scans/2026/2026-06-02/{rolls['Roll A']['name']}/000001.jpg")
        self.assertEqual(tails[2], "Raws/Film Scans/2026/2026-06-02/Tri-X/000001.jpg")

    def test_a_missing_photograph_brought_back_takes_its_new_address(self):
        body = self.jpeg(seed="m")
        lost = self.write(self.hot_root, "Snapshots/2025/2025-01-01/gone.jpg", body)
        photos.put(self.conn, lost, self.hot["uuid"], "Snapshots/2025/2025-01-01/gone.jpg")
        photo_id = self.conn.execute("SELECT id FROM images").fetchone()[0]
        cull.pick(self.conn, (photo_id,))
        os.remove(lost)
        copies.sweep(self.conn, self.hot["uuid"])
        self.assertEqual(copies.drives_holding(self.conn, photo_id), [])

        card = self.card({"IMG_0007.JPG": body})
        result = intake.bring(self.conn, self.hot["uuid"], intake.SNAPSHOTS, intake.scan(self.conn, card))

        row = self.conn.execute("SELECT id, tail, status FROM images").fetchone()
        self.assertEqual(result["brought"], 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM images").fetchone()[0], 1)
        self.assertEqual(row["id"], photo_id)
        self.assertEqual(row["tail"], "Snapshots/2026/2026-05-26/IMG_0007.JPG")
        self.assertEqual(row["status"], "picked")


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

    def test_a_rewritten_file_is_re_identified_and_its_decisions_carried(self):
        path = self.write(self.hot_root, "Raws/2026/change.jpg", b"before")
        copies.sweep(self.conn, self.hot["uuid"])
        photo_id = self.conn.execute("SELECT id FROM images").fetchone()["id"]
        before = photos.content_hash(path)
        self.conn.execute("UPDATE images SET content_hash = ? WHERE id = ?", (before, photo_id))
        cull.pick(self.conn, (photo_id,))
        with open(path, "wb") as handle:
            handle.write(b"after and a different size")

        result = copies.sweep(self.conn, self.hot["uuid"])

        after = photos.content_hash(path)
        row = self.conn.execute("SELECT content_hash, status, file_size FROM images WHERE id = ?", (photo_id,)).fetchone()
        self.assertEqual(result["changed"], ["Raws/2026/change.jpg"])
        self.assertEqual(result["photos_rewritten"], 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM images").fetchone()[0], 1)
        self.assertEqual(row["content_hash"], after)
        self.assertEqual(row["file_size"], os.path.getsize(path))
        # the pick went with the photograph to its new identity, and the old
        # identity's row is still in the log
        self.assertEqual(decisions.latest(self.conn, after, decisions.STATUS), "picked")
        self.assertEqual(decisions.latest(self.conn, before, decisions.STATUS), "picked")
        self.assertEqual(photos.open_photo(self.conn, photo_id), path)

    def test_a_moved_file_keeps_its_row_and_its_decisions(self):
        path = self.write(self.hot_root, "Raws/2026/2026-01-01/frame.jpg", b"same bytes either place")
        copies.sweep(self.conn, self.hot["uuid"])
        photo_id = self.conn.execute("SELECT id FROM images").fetchone()["id"]
        cull.pick(self.conn, (photo_id,)) if self.conn.execute(
            "SELECT content_hash FROM images WHERE id = ?", (photo_id,)).fetchone()[0] else None
        # a person moves the shoot into a renamed folder in Explorer
        new_dir = os.path.join(self.hot_root, "Raws", "2026", "2026-01-01 Lake")
        os.rename(os.path.dirname(path), new_dir)

        result = copies.sweep(self.conn, self.hot["uuid"])

        row = self.conn.execute("SELECT id, tail FROM images").fetchone()
        self.assertEqual(result["photos_moved"], 1)
        self.assertEqual(result["photos_added"], 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM images").fetchone()[0], 1)
        self.assertEqual(row["id"], photo_id)
        self.assertEqual(row["tail"], "Raws/2026/2026-01-01 Lake/frame.jpg")
        self.assertEqual(len(copies.drives_holding(self.conn, photo_id)), 1)

    def test_a_move_on_one_drive_records_an_alternate_tail_when_the_archive_still_holds_it(self):
        body = b"held on both drives"
        hot_path = self.write(self.hot_root, "Raws/2026/frame.jpg", body)
        cold_path = os.path.join(self.cold_root, "Raws", "2026", "frame.jpg")
        os.makedirs(os.path.dirname(cold_path), exist_ok=True)
        shutil.copy2(hot_path, cold_path)  # a backup keeps the file's time
        copies.sweep(self.conn, self.hot["uuid"])
        copies.sweep(self.conn, self.cold["uuid"])
        photo_id = self.conn.execute("SELECT id FROM images").fetchone()["id"]
        os.makedirs(os.path.join(self.hot_root, "Raws", "2026-moved"))
        os.rename(hot_path, os.path.join(self.hot_root, "Raws", "2026-moved", "frame.jpg"))

        result = copies.sweep(self.conn, self.hot["uuid"])

        self.assertEqual(result["photos_moved"], 1)
        self.assertEqual(self.conn.execute("SELECT tail FROM images WHERE id = ?", (photo_id,)).fetchone()[0], "Raws/2026/frame.jpg")
        holding = {d["uuid"]: d["copy_tail"] for d in copies.drives_holding(self.conn, photo_id)}
        self.assertEqual(holding[self.hot["uuid"]], "Raws/2026-moved/frame.jpg")
        self.assertIsNone(holding[self.cold["uuid"]])
        self.assertEqual(photos.open_photo(self.conn, photo_id), os.path.join(self.hot_root, "Raws", "2026-moved", "frame.jpg"))

    def test_a_renamed_file_becomes_one_row_again_once_identified(self):
        # A new name is not matched by name, size and time; it is admitted as
        # a new row, and when the worker learns its identity the old address,
        # which nothing holds any more, leaves -- with the decisions safe in
        # the log under the identity both rows share.
        path = self.write(self.hot_root, "Raws/2026/IMG_0001.jpg", b"renamed later")
        copies.sweep(self.conn, self.hot["uuid"])
        old_id = self.conn.execute("SELECT id FROM images").fetchone()["id"]
        while work.step(self.conn, (), yield_to=lambda: False):
            pass
        cull.pick(self.conn, (old_id,))
        os.rename(path, os.path.join(self.hot_root, "Raws", "2026", "lake.jpg"))
        copies.sweep(self.conn, self.hot["uuid"])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM images").fetchone()[0], 2)

        while work.step(self.conn, (), yield_to=lambda: False):
            pass

        rows = self.conn.execute("SELECT id, tail, status FROM images").fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["tail"], "Raws/2026/lake.jpg")
        self.assertEqual(rows[0]["status"], "picked")
        self.assertNotEqual(rows[0]["id"], old_id)

    def test_a_sweep_of_one_folder_leaves_the_rest_alone(self):
        kept = self.write(self.hot_root, "Raws/2025/kept.jpg", b"kept")
        gone = self.write(self.hot_root, "Raws/2026/gone.jpg", b"gone")
        copies.sweep(self.conn, self.hot["uuid"])
        os.remove(gone)
        os.remove(kept)

        result = copies.sweep(self.conn, self.hot["uuid"], under="Raws/2026")

        held = {row["tail"] for row in self.conn.execute(
            "SELECT i.tail FROM copies c JOIN images i ON i.id = c.photo_id")}
        self.assertEqual(result["copies_retired"], 1)
        self.assertIn("Raws/2025/kept.jpg", held)
        self.assertNotIn("Raws/2026/gone.jpg", held)

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
        with patch.object(copies, "walk_tails", return_value=(set(), set(), False)):
            self.assertFalse(copies.sweep(self.conn, self.cold["uuid"])["applied"])
        self.assertTrue(copies.drives_holding(self.conn, image))

    def test_a_drive_that_vanishes_mid_sweep_changes_nothing(self):
        self.write(self.cold_root)
        image = self.photo()
        copies.saw(self.conn, image, int(self.cold["id"]))
        self.conn.commit()
        real = copies.walk_tails

        def pull_the_plug(root, under=""):
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


class AnAwayDriveIsNotAMissingPhotograph(CoreCase):
    def test_an_away_drive_keeps_every_hint_and_offers_nothing(self):
        # The sentence the whole design turns on, doing its job in the one
        # place where being wrong loses photographs: a drive that is not here
        # is not swept, so nothing it holds is ever retired or shown missing.
        path = self.write(self.cold_root)
        image = self.photo(size=os.path.getsize(path))
        copies.saw(self.conn, image, int(self.cold["id"]))
        self.conn.commit()
        shutil.rmtree(self.cold_root)

        result = copies.sweep(self.conn, self.cold["uuid"])

        self.assertEqual(result, {"drive": self.cold["uuid"], "applied": False, "reason": "not attached"})
        self.assertEqual(len(copies.drives_holding(self.conn, image)), 1)
        row = library_surface.photos(self.conn, reachable_on=())[0]
        self.assertEqual((row["placed"], row["reachable"]), (1, 0))


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
        decisions.decide(self.conn, "abc", decisions.STATUS, "picked", at=7.0)
        decisions.decide(self.conn, "abc", decisions.STATUS, "unflagged", at=7.0)
        self.assertEqual(decisions.latest(self.conn, "abc", decisions.STATUS), "unflagged")

    def test_undo_appends_so_the_log_never_lies(self):
        decisions.decide(self.conn, "abc", decisions.STATUS, "unflagged", at=1.0)
        decisions.decide(self.conn, "abc", decisions.STATUS, "trashed", at=2.0)
        self.assertEqual(decisions.undo(self.conn, "abc", decisions.STATUS), "unflagged")
        self.assertEqual(len(decisions.history(self.conn, "abc")), 3)

    def test_a_subject_is_any_stable_identity(self):
        # What killed the all-zeros fake content hash a collection needed in
        # order to be a row it could decide about.
        decisions.decide(self.conn, "Raws/Digital/2026", decisions.NAME, "Iceland")
        self.assertEqual(decisions.latest(self.conn, "Raws/Digital/2026", decisions.NAME), "Iceland")


class CullIsAReversibleDecision(CoreCase):
    def add(self, name: str, digest: str, *, status: str = "unflagged") -> int:
        return int(
            self.conn.execute(
                "INSERT INTO images(filename, tail, content_hash, status) VALUES (?, ?, ?, ?)",
                (name, f"Raws/{name}", digest, status),
            ).lastrowid
        )

    def copied(self, body=b"one complete photograph"):
        hot_path = self.write(self.hot_root, body=body)
        cold_path = self.write(self.cold_root, body=body)
        digest = photos.content_hash(hot_path)
        photo_id = int(
            self.conn.execute(
                "INSERT INTO images(filename, tail, content_hash) VALUES (?, ?, ?)",
                ("x.CR3", TAIL, digest),
            ).lastrowid
        )
        copies.saw(self.conn, photo_id, int(self.hot["id"]))
        copies.saw(self.conn, photo_id, int(self.cold["id"]))
        self.conn.commit()
        cull.reject(self.conn, (photo_id,))
        return photo_id, hot_path, cold_path

    def test_a_turn_is_the_same_decision_shape_as_a_pick(self):
        photo = self.add("scan.tif", "7" * 64)
        first = cull.turn(self.conn, (photo,))
        second = cull.turn(self.conn, (photo,), by=90)
        shown = lambda: self.conn.execute("SELECT rotate, status FROM images WHERE id = ?", (photo,)).fetchone()  # noqa: E731
        self.assertEqual(tuple(shown()), (180, "unflagged"))
        self.assertEqual(first["changed"][0]["family"], decisions.ROTATE)
        self.assertEqual(decisions.latest(self.conn, "7" * 64, decisions.ROTATE), 180)
        # Undo of the second turn restores exactly the first; a stale undo of the
        # first is refused because the second intervened.
        cull.undo(self.conn, second["changed"])
        self.assertEqual(tuple(shown()), (90, "unflagged"))
        with self.assertRaises(ValueError):
            cull.undo(self.conn, first["changed"])
        cull.turn(self.conn, (photo,), by=270)
        self.assertEqual(tuple(shown()), (0, "unflagged"))

    def test_pick_clear_and_undo_are_one_status_dimension(self):
        photo = self.add("frame.jpg", "f" * 64)

        picked = cull.pick(self.conn, (photo,))
        self.assertEqual(
            self.conn.execute("SELECT status FROM images WHERE id = ?", (photo,)).fetchone()[0],
            "picked",
        )
        cleared = cull.clear(self.conn, (photo,))
        self.assertEqual(cleared["changed"][0]["before"], "picked")

        cull.undo(self.conn, cleared["changed"])
        self.assertEqual(
            self.conn.execute("SELECT status FROM images WHERE id = ?", (photo,)).fetchone()[0],
            "picked",
        )
        self.assertEqual(picked["changed"][0]["before"], "unflagged")

    def test_trash_restore_and_undo_preserve_the_previous_answer(self):
        picked = self.add("picked.jpg", "a" * 64, status="picked")
        unflagged = self.add("unflagged.jpg", "b" * 64)
        decisions.decide(self.conn, "a" * 64, decisions.STATUS, "picked")
        self.conn.commit()

        action = cull.reject(self.conn, (picked, unflagged))

        self.assertEqual(trash.count(self.conn), 2)
        self.assertEqual(library_surface.photos(self.conn), [])
        self.assertEqual(
            [photo["id"] for photo in library_surface.trash(self.conn)],
            [unflagged, picked],
        )
        restored = cull.restore(self.conn, (picked,))
        self.assertEqual(restored["changed"][0]["after"], "picked")
        self.assertEqual(trash.count(self.conn), 1)

        cull.undo(self.conn, restored["changed"])
        self.assertEqual(trash.count(self.conn), 2)
        cull.undo(self.conn, action["changed"][1:])
        statuses = {
            row["filename"]: row["status"]
            for row in self.conn.execute("SELECT filename, status FROM images")
        }
        self.assertEqual(
            statuses,
            {"picked.jpg": "trashed", "unflagged.jpg": "unflagged"},
        )

    def test_a_later_decision_makes_an_old_undo_stale(self):
        photo = self.add("frame.jpg", "c" * 64)
        action = cull.reject(self.conn, (photo,))
        decisions.decide(self.conn, "c" * 64, decisions.STATUS, "unflagged")
        self.conn.execute("UPDATE images SET status = 'unflagged' WHERE id = ?", (photo,))
        self.conn.commit()

        with self.assertRaisesRegex(ValueError, "stale"):
            cull.undo(self.conn, action["changed"])

        self.assertEqual(trash.count(self.conn), 0)
        self.assertEqual(decisions.latest(self.conn, "c" * 64, decisions.STATUS), "unflagged")

    def test_an_invalid_member_refuses_the_whole_selection(self):
        photo = self.add("frame.jpg", "d" * 64)

        with self.assertRaisesRegex(ValueError, "missing"):
            cull.reject(self.conn, (photo, 999_999))

        self.assertEqual(trash.count(self.conn), 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0], 0)
        for limit, offset in ((0, 0), (501, 0), (20, -1)):
            with self.assertRaises(ValueError):
                library_surface.trash(self.conn, limit=limit, offset=offset)

    def test_a_large_selection_is_one_bound_argument_and_one_transaction(self):
        total = 5_000
        self.conn.executemany(
            "INSERT INTO images(filename, tail, content_hash) VALUES (?, ?, ?)",
            (
                (f"{index}.jpg", f"Raws/{index}.jpg", f"{index:064x}")
                for index in range(1, total + 1)
            ),
        )
        self.conn.commit()

        action = cull.reject(self.conn, range(1, total + 1))

        self.assertEqual(len(action["changed"]), total)
        self.assertEqual(trash.count(self.conn), total)
        cull.undo(self.conn, action["changed"])
        self.assertEqual(trash.count(self.conn), 0)

    def test_one_identity_updates_every_matching_catalog_row(self):
        digest = "e" * 64
        first = self.add("one.jpg", digest)
        self.add("two.jpg", digest)

        action = cull.reject(self.conn, (first,))

        self.assertEqual(len(action["changed"]), 1)
        self.assertEqual(action["changed"][0]["photos"], 2)
        self.assertEqual(trash.count(self.conn), 2)

    def test_empty_trash_reverifies_and_removes_every_known_copy(self):
        _photo, hot_path, cold_path = self.copied()

        preview = trash.empty(self.conn, expected_count=1, dry_run=True)
        result = trash.empty(self.conn, expected_count=1)

        self.assertEqual(preview, {"count": 1, "identities": 1, "files": 2})
        self.assertEqual(result, {"emptied": [1], "errors": [], "remaining": 0})
        self.assertFalse(os.path.exists(hot_path))
        self.assertFalse(os.path.exists(cold_path))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM images").fetchone()[0], 0)
        self.assertGreater(self.conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0], 0)

    def test_empty_trash_refuses_an_away_drive_before_deleting_anything(self):
        _photo, hot_path, cold_path = self.copied()
        os.remove(os.path.join(self.cold_root, drives.MARKER_NAME))

        with self.assertRaisesRegex(ValueError, "away"):
            trash.empty(self.conn, expected_count=1)

        self.assertTrue(os.path.exists(hot_path))
        self.assertTrue(os.path.exists(cold_path))
        self.assertEqual(trash.count(self.conn), 1)

    def test_empty_trash_finds_canonical_copies_even_when_every_hint_is_missing(self):
        photo, hot_path, cold_path = self.copied()
        self.conn.execute("DELETE FROM copies WHERE photo_id = ?", (photo,))
        self.conn.commit()

        preview = trash.empty(self.conn, expected_count=1, dry_run=True)
        result = trash.empty(self.conn, expected_count=1)

        self.assertEqual(preview["files"], 2)
        self.assertEqual(result["remaining"], 0)
        self.assertFalse(os.path.exists(hot_path))
        self.assertFalse(os.path.exists(cold_path))

    def test_empty_trash_refuses_changed_bytes_and_a_stale_visible_count(self):
        _photo, hot_path, cold_path = self.copied()
        with open(cold_path, "wb") as handle:
            handle.write(b"changed after it was catalogued")

        with self.assertRaisesRegex(ValueError, "changed|differ"):
            trash.empty(self.conn, expected_count=1)
        self.assertTrue(os.path.exists(hot_path))
        self.assertTrue(os.path.exists(cold_path))

        with open(cold_path, "wb") as handle:
            handle.write(b"one complete photograph")
        with self.assertRaisesRegex(ValueError, "count changed"):
            trash.empty(self.conn, expected_count=2)
        self.assertTrue(os.path.exists(hot_path))
        self.assertTrue(os.path.exists(cold_path))

    def test_an_interrupted_empty_keeps_the_record_copy_and_can_resume(self):
        photo, hot_path, cold_path = self.copied()
        real_remove = trash.os.remove

        def fail_on_record(path):
            if os.path.normcase(path) == os.path.normcase(cold_path):
                raise OSError("archive became read-only")
            real_remove(path)

        with patch.object(trash.os, "remove", side_effect=fail_on_record):
            result = trash.empty(self.conn, expected_count=1)

        self.assertFalse(os.path.exists(hot_path))
        self.assertTrue(os.path.exists(cold_path))
        self.assertEqual(result["remaining"], 1)
        self.assertEqual(
            [row["id"] for row in copies.drives_holding(self.conn, photo)],
            [self.cold["id"]],
        )
        resumed = trash.empty(self.conn, expected_count=1)
        self.assertEqual(resumed["remaining"], 0)
        self.assertFalse(os.path.exists(cold_path))


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

        self.assertIn("idx_live_stars", plan)

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
        decisions.decide(self.conn, digest, decisions.ROTATE, 90)

        rebuilt = library_surface.reindex(self.conn)

        turns = [row[0] for row in self.conn.execute("SELECT rotate FROM images ORDER BY id")]
        self.assertEqual(turns, [90, 90])
        self.assertEqual(rebuilt[decisions.ROTATE], 2)

    def test_an_invalid_decision_cannot_partially_rebuild_the_read_index(self):
        first, second = "d" * 64, "e" * 64
        self.conn.executemany(
            "INSERT INTO images(tail, content_hash) VALUES (?, ?)",
            (("Raws/one.jpg", first), ("Raws/two.jpg", second)),
        )
        decisions.decide(self.conn, first, decisions.STATUS, "trashed")
        decisions.decide(self.conn, second, decisions.ROTATE, 45)

        with self.assertRaises(ValueError):
            library_surface.reindex(self.conn)

        statuses = [row[0] for row in self.conn.execute("SELECT status FROM images ORDER BY id")]
        self.assertEqual(statuses, ["unflagged", "unflagged"])

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

    def test_a_projection_failure_never_costs_the_computed_answer(self):
        # The projection is a derived index reindex rebuilds on every open;
        # its failure is a moment, not a fact about the photograph. The old
        # rule stored it as one and 27 real photos never learned their
        # shape — a "failed" row with no value blocks the retry forever.
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
        held = cache.get(self.conn, "h1", projected)
        self.assertEqual(held["state"], cache.READY)     # the answer survives
        self.assertEqual(held["value"], "answer")
        self.assertEqual(
            self.conn.execute("SELECT stars FROM images WHERE id = ?", (photo_id,)).fetchone()[0],
            0,                                            # the half-write rolled back
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

    def test_an_exif_ifd_at_the_file_tail_is_still_read(self):
        # Lightroom's DNG writer puts the Exif IFD *after* the image data —
        # 38 MB in on a real R5 conversion — so a bounded header read left
        # 2,086 freshly imported DNGs undated while every date sat plainly
        # in the file. The IFD graph is a pointer structure over the whole
        # file; the reader follows it wherever it points.
        taken = b"2026:08:27 07:35:21\0"
        far = 5 * 1024 * 1024  # past the old 4 MiB window
        data = bytearray(far + 32 + len(taken))
        struct.pack_into("<2sHI", data, 0, b"II", 42, 8)
        struct.pack_into("<H", data, 8, 1)
        struct.pack_into("<HHII", data, 10, 0x8769, 4, 1, far)
        struct.pack_into("<H", data, far, 1)
        struct.pack_into("<HHII", data, far + 2, 0x9003, 2, len(taken), far + 32)
        data[far + 32:] = taken

        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "frame.dng")
            with open(path, "wb") as handle:
                handle.write(data)
            found = raw_exif.read(path)

        self.assertEqual(found["date_taken"], "2026:08:27 07:35:21")

    def test_cr3_cmt2_is_the_exif_ifd_itself(self):
        # In CR3, CMT1 carries IFD0 and CMT2 *is* the Exif IFD — its date
        # and lens tags sit at the top level with no 0x8769 pointer.
        # Reading CMT2 with IFD0's map left every CR3 in the real library
        # (657 of them) with a make, a model, and no date at all.
        taken = b"2026:08:23 00:52:27\0"
        tiff = (b"II" + struct.pack("<HI", 42, 8)
                + struct.pack("<H", 1)
                + struct.pack("<HHII", 0x9003, 2, len(taken), 26)
                + struct.pack("<I", 0)
                + taken)
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "frame.cr3")
            with open(path, "wb") as handle:
                handle.write(b"\0" * 16 + b"CMT2" + tiff)
            found = raw_exif.read(path)

        self.assertEqual(found["date_taken"], "2026:08:23 00:52:27")

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
            "SELECT id, subject, value FROM decisions WHERE family = 'compare'").fetchall())
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

    def _identified(self, tail, width=3000, height=2000):
        photo_id = self.photo(tail)
        digest = hashlib.blake2b(tail.encode(), digest_size=32).hexdigest()
        self.conn.execute("UPDATE images SET content_hash = ?, width = ?, height = ? WHERE id = ?",
                          (digest, width, height, photo_id))
        self.conn.commit()
        return photo_id, digest

    def test_a_round_taken_back_stops_counting_and_stays_in_the_log(self):
        # V1's undo wrote a `compare_undone` row that the fit never read, so
        # Undo in Refine did nothing to the ranking. Here the retraction is a
        # round-family row naming the round, and `rounds()` drops both --
        # without deleting anything, so the log still says what happened.
        a, a_hash = self._identified("Raws/a.CR2")
        b, b_hash = self._identified("Raws/b.CR2")
        c, c_hash = self._identified("Raws/c.CR2")
        first = rank.record(self.conn, a, [b, c])
        second = rank.record(self.conn, b, [c])
        self.assertEqual(len(rank.rounds(self.conn)), 2)
        rank.retract(self.conn, first["decision"])
        self.assertEqual(rank.rounds(self.conn), [(b_hash, [c_hash])])
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM decisions WHERE family = 'compare'").fetchone()[0], 3)
        with self.assertRaises(ValueError):
            rank.retract(self.conn, 999999)
        with self.assertRaises(ValueError):
            rank.record(self.conn, self.photo("Raws/unidentified.CR2"), [a])
        self.assertEqual(second["over"], [c_hash])

    def test_a_set_comes_only_from_what_can_be_shown_and_a_pair_is_one_orientation(self):
        # "Limit refine to the files that already have thumbnails so we never
        # get blank spots" (07-21): the candidate query runs over the tile
        # store's `ready` scope, which is the cache itself, not a flag on the
        # row. And a duel of a portrait against a landscape compares frames
        # before photographs, so a pair shares the anchor's orientation.
        import tiles
        from model.scope import all_of
        store = tiles.Store(os.path.join(self.tmp, "tiles"), ceiling_bytes=0)
        ready, unready = [], []
        for i in range(6):
            photo_id, digest = self._identified(f"Raws/r{i}.CR2", width=3000, height=2000)
            cache.put(self.conn, digest, store.grid, cache.Made(path=f"r{i}.jpg", bytes=1))
            ready.append(digest)
        tall_id, tall = self._identified("Raws/tall.CR2", width=2000, height=3000)
        cache.put(self.conn, tall, store.grid, cache.Made(path="tall.jpg", bytes=1))
        for i in range(3):
            _id, digest = self._identified(f"Raws/u{i}.CR2")
            unready.append(digest)
        self.conn.commit()

        chosen = rank.candidates(self.conn, 12, scope=all_of(EVERYTHING, store.ready))
        self.assertEqual({p["hash"] for p in chosen}, set(ready) | {tall})
        self.assertTrue(all(p["hash"] not in unready for p in chosen))

        # the least-judged anchors: judge every landscape once so the portrait
        # anchors the pair, and its companion must then be another portrait --
        # none exists, so the pair falls back to the nearest landscape
        for digest in ready:
            self._round(digest, [ready[0]] if digest != ready[0] else [ready[1]], 1.0)
        pair = rank.candidates(self.conn, 2, scope=all_of(EVERYTHING, store.ready))
        self.assertEqual(pair[0]["hash"], tall)
        self.assertEqual(len(pair), 2)
        # and with a second portrait it is the pair
        tall2_id, tall2 = self._identified("Raws/tall2.CR2", width=2000, height=3000)
        cache.put(self.conn, tall2, store.grid, cache.Made(path="tall2.jpg", bytes=1))
        self.conn.commit()
        pair = rank.candidates(self.conn, 2, scope=all_of(EVERYTHING, store.ready))
        self.assertEqual({p["hash"] for p in pair}, {tall, tall2})
        # what is on screen does not come straight back
        again = rank.candidates(self.conn, 2, scope=all_of(EVERYTHING, store.ready), avoid=[tall, tall2])
        self.assertFalse({tall, tall2} & {p["hash"] for p in again})

    def test_stars_are_the_rankings_face_and_rerank_writes_both(self):
        # "Stars should be applied by the photos elo" (07-31): no manual star
        # exists; a ranked photograph's star is its place in the order, earned
        # after three rounds, and a star read from a sidecar or V1 is evidence
        # in the log that no longer projects to the column.
        ids = {}
        for name in "abcdefghij":
            ids[name] = self._identified(f"Raws/{name}.CR2")
        order = "abcdefghij"   # a beats everyone below it, three times over
        for repeat in range(rank.EARNED):
            for i, name in enumerate(order[:-1]):
                rank.record(self.conn, ids[name][0], [ids[order[i + 1]][0]])
        scores = rank.strength(self.conn)
        starred = rank.stars(scores, rank.seen(self.conn))
        self.assertEqual(starred[ids["a"][1]], 5)      # the best of even ten
        self.assertEqual(starred[ids["b"][1]], 2)      # each star halves the set
        self.assertEqual(starred[ids["c"][1]], 1)      # the top third's tail
        self.assertEqual(starred[ids["d"][1]], 1)
        self.assertEqual(starred[ids["j"][1]], 0)      # ranked, below the third: bare
        self.assertTrue(all(0 <= s <= 5 for s in starred.values()))
        # seen fewer than three rounds: ranked but not yet starred
        fresh_id, fresh = self._identified("Raws/fresh.CR2")
        rank.record(self.conn, fresh_id, [ids["j"][0]])
        self.assertNotIn(fresh, rank.stars(rank.strength(self.conn), rank.seen(self.conn)))
        # and a sidecar star stays in the log without touching the column
        decisions.decide(self.conn, ids["j"][1], decisions.STAR, 5)
        self.assertNotIn(decisions.STAR, decisions.PROJECTED)
        library_surface.rerank(self.conn)
        rows = {row["tail"]: (row["elo"], row["stars"]) for row in self.conn.execute("SELECT tail, elo, stars FROM images")}
        self.assertEqual(rows["Raws/a.CR2"][1], 5)
        self.assertEqual(rows["Raws/j.CR2"][1], 0)     # ranked, below the third
        self.assertGreater(rows["Raws/a.CR2"][0], rows["Raws/j.CR2"][0])
        self.assertEqual(rows["Raws/fresh.CR2"][1], 0)
        # taking the rounds back empties the order again
        for row in self.conn.execute("SELECT id FROM decisions WHERE family = 'compare' AND value LIKE '%over%'").fetchall():
            rank.retract(self.conn, row["id"])
        library_surface.rerank(self.conn)
        self.assertEqual({row[0] for row in self.conn.execute("SELECT stars FROM images")}, {0})
        self.assertEqual({row[0] for row in self.conn.execute("SELECT elo FROM images")}, {rank.BASE})

    def test_every_shoot_keeps_a_keeper(self):
        # Five and four stars are absolute — portfolio currency — but the
        # top tenth of any shoot (never fewer than its best frame) reads
        # three even when the world ranked it below the global cut.
        strong = [self._identified(f"Raws/2026-01-01/s{i}.CR2") for i in range(10)]
        weak = [self._identified(f"Raws/2026-02-02/w{i}.CR2") for i in range(10)]
        for repeat in range(rank.EARNED):
            everyone = strong + weak
            for i in range(len(everyone) - 1):
                rank.record(self.conn, everyone[i][0], [everyone[i + 1][0]])
        shoots = {digest: f"Raws/2026-0{1 if (pid, digest) in strong else 2}-0{1 if (pid, digest) in strong else 2}"
                  for pid, digest in strong + weak}
        starred = rank.stars(rank.strength(self.conn), rank.seen(self.conn), shoots)
        best_weak = weak[0][1]
        self.assertEqual(starred[best_weak], 3, "the weak shoot keeps its keeper")
        self.assertLess(starred[weak[1][1]], 3, "one keeper, not a tide")
        self.assertLessEqual(
            max(starred[d] for _, d in weak), 3,
            "grace never mints a four or five — those are absolute")

    def test_the_worker_grows_the_space_from_the_tiles(self):
        # A vector is computed from the grid tile: owed only where a tile
        # exists, read from the tile file (so the archive drive may be away),
        # skipped entirely on a machine that cannot run the model, and keyed
        # by the recipe string the live backfill has been writing since 08-17.
        import struct

        import embed
        import numpy as np
        import render
        import tiles as tile_store
        import work

        store = tile_store.Store(os.path.join(self.tmp, "tiles"), ceiling_bytes=0)
        space = embed.kind(store)
        self.assertEqual(
            cache.canonical(space, {"model": embed.KEY}),
            '{"model":"google--siglip2-so400m-patch14-384@main:1152"}',
        )
        self.assertEqual(cache.canonical(space, {"model": embed.KEY}), embed.RECIPE)

        # a photograph with a tile but no reachable copy: the archive is away
        tiled_id, tiled = self._identified("Raws/2026/away.CR2")
        tile_path = store.path(tiled, render.GRID)
        os.makedirs(os.path.dirname(tile_path), exist_ok=True)
        Image.new("RGB", (64, 40), (90, 120, 200)).save(tile_path, "JPEG")
        cache.put(self.conn, tiled, store.grid, cache.Made(path=tile_path, bytes=os.path.getsize(tile_path)))
        # a photograph with no tile yet: not owed a vector at all
        self._identified("Raws/2026/untiled.CR2")
        self.conn.commit()

        recipe = {"model": embed.KEY}
        self.assertEqual(work.owing(self.conn, space, recipe=recipe), 1)

        read_from = []

        def fake_vector(source):
            read_from.append(source)
            with open(source, "rb") as handle:
                seed = struct.unpack("<I", handle.read(4).ljust(4, b"\0"))[0]
            out = np.random.default_rng(seed).normal(size=8).astype(np.float32)
            return out / np.linalg.norm(out)

        with patch.object(embed, "ready", lambda: False):
            self.assertIsNone(work.step(self.conn, (space,)))
        with patch.object(embed, "ready", lambda: True), patch.object(embed, "vector", fake_vector):
            did = work.step(self.conn, (space,))
        self.assertEqual(did, {"did": "embedding", "photo": tiled_id, "recipe": recipe})
        self.assertEqual(read_from, [tile_path])
        row = cache.get(self.conn, tiled, space, recipe)
        self.assertEqual(row["state"], cache.READY)
        self.assertEqual(len(row["value"]), 8 * 4)
        self.assertEqual(work.owing(self.conn, space, recipe=recipe), 0)

        subjects, vectors = rank.space(self.conn)
        self.assertEqual(subjects, [tiled])
        self.assertEqual(vectors.shape, (1, 8))

    def test_the_space_reaches_photographs_you_never_judged(self):
        # The propagation promise, end to end: four judged pairs along one
        # axis, two strangers with vectors and no rounds -- rerank writes the
        # stranger who looks like the winners above the one who looks like
        # the losers, stars stay earned-only, and retracting every round
        # returns the whole order to base.
        import embed
        import numpy as np

        axis = np.eye(8, dtype=np.float32)
        planted = []

        def plant(tail, vector):
            photo_id, digest = self._identified(tail)
            self.conn.execute(
                "INSERT INTO cache(hash, kind, recipe, state, value, bytes, at)"
                " VALUES (?, 'embedding', ?, 'ready', ?, ?, 1.0)",
                (digest, embed.RECIPE, vector.tobytes(), vector.nbytes),
            )
            planted.append((photo_id, digest))
            return photo_id, digest

        for pair in range(4):
            winner_id, _w = plant(f"Raws/win{pair}.CR2", axis[0])
            loser_id, _l = plant(f"Raws/lose{pair}.CR2", axis[1])
            for _ in range(rank.EARNED):
                rank.record(self.conn, winner_id, [loser_id])
        _sa_id, stranger_winner = plant("Raws/stranger-good.CR2", axis[0])
        _sb_id, stranger_loser = plant("Raws/stranger-bad.CR2", axis[1])
        self.conn.commit()

        library_surface.rerank(self.conn, *rank.space(self.conn))
        elo = {row["hash"]: row["elo"] for row in
               self.conn.execute("SELECT content_hash AS hash, elo FROM images WHERE content_hash IS NOT NULL")}
        stars = {row["hash"]: row["stars"] for row in
                 self.conn.execute("SELECT content_hash AS hash, stars FROM images WHERE content_hash IS NOT NULL")}
        self.assertGreater(elo[stranger_winner], elo[stranger_loser])
        self.assertNotEqual(elo[stranger_winner], rank.BASE)
        self.assertEqual(stars[stranger_winner], 0)      # predicted, never earned
        self.assertEqual(len([h for h, e in elo.items() if e != rank.BASE]), 10)

        for row in self.conn.execute(
                "SELECT id FROM decisions WHERE family = 'compare' AND value LIKE '%over%'").fetchall():
            rank.retract(self.conn, row["id"])
        library_surface.rerank(self.conn, *rank.space(self.conn))
        moved = self.conn.execute("SELECT COUNT(*) FROM images WHERE elo != ?", (rank.BASE,)).fetchone()[0]
        self.assertEqual(moved, 0)

    def test_judged_counts_photographs_in_the_scope_that_were_in_a_round(self):
        from model.scope import folder
        a, a_hash = self._identified("Raws/2026/a.CR2")
        b, b_hash = self._identified("Raws/2026/b.CR2")
        c, c_hash = self._identified("Snapshots/c.jpg")
        self.assertEqual(rank.judged(self.conn), 0)
        rank.record(self.conn, a, [b])
        self.assertEqual(rank.judged(self.conn), 2)
        self.assertEqual(rank.judged(self.conn, folder("Raws")), 2)
        self.assertEqual(rank.judged(self.conn, folder("Snapshots")), 0)
        rank.record(self.conn, c, [a])
        self.assertEqual(rank.judged(self.conn, folder("Snapshots")), 1)


class CriteriaAreExecutable(CoreCase):
    """The chip language: AND across, OR within, NOT null-safe, and a smart
    set is its rules where a fixed set is its members."""

    def _photo(self, tail, camera=None, taken=None, stars=0, status="unflagged"):
        photo_id = self.photo(tail)
        digest = hashlib.blake2b(tail.encode(), digest_size=32).hexdigest()
        self.conn.execute(
            "UPDATE images SET content_hash = ?, camera_model = ?, date_taken = ?,"
            " stars = ?, status = ? WHERE id = ?",
            (digest, camera, taken, stars, status, photo_id))
        self.conn.commit()
        return photo_id, digest

    def _matching(self, chips):
        from model import criteria

        clause, args = scope.where(criteria.compile(self.conn, chips))
        return {row["id"] for row in self.conn.execute(
            f"SELECT i.id FROM images i WHERE {clause}", args)}

    def test_chips_and_across_or_within_and_negate_null_safely(self):
        from model import criteria

        r5, _ = self._photo("Raws/a.CR2", camera="EOS R5", taken="2026-05-01 10:00:00", stars=4)
        rp, _ = self._photo("Raws/b.CR2", camera="EOS RP", taken="2026-06-01 10:00:00", stars=2)
        phone, _ = self._photo("Snapshots/c.jpg", camera="Pixel 3a", taken="2026-05-15 10:00:00")
        unread, _ = self._photo("Raws/d.CR2", camera=None, taken=None)

        either = self._matching([{"is": "camera", "values": ["EOS R5", "EOS RP"]}])
        self.assertEqual(either, {r5, rp})
        narrowed = self._matching([
            {"is": "camera", "values": ["EOS R5", "EOS RP"]},
            {"is": "stars", "least": 3},
        ])
        self.assertEqual(narrowed, {r5})
        # NOT includes the photograph that names no camera at all
        self.assertEqual(
            self._matching([{"is": "camera", "values": ["EOS R5"], "not": True}]),
            {rp, phone, unread})
        # a whole-day range is inclusive of its end day
        may = self._matching([{"is": "taken", "from": "2026-05-01", "to": "2026-05-15"}])
        self.assertEqual(may, {r5, phone})
        self.assertEqual(self._matching([{"is": "folder", "values": ["Raws"]}]), {r5, rp, unread})

        for bad in (
            [{"is": "sharpness", "least": 3}],
            [{"is": "stars", "least": 9}],
            [{"is": "camera", "values": []}],
            [{"is": "taken"}],
            [{"is": "camera", "values": ["x"], "size": 1}],
        ):
            with self.assertRaises(ValueError):
                criteria.check(bad)

    def test_a_smart_set_is_its_rules_and_freeze_is_dropping_them(self):
        from model import criteria

        keeper, keeper_hash = self._photo("Raws/keep.CR2", stars=4)
        passed, _ = self._photo("Raws/pass.CR2", stars=1)
        smart = sets.create(self.conn, "Best raws", criteria=[
            {"is": "folder", "values": ["Raws"]}, {"is": "stars", "least": 3}])

        def held(set_id):
            clause, args = scope.where(criteria.resolve(self.conn, set_id))
            return {row["id"] for row in self.conn.execute(
                f"SELECT i.id FROM images i WHERE {clause}", args)}

        self.assertEqual(held(smart), {keeper})
        # membership follows the facts, no rows written
        riser, riser_hash = self._photo("Raws/riser.CR2", stars=5)
        self.assertEqual(held(smart), {keeper, riser})

        # freeze: write the members, drop the rules -- and the set stops moving
        sets.add(self.conn, smart, [keeper_hash, riser_hash])
        sets.redefine(self.conn, smart, None)
        self.conn.execute("UPDATE images SET stars = 0 WHERE content_hash = ?", (riser_hash,))
        self.conn.commit()
        self.assertEqual(held(smart), {keeper, riser})
        self.assertNotIn("criteria", sets.describe(self.conn, smart))

        # an intersection is two `in` chips, and renaming keeps rules
        place = sets.create(self.conn, "Utah")
        sets.add(self.conn, place, [keeper_hash])
        both = sets.create(self.conn, "Utah keepers", criteria=[
            {"is": "in", "values": [smart]}, {"is": "in", "values": [place]}])
        self.assertEqual(held(both), {keeper})
        sets.rename(self.conn, both, "Utah best")
        self.assertEqual(sets.describe(self.conn, both)["criteria"][0]["is"], "in")

        # a cycle answers empty instead of recursing
        ouro = sets.create(self.conn, "Ouroboros", criteria=[{"is": "stars", "least": 1}])
        sets.redefine(self.conn, ouro, [{"is": "in", "values": [ouro]}])
        self.assertEqual(held(ouro), set())


class CollectionsAreOneSurface(CoreCase):
    """The product verbs over sets: the shelf, the pinned pair, Quick's one
    key, Freeze, and Save-view folding the view into chips."""

    def setUp(self):
        super().setUp()
        self.library = boot.Library(os.path.join(self.tmp, "catalog.db"),
                                    os.path.join(self.tmp, "tiles"))
        self.addCleanup(self.library.close)
        self.conn = self.library.conn

    def _photo(self, tail, stars=0):
        self.conn.execute("INSERT INTO images(tail, content_hash, stars) VALUES (?, ?, ?)",
                          (tail, hashlib.blake2b(tail.encode(), digest_size=32).hexdigest(), stars))
        self.conn.commit()
        row = self.conn.execute("SELECT id, content_hash AS hash FROM images WHERE tail = ?",
                                (tail,)).fetchone()
        return int(row["id"]), str(row["hash"])

    def _ids(self, scope_):
        clause, args = scope.where(scope_)
        return {int(r[0]) for r in self.conn.execute(
            f"SELECT i.id FROM images i WHERE {clause}", args)}

    def test_the_shelf_is_the_name_and_a_parent_is_the_union(self):
        utah_id, utah = self._photo("Raws/utah.CR2")
        cali_id, cali = self._photo("Raws/cali.CR2")
        lands_id, lands = self._photo("Raws/lands.CR2", stars=4)
        parent = self.library.create_album("America")
        child_a = self.library.create_album("America/Utah")
        child_b = self.library.create_album("America/California")
        self.library.add_to_album(child_a["id"], [utah_id])
        self.library.add_to_album(child_b["id"], [cali_id])
        self.assertEqual(self._ids(self.library._shelf(parent["id"])), {utah_id, cali_id})
        self.assertEqual(self._ids(self.library._shelf(child_a["id"])), {utah_id})

        # an intersection is a smart collection of two `in` chips
        lands_set = self.library.create_album("Landscapes")
        self.library.add_to_album(lands_set["id"], [utah_id, lands_id])
        both = self.library.create_album("Utah landscapes", chips=[
            {"is": "in", "values": [lands_set["id"]]},
            {"is": "in", "values": [child_a["id"]]},
        ])
        self.assertEqual(self._ids(self.library._shelf(both["id"])), {utah_id})

        # the view composes folder, collection and chips into one scope
        looking = self.library.viewing({"album": lands_set["id"],
                                        "chips": [{"is": "stars", "least": 3}]})
        self.assertEqual(self._ids(looking), {lands_id})

    def test_pinned_quick_freeze_and_save_view(self):
        a_id, _a = self._photo("Raws/a.CR2", stars=5)
        b_id, _b = self._photo("Raws/b.CR2")

        listed = {c["id"]: c for c in self.library.albums()}
        self.assertTrue(listed["quick"]["pinned"])
        self.assertTrue(listed["last-import"]["pinned"])
        with self.assertRaises(ValueError):
            self.library.forget_album("quick")

        # one key: in when any are out, out when all are in
        self.assertEqual(self.library.quick([a_id, b_id]), {"added": 2, "count": 2})
        self.assertEqual(self.library.quick([a_id, b_id]), {"removed": 2, "count": 0})
        self.assertEqual(self.library.quick([a_id]), {"added": 1, "count": 1})

        # A smart album takes exceptions: (rules ∪ pinned) ∖ denied. The
        # starless photograph pins in past the rules, the five-star one is
        # denied past them — the decade-old workaround the pro tools never
        # closed, closed.
        smart = self.library.create_album("Best", chips=[{"is": "stars", "least": 3}])
        self.assertEqual(self._ids(self.library._shelf(smart["id"])), {a_id})
        self.library.add_to_album(smart["id"], [b_id])
        self.assertEqual(self._ids(self.library._shelf(smart["id"])), {a_id, b_id})
        self.library.remove_from_album(smart["id"], [a_id])
        self.assertEqual(self._ids(self.library._shelf(smart["id"])), {b_id})
        self.assertEqual(self.library.freeze_album(smart["id"]), {"frozen": 1})
        self.assertEqual(self._ids(self.library._shelf(smart["id"])), {b_id})
        with self.assertRaises(ValueError):
            self.library.freeze_album(smart["id"])       # already plain

        saved = self.library.save_view("May raws", {
            "folders": ["Raws"], "chips": [{"is": "stars", "least": 3}]})
        rules = sets.describe(self.conn, saved["id"])["criteria"]
        self.assertEqual({chip["is"] for chip in rules}, {"folder", "stars"})
        with self.assertRaises(ValueError):
            self.library.save_view("Everything", {})

        kept = self.library.save_photos("Moment", [a_id, b_id])
        self.assertEqual(kept["kept"], 2)
        self.assertEqual(self._ids(self.library._shelf(kept["id"])), {a_id, b_id})


class SearchNeverRefuses(CoreCase):
    def setUp(self):
        super().setUp()
        self.library = boot.Library(os.path.join(self.tmp, "catalog.db"),
                                    os.path.join(self.tmp, "tiles"))
        self.addCleanup(self.library.close)
        self.conn = self.library.conn

    def _photo(self, tail, camera=None, taken=None):
        photo_id = self.photo(tail)
        digest = hashlib.blake2b(tail.encode(), digest_size=32).hexdigest()
        self.conn.execute(
            "UPDATE images SET content_hash = ?, camera_model = ?, date_taken = ? WHERE id = ?",
            (digest, camera, taken, photo_id))
        self.conn.commit()
        return photo_id, digest

    def test_words_find_files_cameras_and_named_sets(self):
        import search as finding

        by_name, _h1 = self._photo("Raws/2026/zion-hike.CR2")
        by_camera, _h2 = self._photo("Raws/2026/0001.CR2", camera="Canon EOS RP")
        named, named_hash = self._photo("Snapshots/2025/img.jpg")
        trashed, _h3 = self._photo("Raws/2026/zion-lost.CR2")
        self.conn.execute("UPDATE images SET status = 'trashed' WHERE id = ?", (trashed,))
        keyword = sets.create(self.conn, "travel/zion", kind=sets.LABEL)
        sets.add(self.conn, keyword, [named_hash])
        self.conn.commit()

        found = finding.search(self.conn, "zion")
        self.assertIn(by_name, found)
        self.assertIn(named, found)          # through the set's name
        self.assertNotIn(trashed, found)
        self.assertIn(by_camera, finding.search(self.conn, "canon"))
        self.assertEqual(finding.search(self.conn, "   "), [])

    def test_agreement_outranks_confidence_and_nothing_blocks_on_vectors(self):
        # RRF: a photograph two doors agree on beats either door's favourite.
        # And with no space at all the same query still answers -- degrading
        # is the design, not a fallback.
        import numpy as np

        import search as finding

        agreed, agreed_hash = self._photo("Raws/2026/sunset-01.CR2")
        word_only, _ = self._photo("Raws/2026/sunset-02.CR2")
        look_only, look_hash = self._photo("Raws/2026/0002.CR2")
        axis = np.eye(4, dtype=np.float32)
        space = ([agreed_hash, look_hash], np.stack([axis[0], axis[0]]))

        with_space = finding.search(self.conn, "sunset", space=space, query_vector=axis[0])
        self.assertEqual(with_space[0], agreed)
        self.assertEqual(set(with_space), {agreed, word_only, look_only})

        without = finding.search(self.conn, "sunset")
        self.assertEqual(set(without), {agreed, word_only})

    def test_a_teaching_answers_before_any_lane_runs(self):
        # Y and N recompute the one word inside teach() itself, against the
        # space the caller already holds — the word's view updates at the
        # keystroke, and searching the word is smarter everywhere: what the
        # owner said it is not never comes back.
        import numpy as np

        cat, cat_hash = self._photo("Raws/2026/cat-01.CR2")
        twin, twin_hash = self._photo("Raws/2026/cat-02.CR2")
        _dog, dog_hash = self._photo("Snapshots/2025/dog.jpg")
        axis = np.eye(4, dtype=np.float32)
        space = ([cat_hash, twin_hash, dog_hash], np.stack([axis[0], axis[0], axis[1]]))

        def worn():
            return {row["id"] for row in library_surface.photos(
                self.conn, scope=scope.label(["cats"]))}

        self.library.teach("cats", [cat], True, space=space)
        self.assertEqual(worn(), {cat, twin})   # anchor plus its lookalike, at once

        self.library.teach("cats", [twin], False, space=space)
        self.assertEqual(worn(), {cat})         # the exclusion holds at once

        found = [row["id"] for row in self.library.search("cats", space=space)["photos"]]
        self.assertIn(cat, found)
        self.assertNotIn(twin, found)           # denied is denied in search too

    def test_two_folders_are_one_view(self):
        # A shoot that spanned two days browses as their union — several
        # folders are one view, the same answer a folder chip with two
        # values gives.
        a, _ = self._photo("Raws/2026/2026-04-18/one.cr3")
        b, _ = self._photo("Raws/2026/2026-04-19/two.cr3")
        self._photo("Snapshots/other.jpg")
        looking = self.library.viewing(
            {"folders": ["Raws/2026/2026-04-18", "Raws/2026/2026-04-19"]})
        self.assertEqual(
            {row["id"] for row in library_surface.photos(self.conn, scope=looking)},
            {a, b})

    def test_days_sum_to_the_grid_and_sessions_wear_names(self):
        # The chapter list is index arithmetic: same scope, same order as the
        # newest-sorted page query, undated photographs one chapter at the
        # end. A session is the same walk with a rest gap, named by what
        # most of it wears.
        _a, a_hash = self._photo("Raws/2026/d1.CR2", taken="2026-07-11 10:00:00")
        _b, b_hash = self._photo("Raws/2026/d2.CR2", taken="2026-07-11 11:00:00")
        self._photo("Raws/2026/d3.CR2", taken="2026-06-01 09:00:00")
        self._photo("Raws/2026/d4.CR2")
        self.conn.executemany(
            "INSERT INTO cache (hash, kind, recipe, state, value, at)"
            " VALUES (?, 'label', '', 'ready', ?, 1)",
            [(a_hash, '["Cats"]'), (b_hash, '["Cats"]')])
        self.conn.commit()

        chapters = library_surface.days(self.conn)
        self.assertEqual([(row["day"], row["count"]) for row in chapters],
                         [("2026-07-11", 2), ("2026-06-01", 1), ("", 1)])
        self.assertEqual(sum(row["count"] for row in chapters),
                         library_surface.size(self.conn))

        shoots = library_surface.sessions(self.conn)
        self.assertEqual([s["count"] for s in shoots], [2, 1])
        self.assertEqual(shoots[0]["title"], "Jul 11 · Cats")
        self.assertEqual(shoots[0]["from"], "2026-07-11 10:00:00")
        self.assertEqual(shoots[0]["to"], "2026-07-11 11:00:00")

    def test_a_selection_finds_its_neighbours_and_never_itself(self):
        # More-like-this is the same search asked with photographs: the query
        # vector is the selection's centre in the space -- no words and no
        # model -- and the seeds stay out of their own answer.
        import numpy as np

        seed, seed_hash = self._photo("Raws/2026/dune-01.CR2")
        near, _near_hash = self._photo("Raws/2026/dune-02.CR2")
        _far, far_hash = self._photo("Snapshots/2025/cat.jpg")
        axis = np.eye(4, dtype=np.float32)
        space = ([seed_hash, _near_hash, far_hash],
                 np.stack([axis[0], axis[0], axis[1]]))

        answer = self.library.search("", like=[seed], space=space)
        ids = [row["id"] for row in answer["photos"]]
        self.assertEqual(ids[0], near)
        self.assertNotIn(seed, ids)

        # Before the first rank pass there is no space memo: no words plus no
        # vector answers empty, never an error.
        self.assertEqual(self.library.search("", like=[seed], space=None)["photos"], [])

    def test_one_photograph_is_one_result_however_many_rows_hold_it(self):
        import search as finding

        digest = hashlib.blake2b(b"the-same-bytes", digest_size=32).hexdigest()
        self.conn.executemany(
            "INSERT INTO images(tail, content_hash) VALUES (?, ?)",
            (("Snapshots/2025/beach.jpg", digest), ("Edits/2025/beach.jpg", digest)),
        )
        self.conn.commit()
        found = finding.search(self.conn, "beach")
        self.assertEqual(len(found), 1)

    def test_a_page_of_results_arrives_in_rank_order_with_renditions(self):
        first, _ = self._photo("Raws/2026/pier-b.CR2", taken="2026-01-01T00:00:00")
        second, _ = self._photo("Raws/2026/pier-a.CR2", taken="2026-06-01T00:00:00")
        import tiles as tile_store
        store = tile_store.Store(os.path.join(self.tmp, "tiles"), ceiling_bytes=0)
        answer = library_surface.photos(
            self.conn, scope=scope.ids([first, second]), sort="newest",
            limit=10, offset=0, renditions=store.renditions, reachable_on=[])
        self.assertEqual({row["id"] for row in answer}, {first, second})
        for row in answer:
            self.assertIn("tile", row)


class ALookIsAChromaFact(CoreCase):
    """Color, black & white, or sepia: chroma statistics over the lit pixels
    answer outright, and the Look chip reads the measured word."""

    def _tile(self, name, rgb):
        from PIL import Image

        path = os.path.join(self.tmp, name)
        Image.new("RGB", (64, 64), rgb).save(path, "JPEG", quality=95)
        return path

    def test_chroma_names_the_look(self):
        import photostats

        self.assertEqual(photostats.measure(self._tile("g.jpg", (128, 128, 128)))["look"], "bw")
        self.assertEqual(photostats.measure(self._tile("s.jpg", (140, 130, 115)))["look"], "sepia")
        self.assertEqual(photostats.measure(self._tile("c.jpg", (200, 80, 60)))["look"], "color")

    def test_the_look_chip_reads_the_measured_word(self):
        from model import criteria

        mono = self.photo("Raws/mono.cr3")
        vivid = self.photo("Raws/vivid.cr3")
        for pid, digest, look in ((mono, "aa" * 32, "bw"), (vivid, "bb" * 32, "color")):
            self.conn.execute("UPDATE images SET content_hash = ? WHERE id = ?", (digest, pid))
            self.conn.execute(
                "INSERT INTO cache (hash, kind, recipe, state, value, at)"
                " VALUES (?, 'photostats', '{}', 'ready', ?, 1)",
                (digest, '{"chroma_mean": 1.0, "look": "%s"}' % look))

        found = [r["id"] for r in library_surface.photos(
            self.conn, scope=criteria.compile(self.conn, [{"is": "look", "values": ["bw"]}]))]
        self.assertEqual(found, [mono])


class APlaceIsAFunctionOfTime(CoreCase):
    """The camera knows when, the phone knows where: a GPX track places
    every dated photograph inside its span, interpolated between points; a
    file that carries its own position is left alone."""

    GPX = """<?xml version="1.0"?>
<gpx xmlns="http://www.topografix.com/GPX/1/1" version="1.1">
 <trk><trkseg>
  <trkpt lat="37.0" lon="-118.0"><time>2026-05-26T18:00:00Z</time></trkpt>
  <trkpt lat="38.0" lon="-119.0"><time>2026-05-26T18:10:00Z</time></trkpt>
 </trkseg></trk></gpx>"""

    def _dated(self, tail, digest, when):
        pid = self.photo(tail)
        self.conn.execute(
            "UPDATE images SET content_hash = ?, date_taken = ? WHERE id = ?",
            (digest, when, pid))
        return pid

    def _track(self):
        path = os.path.join(self.tmp, "walk.gpx")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(self.GPX)
        return path

    def test_a_track_places_dated_photographs_between_its_points(self):
        import places

        digest = "ab" * 32
        self._dated("Raws/here.cr3", digest, "2026-05-26 18:05:00")
        self._dated("Raws/far.cr3", "cd" * 32, "2026-05-26 23:00:00")

        placed = places.adopt_track(self.conn, self._track(), offset_seconds=0)
        self.assertEqual(placed, 1, "only the photograph inside the span")
        held = places.of(self.conn, digest)
        self.assertAlmostEqual(held["lat"], 37.5, places=4)
        self.assertAlmostEqual(held["lon"], -118.5, places=4)
        # Reading the same track twice decides nothing new.
        self.assertEqual(places.adopt_track(self.conn, self._track(), offset_seconds=0), 0)

    def test_a_file_that_knows_its_own_place_is_left_alone(self):
        import places
        from model import cache as caching

        digest = "ef" * 32
        self._dated("Raws/phone.jpg", digest, "2026-05-26 18:05:00")
        self.conn.execute(
            "INSERT INTO cache (hash, kind, recipe, state, value, at)"
            " VALUES (?, 'metadata', '{}', 'ready', ?, 1)",
            (digest, '{"width": 100, "height": 100, "lat": 1.0, "lon": 2.0}'))
        self.assertEqual(places.adopt_track(self.conn, self._track(), offset_seconds=0), 0)
        self.assertIsNone(places.of(self.conn, digest), "the file already said")


class AStackIsACadence(CoreCase):
    """Only exactness makes a set: four or more frames on one repeated
    interval stack behind their first frame; anything less regular is just
    photographs. The stack is a projection over capture times, the collapse
    is one scope criterion, and the chip steps inside."""

    def _at(self, tail, when):
        pid = self.photo(tail)
        self.conn.execute("UPDATE images SET date_taken = ? WHERE id = ?", (when, pid))
        return pid

    def test_a_run_at_one_beat_stacks_behind_its_first_frame(self):
        import stacks

        run = [self._at(f"Raws/t{i}.cr3", f"2026-05-26 18:00:{i * 2:02d}") for i in range(5)]
        loner = self._at("Raws/loner.cr3", "2026-05-26 18:30:00")
        trio = [self._at(f"Raws/x{i}.cr3", f"2026-05-26 19:00:{i * 3:02d}") for i in range(3)]

        self.assertEqual(stacks.project(self.conn), 4)
        held = {r[0]: r[1] for r in self.conn.execute("SELECT id, stack_of FROM images")}
        self.assertIsNone(held[run[0]], "the first frame is the cover")
        self.assertTrue(all(held[i] == run[0] for i in run[1:]))
        self.assertIsNone(held[loner])
        self.assertTrue(all(held[i] is None for i in trio), "three frames are not a set")

    def test_a_burst_shares_one_second_and_still_stacks(self):
        import stacks

        burst = [self._at(f"Raws/b{i}.cr3", "2026-05-26 18:00:07") for i in range(4)]
        self.assertEqual(stacks.project(self.conn), 3)
        held = {r[0]: r[1] for r in self.conn.execute("SELECT id, stack_of FROM images")}
        self.assertTrue(all(held[i] == burst[0] for i in burst[1:]))

    def test_midnight_dates_carry_no_cadence(self):
        # Film scans arrive at exact midnight in batches; forty frames "in
        # one second" there is a save, not a burst.
        import stacks

        for i in range(6):
            self._at(f"Raws/scan{i}.tif", "2026-05-26 00:00:00")
        self.assertEqual(stacks.project(self.conn), 0)

    def test_the_collapse_hides_members_and_the_chip_steps_inside(self):
        import stacks
        from model import criteria
        from model.scope import all_of, covers_only

        run = [self._at(f"Raws/c{i}.cr3", f"2026-05-26 18:00:{i:02d}") for i in range(4)]
        stacks.project(self.conn)

        resting = [r["id"] for r in library_surface.photos(
            self.conn, scope=covers_only())]
        self.assertIn(run[0], resting)
        self.assertTrue(all(i not in resting for i in run[1:]))
        cover = next(r for r in library_surface.photos(self.conn, scope=covers_only())
                     if r["id"] == run[0])
        self.assertEqual(cover["stack"], 3, "the cover carries its member count")

        inside = [r["id"] for r in library_surface.photos(
            self.conn,
            scope=criteria.compile(self.conn, [{"is": "stack", "values": [str(run[0])]}]))]
        self.assertEqual(sorted(inside), sorted(run))


class AModeIsAnOrdering(CoreCase):
    """Rank's selection modes are small pure orderings over one pool —
    tournament seats the judged leaders, diverse spreads across the range,
    random is a walk — never a second candidate machinery."""

    def _pool(self):
        held = []
        for i in range(8):
            photo_id = self.photo(f"Raws/p{i}.cr3")
            digest = f"{i:064x}"
            self.conn.execute(
                "UPDATE images SET content_hash = ?, elo = ? WHERE id = ?",
                (digest, 1100 + i * 40, photo_id))
            held.append((photo_id, digest))
        self.conn.commit()
        return held

    def test_tournament_seats_the_leaders_and_diverse_spans_the_range(self):
        pool = self._pool()
        # Four have been through rounds; four never have.
        rank.record(self.conn, pool[7][0], [pool[6][0]])
        rank.record(self.conn, pool[5][0], [pool[4][0]])

        seated = rank.candidates(self.conn, 4, mode="tournament")
        # The court is the judged top of the table; within it the crown
        # circulates by wear, so the seating is a set, not a fixed order.
        self.assertEqual({p["id"] for p in seated},
                         {pool[7][0], pool[6][0], pool[5][0], pool[4][0]})

        spread = rank.candidates(self.conn, 3, mode="diverse")
        ratings = sorted(p["rating"] for p in spread)
        self.assertEqual(ratings[0], 1100.0)     # the bottom of the range
        self.assertEqual(ratings[-1], 1380.0)    # and the top

        drawn = rank.candidates(self.conn, 4, mode="random")
        self.assertEqual(len(drawn), 4)
        self.assertEqual(len({p["hash"] for p in drawn}), 4)

        # An unknown mode answers like the default rather than refusing —
        # a stale window after an update must still deal a hand.
        self.assertEqual(len(rank.candidates(self.conn, 4, mode="later-idea")), 4)

    def test_learn_teaches_where_unsure_and_finds_among_the_leaders(self):
        # The simulation's winner: teaching rounds are the most uncertain
        # window of the rating-sorted pool; once everything has been seen,
        # one round in three finds among the top band.
        pool = self._pool()
        rank.record(self.conn, pool[7][0], [pool[6][0]])
        rank.record(self.conn, pool[5][0], [pool[4][0]])

        held = rank._finding_round
        try:
            rank._finding_round = lambda: True   # would find — but not yet
            taught = rank.candidates(self.conn, 4, mode="learn")
            # Half the pool is unseen: no finding rounds before coverage,
            # and the teaching window is exactly the unseen (most
            # uncertain, ratings tied — one contiguous window).
            self.assertTrue(all(p["comparisons"] == 0 for p in taught))

            for i in range(0, 4):
                rank.record(self.conn, pool[i][0], [pool[(i + 1) % 4][0]])
            found = rank.candidates(self.conn, 4, mode="learn")
            tops = {p["hash"] for p in found}
            self.assertIn(pool[7][1], tops, "the finding round visits the leader")

            rank._finding_round = lambda: False
            taught = rank.candidates(self.conn, 4, mode="learn")
            self.assertEqual(len(taught), 4)
        finally:
            rank._finding_round = held


class AnEditIsADecision(CoreCase):
    """Develop's ground rules: Lightroom's sidecar reads into a decision in
    Lightroom's own spelling, the crop projects to a column, the rendition
    recipe built in SQL is byte-identical to the one Python stores under,
    and an edited photograph owes its cropped tile while keeping its plain
    one — because the embedding and the faces read the plain pixels."""

    SIDECAR = """<x:xmpmeta xmlns:x="adobe:ns:meta/">
 <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
  <rdf:Description rdf:about=""
    xmlns:crs="http://ns.adobe.com/camera-raw-settings/1.0/"
   crs:RawFileName="frame.cr3"
   crs:CropLeft="0.043210"
   crs:CropTop="0.1"
   crs:CropRight="0.9"
   crs:CropBottom="0.95"
   crs:CropAngle="0"
   crs:Exposure2012="+0.50">
   <crs:ToneCurvePV2012>
    <rdf:Seq>
     <rdf:li>0, 0</rdf:li>
     <rdf:li>255, 255</rdf:li>
    </rdf:Seq>
   </crs:ToneCurvePV2012>
  </rdf:Description>
 </rdf:RDF>
</x:xmpmeta>"""

    def test_a_sidecar_reads_whole_and_projects_its_crop(self):
        import develop as developing

        photo_id = self.photo("Raws/frame.cr3")
        digest = "d" * 64
        self.conn.execute("UPDATE images SET content_hash = ? WHERE id = ?", (digest, photo_id))
        folder = os.path.join(self.tmp, "Raws")
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "frame.xmp"), "w", encoding="utf-8") as handle:
            handle.write(self.SIDECAR)

        adopted = developing.adopt(self.conn, self.tmp, ["Raws/frame.xmp"])
        self.assertEqual(adopted, 1)
        held = developing.settings(self.conn, digest)
        self.assertEqual(held["Exposure2012"], "+0.50")        # spelling kept
        self.assertEqual(held["ToneCurvePV2012"], ["0, 0", "255, 255"])
        column = self.conn.execute(
            "SELECT develop FROM images WHERE id = ?", (photo_id,)).fetchone()["develop"]
        fragment = json.loads(column)
        self.assertEqual(fragment["CropLeft"], 0.04321)        # typed, kept
        self.assertEqual(fragment["Exposure2012"], 0.5)        # the slider renders
        self.assertNotIn("RawFileName", fragment)              # not a rendered key
        self.assertNotIn("CropAngle", fragment)                # zero says nothing

        # An unchanged sidecar appends nothing on the next sweep.
        self.assertEqual(developing.adopt(self.conn, self.tmp, ["Raws/frame.xmp"]), 0)

    def test_a_dng_is_its_own_sidecar(self):
        # Lightroom writes sidecars only beside proprietary raws; a DNG's
        # settings live in its own XMP packet. The sweep hands adopt no
        # sidecar for it — the unheard DNG is found and read by itself.
        import develop as developing

        photo_id = self.photo("Raws/inside.dng")
        digest = "f" * 64
        self.conn.execute(
            "UPDATE images SET content_hash = ?, file_ext = '.dng' WHERE id = ?",
            (digest, photo_id))
        folder = os.path.join(self.tmp, "Raws")
        os.makedirs(folder, exist_ok=True)
        packet = self.SIDECAR.replace("frame.cr3", "inside.dng")
        with open(os.path.join(folder, "inside.dng"), "wb") as handle:
            handle.write(b"II*\x00" + b"\x00" * 64 + packet.encode() + b"\x00" * 8)

        self.assertEqual(developing.adopt(self.conn, self.tmp, []), 1)
        held = developing.settings(self.conn, digest)
        self.assertEqual(held["Exposure2012"], "+0.50")
        # The next sweep re-reads nothing and appends nothing.
        self.assertEqual(developing.adopt(self.conn, self.tmp, []), 0)

    def test_a_dng_with_nothing_inside_gets_one_receipt(self):
        # The receipt — one empty decision — is what keeps every sweep from
        # re-reading megabytes of every settings-less DNG forever.
        import develop as developing

        photo_id = self.photo("Raws/plain.dng")
        digest = "a1" * 32
        self.conn.execute(
            "UPDATE images SET content_hash = ?, file_ext = '.dng' WHERE id = ?",
            (digest, photo_id))
        folder = os.path.join(self.tmp, "Raws")
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "plain.dng"), "wb") as handle:
            handle.write(b"II*\x00" + b"\x00" * 128)

        self.assertEqual(developing.adopt(self.conn, self.tmp, []), 1)
        self.assertEqual(developing.settings(self.conn, digest), {})
        self.assertEqual(developing.adopt(self.conn, self.tmp, []), 0)
        rows = self.conn.execute(
            "SELECT COUNT(*) FROM decisions WHERE subject = ?", (digest,)).fetchone()[0]
        self.assertEqual(rows, 1)

    def test_the_sql_recipe_splice_is_byte_identical_to_canonical(self):
        import develop as developing

        photo_id = self.photo("Raws/spliced.cr3")
        digest = "e" * 64
        self.conn.execute("UPDATE images SET content_hash = ? WHERE id = ?", (digest, photo_id))
        developing.edit(self.conn, digest, dict(zip(
            developing.CROP_KEYS, (0.043210, 0.1, 0.9, 0.95))))

        store = tiles.Store(os.path.join(self.tmp, "tiles"))
        param, expression = store.grid.keyed
        base = cache.canonical(store.grid, {})
        prefix = f'{{"{param}":'
        suffix = "," + base[1:] if base != "{}" else "}"
        spliced = self.conn.execute(
            f"SELECT ? || {expression} || ? FROM images i WHERE i.id = ?",
            (prefix, suffix, photo_id)).fetchone()[0]
        fragment = self.conn.execute(
            "SELECT develop FROM images WHERE id = ?", (photo_id,)).fetchone()["develop"]
        import json as coding

        self.assertEqual(spliced, cache.canonical(store.grid, {"edit": coding.loads(fragment)}))

    def test_an_edited_photo_owes_both_recipes_and_a_plain_one_owes_one(self):
        import develop as developing

        store = tiles.Store(os.path.join(self.tmp, "tiles"))
        edited = self.photo("Raws/edited.cr3")
        plain = self.photo("Raws/plain.cr3")
        self.conn.executemany(
            "UPDATE images SET content_hash = ? WHERE id = ?",
            [("a" * 64, edited), ("b" * 64, plain)])
        developing.edit(self.conn, "a" * 64, dict(zip(
            developing.CROP_KEYS, (0.25, 0.25, 0.75, 0.75))))

        base = [row["id"] for row in work.owed(self.conn, store.grid, recipe={})]
        keyed = [row["id"] for row in work.owed(self.conn, store.grid, recipe={}, keyed=True)]
        self.assertEqual(set(base), {edited, plain})   # plain pixels for everyone
        self.assertEqual(keyed, [edited])              # the edit owes its own look
        self.assertEqual(
            work.owing(self.conn, store.grid), 3)      # two plain, one cropped

    def test_the_owner_outranks_the_sidecar_and_uncrop_clears_the_column(self):
        import develop as developing

        photo_id = self.photo("Raws/mine.cr3")
        digest = "f" * 64
        self.conn.execute("UPDATE images SET content_hash = ? WHERE id = ?", (digest, photo_id))
        decisions.decide(self.conn, digest, developing.FAMILY,
                         dict(zip(developing.CROP_KEYS, (0.1, 0.1, 0.9, 0.9))),
                         by=developing.BY_FILE)
        developing.project(self.conn, digest)
        developing.edit(self.conn, digest, {key: None for key in developing.CROP_KEYS})
        self.assertIsNone(self.conn.execute(
            "SELECT develop FROM images WHERE id = ?", (photo_id,)).fetchone()["develop"])

    def test_write_back_replaces_the_crs_surface_and_keeps_everything_else(self):
        import develop as developing

        photo_id = self.photo("Raws/loop.cr3")
        digest = "b" * 64
        self.conn.execute("UPDATE images SET content_hash = ? WHERE id = ?", (digest, photo_id))
        folder = os.path.join(self.tmp, "Raws")
        os.makedirs(folder, exist_ok=True)
        raw = os.path.join(folder, "loop.cr3")
        with open(os.path.join(folder, "loop.xmp"), "w", encoding="utf-8") as handle:
            handle.write(self.SIDECAR.replace(
                'xmlns:crs="http://ns.adobe.com/camera-raw-settings/1.0/"',
                'xmlns:crs="http://ns.adobe.com/camera-raw-settings/1.0/"\n'
                '    xmlns:tiff="http://ns.adobe.com/tiff/1.0/"\n'
                '   tiff:Make="Canon"'))

        developing.adopt(self.conn, self.tmp, ["Raws/loop.xmp"])
        developing.edit(self.conn, digest, {"Exposure2012": 1.25, "CropLeft": None,
                                            "CropTop": None, "CropRight": None,
                                            "CropBottom": None})
        said = developing.write_sidecar(self.conn, digest, raw)
        self.assertEqual(said["status"], "written")

        back = developing.read_sidecar(said["target"])
        self.assertEqual(back["Exposure2012"], "+1.25")         # ours, Adobe-spelled
        self.assertNotIn("CropLeft", back)                       # the reset held
        self.assertEqual(back["ToneCurvePV2012"], ["0, 0", "255, 255"])
        text = open(said["target"], encoding="utf-8").read()
        self.assertIn("Canon", text)                             # the non-crs block survived

        # Writing again with nothing changed says so and touches nothing.
        self.assertEqual(developing.write_sidecar(self.conn, digest, raw)["status"], "unchanged")

    def test_the_sidecar_carries_the_verdicts_lightroom_reads(self):
        # Stars become xmp:Rating, a pick becomes the Green label (XMP has
        # no flag field Lightroom reads), and the taught words become
        # dc:subject keywords unioned with the file's own — Lightroom's
        # vocabulary is never dropped.
        import develop as developing

        photo_id = self.photo("Raws/told.cr3")
        digest = "d1" * 32
        self.conn.execute(
            "UPDATE images SET content_hash = ?, stars = 4, status = 'picked' WHERE id = ?",
            (digest, photo_id))
        self.conn.execute(
            "INSERT INTO cache (hash, kind, recipe, state, value, at)"
            " VALUES (?, 'alike', '{}', 'ready', ?, 1)", (digest, '["sunset"]'))
        folder = os.path.join(self.tmp, "Raws")
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "told.xmp"), "w", encoding="utf-8") as handle:
            handle.write(
                '<x:xmpmeta xmlns:x="adobe:ns:meta/">'
                '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
                '<rdf:Description rdf:about=""'
                ' xmlns:dc="http://purl.org/dc/elements/1.1/">'
                '<dc:subject><rdf:Bag><rdf:li>their-word</rdf:li></rdf:Bag></dc:subject>'
                '</rdf:Description></rdf:RDF></x:xmpmeta>')

        raw = os.path.join(folder, "told.cr3")
        said = developing.write_sidecar(self.conn, digest, raw)
        self.assertEqual(said["status"], "written")
        text = open(said["target"], encoding="utf-8").read()
        self.assertIn('Rating="4"', text)
        self.assertIn('Label="Green"', text)
        self.assertIn("their-word", text)                # theirs kept
        self.assertIn("sunset", text)                    # ours joined
        self.assertEqual(
            developing.write_sidecar(self.conn, digest, raw)["status"], "unchanged")

    def test_a_cropped_tile_is_cut_from_the_plain_loupe_without_the_original(self):
        import develop as developing
        from PIL import Image as Pillow

        store = tiles.Store(os.path.join(self.tmp, "tiles"))
        digest = "c" * 64
        image = Pillow.new("RGB", (1000, 800), (10, 200, 30))
        image.paste((250, 20, 20), (0, 0, 500, 800))    # left half red
        store._publish(store.path(digest, render.LOUPE), render.encode(image))

        made = store._answer("", digest, render.GRID,
                             dict(zip(developing.CROP_KEYS, (0.5, 0.0, 1.0, 1.0))))
        with Pillow.open(made.path) as cropped:
            width, height = cropped.size
            middle = cropped.getpixel((width // 2, height // 2))
        self.assertAlmostEqual(width / height, 500 / 800, places=1)
        self.assertGreater(middle[1], middle[0])         # the green half survived


class ASliderIsADecision(CoreCase):
    """The editing workspace's whole path: a slider release is one decision,
    the decision is the recipe, the recipe is the tile — and the preview
    asks without writing anything."""

    def setUp(self):
        super().setUp()
        self.library = boot.Library(os.path.join(self.tmp, "catalog.db"),
                                    os.path.join(self.tmp, "tiles"))
        self.addCleanup(self.library.close)
        self.conn = self.library.conn

    def _lit(self, path):
        from PIL import Image as Pillow

        with Pillow.open(path) as image:
            import numpy as np

            return float(np.asarray(image.convert("L"), dtype=np.float32).mean())

    def test_exposure_brightens_the_tile_and_none_takes_it_back(self):
        import develop as developing
        from PIL import Image as Pillow

        photo_id = self.photo("Raws/lit.cr3")
        digest = "9" * 64
        self.conn.execute("UPDATE images SET content_hash = ? WHERE id = ?", (digest, photo_id))
        self.conn.commit()
        plain = self.library.tiles.path(digest, render.LOUPE)
        self.library.tiles._publish(
            plain, render.encode(Pillow.new("RGB", (640, 420), (96, 96, 96))))

        said = self.library.develop(photo_id, {"Exposure2012": 1.0})
        self.assertTrue(said["made"])
        fragment = json.loads(said["develop"])
        self.assertEqual(fragment, {"Exposure2012": 1.0})

        held = self.conn.execute(
            "SELECT recipe, path, state FROM cache WHERE hash = ? AND kind = 'grid'"
            " AND recipe != '{}'", (digest,)).fetchone()
        self.assertEqual(held["state"], "ready")
        self.assertEqual(held["recipe"], '{"edit":{"Exposure2012":1.0}}')
        self.assertGreater(self._lit(held["path"]), self._lit(plain) + 20)
        # The loupe's edited answer belongs to the worker, not this verb:
        # at loupe size the pipeline is tens of seconds, and the verb holds
        # the library's one lane.
        self.assertEqual(
            [row["id"] for row in work.owed(self.conn, self.library.tiles.loupe,
                                            recipe={}, keyed=True)],
            [photo_id])

        # The preview asks with an uncommitted patch and writes nothing.
        decisions_before = self.conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]
        uri = self.library.develop_preview(photo_id, {"Exposure2012": -2.0}, size=320)
        self.assertIn(".look-", uri)          # a file the window loads like a tile
        self.assertIn("?look=", uri)          # named per look, cached never
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0],
            decisions_before)

        # None rests the slider; the photograph goes back to its plain answer.
        said = self.library.develop(photo_id, {"Exposure2012": None})
        self.assertIsNone(said["develop"])
        self.assertIsNone(self.conn.execute(
            "SELECT develop FROM images WHERE id = ?", (photo_id,)).fetchone()["develop"])
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM cache WHERE hash = ? AND kind IN ('grid','loupe')"
            " AND recipe != '{}'", (digest,)).fetchone()[0], 0)

    def test_a_setting_this_build_does_not_render_is_refused(self):
        photo_id = self.photo("Raws/strange.cr3")
        self.conn.execute("UPDATE images SET content_hash = ? WHERE id = ?", ("8" * 64, photo_id))
        self.conn.commit()
        with self.assertRaises(ValueError):
            self.library.develop(photo_id, {"PerspectiveUpright": 1})


class ASplitPersonHealsByName(CoreCase):
    """Two face groups introduced as the same person are one person: one
    shelf row, all their photographs, and the newest name outvoting the
    older ones a healed split still carries."""

    def _face(self, tail, direction):
        import base64
        import json

        import faces as facing
        import numpy as np

        photo_id = self.photo(tail)
        digest = hashlib.blake2b(tail.encode(), digest_size=32).hexdigest()
        self.conn.execute("UPDATE images SET content_hash = ? WHERE id = ?", (digest, photo_id))
        vec = np.zeros(512, dtype=np.float16)
        vec[direction] = 1.0
        value = json.dumps({"n": 1, "boxes": [[0.4, 0.3, 0.2, 0.2]], "scores": [0.9],
                            "vecs": base64.b64encode(vec.tobytes()).decode("ascii")})
        self.conn.execute(
            "INSERT OR REPLACE INTO cache (hash, kind, recipe, state, value, at)"
            " VALUES (?, 'faces', ?, 'ready', ?, 1)", (digest, facing.RECIPE, value))
        return digest

    def test_naming_two_groups_alike_folds_them_and_rename_wins(self):
        import people as persons

        for i in range(3):
            self._face(f"Raws/left-{i}.CR2", 0)
        for i in range(3):
            self._face(f"Raws/right-{i}.CR2", 7)
        self.conn.commit()

        persons.repeople(self.conn)
        held = {g["name"]: g for g in persons.groups(self.conn)}
        self.assertEqual(set(held), {"Someone 1", "Someone 2"})

        persons.name(self.conn, held["Someone 1"]["exemplar"], "Eris")
        persons.repeople(self.conn)
        held = {g["name"]: g for g in persons.groups(self.conn)}
        self.assertEqual(set(held), {"Eris", "Someone 1"})

        # The heal: the other half is introduced under the same name.
        persons.name(self.conn, held["Someone 1"]["exemplar"], "Eris")
        persons.repeople(self.conn)
        whole = persons.groups(self.conn)
        self.assertEqual([(g["name"], g["photos"]) for g in whole], [("Eris", 6)])

        # Renaming the healed person renames all of it — the new decision
        # outvotes both older names.
        persons.name(self.conn, whole[0]["exemplar"], "Iris")
        persons.repeople(self.conn)
        self.assertEqual([(g["name"], g["photos"]) for g in persons.groups(self.conn)],
                         [("Iris", 6)])


if __name__ == "__main__":
    unittest.main()
