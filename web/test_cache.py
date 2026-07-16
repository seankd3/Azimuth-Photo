"""HTTP behavior gates for cache-control mutations."""

import asyncio
import os

from fastapi.testclient import TestClient

import thumbnails
import db
import settings
from test_support import BackendTestCase


class CacheRouteTests(BackendTestCase):
    async def _request(self, method, path, **kwargs):
        def send():
            with TestClient(__import__("app").app) as client:
                return client.request(method, path, **kwargs)
        return await asyncio.to_thread(send)

    async def test_clear_deletes_cache_entries_but_not_originals(self):
        source = await self._source()
        image_id = await self._image(source["id"], "cached.jpg")
        original = os.path.join(source["path"], "cached.jpg")
        with open(original, "wb") as handle:
            handle.write(b"original")
        await self._cache_entry(image_id)

        cleared = await self._request("POST", "/api/cache/clear")
        self.assertEqual(cleared.status_code, 200, cleared.text)
        self.assertTrue(cleared.json()["ok"])
        conn = await db.get_db()
        try:
            entries = await (await conn.execute("SELECT image_id FROM cache_entries WHERE image_id = ?", (image_id,))).fetchall()
        finally:
            await conn.close()
        self.assertEqual(entries, [])
        self.assertTrue(os.path.exists(original))

    async def test_clear_refusal_preserves_library_and_cache_metadata(self):
        source = await self._source()
        image_id = await self._image(source["id"], "safe.jpg")
        await self._cache_entry(image_id)
        old_settings = settings.get_settings()
        unsafe = os.path.join(self.tempdir.name, "unsafe-cache")
        os.makedirs(unsafe)
        with open(os.path.join(unsafe, "not-a-cache-file"), "wb") as handle:
            handle.write(b"keep")
        settings.save_settings({**old_settings, "ssd_cache_dir": unsafe})
        thumbnails.configure(settings.get_settings())
        try:
            refused = await self._request("POST", "/api/cache/clear")
        finally:
            settings.save_settings(old_settings)
            thumbnails.configure(old_settings)
        self.assertEqual(refused.status_code, 400, refused.text)
        self.assertIsNotNone(await self._image_row(image_id))
