"""Move import mode: copy + verify + delete, with the deletion hash-gated
per file and Move-from-library degrading to Copy. Fixtures only — never a
real catalog."""

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
        await asyncio.wait_for(staging._tasks[job.id], timeout=10)

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
        return await staging.import_repository.image_paths_by_content_hash(db.DB_PATH, content_hash)

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
