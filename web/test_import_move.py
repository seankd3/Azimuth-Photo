"""Move import mode: copy + verify + delete, with the deletion hash-gated
per file and Move-from-library degrading to Copy. Fixtures only — never a
real catalog."""

from core.catalog_path import catalog_path
import asyncio
import os
from pathlib import Path
from unittest.mock import patch

from test_support import *  # noqa: F401,F403
from features.imports import card, staging


class MoveImportTests(BackendTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.originals = Path(self.tempdir.name) / "originals"
        self._old_originals = os.environ.get("AZIMUTH_ORIGINALS_DIR")
        os.environ["AZIMUTH_ORIGINALS_DIR"] = str(self.originals)

    async def asyncTearDown(self):
        if self._old_originals is None:
            os.environ.pop("AZIMUTH_ORIGINALS_DIR", None)
        else:
            os.environ["AZIMUTH_ORIGINALS_DIR"] = self._old_originals
        await super().asyncTearDown()

    async def _wait(self, job):
        await asyncio.wait_for(staging._tasks[job.id], timeout=30)

    def _entry(self, root: Path, path: Path) -> dict:
        stat = path.stat()
        return {
            "key": os.urandom(8).hex(), "name": path.name,
            "rel_path": str(path.relative_to(root)), "path": str(path),
            "size": stat.st_size, "mtime": stat.st_mtime, "taken_at": "2026-07-12 10:00:00",
            "kind": "image", "source_kind": "raw", "suspect": False, "suspect_reason": "",
        }

    def _scan(self, root: Path, entries: list[dict], *, card_source: bool = False):
        scan = staging.Scan(
            id=f"scan-{os.urandom(6).hex()}", path=str(root), include_subfolders=True,
            card_source=card_source, status="done", entries=entries,
        )
        staging._scans[scan.id] = scan
        return scan

    async def _move(self, scan):
        return await staging.start_commit(
            scan, keys="all_checked_default", mode="move", skip_suspects=True,
            clear_card=False, keyword_paths=[], collection_id=None,
        )

    async def _registered_paths(self, path: Path) -> list[str]:
        content_hash, _full, _size = await asyncio.to_thread(card.content_hash_from_stream, path)
        return await staging.import_repository.image_paths_by_content_hash(catalog_path(), content_hash)

    async def test_move_lands_verified_copy_then_drains_source(self):
        stage = Path(self.tempdir.name) / "staging"
        stage.mkdir()
        shot = stage / "CANON0001.CR3"
        shot.write_bytes(b"raw bytes " * 4096)

        job = await self._move(self._scan(stage, [self._entry(stage, shot)]))
        await self._wait(job)

        self.assertEqual(job.phase, "complete")
        self.assertEqual(job.errors, [])
        self.assertFalse(job.move_degraded)
        landed = list(self.originals.rglob(shot.name))
        self.assertEqual(len(landed), 1)
        self.assertEqual(landed[0].read_bytes(), b"raw bytes " * 4096)
        self.assertFalse(shot.exists())
        self.assertEqual(job.cleared_bytes, len(b"raw bytes " * 4096))
        self.assertIn(str(landed[0]), await self._registered_paths(landed[0]))

    async def test_card_move_drains_card_and_rejects_add(self):
        dcim = Path(self.tempdir.name) / "CARD" / "DCIM"
        camera = dcim / "100CANON"
        camera.mkdir(parents=True)
        shot = camera / "CANON0002.CR3"
        shot.write_bytes(b"card raw " * 512)

        scan = self._scan(dcim, [self._entry(dcim, shot)], card_source=True)
        with self.assertRaises(ValueError):
            await staging.start_commit(
                scan, keys="all_checked_default", mode="add", skip_suspects=True,
                clear_card=False, keyword_paths=[], collection_id=None,
            )
        job = await self._move(scan)
        self.assertTrue(job.clear_card)  # Move implies drain — free-space ETA stays honest
        await self._wait(job)

        self.assertEqual(job.phase, "complete")
        self.assertEqual(job.errors, [])
        self.assertFalse(shot.exists())
        self.assertEqual(len(list(self.originals.rglob(shot.name))), 1)

    async def test_corrupted_copy_keeps_source_and_reports(self):
        stage = Path(self.tempdir.name) / "staging"
        stage.mkdir()
        shot = stage / "CANON0003.CR3"
        shot.write_bytes(b"precious " * 1024)

        # Destination hash never matches: every copy attempt must fail closed.
        with patch.object(card, "compute_full_hash", return_value="0" * 32):
            job = await self._move(self._scan(stage, [self._entry(stage, shot)]))
            await self._wait(job)

        self.assertEqual(job.phase, "complete")
        self.assertEqual(len(job.errors), 1)
        self.assertEqual(job.errors[0]["name"], shot.name)
        self.assertTrue(shot.exists())
        self.assertEqual(shot.read_bytes(), b"precious " * 1024)
        self.assertEqual(list(self.originals.rglob(shot.name)), [])

    async def test_deletion_gate_rechecks_landed_bytes(self):
        stage = Path(self.tempdir.name) / "staging"
        stage.mkdir()
        shot = stage / "CANON0004.CR3"
        shot.write_bytes(b"short copy " * 256)

        real_copy = card.copy_verified

        def lying_copy(source, directory, **kwargs):
            result = real_copy(source, directory, **kwargs)
            result["bytes"] = int(result["bytes"]) + 1  # landed size no longer matches
            return result

        with patch.object(card, "copy_verified", side_effect=lying_copy):
            job = await self._move(self._scan(stage, [self._entry(stage, shot)]))
            await self._wait(job)

        self.assertEqual(job.phase, "complete")
        self.assertEqual(len(job.errors), 1)
        self.assertIn("source kept", job.errors[0]["message"])
        self.assertTrue(shot.exists())  # the import landed, the unverified delete did not

    async def test_move_from_inside_library_degrades_to_copy(self):
        library = Path(self.tempdir.name) / "library-source"
        library.mkdir()
        await db.add_or_restore_source(str(library))
        shot = library / "CANON0005.CR3"
        shot.write_bytes(b"library data " * 512)

        job = await self._move(self._scan(library, [self._entry(library, shot)]))
        await self._wait(job)

        self.assertEqual(job.phase, "complete")
        self.assertEqual(job.errors, [])
        self.assertTrue(job.move_degraded)
        self.assertTrue(shot.exists())  # library originals are never Move-deleted
        self.assertEqual(job.cleared_bytes, 0)
        self.assertEqual(len(list(self.originals.rglob(shot.name))), 1)

    async def test_ancestor_scan_move_never_deletes_library_originals(self):
        # The reviewer's PoC: scan an ANCESTOR of the library (a drive-letter
        # rail entry) — the walk reaches library originals whose "copy" is the
        # file itself. The per-entry guard must keep every one of them.
        stage = Path(self.tempdir.name) / "staging"
        stage.mkdir()
        shot = stage / "CANON0100.CR3"
        shot.write_bytes(b"irreplaceable " * 2048)
        job1 = await self._move(self._scan(stage, [self._entry(stage, shot)]))
        await self._wait(job1)
        landed = list(self.originals.rglob(shot.name))
        self.assertEqual(len(landed), 1)

        ancestor = Path(self.tempdir.name)  # parent of originals/
        job2 = await self._move(self._scan(ancestor, [self._entry(ancestor, landed[0])]))
        await self._wait(job2)

        self.assertEqual(job2.phase, "complete")
        self.assertEqual(job2.errors, [])
        self.assertTrue(landed[0].exists())  # the library original survived
        self.assertEqual(landed[0].read_bytes(), b"irreplaceable " * 2048)
        self.assertEqual(job2.cleared_bytes, 0)
        self.assertTrue(job2.move_degraded)
        self.assertEqual(job2.skipped_duplicates, 1)

    async def test_junction_into_library_move_never_deletes(self):
        # A junction/symlink outside the library pointing into it: the scan
        # root and entry paths look safe, but the bytes live in the library.
        # The guard judges the resolved path and must refuse deletion.
        stage = Path(self.tempdir.name) / "staging"
        stage.mkdir()
        shot = stage / "CANON0200.CR3"
        shot.write_bytes(b"linked bytes " * 1024)
        job1 = await self._move(self._scan(stage, [self._entry(stage, shot)]))
        await self._wait(job1)
        landed = list(self.originals.rglob(shot.name))
        self.assertEqual(len(landed), 1)

        # The link sits INSIDE an innocent scan root, so the scan-level degrade
        # check stays False and only the per-entry guard stands in the way.
        wrap = Path(self.tempdir.name) / "linkedwrap"
        wrap.mkdir()
        link_root = wrap / "lib"
        try:
            os.symlink(self.originals, link_root, target_is_directory=True)
        except OSError:
            try:
                import _winapi

                _winapi.CreateJunction(str(self.originals), str(link_root))
            except (ImportError, OSError):
                self.skipTest("neither symlinks nor junctions available")
        through_link = link_root / landed[0].relative_to(self.originals)
        self.assertTrue(through_link.exists())

        job2 = await self._move(self._scan(wrap, [self._entry(wrap, through_link)]))
        await self._wait(job2)

        self.assertEqual(job2.phase, "complete")
        self.assertEqual(job2.errors, [])
        self.assertTrue(landed[0].exists())  # the real file behind the link survived
        self.assertEqual(job2.cleared_bytes, 0)
        self.assertTrue(job2.move_degraded)

    async def test_refuse_source_delete_reasons(self):
        # The guard unit: samefile, library-root, and astro refusals each
        # keep the source on their own, independent of scan-level state.
        keep = Path(self.tempdir.name) / "keep.jpg"
        keep.write_bytes(b"keep me")
        relative_alias = os.path.join(str(self.tempdir.name), ".", "keep.jpg")
        self.assertIn("same file", staging._refuse_source_delete(str(keep), relative_alias, []))
        self.assertIn("library root", staging._refuse_source_delete(
            str(keep), None, [str(self.tempdir.name)]
        ))
        astro = Path(self.tempdir.name) / "Astrophotography" / "m31.tif"
        astro.parent.mkdir()
        astro.write_bytes(b"stars")
        self.assertIn("Astrophotography", staging._refuse_source_delete(str(astro), None, []))
        other = Path(self.tempdir.name) / "other.jpg"
        other.write_bytes(b"a distinct landed copy")
        self.assertIsNone(staging._refuse_source_delete(str(keep), str(other), []))

    async def test_astro_subtree_is_untouchable(self):
        # MASTER_PLAN §1.10: no import path reads from or deletes under an
        # Astrophotography/ root — the scanner skips it and commit refuses it.
        stage = Path(self.tempdir.name) / "staging"
        astro = stage / "Astrophotography"
        astro.mkdir(parents=True)
        starfield = astro / "M31_0001.TIF"
        starfield.write_bytes(b"calibrated stars " * 256)
        normal = stage / "CANON0300.JPG"
        from PIL import Image

        Image.new("RGB", (64, 48)).save(normal, "JPEG")

        scan = staging.Scan(
            id=f"scan-{os.urandom(6).hex()}", path=str(stage), include_subfolders=True,
            card_source=False,
        )
        await asyncio.to_thread(staging._enumerate_scan, scan)
        self.assertEqual([entry["name"] for entry in scan.entries], [normal.name])

        # Defense in depth: even a hand-crafted entry under astro is refused.
        forced = self._scan(stage, [self._entry(stage, starfield)])
        job = await self._move(forced)
        await self._wait(job)
        self.assertEqual(job.phase, "complete")
        self.assertEqual(len(job.errors), 1)
        self.assertIn("Astrophotography", job.errors[0]["message"])
        self.assertTrue(starfield.exists())
        self.assertEqual(list(self.originals.rglob(starfield.name)), [])

    async def test_mixed_batch_with_locked_file_completes_the_rest(self):
        stage = Path(self.tempdir.name) / "staging"
        stage.mkdir()
        shots = []
        for name in ("CANON0006.CR3", "LOCKED.CR3", "CANON0008.CR3"):
            shot = stage / name
            shot.write_bytes(name.encode() * 512)
            shots.append(shot)
        sizes = [shot.stat().st_size for shot in shots]

        real_remove = card.remove_verified_card_file

        def locked_remove(path):
            if Path(path).name == "LOCKED.CR3":
                raise PermissionError(13, "The process cannot access the file", path)
            real_remove(path)

        with patch.object(card, "remove_verified_card_file", side_effect=locked_remove):
            job = await self._move(self._scan(stage, [self._entry(stage, shot) for shot in shots]))
            await self._wait(job)

        self.assertEqual(job.phase, "complete")
        self.assertEqual(len(job.errors), 1)
        self.assertEqual(job.errors[0]["name"], "LOCKED.CR3")
        self.assertFalse(shots[0].exists())
        self.assertTrue(shots[1].exists())  # locked source stays; its copy still imported
        self.assertFalse(shots[2].exists())
        for shot in shots:
            self.assertEqual(len(list(self.originals.rglob(shot.name))), 1)
        self.assertEqual(job.files_done, 3)
        self.assertEqual(job.cleared_bytes, sizes[0] + sizes[2])
