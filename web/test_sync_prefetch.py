"""Resumability, budget, and network-quiet predictive prefetch coverage."""

from __future__ import annotations

import io
import json
import sqlite3
import tarfile

import db
from features.sync.prefetch import ThumbPrefetcher
from test_support import BackendTestCase


class PrefetchTests(BackendTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        source = await self._source("hub")
        self.image = await self._image(source["id"], "hub.jpg")
        self.neighbor = await self._image(source["id"], "hub-neighbor.jpg")
        self.third = await self._image(source["id"], "hub-third.jpg")
        conn = await db.get_db()
        try:
            await conn.execute("ALTER TABLE images ADD COLUMN hub_image_id INTEGER")
        except Exception:
            pass
        try:
            await conn.execute("ALTER TABLE images ADD COLUMN hub_remote INTEGER NOT NULL DEFAULT 0")
        except Exception:
            pass
        await conn.execute(
            "UPDATE images SET hub_image_id = 9, hub_remote = 1, flag = 'picked', date_taken = '2026-07-11' WHERE id = ?",
            (self.image,),
        )
        await conn.execute(
            "UPDATE images SET hub_image_id = 10, hub_remote = 1, date_taken = '2026-07-11' WHERE id = ?",
            (self.neighbor,),
        )
        await conn.execute(
            "UPDATE images SET hub_image_id = 11, hub_remote = 1, date_taken = '2026-07-11' WHERE id = ?",
            (self.third,),
        )
        await conn.commit()
        await conn.close()

    @staticmethod
    def _pack(hub_ids: list[int] | None = None) -> bytes:
        ids = hub_ids or [9]
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode="w") as archive:
            for hub_id in ids:
                data = f"packed-thumb-{hub_id}".encode()
                info = tarfile.TarInfo(f"{hub_id}.jpg")
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))
            trailer = json.dumps({"after_id": max(ids), "skipped": []}).encode()
            info = tarfile.TarInfo("trailer.json")
            info.size = len(trailer)
            archive.addfile(info, io.BytesIO(trailer))
        return stream.getvalue()

    def _index_cache(self, size: str, image_id: int, data: bytes) -> None:
        conn = sqlite3.connect(db.DB_PATH)
        try:
            conn.execute(
                "INSERT OR REPLACE INTO cache_entries"
                "(cache_root, size, image_id, path, source_signature, size_bytes, last_accessed, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (self.tempdir.name, size, image_id, f"{size}-{image_id}", "sig", len(data), 0, 0),
            )
            conn.commit()
        finally:
            conn.close()

    async def test_resumes_pack_and_defers_prediction_while_uploading(self):
        calls = []

        async def request(method, url, *, body=None, headers=None):
            calls.append(url)
            if "/thumbs/pack" in url:
                return 200, {}, self._pack()
            return 200, {}, b"single-thumb"

        stored = {}

        def store(size, image_id, _signature, data):
            stored[(size, image_id)] = data
            self._index_cache(size, image_id, data)

        prefetch = ThumbPrefetcher(
            db_path=db.DB_PATH,
            hub="http://hub",
            request=request,
            store=store,
            cache_root=self.tempdir.name,
            budget_bytes=8 * 1024 ** 3,
        )
        await prefetch.prefetch_once()
        self.assertEqual(prefetch.status()["after_id"], 9)
        self.assertTrue(any("order=newest" in url for url in calls if "/thumbs/pack" in url))
        await prefetch.enqueue_loupe_neighbors(self.image)
        self.assertEqual(await prefetch.run_predictive_once(uploads_active=True), 0)
        self.assertGreater(prefetch.status()["queued"], 0)
        self.assertEqual(await prefetch.run_predictive_once(uploads_active=False), 1)
        # Browse tier incomplete → predictive stores sm, not md/lg.
        self.assertIn(("sm", self.neighbor), stored)
        self.assertNotIn(("md", self.neighbor), stored)

        budgeted = ThumbPrefetcher(
            db_path=db.DB_PATH,
            hub="http://hub",
            request=request,
            store=lambda *_args: None,
            cache_root=self.tempdir.name,
            budget_bytes=0,
        )
        self.assertEqual((await budgeted.prefetch_once())["state"], "budget")

    async def test_browse_first_fills_sm_before_md(self):
        """Equal sm+md cycles request md while sm lags; browse-first does not."""

        pack_sizes: list[str] = []
        sm_hub_ids = [9, 10, 11]

        async def request(method, url, *, body=None, headers=None):
            if "/thumbs/pack" in url:
                size = "md" if "size=md" in url else "sm"
                pack_sizes.append(size)
                if size == "sm":
                    after = 0
                    if "after_id=" in url:
                        after = int(url.split("after_id=")[1].split("&")[0])
                    nxt = next((hid for hid in sm_hub_ids if hid > after), None)
                    if nxt is None:
                        return 200, {}, self._pack([sm_hub_ids[-1]])
                    return 200, {}, self._pack([nxt])
                return 200, {}, self._pack([11])
            return 200, {}, b"single"

        stored: dict[tuple[str, int], bytes] = {}

        def store(size, image_id, _signature, data):
            stored[(size, image_id)] = data
            self._index_cache(size, image_id, data)

        prefetch = ThumbPrefetcher(
            db_path=db.DB_PATH,
            hub="http://hub",
            request=request,
            store=store,
            cache_root=self.tempdir.name,
            budget_bytes=8 * 1024 ** 3,
        )

        # BEFORE: legacy sm-then-md each cycle requests md while sm incomplete.
        for _ in range(2):
            await prefetch.prefetch_once(size="sm", limit=1)
            await prefetch.prefetch_once(size="md", limit=1)
        legacy_sizes = list(pack_sizes)
        self.assertIn("md", legacy_sizes)

        await prefetch._set_state("after:sm", "0")
        await prefetch._set_state("after:md", "0")
        pack_sizes.clear()
        stored.clear()
        conn = sqlite3.connect(db.DB_PATH)
        try:
            conn.execute("DELETE FROM cache_entries WHERE cache_root = ?", (self.tempdir.name,))
            conn.commit()
        finally:
            conn.close()

        before_sm = (await prefetch._library_progress("sm"))[0]
        sm_growth = []
        status = {}
        for _ in range(5):
            status = await prefetch.prefetch_browse_first(limit=1)
            cached, _total = await prefetch._library_progress("sm")
            sm_growth.append(cached)
            if await prefetch.browse_tier_complete():
                break
        self.assertGreater(sm_growth[-1], before_sm)
        self.assertTrue(all(size == "sm" for size in pack_sizes), pack_sizes)
        self.assertEqual(status.get("tier"), "browse")

        # Seed full sm coverage, then browse-first must advance to md.
        pack_sizes.clear()
        for image_id in (self.image, self.neighbor, self.third):
            self._index_cache("sm", image_id, b"x")
        self.assertTrue(await prefetch.browse_tier_complete())
        loupe = await prefetch.prefetch_browse_first(limit=1)
        self.assertEqual(pack_sizes, ["md"])
        self.assertEqual(loupe["size"], "md")
        self.assertEqual(loupe["tier"], "loupe")

    async def test_pack_prefetch_runs_while_user_is_browsing(self):
        """Bulk pack fill must not stall behind the idle gate during refine."""

        from features.sync import preview_mirror
        from features.sync.sync_worker import SyncWorker

        pack_calls = []

        async def request(method, url, *, body=None, headers=None):
            if "/thumbs/pack" in url:
                pack_calls.append(url)
                return 200, {}, self._pack([9])
            if "/api/version" in url or "/api/sync/" in url:
                return 200, {"content-type": "application/json"}, b"{}"
            return 200, {}, b""

        worker = SyncWorker(db_path=db.DB_PATH, hub="http://hub", request=request)
        worker.prefetch = ThumbPrefetcher(
            db_path=db.DB_PATH,
            hub="http://hub",
            request=request,
            store=lambda *_a: None,
            cache_root=self.tempdir.name,
            budget_bytes=8 * 1024 ** 3,
        )
        preview_mirror.note_request()
        self.assertFalse(preview_mirror.is_idle(idle_seconds=10.0))
        await worker._run_prefetch()
        self.assertEqual(len(pack_calls), 1)
        self.assertIn("order=newest", pack_calls[0])


if __name__ == "__main__":
    unittest.main()


def test_auto_budget_adapts_to_free_disk(tmp_path):
    from features.sync import prefetch as prefetch_mod

    # 200GB free -> 25% = 50GB (inside 8-64 clamp); tiny disks clamp to the
    # floor; huge disks clamp to the ceiling.
    import shutil as real_shutil
    import unittest.mock as mock
    with mock.patch.object(real_shutil, "disk_usage", return_value=mock.Mock(free=200 * 1024 ** 3)):
        assert prefetch_mod.auto_thumb_budget_bytes(str(tmp_path)) == 50 * 1024 ** 3
    with mock.patch.object(real_shutil, "disk_usage", return_value=mock.Mock(free=4 * 1024 ** 3)):
        assert prefetch_mod.auto_thumb_budget_bytes(str(tmp_path)) == 8 * 1024 ** 3
    with mock.patch.object(real_shutil, "disk_usage", return_value=mock.Mock(free=1024 * 1024 ** 3)):
        assert prefetch_mod.auto_thumb_budget_bytes(str(tmp_path)) == 64 * 1024 ** 3
