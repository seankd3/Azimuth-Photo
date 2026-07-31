import asyncio
import io
import os
import zipfile
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from starlette.datastructures import UploadFile

from test_support import *  # noqa: F401,F403
from features.imports import card, film, staging


class StagedImportTests(BackendTestCase):
    async def _wait(self, job):
        await asyncio.wait_for(staging._tasks[job.id], timeout=10)

    async def _card_scan(self, card_root: Path, entries: list[dict]):
        scan = staging.Scan(
            id=f"scan-{os.urandom(6).hex()}", path=str(card_root), include_subfolders=True,
            card_source=True, status="done", entries=entries,
        )
        staging._scans[scan.id] = scan
        return scan

    def _entry(self, root: Path, path: Path) -> dict:
        stat = path.stat()
        return {
            "key": os.urandom(8).hex(), "name": path.name,
            "rel_path": str(path.relative_to(root)), "path": str(path),
            "size": stat.st_size, "mtime": stat.st_mtime, "taken_at": "2026-07-12 10:00:00",
            "kind": "video" if path.suffix.lower() in card.VIDEO_EXTENSIONS else "image",
            "suspect": False, "suspect_reason": "",
        }

    async def test_duplicate_at_destination_registers_before_card_clear(self):
        root = Path(self.tempdir.name)
        originals = root / "originals"
        old_root = os.environ.get("AZIMUTH_ORIGINALS_DIR")
        os.environ["AZIMUTH_ORIGINALS_DIR"] = str(originals)
        try:
            dcim = root / "CARD" / "DCIM"
            camera = dcim / "100CANON"
            camera.mkdir(parents=True)
            shot = camera / "CANON0001.CR3"
            shot.write_bytes(b"copied by a crashed import")

            # A previous import copied and verified this file but crashed before
            # registering it: bytes on disk, no catalog row.
            stranded_dir = originals / "RAWS" / "2026" / "2026-07-12"
            stranded_dir.mkdir(parents=True)
            stranded = stranded_dir / shot.name
            stranded.write_bytes(shot.read_bytes())

            scan = await self._card_scan(dcim, [self._entry(dcim, shot)])
            job = await staging.start_commit(scan, keys="all_checked_default", mode="copy", skip_suspects=True, clear_card=True, keyword_paths=[], collection_id=None)
            await self._wait(job)

            self.assertEqual(job.phase, "complete")
            self.assertEqual(job.skipped_duplicates, 1)
            self.assertFalse(shot.exists())
            content_hash, _full, _size = await asyncio.to_thread(card.content_hash_from_stream, stranded)
            registered = await staging.import_repository.image_paths_by_content_hash(db.DB_PATH, content_hash)
            self.assertIn(str(stranded), registered)
        finally:
            if old_root is None:
                os.environ.pop("AZIMUTH_ORIGINALS_DIR", None)
            else:
                os.environ["AZIMUTH_ORIGINALS_DIR"] = old_root

    async def test_staged_import_registers_aspect_at_insert(self):
        # Placeholder cards need final geometry from the first render
        # (docs/ASPECT_AT_SCAN_SPEC.md): a canvas import must store
        # orientation + aspect_ratio at registration, not wait for the
        # metadata worker.
        from PIL import Image

        root = Path(self.tempdir.name)
        originals = root / "originals"
        old_root = os.environ.get("AZIMUTH_ORIGINALS_DIR")
        os.environ["AZIMUTH_ORIGINALS_DIR"] = str(originals)
        try:
            dcim = root / "CARD" / "DCIM"
            camera = dcim / "100CANON"
            camera.mkdir(parents=True)
            shot = camera / "WIDE0001.JPG"
            Image.new("RGB", (400, 300)).save(shot, "JPEG")

            scan = await self._card_scan(dcim, [self._entry(dcim, shot)])
            job = await staging.start_commit(scan, keys="all_checked_default", mode="copy", skip_suspects=True, clear_card=False, keyword_paths=[], collection_id=None)
            await self._wait(job)
            self.assertEqual(job.phase, "complete")

            from data import connection
            conn = await connection.open_async(db.DB_PATH)
            try:
                cursor = await conn.execute(
                    "SELECT orientation, aspect_ratio FROM images WHERE filename = ?",
                    (shot.name,),
                )
                row = await cursor.fetchone()
            finally:
                await connection.close_async(conn, db_path=db.DB_PATH)
            self.assertIsNotNone(row)
            self.assertEqual(row["orientation"], "landscape")
            self.assertAlmostEqual(float(row["aspect_ratio"]), 400 / 300, places=3)
        finally:
            if old_root is None:
                os.environ.pop("AZIMUTH_ORIGINALS_DIR", None)
            else:
                os.environ["AZIMUTH_ORIGINALS_DIR"] = old_root

    async def test_alpha_png_preview_encodes_as_jpeg(self):
        from PIL import Image

        root = Path(self.tempdir.name)
        source = root / "alpha.png"
        Image.new("RGBA", (64, 48), (200, 60, 60, 128)).save(source)
        scan = await self._card_scan(root, [self._entry(root, source)])
        scan.card_source = False
        data = staging.thumbnail_bytes(scan, scan.entries[0])
        self.assertEqual(data[:3], b"\xff\xd8\xff")

    async def test_mixed_folder_classifies_itself_from_provenance(self):
        from PIL import Image

        root = Path(self.tempdir.name)
        originals = root / "library"
        old_root = os.environ.get("AZIMUTH_ORIGINALS_DIR")
        os.environ["AZIMUTH_ORIGINALS_DIR"] = str(originals)
        try:
            shoot = root / "dump"
            shoot.mkdir()
            (shoot / "CANON0001.CR3").write_bytes(b"raw camera bytes")
            phone_exif = Image.Exif()
            phone_exif[271] = "Google"
            phone_exif[272] = "Pixel 10a"
            Image.new("RGB", (32, 24), (10, 90, 40)).save(shoot / "PXL_PLAIN.jpg", exif=phone_exif)
            edited_exif = Image.Exif()
            edited_exif[305] = "Adobe Lightroom 14.2 (Windows)"
            Image.new("RGB", (32, 24), (90, 10, 40)).save(shoot / "final-edit.jpg", exif=edited_exif)
            Image.new("RGB", (32, 24), (40, 10, 90)).save(
                shoot / "roll12-frame08.tif", tiffinfo={271: "EPSON", 272: "Perfection V600"}
            )

            scan = staging.Scan(
                id="scan-classify", path=str(shoot), include_subfolders=False,
                card_source=False, status="done",
            )
            staging._enumerate_scan(scan)
            by_name = {entry["name"]: entry for entry in scan.entries}
            self.assertEqual(by_name["CANON0001.CR3"]["category"], "raw")
            self.assertEqual(by_name["PXL_PLAIN.jpg"]["category"], "personal")
            self.assertEqual(by_name["final-edit.jpg"]["category"], "export")
            self.assertEqual(by_name["roll12-frame08.tif"]["category"], "film")

            staging._scans[scan.id] = scan
            job = await staging.start_commit(scan, keys="all_checked_default", mode="copy", skip_suspects=True, clear_card=False, keyword_paths=[], collection_id=None)
            await self._wait(job)
            self.assertEqual(job.phase, "complete")
            landed = {path.name: path for path in originals.rglob("*") if path.is_file()}
            self.assertIn("Raws", str(landed["CANON0001.CR3"]))
            self.assertIn("Snapshots", str(landed["PXL_PLAIN.jpg"]))
            self.assertIn("Edits", str(landed["final-edit.jpg"]))
            self.assertIn(str(Path("Raws") / "Film Scans"), str(landed["roll12-frame08.tif"]))
        finally:
            if old_root is None:
                os.environ.pop("AZIMUTH_ORIGINALS_DIR", None)
            else:
                os.environ["AZIMUTH_ORIGINALS_DIR"] = old_root

    async def test_category_correction_is_remembered_for_the_source(self):
        from PIL import Image

        root = Path(self.tempdir.name)
        originals = root / "library"
        old_root = os.environ.get("AZIMUTH_ORIGINALS_DIR")
        os.environ["AZIMUTH_ORIGINALS_DIR"] = str(originals)
        try:
            scans_dir = root / "scanner-drops"
            scans_dir.mkdir()
            Image.new("RGB", (32, 24), (5, 5, 5)).save(scans_dir / "frame01.jpg")

            scan = staging.Scan(id="scan-mem1", path=str(scans_dir), include_subfolders=False, card_source=False, status="done")
            staging._enumerate_scan(scan)
            self.assertEqual(scan.entries[0]["category"], "raw")  # bare JPEG defaults to Raws
            staging._scans[scan.id] = scan
            job = await staging.start_commit(scan, keys="all_checked_default", mode="copy", skip_suspects=True, clear_card=False, keyword_paths=[], collection_id=None, category="film")
            await self._wait(job)
            self.assertEqual(job.phase, "complete")
            landed = [path for path in originals.rglob("frame01*") if path.is_file()]
            self.assertIn("Film Scans", str(landed[0]))

            # The correction sticks: the same source now classifies itself.
            rescan = staging.Scan(id="scan-mem2", path=str(scans_dir), include_subfolders=False, card_source=False, status="done")
            staging._enumerate_scan(rescan)
            self.assertEqual(rescan.entries[0]["category"], "film")
        finally:
            if old_root is None:
                os.environ.pop("AZIMUTH_ORIGINALS_DIR", None)
            else:
                os.environ["AZIMUTH_ORIGINALS_DIR"] = old_root

    async def test_junk_directories_and_appledouble_files_are_fenced_out(self):
        from PIL import Image

        root = Path(self.tempdir.name) / "messy"
        (root / ".lrt" / "previews").mkdir(parents=True)
        (root / "PreviewCache").mkdir()
        (root / "__MACOSX").mkdir()
        Image.new("RGB", (16, 12)).save(root / "keeper.jpg")
        Image.new("RGB", (16, 12)).save(root / ".lrt" / "previews" / "cache0001.jpg")
        Image.new("RGB", (16, 12)).save(root / "PreviewCache" / "cache0002.jpg")
        Image.new("RGB", (16, 12)).save(root / "__MACOSX" / "._keeper.jpg")
        Image.new("RGB", (16, 12)).save(root / "._sidecar.jpg")

        scan = staging.Scan(id="scan-junk", path=str(root), include_subfolders=True, card_source=False, status="done")
        staging._enumerate_scan(scan)
        self.assertEqual([entry["name"] for entry in scan.entries], ["keeper.jpg"])

    async def test_card_copy_collisions_duplicates_clear_and_rerun(self):
        root = Path(self.tempdir.name)
        originals = root / "originals"
        old_root = os.environ.get("AZIMUTH_ORIGINALS_DIR")
        os.environ["AZIMUTH_ORIGINALS_DIR"] = str(originals)
        try:
            camera = root / "CARD" / "DCIM"
            first_dir, second_dir = camera / "100CANON", camera / "101CANON"
            first_dir.mkdir(parents=True)
            second_dir.mkdir()
            one = first_dir / "CANON9999.CR3"
            two = second_dir / "CANON9999.CR3"
            duplicate = second_dir / "DUPE.CR3"
            broken = second_dir / "YANK.CR3"
            one.write_bytes(b"first camera file")
            two.write_bytes(b"second camera file")
            duplicate.write_bytes(b"already cataloged")
            broken.write_bytes(b"will be yanked")

            known_dir = root / "known"
            known_dir.mkdir()
            known = known_dir / duplicate.name
            known.write_bytes(duplicate.read_bytes())
            source = await db.add_or_restore_source(str(known_dir))
            await db.insert_images_batch([(known.name, str(known), ".cr3", known.stat().st_size, known.stat().st_mtime)], source_id=source["id"])
            known_hash, _full, _size = await asyncio.to_thread(card.content_hash_from_stream, known)
            await staging.import_repository.set_image_content_hash(db.DB_PATH, str(known), known_hash)

            # A card yanked during its file leaves that original untouched and
            # unregistered; a rerun after reinsertion heals it.
            original_copy = card.copy_verified

            def copy_or_yank(source_path, directory, **kwargs):
                if source_path == str(broken):
                    raise OSError("card was removed during copy")
                return original_copy(source_path, directory, **kwargs)

            card.copy_verified = copy_or_yank
            entries = [self._entry(camera, path) for path in (one, two, duplicate)]
            entries.append(self._entry(camera, broken))
            scan = await self._card_scan(camera, entries)
            job = await staging.start_commit(scan, keys="all_checked_default", mode="copy", skip_suspects=True, clear_card=True, keyword_paths=[], collection_id=None)
            await self._wait(job)
            card.copy_verified = original_copy

            self.assertEqual(job.phase, "complete")
            self.assertEqual(job.skipped_duplicates, 1)
            landed = sorted((originals / "RAWS" / "2026" / "2026-07-12").glob("CANON9999*"))
            self.assertEqual([path.name for path in landed], ["CANON9999-2.CR3", "CANON9999.CR3"])
            self.assertFalse(one.exists())
            self.assertFalse(two.exists())
            self.assertFalse(duplicate.exists())
            self.assertTrue(broken.exists())
            self.assertEqual(len(job.errors), 1)

            broken.write_bytes(b"will be yanked")
            repair_scan = await self._card_scan(camera, [self._entry(camera, broken)])
            repair = await staging.start_commit(repair_scan, keys="all_checked_default", mode="copy", skip_suspects=True, clear_card=True, keyword_paths=[], collection_id=None)
            await self._wait(repair)
            self.assertEqual(repair.phase, "complete")
            self.assertFalse(broken.exists())

            empty_scan = await self._card_scan(camera, [])
            no_op = await staging.start_commit(empty_scan, keys="all_checked_default", mode="copy", skip_suspects=True, clear_card=True, keyword_paths=[], collection_id=None)
            await self._wait(no_op)
            self.assertEqual(no_op.status()["files_done"], 0)
            self.assertEqual(no_op.status()["skipped_duplicates"], 0)
        finally:
            if old_root is None:
                os.environ.pop("AZIMUTH_ORIGINALS_DIR", None)
            else:
                os.environ["AZIMUTH_ORIGINALS_DIR"] = old_root

    async def test_cancel_mid_job_persists_partial_batch_without_clearing_uncopied_card_file(self):
        root = Path(self.tempdir.name)
        originals = root / "originals"
        old_root = os.environ.get("AZIMUTH_ORIGINALS_DIR")
        os.environ["AZIMUTH_ORIGINALS_DIR"] = str(originals)
        try:
            camera = root / "CARD" / "DCIM" / "100CANON"
            camera.mkdir(parents=True)
            first = camera / "FIRST.CR3"
            second = camera / "SECOND.CR3"
            first.write_bytes(b"first safely copied file")
            second.write_bytes(b"second must remain on card")
            scan = await self._card_scan(
                root / "CARD" / "DCIM",
                [self._entry(root / "CARD" / "DCIM", first), self._entry(root / "CARD" / "DCIM", second)],
            )

            original_import_entry = staging._import_entry

            async def cancel_after_first(job, entry):
                await original_import_entry(job, entry)
                if entry["name"] == first.name:
                    staging.request_cancel(job)

            with patch.object(staging, "_import_entry", side_effect=cancel_after_first):
                job = await staging.start_commit(
                    scan,
                    keys="all_checked_default",
                    mode="copy",
                    skip_suspects=True,
                    clear_card=True,
                    keyword_paths=[],
                    collection_id=None,
                )
                await self._wait(job)

            batch = await staging.import_repository.import_batch(db.DB_PATH, job.batch_id)
            self.assertEqual(job.phase, "cancelled")
            self.assertEqual(batch["status"], "cancelled")
            self.assertEqual(batch["imported_files"], 1)
            self.assertEqual(batch["total_files"], 2)
            self.assertEqual(len(batch["images"]), 1)
            self.assertTrue(Path(batch["images"][0]["filepath"]).is_file())
            self.assertFalse(first.exists(), "a verified and registered copy may be cleared from the card")
            self.assertTrue(second.exists(), "an uncopied card original must never be cleared")
        finally:
            if old_root is None:
                os.environ.pop("AZIMUTH_ORIGINALS_DIR", None)
            else:
                os.environ["AZIMUTH_ORIGINALS_DIR"] = old_root

    async def test_scan_hints_are_incremental_and_add_keeps_files_in_place(self):
        root = Path(self.tempdir.name)
        incoming = root / "incoming"
        incoming.mkdir()
        image = incoming / "same.jpg"
        image.write_bytes(b"new payload")
        existing = root / "existing"
        existing.mkdir()
        (existing / "same.jpg").write_bytes(b"old payload")
        source = await db.add_or_restore_source(str(existing))
        old = existing / "same.jpg"
        await db.insert_images_batch([(old.name, str(old), ".jpg", old.stat().st_size, old.stat().st_mtime)], source_id=source["id"])
        await db.add_or_restore_source(str(incoming))

        old_root = os.environ.get("AZIMUTH_ORIGINALS_DIR")
        os.environ["AZIMUTH_ORIGINALS_DIR"] = str(root / "originals")
        try:
            scan = await staging.start_scan(str(incoming), False)
            await asyncio.wait_for(staging._tasks[scan.id], timeout=10)
            page = staging.scan_page(scan, 0)
            self.assertEqual(page["status"], "done")
            self.assertEqual(page["total_seen"], 1)
            self.assertTrue(page["entries"][0]["suspect"])
            job = await staging.start_commit(scan, keys=[page["entries"][0]["key"]], mode="add", skip_suspects=True, clear_card=False, keyword_paths=[], collection_id=None)
            await self._wait(job)
            self.assertEqual(job.phase, "complete")
            self.assertTrue(image.exists())
            self.assertEqual(job.skipped_duplicates, 0)
        finally:
            if old_root is None:
                os.environ.pop("AZIMUTH_ORIGINALS_DIR", None)
            else:
                os.environ["AZIMUTH_ORIGINALS_DIR"] = old_root

    def _tiff_bytes(self, color=(20, 20, 20)) -> bytes:
        from PIL import Image

        buffer = io.BytesIO()
        Image.new("RGB", (24, 16), color).save(buffer, "TIFF")
        return buffer.getvalue()

    async def test_film_zip_lands_under_film_scans_archive_folder(self):
        root = Path(self.tempdir.name)
        originals = root / "library"
        old_root = os.environ.get("AZIMUTH_ORIGINALS_DIR")
        os.environ["AZIMUTH_ORIGINALS_DIR"] = str(originals)
        try:
            archive = io.BytesIO()
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr("scans/frame01.tif", self._tiff_bytes((10, 10, 10)))
                bundle.writestr("scans/deep/frame02.tif", self._tiff_bytes((20, 20, 20)))
                bundle.writestr("__MACOSX/._frame01.tif", b"resource fork junk")
                bundle.writestr("../evil.tif", self._tiff_bytes((30, 30, 30)))
                bundle.writestr("scans/notes.txt", b"lab notes")
            with patch.object(film, "staging_root", return_value=root / "filmstage"):
                staged = await film.stage_uploads(
                    [UploadFile(io.BytesIO(archive.getvalue()), filename="roll12.zip")]
                )
                self.assertEqual(staged["label"], "roll12")
                self.assertEqual(staged["staged_files"], 3)
                # The traversal member was flattened inside the batch dir, not written outside it.
                extracted = sorted(path.name for path in Path(staged["path"]).rglob("*") if path.is_file())
                self.assertEqual(extracted, ["evil.tif", "frame01.tif", "frame02.tif"])

                scan = await staging.start_film_scan(staged["path"], label=staged["label"])
                await asyncio.wait_for(staging._tasks[scan.id], timeout=10)
                self.assertEqual(scan.status, "done")
                self.assertEqual({entry["category"] for entry in scan.entries}, {"film"})

                with self.assertRaises(ValueError):
                    await staging.start_commit(scan, keys="all_checked_default", mode="add", skip_suspects=True, clear_card=False, keyword_paths=[], collection_id=None)
                job = await staging.start_commit(scan, keys="all_checked_default", mode="copy", skip_suspects=True, clear_card=False, keyword_paths=[], collection_id=None)
                await self._wait(job)
                self.assertEqual(job.phase, "complete")
                self.assertEqual(job.errors, [])

                landed = sorted(path for path in originals.rglob("*") if path.is_file())
                # One archive = one folder named from the archive filename — never a date tree.
                self.assertEqual(
                    sorted(path.name for path in landed),
                    ["evil.tif", "frame01.tif", "frame02.tif"],
                )
                for path in landed:
                    self.assertEqual(path.parent, originals / "Raws" / "Film Scans" / "roll12")
                batch = await staging.import_repository.import_batch(db.DB_PATH, job.batch_id)
                self.assertEqual(batch["name"], "roll12")
                # A clean full commit reclaims the transient extraction dir.
                self.assertFalse(Path(staged["path"]).exists())
                self.assertIsNone(staging.scan_for_id(scan.id))
        finally:
            if old_root is None:
                os.environ.pop("AZIMUTH_ORIGINALS_DIR", None)
            else:
                os.environ["AZIMUTH_ORIGINALS_DIR"] = old_root

    async def test_film_loose_tiffs_batch_together_and_collide_safely(self):
        root = Path(self.tempdir.name)
        originals = root / "library"
        old_root = os.environ.get("AZIMUTH_ORIGINALS_DIR")
        os.environ["AZIMUTH_ORIGINALS_DIR"] = str(originals)
        try:
            with patch.object(film, "staging_root", return_value=root / "filmstage"):
                staged = await film.stage_uploads([
                    UploadFile(io.BytesIO(self._tiff_bytes((11, 11, 11))), filename="frame.tif"),
                    UploadFile(io.BytesIO(self._tiff_bytes((22, 22, 22))), filename="frame.tif"),
                ])
                self.assertEqual(staged["staged_files"], 2)
                self.assertEqual(staged["skipped"], [])
                scan = await staging.start_film_scan(staged["path"], label=staged["label"])
                await asyncio.wait_for(staging._tasks[scan.id], timeout=10)
                job = await staging.start_commit(scan, keys="all_checked_default", mode="copy", skip_suspects=True, clear_card=False, keyword_paths=[], collection_id=None)
                await self._wait(job)
                self.assertEqual(job.phase, "complete")
                landed = sorted(path.name for path in (originals / "Raws" / "Film Scans" / staged["label"]).glob("*"))
                self.assertEqual(landed, ["frame-2.tif", "frame.tif"])
        finally:
            if old_root is None:
                os.environ.pop("AZIMUTH_ORIGINALS_DIR", None)
            else:
                os.environ["AZIMUTH_ORIGINALS_DIR"] = old_root

    async def test_film_route_refuses_rar_and_empty_uploads_honestly(self):
        root = Path(self.tempdir.name)
        with patch.object(film, "staging_root", return_value=root / "filmstage"):
            with TestClient(app_module.app) as client:
                response = client.post(
                    "/api/import/film",
                    files=[("files", ("negatives.rar", b"Rar!\x1a\x07\x00", "application/vnd.rar"))],
                )
                self.assertEqual(response.status_code, 400)
                self.assertIn("ZIP", response.json()["error"])
                response = client.post(
                    "/api/import/film",
                    files=[("files", ("notes.txt", b"not a scan", "text/plain"))],
                )
                self.assertEqual(response.status_code, 400)
            # Refused uploads leave no staging spill behind.
            self.assertEqual([path for path in (root / "filmstage").rglob("*")], [])

    async def test_routes_reject_paths_and_previews_without_scan_keys(self):
        root = Path(self.tempdir.name)
        settings.save_settings({"import_root": str(root)})
        with TestClient(app_module.app) as client:
            self.assertEqual(client.get("/api/import/browse", params={"path": "/"}).status_code, 400)
            self.assertEqual(client.get("/api/import/scan/not-a-scan/thumb/raw-path").status_code, 404)
