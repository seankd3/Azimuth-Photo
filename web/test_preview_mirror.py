"""Satellite preview mirror: hit/miss/tee, LRU cap, version invalidation, idle gate."""

from __future__ import annotations

import os
import time
import unittest
from unittest import mock

import db
import thumbnails
from features.media import routes as media_routes
from features.sync import preview_mirror
from test_support import BackendTestCase, HeaderRequest
from thumbnails import cache_entries as thumbnail_cache_entries


class PreviewMirrorTests(BackendTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.old_cache_dir = thumbnails.SSD_CACHE_DIR
        self.old_allocations = dict(thumbnails._disk_allocations)
        self.old_mirror_env = os.environ.get("PHOTOARCHIVE_MIRROR_MAX_BYTES")
        self.old_mode = os.environ.get("PHOTOARCHIVE_MODE")
        self.old_hub = os.environ.get("PHOTOARCHIVE_HUB_URL")
        self.old_last_request = preview_mirror.last_request_at()

        thumbnails.SSD_CACHE_DIR = os.path.join(self.tempdir.name, "thumbs")
        os.makedirs(thumbnails.SSD_CACHE_DIR, exist_ok=True)
        for size in ("sm", "md"):
            os.makedirs(os.path.join(thumbnails.SSD_CACHE_DIR, size), exist_ok=True)
        thumbnails._disk_allocations.update(
            {"sm": 64 * 1024 * 1024, "md": 64 * 1024 * 1024, "lg": 0, "full": 0}
        )
        thumbnails._ensure_disk_cache_dirs()
        thumbnails._clear_memory_cache()
        thumbnails._clear_disk_index()
        thumbnails._tier_byte_totals.clear()
        with thumbnails._write_queue_lock:
            thumbnails._write_queue.clear()
        with thumbnails._disk_index_lock:
            thumbnail_cache_entries._disk_index_built = True

        os.environ["PHOTOARCHIVE_MODE"] = "satellite"
        os.environ["PHOTOARCHIVE_HUB_URL"] = "http://hub.test"
        preview_mirror._last_request_at = 0.0

        source = await self._source("hub")
        self.image_id = await self._image(source["id"], "remote.jpg")
        conn = await db.get_db()
        try:
            try:
                await conn.execute("ALTER TABLE images ADD COLUMN hub_image_id INTEGER")
            except Exception:
                pass
            try:
                await conn.execute("ALTER TABLE images ADD COLUMN hub_remote INTEGER NOT NULL DEFAULT 0")
            except Exception:
                pass
            await conn.execute(
                "UPDATE images SET hub_image_id = 99, hub_remote = 1, "
                "content_hash = ?, status = 'kept', date_taken = '2026-07-18' WHERE id = ?",
                ("abcd" * 8, self.image_id),
            )
            await conn.commit()
        finally:
            await conn.close()
        self.image = {
            "id": self.image_id,
            "hub_image_id": 99,
            "hub_remote": 1,
            "content_hash": "abcd" * 8,
            "status": "kept",
            "missing_at": None,
            "filepath": "hub://remote.jpg",
            "source_path": "hub://",
            "source_online": 1,
        }
        self.version = preview_mirror.preview_version_for_image(self.image)

    async def asyncTearDown(self):
        thumbnails.SSD_CACHE_DIR = self.old_cache_dir
        thumbnails._disk_allocations.clear()
        thumbnails._disk_allocations.update(self.old_allocations)
        if self.old_mirror_env is None:
            os.environ.pop("PHOTOARCHIVE_MIRROR_MAX_BYTES", None)
        else:
            os.environ["PHOTOARCHIVE_MIRROR_MAX_BYTES"] = self.old_mirror_env
        if self.old_mode is None:
            os.environ.pop("PHOTOARCHIVE_MODE", None)
        else:
            os.environ["PHOTOARCHIVE_MODE"] = self.old_mode
        if self.old_hub is None:
            os.environ.pop("PHOTOARCHIVE_HUB_URL", None)
        else:
            os.environ["PHOTOARCHIVE_HUB_URL"] = self.old_hub
        preview_mirror._last_request_at = self.old_last_request
        await super().asyncTearDown()

    def test_preview_version_uses_content_hash(self):
        self.assertEqual(self.version, f"pv:{'abcd' * 8}")

    def test_hit_miss_and_version_mismatch_invalidation(self):
        payload = b"jpeg-bytes-v1"
        self.assertTrue(preview_mirror.put(self.image_id, "md", self.version, payload))
        thumbnails._flush_write_queue()

        hit = preview_mirror.read_local(self.image_id, "md", self.version)
        self.assertIsNotNone(hit)
        self.assertEqual(hit[1], payload)

        self.assertIsNone(preview_mirror.get_local(self.image_id, "md", "pv:stale-version"))
        # Stale entry deleted lazily.
        self.assertIsNone(thumbnails.fast_disk_path_entry("md", self.image_id))
        self.assertFalse(os.path.exists(thumbnails._thumbnail_disk_path("md", self.image_id)))

    def test_lru_eviction_at_byte_cap(self):
        os.environ["PHOTOARCHIVE_MIRROR_MAX_BYTES"] = "1000000"
        for image_id, stamp in ((101, 1.0), (102, 2.0), (103, 3.0)):
            data = b"x" * 50
            version = f"pv:id:{image_id}"
            self.assertTrue(preview_mirror.put(image_id, "sm", version, data, hot=False))
            thumbnails._flush_write_queue()
            conn = thumbnail_cache_entries._db_connect()
            try:
                conn.execute(
                    "UPDATE cache_entries SET last_accessed = ? "
                    "WHERE cache_root = ? AND size = ? AND image_id = ?",
                    (stamp, thumbnails.SSD_CACHE_DIR, "sm", image_id),
                )
                conn.commit()
            finally:
                conn.close()

        os.environ["PHOTOARCHIVE_MIRROR_MAX_BYTES"] = "120"
        reclaimed = preview_mirror.enforce_byte_cap(max_bytes=120)
        self.assertGreater(reclaimed, 0)
        self.assertIsNone(thumbnails.fast_disk_path_entry("sm", 101))
        self.assertIsNotNone(thumbnails.fast_disk_path_entry("sm", 103))
        self.assertLessEqual(preview_mirror.mirror_bytes_used(), 120)

    async def test_idle_gate_refuses_burst_while_request_is_fresh(self):
        preview_mirror.note_request()
        filler = preview_mirror.PreviewMirrorFiller(
            db_path=db.DB_PATH,
            hub="http://hub.test",
            idle_seconds=10.0,
            burst_limit=4,
            burst_sleep_seconds=0.0,
        )

        async def request(*_args, **_kwargs):
            raise AssertionError("hub must not be contacted while busy")

        filler._request = request
        status = await filler.burst_once()
        self.assertEqual(status["state"], "refused_busy")
        self.assertGreaterEqual(status["refused_busy"], 1)

        preview_mirror._last_request_at = time.monotonic() - 30.0
        self.assertTrue(preview_mirror.is_idle(idle_seconds=10.0))

    async def test_tee_fetches_once_stores_and_serves(self):
        calls = []

        async def request(method, url, *, body=None, headers=None):
            calls.append((method, url))
            return 200, {"Content-Type": "image/jpeg"}, b"remote-md-bytes"

        stored = await preview_mirror.fetch_and_store(
            self.image, "md", hub="http://hub.test", request=request
        )
        self.assertEqual(stored[0], self.version)
        self.assertEqual(stored[1], b"remote-md-bytes")
        self.assertEqual(len(calls), 1)
        hit = preview_mirror.read_local(self.image_id, "md", self.version)
        self.assertEqual(hit[1], b"remote-md-bytes")

        with mock.patch.object(media_routes, "_urllib_request", request), mock.patch.object(
            media_routes,
            "_configured_db_path",
            return_value=db.DB_PATH,
        ):
            response = await media_routes.thumbnail_response(HeaderRequest(), "md", self.image_id)
        self.assertEqual(response.status_code, 200)
        if hasattr(response, "path"):
            with open(response.path, "rb") as handle:
                self.assertEqual(handle.read(), b"remote-md-bytes")
        else:
            self.assertEqual(response.body, b"remote-md-bytes")
        self.assertEqual(len(calls), 1)

    async def test_remote_miss_tees_through_media_route(self):
        calls = []

        async def request(method, url, *, body=None, headers=None):
            calls.append(url)
            return 200, {"content-type": "image/jpeg"}, b"tee-body"

        with mock.patch.object(media_routes, "_urllib_request", request), mock.patch.object(
            media_routes,
            "_configured_db_path",
            return_value=db.DB_PATH,
        ):
            response = await media_routes.thumbnail_response(HeaderRequest(), "sm", self.image_id)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.body, b"tee-body")
        self.assertEqual(len(calls), 1)
        hit = preview_mirror.read_local(self.image_id, "sm", self.version)
        self.assertEqual(hit[1], b"tee-body")

    async def test_warm_local_hit_never_contacts_hub(self):
        """Local-first: a cached sm/md must not block on or call the hub."""

        payload = b"warm-local-sm"
        self.assertTrue(preview_mirror.put(self.image_id, "sm", self.version, payload))
        thumbnails._flush_write_queue()

        async def request(*_args, **_kwargs):
            raise AssertionError("hub must not be contacted on local hit")

        with mock.patch.object(media_routes, "_urllib_request", request), mock.patch.object(
            media_routes,
            "_configured_db_path",
            return_value=db.DB_PATH,
        ):
            response = await media_routes.thumbnail_response(HeaderRequest(), "sm", self.image_id)
        self.assertEqual(response.status_code, 200)
        if hasattr(response, "path"):
            with open(response.path, "rb") as handle:
                self.assertEqual(handle.read(), payload)
        else:
            self.assertEqual(response.body, payload)


if __name__ == "__main__":
    unittest.main()
