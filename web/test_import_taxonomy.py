"""Library taxonomy routing — four destinations + phone-under-RAWs regression."""

from __future__ import annotations

import asyncio
import errno
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image

from test_support import *  # noqa: F401,F403
from features.imports import card, staging, taxonomy
from features.sync import hub, hub_routes


class TaxonomyRoutingTableTests(unittest.TestCase):
    def test_raw_camera_extensions_route_to_raws(self):
        for name in ("shot.CR3", "a.cr2", "b.ARW", "c.nef", "d.raf", "e.dng", "f.orf", "g.rw2"):
            self.assertEqual(
                taxonomy.route_destination(filename=name, source_kind="camera_card"),
                taxonomy.DEST_RAWS,
                name,
            )

    def test_phone_source_routes_stills_and_phone_dng_to_personal(self):
        for name in ("PXL_1.jpg", "IMG_2.HEIC", "shot.heif", "PXL_3.dng"):
            self.assertEqual(
                taxonomy.route_destination(filename=name, source_kind="phone"),
                taxonomy.DEST_PERSONAL,
                name,
            )

    def test_phone_folder_hint_is_sibling_not_nested(self):
        dest = taxonomy.destination_directory(
            "/library",
            filename="PXL_1.jpg",
            year="2026",
            day="2026-07-10",
            folder_hint="Personal Photos",
        )
        self.assertEqual(
            dest,
            Path("/library/Personal Photos/2026/2026-07-10"),
        )
        self.assertNotIn("RAWS", dest.parts)

    def test_export_and_film_scan_sources(self):
        self.assertEqual(
            taxonomy.route_destination(filename="edit.jpg", source_kind="export"),
            taxonomy.DEST_EXPORTS,
        )
        self.assertEqual(
            taxonomy.route_destination(filename="scan.tif", source_kind="film_scan"),
            taxonomy.DEST_FILM,
        )
        self.assertEqual(
            taxonomy.route_destination(filename="scan.TIFF", source_kind="unknown"),
            taxonomy.DEST_FILM,
        )

    def test_heic_without_source_still_personal(self):
        self.assertEqual(
            taxonomy.route_destination(filename="roll.HEIC"),
            taxonomy.DEST_PERSONAL,
        )

    def test_camera_jpeg_without_phone_source_stays_raws(self):
        # Camera-card JPEG companions and legacy sync seeds must not flip to Personal.
        self.assertEqual(
            taxonomy.route_destination(filename="IMG_0001.JPG", source_kind="camera_card"),
            taxonomy.DEST_RAWS,
        )
        self.assertEqual(
            taxonomy.route_destination(filename="seed.jpg"),
            taxonomy.DEST_RAWS,
        )

    def test_infer_phone_from_path_markers(self):
        self.assertEqual(
            taxonomy.infer_source_kind(
                filename="x.jpg",
                path="/mnt/phone/Camera Roll/x.jpg",
            ),
            "phone",
        )
        self.assertEqual(
            taxonomy.infer_source_kind(
                filename="x.tif",
                path="/mnt/expansion/Photos/Film Scans/lab/x.tif",
            ),
            "film_scan",
        )

    def test_windows_phone_camera_path_routes_to_personal_photos(self):
        kind = taxonomy.infer_source_kind(
            filename="PXL_20260715.jpg",
            path=r"D:\DCIM\Camera\PXL_20260715.jpg",
        )

        self.assertEqual(kind, "phone")
        self.assertEqual(
            taxonomy.route_destination(filename="PXL_20260715.jpg", source_kind=kind),
            taxonomy.DEST_PERSONAL,
        )


class CardPlatformTests(unittest.TestCase):
    def test_windows_fixed_drive_is_eligible_only_when_it_exposes_dcim(self):
        self.assertTrue(card._is_windows_card_drive(card.WINDOWS_DRIVE_REMOVABLE, has_dcim=False))
        self.assertTrue(card._is_windows_card_drive(card.WINDOWS_DRIVE_FIXED, has_dcim=True))
        self.assertFalse(card._is_windows_card_drive(card.WINDOWS_DRIVE_FIXED, has_dcim=False))

    def test_copy_verified_falls_back_when_hardlinks_are_unsupported(self):
        with tempfile.TemporaryDirectory() as root:
            source = Path(root) / "card" / "IMAGE.CR3"
            destination = Path(root) / "library"
            source.parent.mkdir()
            source.write_bytes(b"verified exfat payload")

            with patch.object(os, "link", side_effect=OSError(errno.EPERM, "hardlinks unsupported")):
                result = card.copy_verified(str(source), str(destination), retry_count=0)

            landed = Path(result["destination"])
            self.assertEqual(landed.read_bytes(), source.read_bytes())
            self.assertEqual(card.compute_full_hash(landed), result["full_hash"])
            self.assertFalse(any(destination.glob("*.importing")))


class StagedImportTaxonomyTests(BackendTestCase):
    async def _wait(self, job):
        await asyncio.wait_for(staging._tasks[job.id], timeout=10)

    async def _card_scan(self, card_root: Path, entries: list[dict], *, card_source: bool = True):
        scan = staging.Scan(
            id=f"scan-{os.urandom(6).hex()}",
            path=str(card_root),
            include_subfolders=True,
            card_source=card_source,
            status="done",
            entries=entries,
        )
        staging._scans[scan.id] = scan
        return scan

    def _entry(self, root: Path, path: Path, *, taken_at: str = "2026-07-12 10:00:00") -> dict:
        stat = path.stat()
        return {
            "key": os.urandom(8).hex(),
            "name": path.name,
            "rel_path": str(path.relative_to(root)),
            "path": str(path),
            "size": stat.st_size,
            "mtime": stat.st_mtime,
            "taken_at": taken_at,
            "kind": "video" if path.suffix.lower() in card.VIDEO_EXTENSIONS else "image",
            "suspect": False,
            "suspect_reason": "",
        }

    async def test_four_destination_routings_on_copy(self):
        root = Path(self.tempdir.name)
        library = root / "Photos"
        old_root = os.environ.get("PHOTOARCHIVE_ORIGINALS_DIR")
        os.environ["PHOTOARCHIVE_ORIGINALS_DIR"] = str(library)
        try:
            sources = root / "incoming"
            camera = sources / "CARD" / "DCIM" / "100CANON"
            phone = sources / "Camera Roll"
            film = sources / "Film Scans" / "lab"
            exports = sources / "Exported Edits" / "2026"
            for folder in (camera, phone, film, exports):
                folder.mkdir(parents=True)

            raw = camera / "IMG_0001.CR3"
            phone_jpg = phone / "PXL_20260712_120000.jpg"
            film_tif = film / "scan-001.tif"
            export_jpg = exports / "edit-final.jpg"
            raw.write_bytes(b"canon-raw-bytes")
            phone_jpg.write_bytes(b"phone-jpeg-bytes")
            film_tif.write_bytes(b"tiff-scan-bytes")
            export_jpg.write_bytes(b"export-jpeg-bytes")

            cases = [
                (camera, raw, library / "RAWS" / "2026" / "2026-07-12" / raw.name),
                (phone, phone_jpg, library / "Personal Photos" / "2026" / "2026-07-12" / phone_jpg.name),
                (film, film_tif, library / "Film Scans" / "2026" / "2026-07-12" / film_tif.name),
                (exports, export_jpg, library / "Exported Edits" / "2026" / "2026-07-12" / export_jpg.name),
            ]
            for source_root, path, expected in cases:
                scan = await self._card_scan(
                    source_root,
                    [self._entry(source_root, path)],
                    card_source=source_root == camera,
                )
                job = await staging.start_commit(
                    scan,
                    keys="all_checked_default",
                    mode="copy",
                    skip_suspects=True,
                    clear_card=False,
                    keyword_paths=[],
                    collection_id=None,
                )
                await self._wait(job)
                self.assertEqual(job.phase, "complete", job.errors)
                self.assertTrue(expected.is_file(), expected)
                self.assertEqual(expected.read_bytes(), path.read_bytes())
                # Regression: phone must never land under RAWS.
                if "Personal Photos" in str(expected):
                    self.assertFalse(
                        (library / "RAWS" / "Personal Photos").exists()
                        or any("RAWS" in Path(row["filepath"]).parts and "Personal Photos" in Path(row["filepath"]).parts
                               for row in job.image_rows)
                    )
        finally:
            if old_root is None:
                os.environ.pop("PHOTOARCHIVE_ORIGINALS_DIR", None)
            else:
                os.environ["PHOTOARCHIVE_ORIGINALS_DIR"] = old_root

    async def test_phone_jpeg_from_card_scan_not_under_raws_when_phone_path(self):
        root = Path(self.tempdir.name)
        library = root / "Photos"
        old_root = os.environ.get("PHOTOARCHIVE_ORIGINALS_DIR")
        os.environ["PHOTOARCHIVE_ORIGINALS_DIR"] = str(library)
        try:
            phone_dump = root / "Google Photos" / "Camera"
            phone_dump.mkdir(parents=True)
            shot = phone_dump / "PXL_phone.jpg"
            shot.write_bytes(b"cellphone-jpeg")
            scan = await self._card_scan(phone_dump, [self._entry(phone_dump, shot)], card_source=False)
            job = await staging.start_commit(
                scan, keys="all_checked_default", mode="copy", skip_suspects=True,
                clear_card=False, keyword_paths=[], collection_id=None,
            )
            await self._wait(job)
            landed = library / "Personal Photos" / "2026" / "2026-07-12" / shot.name
            self.assertTrue(landed.is_file(), landed)
            self.assertFalse((library / "RAWS" / "2026" / "2026-07-12" / shot.name).exists())
            self.assertFalse((library / "RAWS" / "Personal Photos").exists())
        finally:
            if old_root is None:
                os.environ.pop("PHOTOARCHIVE_ORIGINALS_DIR", None)
            else:
                os.environ["PHOTOARCHIVE_ORIGINALS_DIR"] = old_root


class HubPhoneUnderRawsRegressionTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.db_path = str(self.root / "hub.db")
        self.intake = self.root / "_intake"
        self.raws = self.root / "RAWS"
        self.old_db_path = db.DB_PATH
        db.DB_PATH = self.db_path
        asyncio.run(db.init_db())
        hub_routes.configure(
            db_path=lambda: self.db_path,
            intake_root=lambda: self.intake,
            raws_root=lambda: self.raws,
        )
        from fastapi import FastAPI
        api = FastAPI()
        api.include_router(hub_routes.router)
        self.client_context = TestClient(api)
        self.client = self.client_context.__enter__()

    def tearDown(self):
        self.client_context.__exit__(None, None, None)
        db.DB_PATH = self.old_db_path
        self.tempdir.cleanup()

    def _jpeg(self, name: str, color: tuple[int, int, int]) -> bytes:
        path = self.root / name
        Image.new("RGB", (4, 3), color).save(path, format="JPEG")
        payload = path.read_bytes()
        path.unlink()
        return payload

    def test_phone_folder_lands_beside_raws_not_inside(self):
        import hashlib
        from features.sync import hashing

        payload = self._jpeg("personal.jpg", (11, 22, 33))
        content_hash = hashlib.blake2b(digest_size=16)
        content_hash.update(payload[: hashing.HASH_PREFIX_BYTES])
        content_hash.update(len(payload).to_bytes(8, "little"))
        digest = content_hash.hexdigest()
        full = hashlib.blake2b(payload, digest_size=16).hexdigest()
        response = self.client.post(
            "/api/sync/manifest",
            json={"items": [{
                "content_hash": digest,
                "full_hash": full,
                "bytes": len(payload),
                "filename": "personal.jpg",
                "date_taken": "2024-06-07",
                "folder": "Personal Photos",
            }]},
        )
        self.assertEqual(response.status_code, 200, response.text)
        upload = self.client.post(
            f"/api/sync/upload/{digest}",
            headers={"X-Offset": "0", "X-Total-Bytes": str(len(payload))},
            content=payload,
        )
        self.assertEqual(upload.status_code, 200, upload.text)
        good = self.root / "Personal Photos" / "2024" / "2024-06-07" / "personal.jpg"
        nested_bug = self.raws / "Personal Photos" / "2024" / "2024-06-07" / "personal.jpg"
        self.assertTrue(good.is_file(), good)
        self.assertEqual(good.read_bytes(), payload)
        self.assertFalse(nested_bug.exists(), "phone shots must not nest under RAWS")


class ReclassifyGatedTests(BackendTestCase):
    async def test_reclassify_requires_confirm_and_does_not_auto_move(self):
        root = Path(self.tempdir.name)
        library = root / "Photos"
        bad = library / "RAWS" / "Personal Photos" / "2026" / "2026-07-10"
        bad.mkdir(parents=True)
        stranded = bad / "PXL_stranded.jpg"
        stranded.write_bytes(b"mis-nested-phone")
        source = await db.add_or_restore_source(str(library / "RAWS"))
        await db.insert_images_batch(
            [(stranded.name, str(stranded), ".jpg", stranded.stat().st_size, stranded.stat().st_mtime)],
            source_id=source["id"],
        )

        preview = await taxonomy.reclassify_misplaced_personal_photos(
            db.DB_PATH, library, confirm=False, dry_run=True,
        )
        self.assertEqual(preview["action"], "preview")
        self.assertGreaterEqual(preview["count"], 1)
        self.assertTrue(stranded.exists())

        preview2 = await taxonomy.reclassify_misplaced_personal_photos(
            db.DB_PATH, library, confirm=True, dry_run=True,
        )
        self.assertEqual(preview2["action"], "preview")
        self.assertTrue(stranded.exists())

        result = await taxonomy.reclassify_misplaced_personal_photos(
            db.DB_PATH, library, confirm=True, dry_run=False, move_files=True,
        )
        self.assertEqual(result["action"], "reclassify")
        self.assertEqual(result["updated"], 1)
        good = library / "Personal Photos" / "2026" / "2026-07-10" / "PXL_stranded.jpg"
        self.assertTrue(good.is_file())
        self.assertFalse(stranded.exists())


if __name__ == "__main__":
    unittest.main()
