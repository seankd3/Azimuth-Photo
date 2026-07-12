"""Resumability, budget, and network-quiet predictive prefetch coverage."""

from __future__ import annotations

import io
import json
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
        conn = await db.get_db()
        try:
            await conn.execute("ALTER TABLE images ADD COLUMN hub_image_id INTEGER")
        except Exception:
            pass
        try:
            await conn.execute("ALTER TABLE images ADD COLUMN hub_remote INTEGER NOT NULL DEFAULT 0")
        except Exception:
            pass
        await conn.execute("UPDATE images SET hub_image_id = 9, hub_remote = 1, flag = 'picked', date_taken = '2026-07-11' WHERE id = ?", (self.image,))
        await conn.execute("UPDATE images SET hub_image_id = 10, hub_remote = 1, date_taken = '2026-07-11' WHERE id = ?", (self.neighbor,))
        await conn.commit()
        await conn.close()

    @staticmethod
    def _pack() -> bytes:
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode="w") as archive:
            data = b"packed-thumb"
            info = tarfile.TarInfo("9.jpg")
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
            trailer = json.dumps({"after_id": 9, "skipped": [10]}).encode()
            info = tarfile.TarInfo("trailer.json")
            info.size = len(trailer)
            archive.addfile(info, io.BytesIO(trailer))
        return stream.getvalue()

    async def test_resumes_pack_and_defers_prediction_while_uploading(self):
        calls = []

        async def request(method, url, *, body=None, headers=None):
            calls.append(url)
            if "/thumbs/pack" in url:
                return 200, {}, self._pack()
            return 200, {}, b"single-thumb"

        stored = {}
        prefetch = ThumbPrefetcher(
            db_path=db.DB_PATH,
            hub="http://hub",
            request=request,
            store=lambda size, image_id, _signature, data: stored.__setitem__((size, image_id), data),
            cache_root=self.tempdir.name,
            budget_bytes=8 * 1024 ** 3,
        )
        await prefetch.prefetch_once()
        self.assertEqual(prefetch.status()["after_id"], 9)
        await prefetch.enqueue_loupe_neighbors(self.image)
        self.assertEqual(await prefetch.run_predictive_once(uploads_active=True), 0)
        self.assertGreater(prefetch.status()["queued"], 0)
        self.assertEqual(await prefetch.run_predictive_once(uploads_active=False), 1)
        self.assertIn(("md", self.neighbor), stored)

        budgeted = ThumbPrefetcher(
            db_path=db.DB_PATH, hub="http://hub", request=request, store=lambda *_args: None,
            cache_root=self.tempdir.name, budget_bytes=0,
        )
        self.assertEqual((await budgeted.prefetch_once())["state"], "budget")
