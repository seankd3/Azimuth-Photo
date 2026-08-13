"""HTTP behavior gates for cache-control mutations."""

import asyncio
import os

from fastapi.testclient import TestClient

import thumbnails
from thumbnails import cache_entries as thumbnail_cache_entries
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

    async def _set_content_hash(self, image_id, value):
        conn = await db.get_db()
        try:
            await conn.execute("UPDATE images SET content_hash = ? WHERE id = ?", (value, image_id))
            await conn.commit()
        finally:
            await conn.close()

    async def _entry_identity(self, image_id):
        conn = await db.get_db()
        try:
            row = await (await conn.execute(
                "SELECT content_hash, recipe FROM cache_entries WHERE image_id = ?",
                (image_id,),
            )).fetchone()
        finally:
            await conn.close()
        return (row["content_hash"], row["recipe"]) if row else None

    async def test_new_cache_entries_stamp_the_catalog_content_hash(self):
        """Every write carries the derivation identity — no caller threads it.

        Connecting against a real catalog must select the stamped insert (the
        single statement every store and flush runs), and that statement must
        pull the hash from the images row by itself.
        """

        source = await self._source()
        image_id = await self._image(source["id"], "stamped.jpg")
        await self._set_content_hash(image_id, "aa" * 16)
        thumbnail_cache_entries.close_persistent_conn()
        self.addAsyncCleanup(asyncio.to_thread, thumbnail_cache_entries.close_persistent_conn)

        def write_one():
            conn = thumbnail_cache_entries._db_connect()
            self.assertIs(
                thumbnail_cache_entries._insert_entry_sql,
                thumbnail_cache_entries._INSERT_ENTRY_STAMPED,
            )
            conn.execute(
                thumbnail_cache_entries._insert_entry_sql,
                (thumbnails.SSD_CACHE_DIR, "sm", image_id, "/tile", "sig-x", 9, 1.0, 2.0),
            )
            conn.commit()

        await asyncio.to_thread(write_one)
        self.assertEqual(await self._entry_identity(image_id), ("aa" * 16, ""))

    async def test_boot_backfill_stamps_rows_hashed_after_their_tile(self):
        """H1 hashes images over time; the boot warm task stamps their old tiles."""

        source = await self._source()
        image_id = await self._image(source["id"], "legacy.jpg")
        await self._cache_entry(image_id)  # written without a content hash
        await self._set_content_hash(image_id, "bb" * 16)
        thumbnail_cache_entries.close_persistent_conn()
        self.addAsyncCleanup(asyncio.to_thread, thumbnail_cache_entries.close_persistent_conn)

        await asyncio.to_thread(thumbnail_cache_entries.stamp_missing_content_hashes)

        self.assertEqual(await self._entry_identity(image_id), ("bb" * 16, ""))

    async def test_pregen_start_and_stop_flip_cache_worker_state(self):
        started = await self._request("POST", "/api/cache/pregen/start")
        self.assertEqual(started.status_code, 200, started.text)
        self.assertEqual(started.json()["cache"]["pregen"]["state"], "running")
        self.assertFalse(started.json()["cache"]["pregen"]["manual_pause"])

        stopped = await self._request("POST", "/api/cache/pregen/stop")
        self.assertEqual(stopped.status_code, 200, stopped.text)
        self.assertEqual(stopped.json()["cache"]["pregen"]["state"], "paused")
        self.assertTrue(stopped.json()["cache"]["pregen"]["manual_pause"])
