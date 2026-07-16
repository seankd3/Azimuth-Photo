"""HTTP behavior gates for catalog-source mutations."""

import asyncio
import os
import time

from fastapi.testclient import TestClient
from PIL import Image

import db
import scanner
import settings
import thumbnails
from data.repositories import catalog as catalog_repository
from features.catalog import metadata as catalog_metadata
from features.catalog import routes as catalog_routes
from features.library import service as library_service
from test_support import BackendTestCase, JsonRequest


class CatalogSourceRouteTests(BackendTestCase):
    async def _request(self, method, path, **kwargs):
        def send():
            with TestClient(__import__("app").app) as client:
                return client.request(method, path, **kwargs)
        return await asyncio.to_thread(send)

    async def _virtual_copy(self, master_id: int) -> int:
        conn = await db.get_db()
        try:
            master = await (await conn.execute(
                "SELECT source_id, filename, filepath FROM images WHERE id = ?",
                (master_id,),
            )).fetchone()
            cursor = await conn.execute(
                "INSERT INTO images (source_id, filename, filepath, status, vc_of) "
                "VALUES (?, ?, ?, 'kept', ?)",
                (master["source_id"], f"Copy of {master['filename']}", master["filepath"], master_id),
            )
            await conn.commit()
            return int(cursor.lastrowid)
        finally:
            await conn.close()

    async def test_sync_missing_mark_cascades_to_virtual_copy(self):
        source = await self._source("sync-missing-vc")
        master_id = await self._image(source["id"], "master.jpg")
        copy_id = await self._virtual_copy(master_id)

        changed = catalog_repository.mark_image_missing_sync(db.DB_PATH, master_id, 111.0)

        self.assertTrue(changed)
        self.assertEqual((await self._image_row(master_id))["missing_at"], 111.0)
        self.assertEqual((await self._image_row(copy_id))["missing_at"], 111.0)

    async def test_batched_missing_marks_cascade_each_timestamp_to_virtual_copies(self):
        source = await self._source("batched-missing-vc")
        first_id = await self._image(source["id"], "first.jpg")
        second_id = await self._image(source["id"], "second.jpg")
        first_copy_id = await self._virtual_copy(first_id)
        second_copy_id = await self._virtual_copy(second_id)

        changed = await asyncio.gather(
            catalog_repository.mark_image_missing(db.DB_PATH, first_id, 111.0),
            catalog_repository.mark_image_missing(db.DB_PATH, second_id, 222.0),
        )

        self.assertEqual(changed, [True, True])
        self.assertEqual((await self._image_row(first_copy_id))["missing_at"], 111.0)
        self.assertEqual((await self._image_row(second_copy_id))["missing_at"], 222.0)

    async def test_zero_byte_missing_mark_cascades_to_virtual_copy(self):
        source = await self._source("zero-byte-missing-vc")
        master_id = await self._image(source["id"], "empty.jpg")
        copy_id = await self._virtual_copy(master_id)
        filepath = (await self._image_row(master_id))["filepath"]
        conn = await db.get_db()
        try:
            await conn.execute("UPDATE images SET file_size = 0 WHERE id = ?", (master_id,))
            await conn.commit()
        finally:
            await conn.close()

        changed = await catalog_repository.mark_zero_byte_images_missing(db.DB_PATH, [filepath])

        self.assertEqual([row["id"] for row in changed], [master_id])
        master = await self._image_row(master_id)
        self.assertIsNotNone(master["missing_at"])
        self.assertEqual((await self._image_row(copy_id))["missing_at"], master["missing_at"])

    async def test_exact_path_restore_cascades_to_virtual_copy(self):
        source = await self._source("exact-path-restore-vc")
        master_id = await self._image(source["id"], "returned.jpg")
        copy_id = await self._virtual_copy(master_id)
        filepath = (await self._image_row(master_id))["filepath"]
        conn = await db.get_db()
        try:
            await conn.execute(
                "UPDATE images SET missing_at = 111 WHERE id IN (?, ?)",
                (master_id, copy_id),
            )
            await conn.commit()
        finally:
            await conn.close()

        await catalog_repository.insert_images_batch(
            db.DB_PATH,
            [("returned.jpg", filepath, ".jpg", 10, 123.0)],
            source_id=source["id"],
        )

        self.assertIsNone((await self._image_row(master_id))["missing_at"])
        self.assertIsNone((await self._image_row(copy_id))["missing_at"])

    async def test_add_source_then_keep_remove_preserves_rows_and_originals(self):
        folder = os.path.join(self.tempdir.name, "camera")
        os.makedirs(folder)
        original = os.path.join(folder, "keep.jpg")
        with open(original, "wb") as handle:
            handle.write(b"original bytes")

        added = await self._request("POST", "/api/catalog/sources", json={"path": folder, "scan": False})
        self.assertEqual(added.status_code, 200, added.text)
        source_id = added.json()["source"]["id"]
        image_id = await self._image(source_id, "keep.jpg")

        removed = await self._request("POST", f"/api/catalog/sources/{source_id}/remove", json={"mode": "keep"})
        self.assertEqual(removed.status_code, 200, removed.text)
        self.assertTrue(removed.json()["kept_data"])
        self.assertEqual((await self._image_row(image_id))["source_id"], source_id)
        self.assertTrue(os.path.exists(original))

    async def test_purge_remove_deletes_catalog_rows_but_never_original_files(self):
        source = await self._source("remove")
        original = os.path.join(source["path"], "original.jpg")
        with open(original, "wb") as handle:
            handle.write(b"do not delete")
        image_id = await self._image(source["id"], "original.jpg")

        removed = await self._request("POST", f"/api/catalog/sources/{source['id']}/remove", json={"mode": "purge"})
        self.assertEqual(removed.status_code, 200, removed.text)
        self.assertFalse(removed.json()["kept_data"])
        conn = await db.get_db()
        try:
            row = await (await conn.execute("SELECT id FROM images WHERE id = ?", (image_id,))).fetchone()
        finally:
            await conn.close()
        self.assertIsNone(row)
        self.assertTrue(os.path.exists(original))

    async def test_first_run_scan_builds_sm_previews_and_makes_rankings_visible(self):
        folder = os.path.join(self.tempdir.name, "first-library")
        cache_dir = os.path.join(self.tempdir.name, "previews")
        os.makedirs(folder)
        Image.new("RGB", (1200, 800), color=(90, 120, 150)).save(
            os.path.join(folder, "first.jpg"),
            "JPEG",
        )

        old_cache_dir = thumbnails.SSD_CACHE_DIR
        old_cache_bytes = thumbnails.SSD_CACHE_BYTES
        old_allocations = dict(thumbnails._disk_allocations)
        old_manual_mode = thumbnails._pregen_manual_mode
        old_manual_pause = thumbnails._pregen_manual_pause
        old_last_activity = thumbnails._last_user_activity
        worker = None
        try:
            settings.save_settings({"setup_completed": False})
            thumbnails.SSD_CACHE_DIR = cache_dir
            thumbnails.SSD_CACHE_BYTES = 1024 * 1024 * 1024
            thumbnails._disk_allocations = {tier: 0 for tier in thumbnails.ALL_TIERS}
            thumbnails._pregen_manual_mode = False
            thumbnails._pregen_manual_pause = True
            thumbnails._last_user_activity = time.monotonic() - 30
            thumbnails._ensure_disk_cache_dirs()
            catalog_metadata.pause_catalog_metadata()

            response = await catalog_routes.start_scan(JsonRequest({"folder": folder}))
            self.assertEqual(response["status"], "started")
            for _attempt in range(100):
                if scanner.scan_state["done"] and not scanner.scan_state["scanning"]:
                    break
                await asyncio.sleep(0.02)
            self.assertTrue(scanner.scan_state["done"], scanner.scan_state)
            self.assertFalse(thumbnails._pregen_manual_pause)
            self.assertTrue(catalog_metadata.catalog_metadata_status()["active"])

            images = await db.get_recent_active_images(limit=1)
            image_id = int(images[0]["id"])
            empty_payload = await library_service.api_rankings_impl(limit=10)
            self.assertEqual(empty_payload["visible_images"], 0)
            worker = asyncio.create_task(thumbnails.run_prefetch_worker())
            for _attempt in range(100):
                if thumbnails.fast_disk_has("sm", image_id):
                    break
                await asyncio.sleep(0.05)

            self.assertTrue(thumbnails.fast_disk_has("sm", image_id))
            await asyncio.to_thread(thumbnails._flush_write_queue)
            payload = await library_service.api_rankings_impl(limit=10)
            self.assertEqual(payload["visible_images"], 1, payload)
            self.assertEqual(payload["hidden_pending_thumbnails"], 0, payload)
            self.assertEqual(payload["images"][0]["id"], image_id)
        finally:
            thumbnails.stop_prefetch()
            if worker is not None:
                worker.cancel()
                await asyncio.gather(worker, return_exceptions=True)
            catalog_metadata.pause_catalog_metadata()
            thumbnails.SSD_CACHE_DIR = old_cache_dir
            thumbnails.SSD_CACHE_BYTES = old_cache_bytes
            thumbnails._disk_allocations = old_allocations
            thumbnails._pregen_manual_mode = old_manual_mode
            thumbnails._pregen_manual_pause = old_manual_pause
            thumbnails._last_user_activity = old_last_activity

    async def test_existing_library_scan_preserves_paused_workers(self):
        await self._source("existing-library")
        folder = os.path.join(self.tempdir.name, "later-source")
        os.makedirs(folder)
        Image.new("RGB", (600, 400), color=(30, 60, 90)).save(
            os.path.join(folder, "later.jpg"),
            "JPEG",
        )
        settings.save_settings({"setup_completed": False})
        old_manual_mode = thumbnails._pregen_manual_mode
        old_manual_pause = thumbnails._pregen_manual_pause
        old_metadata_active = catalog_metadata.catalog_metadata_status()["active"]
        try:
            thumbnails.stop_pregeneration()
            catalog_metadata.pause_catalog_metadata()

            response = await catalog_routes.start_scan(JsonRequest({"folder": folder}))
            self.assertEqual(response["status"], "started")
            for _attempt in range(100):
                if scanner.scan_state["done"] and not scanner.scan_state["scanning"]:
                    break
                await asyncio.sleep(0.02)

            self.assertTrue(thumbnails._pregen_manual_pause)
            self.assertFalse(catalog_metadata.catalog_metadata_status()["active"])
        finally:
            thumbnails._pregen_manual_mode = old_manual_mode
            thumbnails._pregen_manual_pause = old_manual_pause
            if old_metadata_active:
                catalog_metadata.resume_catalog_metadata()
            else:
                catalog_metadata.pause_catalog_metadata()
