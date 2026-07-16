"""Safety regression spine for satellite Free up space."""

from __future__ import annotations

import os
from pathlib import Path
from unittest import mock

from fastapi import BackgroundTasks, Request

from data.repositories import images as image_repository
from features.media import routes as media_routes
from features.sync import freeup, hashing, hub, oplog, satellite
from test_support import BackendTestCase
import db
import thumbnails


class FreeUpSpaceTests(BackendTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        freeup._jobs.clear()
        freeup._tasks.clear()
        self.log_path = Path(self.tempdir.name) / "freeup.jsonl"
        self.source = await self._source("satellite-originals")
        self.next_hub_id = 100
        await satellite.ensure_sync_state(db.DB_PATH)

    async def _synced_original(
        self,
        filename: str,
        payload: bytes,
        *,
        uploaded: bool = True,
        clean: bool = True,
    ) -> tuple[int, Path, str]:
        image_id = await self._image(self.source["id"], filename)
        path = Path(self.source["path"]) / filename
        path.write_bytes(payload)
        content_hash = hashing.compute_content_hash(path)
        self.next_hub_id += 1
        conn = await db.get_db()
        try:
            await conn.execute(
                "UPDATE images SET content_hash = ?, hub_image_id = ?, file_size = ?, "
                "file_modified_at = ?, date_taken = '2020-01-01T00:00:00Z' WHERE id = ?",
                (content_hash, self.next_hub_id, len(payload), path.stat().st_mtime, image_id),
            )
            await conn.execute(
                "INSERT INTO sync_state(content_hash, image_id, last_local_change_at, last_pushed_at, uploaded) "
                "VALUES (?, ?, ?, ?, ?)",
                (content_hash, image_id, 20.0, 30.0 if clean else 10.0, int(uploaded)),
            )
            await conn.commit()
        finally:
            await conn.close()
        return image_id, path, content_hash

    @staticmethod
    async def _confirm_all(content_hashes: list[str]) -> set[str]:
        return set(content_hashes)

    async def test_hub_confirmed_file_is_deleted_and_row_readthroughs(self):
        image_id, path, content_hash = await self._synced_original(
            "safe.raw", b"hub-confirmed original"
        )
        job = freeup.FreeUpJob(older_than_days=30)

        await freeup.run_job(
            job,
            db.DB_PATH,
            confirm=self._confirm_all,
            log_path=self.log_path,
        )

        self.assertEqual(job.phase, "completed")
        self.assertEqual(job.files_done, 1)
        self.assertFalse(path.exists())
        row = await self._image_row(image_id)
        self.assertEqual(row["content_hash"], content_hash)
        self.assertEqual(row["hub_remote"], 1)
        self.assertIsNotNone(row["hub_image_id"])
        self.assertIn('"event":"deleted"', self.log_path.read_text())
        self.assertIn('"hub_confirmed":true', self.log_path.read_text())

        media_row = await image_repository.get_media_image_by_id(db.DB_PATH, image_id)
        self.assertEqual(await media_routes._source_state(media_row), "remote")
        request = Request({"type": "http", "method": "GET", "path": f"/api/full/{image_id}", "headers": []})
        with mock.patch.object(satellite, "hub_url", return_value="http://hub"), mock.patch.object(
            media_routes,
            "_urllib_request",
            new=mock.AsyncMock(return_value=(200, {"content-type": "image/jpeg"}, b"hub-readthrough")),
        ), mock.patch.object(
            media_routes, "_cache_remote_media", return_value="hub-signature"
        ), mock.patch.object(thumbnails, "fast_disk_path_entry", return_value=None):
            response = await media_routes.serve_full_image(
                request, image_id, BackgroundTasks()
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.body, b"hub-readthrough")

    async def test_hub_have_requires_matching_bytes_not_mere_existence(self):
        # A truncated/corrupted hub original at the right path must NOT read as present:
        # deleting the satellite copy on an existence-only signal loses the photo.
        image_id, path, content_hash = await self._synced_original(
            "onhub.raw", b"the true original bytes on the hub"
        )
        # This DB doubles as the "hub": the row is the hub's kept original.
        present = await hub.have_content_hashes(db.DB_PATH, [content_hash])
        self.assertEqual(present, [content_hash])  # intact bytes -> present

        path.write_bytes(b"truncated")  # same path, wrong (shorter) bytes
        present = await hub.have_content_hashes(db.DB_PATH, [content_hash])
        self.assertEqual(present, [])  # corrupted hub copy -> NOT present
        _ = image_id

    async def test_pre_unlink_reconfirm_stops_deletion_when_hub_loses_copy(self):
        image_id, path, content_hash = await self._synced_original(
            "vanishes.raw", b"present at batch time, gone by unlink"
        )
        calls = {"n": 0}

        async def confirm_then_lose(content_hashes: list[str]) -> set[str]:
            # First call = the batch confirmation (present). Second call = the
            # per-file re-confirm immediately before unlink (hub has since lost it).
            calls["n"] += 1
            return set(content_hashes) if calls["n"] == 1 else set()

        job = freeup.FreeUpJob(older_than_days=0)
        await freeup.run_job(job, db.DB_PATH, confirm=confirm_then_lose, log_path=self.log_path)

        self.assertGreaterEqual(calls["n"], 2)  # it re-confirmed per file
        self.assertEqual(job.files_done, 0)
        self.assertTrue(path.exists())  # original preserved
        self.assertEqual((await self._image_row(image_id))["hub_remote"], 0)

    async def test_recovery_never_marks_remote_when_source_is_offline(self):
        # delete_ready journalled, but the file is absent because its SOURCE ROOT is
        # gone (unplugged), not because it was deleted. Recovery must leave it local.
        image_id, path, content_hash = await self._synced_original(
            "oncard.raw", b"still on the card, source unplugged"
        )
        offline_root = str(Path(self.tempdir.name) / "unplugged-card")
        freeup._append_log(
            self.log_path,
            {
                "event": "delete_ready",
                "image_id": image_id,
                "path": str(Path(offline_root) / "oncard.raw"),
                "source_path": offline_root,
                "content_hash": content_hash,
            },
        )
        recovered = await freeup.recover_incomplete_deletions(db.DB_PATH, log_path=self.log_path)
        self.assertEqual(recovered, 0)
        self.assertEqual((await self._image_row(image_id))["hub_remote"], 0)
        self.assertTrue(path.exists())
        _ = content_hash

    async def test_locally_modified_file_is_skipped_by_final_rehash_guard(self):
        image_id, path, _content_hash = await self._synced_original(
            "edited.raw", b"bytes that reached the hub"
        )
        path.write_bytes(b"new local edit that did not reach hub")
        job = freeup.FreeUpJob(older_than_days=0)

        await freeup.run_job(
            job,
            db.DB_PATH,
            confirm=self._confirm_all,
            log_path=self.log_path,
        )

        self.assertEqual(job.phase, "completed")
        self.assertEqual(job.files_done, 0)
        self.assertEqual(job.skipped_modified, 1)
        self.assertTrue(path.exists())
        self.assertEqual((await self._image_row(image_id))["hub_remote"], 0)
        self.assertIn('"event":"skipped_modified"', self.log_path.read_text())

    async def test_dirty_and_not_uploaded_files_are_never_candidates(self):
        await self._synced_original("dirty.raw", b"dirty", clean=False)
        await self._synced_original("pending.raw", b"pending", uploaded=False)
        _image_id, _path, oplog_hash = await self._synced_original(
            "oplog-pending.raw", b"pending metadata"
        )
        await oplog.append_entry(
            db.DB_PATH,
            content_hash=oplog_hash,
            family="flag",
            payload={"value": "picked"},
        )
        confirm = mock.AsyncMock(return_value=set())

        result = await freeup.freeable(
            db.DB_PATH, 0, confirm=confirm, log_path=self.log_path
        )

        self.assertEqual(result, {"files": 0, "bytes": 0})
        confirm.assert_not_awaited()

    async def test_cancelled_job_resumes_from_remaining_file(self):
        first_id, first_path, _first_hash = await self._synced_original(
            "first.raw", b"first safe original"
        )
        second_id, second_path, _second_hash = await self._synced_original(
            "second.raw", b"second safe original"
        )
        first_job = freeup.FreeUpJob(older_than_days=0)
        hash_calls = 0

        def cancel_during_second_hash(path: str) -> str:
            nonlocal hash_calls
            hash_calls += 1
            digest = hashing.compute_content_hash(path)
            if hash_calls == 2:
                first_job.cancel_requested = True
            return digest

        await freeup.run_job(
            first_job,
            db.DB_PATH,
            confirm=self._confirm_all,
            hash_file=cancel_during_second_hash,
            log_path=self.log_path,
        )

        self.assertEqual(first_job.phase, "cancelled")
        self.assertEqual(first_job.files_done, 1)
        self.assertFalse(first_path.exists())
        self.assertTrue(second_path.exists())
        self.assertEqual((await self._image_row(first_id))["hub_remote"], 1)
        self.assertEqual((await self._image_row(second_id))["hub_remote"], 0)

        resumed = freeup.FreeUpJob(older_than_days=0)
        await freeup.run_job(
            resumed,
            db.DB_PATH,
            confirm=self._confirm_all,
            log_path=self.log_path,
        )

        self.assertEqual(resumed.phase, "completed")
        self.assertEqual(resumed.files_done, 1)
        self.assertFalse(second_path.exists())
        self.assertEqual((await self._image_row(second_id))["hub_remote"], 1)

    async def test_delete_journal_repairs_interruption_after_unlink(self):
        image_id, path, content_hash = await self._synced_original(
            "interrupted.raw", b"delete completed before catalog commit"
        )
        ready = {
            "event": "delete_ready",
            "job_id": "interrupted",
            "image_id": image_id,
            "path": str(path),
            "content_hash": content_hash,
            "hub_image_id": self.next_hub_id,
            "hub_confirmed": True,
            "bytes": path.stat().st_size,
        }
        freeup._append_log(self.log_path, ready)
        path.unlink()

        recovered = await freeup.recover_incomplete_deletions(
            db.DB_PATH, log_path=self.log_path
        )

        self.assertEqual(recovered, 1)
        self.assertEqual((await self._image_row(image_id))["hub_remote"], 1)
        self.assertIn('"event":"recovered"', self.log_path.read_text())

    async def test_hub_have_requires_a_physically_present_active_original(self):
        _present_id, present_path, present_hash = await self._synced_original(
            "present.raw", b"present on hub"
        )
        missing_id, missing_path, missing_hash = await self._synced_original(
            "missing.raw", b"catalog row only"
        )
        missing_path.unlink()
        conn = await db.get_db()
        try:
            await conn.execute("UPDATE images SET missing_at = NULL WHERE id = ?", (missing_id,))
            await conn.commit()
        finally:
            await conn.close()

        present = await hub.have_content_hashes(
            db.DB_PATH, [missing_hash, present_hash]
        )

        self.assertTrue(present_path.exists())
        self.assertEqual(present, [present_hash])


if __name__ == "__main__":
    import unittest

    unittest.main()
