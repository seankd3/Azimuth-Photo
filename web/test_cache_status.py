from test_support import *  # noqa: F401,F403
import unittest.mock


class CacheStatusTests(BackendTestCase):
    async def test_search_and_people_start_previews_dependency(self):
        old_manual_mode = thumbnails._pregen_manual_mode
        old_manual_pause = thumbnails._pregen_manual_pause
        try:
            thumbnails.stop_pregeneration()
            embedding_worker.pause_embedding_worker()
            face_worker.pause_face_worker()

            await ai_routes.api_resume_embeddings()
            self.assertTrue(thumbnails._pregen_manual_mode)
            self.assertFalse(thumbnails._pregen_manual_pause)
            self.assertFalse(embedding_worker.get_worker_status()["manual_pause"])

            thumbnails.stop_pregeneration()
            # The resume route 409s when the optional people pack is absent —
            # this test is about the pregen dependency, not the install state.
            with unittest.mock.patch.object(
                people_routes.capabilities,
                "capability_status",
                return_value={"available": True},
            ):
                await people_routes.api_people_scan_resume()
            self.assertTrue(thumbnails._pregen_manual_mode)
            self.assertFalse(thumbnails._pregen_manual_pause)
            self.assertFalse(face_worker.manual_pause_active())
        finally:
            thumbnails._pregen_manual_mode = old_manual_mode
            thumbnails._pregen_manual_pause = old_manual_pause
            embedding_worker.pause_embedding_worker()
            face_worker.pause_face_worker()

    async def test_stopping_previews_stops_dependent_search_and_people(self):
        old_manual_mode = thumbnails._pregen_manual_mode
        old_manual_pause = thumbnails._pregen_manual_pause
        try:
            thumbnails.start_pregeneration()
            embedding_worker.resume_embedding_worker()
            face_worker.resume_face_worker()

            await cache_routes.cache_pregen_stop()

            self.assertTrue(thumbnails._pregen_manual_pause)
            self.assertTrue(embedding_worker.get_worker_status()["manual_pause"])
            self.assertTrue(face_worker.manual_pause_active())
        finally:
            thumbnails._pregen_manual_mode = old_manual_mode
            thumbnails._pregen_manual_pause = old_manual_pause
            embedding_worker.pause_embedding_worker()
            face_worker.pause_face_worker()

    async def test_search_and_people_gpu_priority_is_first_started(self):
        try:
            embedding_worker.pause_embedding_worker()
            face_worker.pause_face_worker()

            embedding_worker.resume_embedding_worker()
            self.assertEqual(work_coordination.manual_owner(), "embeddings")
            face_worker.resume_face_worker()
            self.assertEqual(work_coordination.manual_owner(), "embeddings")

            embedding_worker.pause_embedding_worker()
            self.assertIsNone(work_coordination.manual_owner())

            face_worker.resume_face_worker()
            self.assertEqual(work_coordination.manual_owner(), "people")
            embedding_worker.resume_embedding_worker()
            self.assertEqual(work_coordination.manual_owner(), "people")
        finally:
            embedding_worker.pause_embedding_worker()
            face_worker.pause_face_worker()

    async def test_cache_entry_count_uses_short_ttl_until_invalidation(self):
        source = await self._source()
        first = await self._image(source["id"], "cache-count-1.jpg")
        second = await self._image(source["id"], "cache-count-2.jpg")
        await self._cache_entry(first, "sm")

        root = thumbnails.SSD_CACHE_DIR
        self.assertEqual(await db._cache_entry_count("sm", root), 1)
        self.assertEqual(
            await cache_entry_repository.cache_entry_count_cached(
                db.DB_PATH,
                size="sm",
                cache_root=root,
                ttl_seconds=db.CACHE_ENTRY_COUNT_TTL_SECONDS,
            ),
            1,
        )
        self.assertEqual(await db.get_cached_image_id_set("sm", root), frozenset({first}))

        await self._cache_entry(second, "sm")
        self.assertEqual(await db._cache_entry_count("sm", root), 1)
        self.assertEqual(
            await cache_entry_repository.cache_entry_count_cached(
                db.DB_PATH,
                size="sm",
                cache_root=root,
                ttl_seconds=db.CACHE_ENTRY_COUNT_TTL_SECONDS,
            ),
            1,
        )
        self.assertEqual(
            await cache_entry_repository.cached_image_id_set_cached(
                db.DB_PATH,
                size="sm",
                cache_root=root,
                ttl_seconds=db.CACHED_IMAGE_IDS_TTL_SECONDS,
            ),
            frozenset({first}),
        )

        db.invalidate_cached_image_ids_cache(cache_root=root, size="sm")
        self.assertEqual(await db._cache_entry_count("sm", root), 2)
        self.assertEqual(
            await cache_entry_repository.cache_entry_count_cached(
                db.DB_PATH,
                size="sm",
                cache_root=root,
                ttl_seconds=db.CACHE_ENTRY_COUNT_TTL_SECONDS,
            ),
            2,
        )
        self.assertEqual(await db.get_cached_image_id_set("sm", root), frozenset({first, second}))

    async def test_purge_source_invalidates_cached_image_id_cache(self):
        source = await self._source("purge-source")
        image_id = await self._image(source["id"], "cached.jpg")
        await self._cache_entry(image_id, "sm")
        db.invalidate_cached_image_ids_cache()
        self.assertIn(
            image_id,
            await db.get_cached_image_id_set("sm", thumbnails.SSD_CACHE_DIR),
        )

        await db.purge_source_catalog_data(source["id"])

        self.assertNotIn(
            image_id,
            await db.get_cached_image_id_set("sm", thumbnails.SSD_CACHE_DIR),
        )

    async def test_thumbnail_store_invalidates_cached_image_id_cache(self):
        source = await self._source("thumb-store-source")
        image_id = await self._image(source["id"], "new-cache.jpg")
        thumbnail_cache_entries._persistent_conn = None
        old_allocations = dict(thumbnails._disk_allocations)
        thumbnails._disk_allocations["sm"] = 10_000
        thumbnail_cache_entries._tier_byte_totals.clear()
        db.invalidate_cached_image_ids_cache()
        try:
            self.assertNotIn(
                image_id,
                await db.get_cached_image_id_set("sm", thumbnails.SSD_CACHE_DIR),
            )

            thumbnail_cache_entries._store_disk_entry(
                "sm",
                image_id,
                "sig-sm",
                os.path.join(self.tempdir.name, "new-cache-sm.jpg"),
                123,
            )

            self.assertIn(
                image_id,
                await db.get_cached_image_id_set("sm", thumbnails.SSD_CACHE_DIR),
            )
        finally:
            thumbnails._disk_allocations.clear()
            thumbnails._disk_allocations.update(old_allocations)
            thumbnail_cache_entries._tier_byte_totals.clear()

    async def test_thumbnail_write_queue_flush_invalidates_cached_image_id_cache(self):
        source = await self._source("thumb-flush-source")
        image_id = await self._image(source["id"], "queued-cache.jpg")
        thumbnail_cache_entries._persistent_conn = None
        old_allocations = dict(thumbnails._disk_allocations)
        old_queue = list(thumbnail_cache_entries._write_queue)
        thumbnails._disk_allocations["sm"] = 10_000
        thumbnail_cache_entries._tier_byte_totals.clear()
        with thumbnail_cache_entries._write_queue_lock:
            thumbnail_cache_entries._write_queue.clear()
        db.invalidate_cached_image_ids_cache()
        try:
            self.assertNotIn(
                image_id,
                await db.get_cached_image_id_set("sm", thumbnails.SSD_CACHE_DIR),
            )

            with thumbnail_cache_entries._write_queue_lock:
                thumbnail_cache_entries._write_queue.append(
                    (
                        "sm",
                        image_id,
                        "sig-sm",
                        os.path.join(self.tempdir.name, "queued-cache-sm.jpg"),
                        123,
                        thumbnails._current_time(),
                    )
                )
            self.assertTrue(thumbnail_cache_entries._flush_write_queue())

            self.assertIn(
                image_id,
                await db.get_cached_image_id_set("sm", thumbnails.SSD_CACHE_DIR),
            )
        finally:
            thumbnails._disk_allocations.clear()
            thumbnails._disk_allocations.update(old_allocations)
            thumbnail_cache_entries._tier_byte_totals.clear()
            with thumbnail_cache_entries._write_queue_lock:
                thumbnail_cache_entries._write_queue.clear()
                thumbnail_cache_entries._write_queue.extend(old_queue)

    async def test_thumbnail_append_preserves_visible_facet_cache(self):
        root = thumbnails.SSD_CACHE_DIR
        key = db._facet_cache_key(visible_thumb_size="sm", cache_root=root)
        cached_groups = [{"date": "2026-05", "label": "May 2026", "count": 1}]
        db._date_groups_cache[key] = {"data": cached_groups, "expires": db._time.time() + 30.0}
        db._map_markers_cache[key] = {"data": [{"id": 1}], "expires": db._time.time() + 30.0}

        db.note_cached_image_ids_added(root, "sm", [123])

        self.assertIn(key, db._date_groups_cache)
        self.assertIn(key, db._map_markers_cache)

    async def test_thumbnail_append_preserves_visible_count_cache(self):
        root = thumbnails.SSD_CACHE_DIR
        key = db._ranking_count_cache_key(visible_thumb_size="sm", cache_root=root)
        db._ranking_count_cache[key] = {"value": 12, "expires": db._time.time() + 30.0}

        db.note_cached_image_ids_added(root, "sm", [123])

        self.assertIn(key, db._ranking_count_cache)

    async def test_thumbnail_memory_warm_reads_cached_sm_md_and_lg(self):
        calls = []
        thumbnails._last_user_activity = thumbnails.time.monotonic() - 30.0

        def fake_read(size, image_id, source_signature=None, *, populate_memory=False):
            calls.append((size, image_id, source_signature, populate_memory))
            return ("sig", b"jpeg")

        thumbnails.fast_disk_read_entry = fake_read

        media_warm.schedule_cached_thumbnail_memory_warm(
            [{"id": 10}, {"id": 11}],
            "sm",
            limit=2,
        )
        media_warm.schedule_cached_thumbnail_memory_warm(
            [{"id": 20}, {"id": 21}],
            "md",
            limit=2,
        )
        media_warm.schedule_cached_thumbnail_memory_warm(
            [{"id": 30}, {"id": 31}],
            "lg",
            limit=2,
        )
        await asyncio.sleep(0.05)

        self.assertIn(("sm", 10, None, True), calls)
        self.assertIn(("sm", 11, None, True), calls)
        self.assertIn(("md", 20, None, True), calls)
        self.assertIn(("md", 21, None, True), calls)
        self.assertIn(("lg", 30, None, True), calls)
        self.assertIn(("lg", 31, None, True), calls)

    async def test_result_thumbnail_memory_warm_reads_small_batch_while_active(self):
        calls = []
        rows = [{"id": idx} for idx in range(1001, 1010)]
        thumbnails._last_user_activity = thumbnails.time.monotonic()

        def fake_read(size, image_id, source_signature=None, *, populate_memory=False):
            calls.append((size, image_id, populate_memory))
            return ("sig", b"jpeg")

        thumbnails.fast_disk_read_entry = fake_read

        media_warm.schedule_result_thumbnail_memory_warm(rows)
        deadline = asyncio.get_running_loop().time() + 0.5
        while len(calls) < 21 and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.01)

        by_size = {}
        for size, image_id, populate_memory in calls:
            self.assertTrue(populate_memory)
            by_size.setdefault(size, []).append(image_id)

        self.assertEqual(by_size.get("sm"), list(range(1001, 1010)))
        self.assertEqual(by_size.get("md"), list(range(1001, 1010)))
        self.assertEqual(by_size.get("lg"), list(range(1001, 1010)))

    async def test_warm_images_deduplicates_ids_and_ignores_invalid_values(self):
        source = await self._source()
        first = await self._image(source["id"], "first.jpg")
        second = await self._image(source["id"], "second.jpg")
        prefetch_calls = []
        full_calls = []

        async def fake_prefetch(rows, tier, limit=None, hot=False):
            prefetch_calls.append({
                "tier": tier,
                "ids": [row["id"] for row in rows],
                "limit": limit,
                "hot": hot,
            })
            return len(rows)

        async def fake_schedule_full(filepath, image_id, *, hot=True):
            full_calls.append({"id": image_id, "hot": hot, "filepath": filepath})

        thumbnails.prefetch_images = fake_prefetch
        thumbnails.schedule_full_image_cache = fake_schedule_full

        result = await media_routes.warm_images(JsonRequest({
            "tiers": {
                "md": [first, str(first), -1, "bad", second, 999999],
                "full": [first, first, second, "nope"],
                "bogus": [first],
            }
        }))

        self.assertEqual(result["images"], 3)
        self.assertEqual(result["scheduled"], {"md": 2, "full": 2})
        self.assertEqual(prefetch_calls, [{
            "tier": "md",
            "ids": [first, second],
            "limit": 2,
            "hot": True,
        }])
        self.assertEqual([call["id"] for call in full_calls], [first, second])
        self.assertTrue(all(call["hot"] for call in full_calls))

    async def test_warm_images_skips_cached_thumbnail_generation_but_primes_memory(self):
        source = await self._source()
        cached = await self._image(source["id"], "cached.jpg")
        uncached = await self._image(source["id"], "uncached.jpg")
        await self._cache_entry(cached, "md")
        prefetch_calls = []
        memory_reads = []

        async def fake_prefetch(rows, tier, limit=None, hot=False):
            prefetch_calls.append({
                "tier": tier,
                "ids": [row["id"] for row in rows],
                "limit": limit,
                "hot": hot,
            })
            return len(rows)

        def fake_read(size, image_id, source_signature=None, *, populate_memory=False):
            memory_reads.append((size, image_id, source_signature, populate_memory))
            return ("sig", b"jpeg")

        thumbnails.prefetch_images = fake_prefetch
        thumbnails.fast_disk_read_entry = fake_read
        media_warm._thumbnail_memory_warm_inflight.clear()

        result = await media_routes.warm_images(JsonRequest({"tiers": {"md": [cached, uncached]}}))
        await asyncio.sleep(0.05)

        self.assertEqual(result["scheduled"], {"md": 1})
        self.assertEqual(prefetch_calls, [{
            "tier": "md",
            "ids": [uncached],
            "limit": 1,
            "hot": True,
        }])
        self.assertIn(("md", cached, None, True), memory_reads)
        self.assertIn(("md", uncached, None, True), memory_reads)

    async def test_warm_images_is_best_effort_when_thumbnail_cache_is_locked(self):
        source = await self._source()
        image_id = await self._image(source["id"], "locked.jpg")

        async def locked_prefetch(*_args, **_kwargs):
            raise sqlite3.OperationalError("database is locked")

        thumbnails.prefetch_images = locked_prefetch

        result = await media_routes.warm_images(JsonRequest({"tiers": {"md": [image_id]}}))

        self.assertEqual(result, {"scheduled": {"md": 0}, "images": 1})

    async def test_warm_images_is_best_effort_when_full_cache_is_locked(self):
        source = await self._source()
        image_id = await self._image(source["id"], "locked-full.jpg")

        async def locked_full(*_args, **_kwargs):
            raise sqlite3.OperationalError("database is locked")

        thumbnails.schedule_full_image_cache = locked_full

        result = await media_routes.warm_images(JsonRequest({"tiers": {"full": [image_id]}}))

        self.assertEqual(result, {"scheduled": {"full": 0}, "images": 1})

    async def test_media_status_reports_cached_tiers_without_image_lookup(self):
        def fake_has_cached_fast(size, image_id):
            return image_id == 42 and size == "md"

        def fake_fast_disk_path_entry(size, image_id):
            if image_id == 42 and size == thumbnails.FULL_TIER:
                return ("sig", "/tmp/full.jpg")
            return None

        thumbnails.has_cached_fast = fake_has_cached_fast
        thumbnails.fast_disk_path_entry = fake_fast_disk_path_entry

        result = await media_routes.image_media_status(42)

        self.assertEqual(set(result["tiers"].keys()), {"sm", "md", "lg", "full"})
        self.assertTrue(result["tiers"]["md"]["cached"])
        self.assertTrue(result["tiers"]["full"]["cached"])
        self.assertEqual(result["best_cached"], "full")
        self.assertEqual(result["tiers"]["md"]["cached_url"], "/api/thumb/md/42?cached=1")

    async def test_batch_media_status_deduplicates_and_limits_ids(self):
        def fake_has_cached_fast(size, image_id):
            return size == "sm" and image_id == 42

        def fake_fast_disk_path_entry(_size, _image_id):
            return None

        old_has_cached_fast = thumbnails.has_cached_fast
        old_fast_disk_path_entry = thumbnails.fast_disk_path_entry
        thumbnails.has_cached_fast = fake_has_cached_fast
        thumbnails.fast_disk_path_entry = fake_fast_disk_path_entry
        try:
            result = await media_routes.images_media_status(JsonRequest({"ids": [42, "42", "bad", 43]}))
        finally:
            thumbnails.has_cached_fast = old_has_cached_fast
            thumbnails.fast_disk_path_entry = old_fast_disk_path_entry

        self.assertEqual([status["id"] for status in result["statuses"]], [42, 43])
        self.assertTrue(result["statuses"][0]["tiers"]["sm"]["cached"])
        self.assertFalse(result["statuses"][1]["tiers"]["sm"]["cached"])

    async def test_thumbnail_matching_disk_etag_returns_304_without_reading_file(self):
        old_memory_get = thumbnails._memory_get_entry_fast
        old_path_entry = thumbnails.fast_disk_path_entry
        old_read_entry = thumbnails.fast_disk_read_entry

        def fake_memory_get(_size, _image_id):
            return None

        def fake_path_entry(size, image_id):
            self.assertEqual(size, "sm")
            self.assertEqual(image_id, 42)
            return ("sig-42", "/tmp/unused.jpg")

        def fail_read(*_args, **_kwargs):
            raise AssertionError("matching ETag should not read thumbnail bytes")

        thumbnails._memory_get_entry_fast = fake_memory_get
        thumbnails.fast_disk_path_entry = fake_path_entry
        thumbnails.fast_disk_read_entry = fail_read
        try:
            response = await media_routes.serve_thumbnail(
                HeaderRequest({"if-none-match": '"sig-42"'}),
                "sm",
                42,
                cached=True,
            )
        finally:
            thumbnails._memory_get_entry_fast = old_memory_get
            thumbnails.fast_disk_path_entry = old_path_entry
            thumbnails.fast_disk_read_entry = old_read_entry

        self.assertEqual(response.status_code, 304)

    async def test_cached_lg_thumbnail_uses_file_response_without_reading_bytes(self):
        old_memory_get = thumbnails._memory_get_entry_fast
        old_path_entry = thumbnails.fast_disk_path_entry
        old_read_entry = thumbnails.fast_disk_read_entry
        thumb_path = os.path.join(self.tempdir.name, "lg.jpg")
        with open(thumb_path, "wb") as f:
            f.write(b"jpeg")

        def fake_memory_get(_size, _image_id):
            return None

        def fake_path_entry(size, image_id):
            self.assertEqual(size, "lg")
            self.assertEqual(image_id, 42)
            return ("sig-42", thumb_path)

        def fail_read(*_args, **_kwargs):
            raise AssertionError("cached lg thumbnails should stream from disk")

        thumbnails._memory_get_entry_fast = fake_memory_get
        thumbnails.fast_disk_path_entry = fake_path_entry
        thumbnails.fast_disk_read_entry = fail_read
        try:
            response = await media_routes.serve_thumbnail(HeaderRequest(), "lg", 42, cached=True)
        finally:
            thumbnails._memory_get_entry_fast = old_memory_get
            thumbnails.fast_disk_path_entry = old_path_entry
            thumbnails.fast_disk_read_entry = old_read_entry

        self.assertIsInstance(response, FileResponse)
        self.assertEqual(response.headers.get("etag"), '"sig-42"')

    async def test_cached_full_image_uses_file_response_without_image_lookup(self):
        old_path_entry = thumbnails.fast_disk_path_entry
        old_get_image = image_repository.get_image_by_id
        full_path = os.path.join(self.tempdir.name, "full.jpg")
        with open(full_path, "wb") as f:
            f.write(b"jpeg")

        def fake_path_entry(size, image_id):
            self.assertEqual(size, thumbnails.FULL_TIER)
            self.assertEqual(image_id, 42)
            return ("full-sig-42", full_path)

        async def fail_get_image(_db_path, _image_id):
            raise AssertionError("cached full image should not hit image lookup")

        thumbnails.fast_disk_path_entry = fake_path_entry
        image_repository.get_image_by_id = fail_get_image
        try:
            response = await media_routes.serve_full_image(
                HeaderRequest(), 42, BackgroundTasks(), cached=True
            )
        finally:
            thumbnails.fast_disk_path_entry = old_path_entry
            image_repository.get_image_by_id = old_get_image

        self.assertIsInstance(response, FileResponse)
        self.assertEqual(response.headers.get("etag"), '"full-sig-42"')

    async def test_cached_full_image_matching_etag_returns_304_without_image_lookup(self):
        old_path_entry = thumbnails.fast_disk_path_entry
        old_get_image = image_repository.get_image_by_id

        def fake_path_entry(size, image_id):
            self.assertEqual(size, thumbnails.FULL_TIER)
            self.assertEqual(image_id, 42)
            return ("full-sig-42", "/tmp/unused-full.jpg")

        async def fail_get_image(_db_path, _image_id):
            raise AssertionError("matching full ETag should not hit image lookup")

        thumbnails.fast_disk_path_entry = fake_path_entry
        image_repository.get_image_by_id = fail_get_image
        try:
            response = await media_routes.serve_full_image(
                HeaderRequest({"if-none-match": '"full-sig-42"'}),
                42,
                BackgroundTasks(),
                cached=True,
            )
        finally:
            thumbnails.fast_disk_path_entry = old_path_entry
            image_repository.get_image_by_id = old_get_image

        self.assertEqual(response.status_code, 304)

    async def test_missing_online_source_returns_gone_and_marks_catalog_row(self):
        source = await self._source("missing-media")
        image_id = await self._image(source["id"], "gone.jpg")

        thumbnail = await media_routes.serve_thumbnail(HeaderRequest(), "sm", image_id)
        full = await media_routes.serve_full_image(HeaderRequest(), image_id, BackgroundTasks())
        row = await self._image_row(image_id)

        self.assertEqual(thumbnail.status_code, 410)
        self.assertEqual(json.loads(thumbnail.body)["reason"], "source_missing")
        self.assertEqual(full.status_code, 410)
        self.assertIsNotNone(row["missing_at"])

    async def test_offline_source_uses_cached_preview_without_marking_image_missing(self):
        source = await self._source("offline-media")
        image_id = await self._image(source["id"], "offline.jpg")
        cached_path = os.path.join(self.tempdir.name, "offline-cache.jpg")
        with open(cached_path, "wb") as handle:
            handle.write(b"cached")
        os.rmdir(source["path"])

        old_memory_get = thumbnails._memory_get_entry_fast
        old_path_entry = thumbnails.fast_disk_path_entry
        thumbnails._memory_get_entry_fast = lambda _size, _image_id: None
        thumbnails.fast_disk_path_entry = lambda _size, _image_id: ("offline-sig", cached_path)
        try:
            cached_response = await media_routes.serve_thumbnail(HeaderRequest(), "sm", image_id)
            thumbnails.fast_disk_path_entry = lambda _size, _image_id: None
            uncached_response = await media_routes.serve_thumbnail(HeaderRequest(), "sm", image_id)
        finally:
            thumbnails._memory_get_entry_fast = old_memory_get
            thumbnails.fast_disk_path_entry = old_path_entry

        self.assertIsInstance(cached_response, FileResponse)
        self.assertEqual(uncached_response.status_code, 404)
        self.assertEqual(json.loads(uncached_response.body)["reason"], "source_offline")
        self.assertIsNone((await self._image_row(image_id))["missing_at"])

    async def test_zero_byte_image_is_quarantined_and_logged_once(self):
        source = await self._source("zero-media")
        image_id = await self._image(source["id"], "empty.jpg")
        filepath = (await self._image_row(image_id))["filepath"]
        open(filepath, "wb").close()

        with unittest.mock.patch.object(media_routes.log, "warning") as warning:
            first = await media_routes.serve_thumbnail(HeaderRequest(), "sm", image_id)
            second = await media_routes.serve_thumbnail(HeaderRequest(), "sm", image_id)

        self.assertEqual(first.status_code, 410)
        self.assertEqual(json.loads(first.body)["reason"], "source_corrupt")
        self.assertEqual(second.status_code, 410)
        self.assertEqual(warning.call_count, 1)
        self.assertIsNotNone((await self._image_row(image_id))["missing_at"])

    async def test_corrupt_nonempty_image_returns_gone_instead_of_server_error(self):
        source = await self._source("corrupt-media")
        image_id = await self._image(source["id"], "corrupt.jpg")
        filepath = (await self._image_row(image_id))["filepath"]
        with open(filepath, "wb") as handle:
            handle.write(b"not a jpeg")

        response = await media_routes.serve_thumbnail(HeaderRequest(), "sm", image_id)

        self.assertEqual(response.status_code, 410)
        self.assertEqual(json.loads(response.body)["reason"], "source_corrupt")
        self.assertIsNotNone((await self._image_row(image_id))["missing_at"])

    async def test_cache_status_reports_preview_and_original_progress_separately(self):
        source = await self._source()
        await self._image(source["id"], "browser-original.jpg")
        await self._image(source["id"], "raw-original.nef")

        def tier(count, bytes_used, budget):
            return {
                "count": count,
                "bytes": bytes_used,
                "current_count": count,
                "current_bytes": bytes_used,
                "stale_count": 0,
                "replacement_mode": False,
                "budget_bytes": budget,
            }

        cache_stats = {
            "memory": {"used_bytes": 0, "limit_bytes": 1, "tiers": {}},
            "disk": {
                "root": thumbnails.SSD_CACHE_DIR,
                "limit_bytes": 1000,
                "used_bytes": 460,
                "tiers": {
                    "sm": tier(2, 20, 100),
                    "md": tier(2, 80, 200),
                    "lg": tier(2, 160, 300),
                    "full": tier(0, 0, 400),
                },
            },
            "thumbnail_config": {"changed_at": 0, "replace_stale_thumbnails": False},
        }
        captured = {}
        def fake_pregen_status(target_total, stats=None, original_total=0, archive_estimates=None):
            captured["target_total"] = target_total
            captured["original_total"] = original_total
            return {
                "state": "running",
                "manual_pause": False,
                "active_phase": "full",
                "phases": {
                    "sm": {"count": 2, "total": 2, "remaining": 0},
                    "md": {"count": 2, "total": 2, "remaining": 0},
                    "lg": {"count": 2, "total": 2, "remaining": 0},
                },
                "preview": {"count": 6, "total": 6, "remaining": 0, "progress_pct": 100.0},
                "originals": {"count": 0, "total": original_total, "remaining": original_total},
                "remaining": 0,
                "eta_seconds": None,
                "original_eta_seconds": None,
                "replacement_mode": False,
            }

        old_cache_stats = thumbnails.cache_stats
        old_pregen_status = thumbnails.get_pregen_status
        old_recommendations = cache_status_service._cache_recommendations
        try:
            thumbnails.cache_stats = lambda: cache_stats
            thumbnails.get_pregen_status = fake_pregen_status
            cache_status_service._cache_recommendations = (
                lambda _cache, eligible, total, browser, estimates=None: {
                    "eligible_images": eligible,
                    "total_images": total,
                    "browser_original_images": browser,
                    "tiers": {},
                }
            )

            result = await cache_status_service.build_cache_status(ahead=0)
        finally:
            thumbnails.cache_stats = old_cache_stats
            thumbnails.get_pregen_status = old_pregen_status
            cache_status_service._cache_recommendations = old_recommendations

        self.assertEqual(captured, {"target_total": 2, "original_total": 1})
        self.assertGreaterEqual(cache_status_service._cache_status_cache_ttl_seconds, 30.0)
        self.assertGreaterEqual(cache_status_service._browser_original_count_cache_ttl_seconds, 30.0)
        self.assertEqual(result["disk"]["tiers"]["sm"]["progress_total"], 2)
        self.assertEqual(result["disk"]["tiers"]["full"]["progress_total"], 1)
        self.assertEqual(result["pregen"]["preview"]["remaining"], 0)
        self.assertEqual(result["pregen"]["originals"]["remaining"], 1)

    async def test_original_cache_status_uses_catalog_bytes_for_capacity(self):
        stats = {
            "disk": {
                "tiers": {
                    "full": {
                        "count": 10,
                        "bytes": 2000,
                        "budget_bytes": 10000,
                    }
                }
            }
        }
        archive_estimates = {"needed_bytes": {thumbnails.FULL_TIER: 10000}}

        result = thumbnails._original_cache_status(
            stats,
            original_total=100,
            archive_estimates=archive_estimates,
        )

        self.assertEqual(result["avg_bytes"], 100)
        self.assertEqual(result["estimated_capacity"], 100)
        self.assertEqual(result["total"], 100)
        self.assertEqual(result["remaining"], 90)

    async def test_cache_status_ttl_is_short_while_idle_warmup_has_remaining_work(self):
        result = {
            "pregen": {
                "state": "idle",
                "enabled": True,
                "manual_pause": False,
                "preview": {"remaining": 25},
                "originals": {"remaining": 0},
            }
        }

        self.assertEqual(cache_status_service._cache_status_ttl(result), 1.0)

    async def test_cache_status_ttl_is_short_while_running(self):
        result = {
            "pregen": {
                "state": "running",
                "enabled": True,
                "manual_pause": False,
                "active_phase": "previews",
            }
        }

        self.assertEqual(cache_status_service._cache_status_ttl(result), 2.0)

    async def test_cache_status_caps_ahead_window(self):
        result = await cache_status_service.build_cache_status(
            ahead=cache_status_service._cache_status_ahead_limit + 100
        )

        self.assertEqual(result["window"], cache_status_service._cache_status_ahead_limit)

    async def test_cache_pregen_status_reuses_cached_status_builder(self):
        first = await cache_status_service.build_cache_status(ahead=0)
        result = await cache_routes.cache_pregen_status()

        self.assertEqual(result["state"], first["pregen"]["state"])
        self.assertIn("preview", result)

    async def test_cache_status_invalidation_expires_settings_without_dropping_stale_data(self):
        first = await settings_routes.api_settings()
        self.assertIsNotNone(settings_status._settings_response_cache["data"])
        self.assertGreater(settings_status._settings_response_cache["expires"], 0)

        cache_status_service.invalidate_cache_status_cache()

        self.assertIsNotNone(settings_status._settings_response_cache["data"])
        self.assertEqual(settings_status._settings_response_cache["expires"], 0)

        second = await settings_routes.api_settings()
        self.assertEqual(second["settings"], first["settings"])
        for _ in range(20):
            if not settings_status.get_settings_response_refreshing():
                break
            await asyncio.sleep(0.01)

    async def test_cache_status_cache_protects_nested_responses(self):
        first = await cache_status_service.build_cache_status(ahead=0)
        self.assertTrue(cache_status_service._cache_status_cache)

        first["disk"]["tiers"]["sm"]["count"] = 999999
        first["pregen"]["phases"]["sm"]["count"] = 999999
        first["system_resources"]["disk"]["free_bytes"] = -1

        second = await cache_status_service.build_cache_status(ahead=0)

        self.assertNotEqual(second["disk"]["tiers"]["sm"]["count"], 999999)
        self.assertNotEqual(second["pregen"]["phases"]["sm"]["count"], 999999)
        self.assertNotEqual(second["system_resources"]["disk"]["free_bytes"], -1)

    async def test_thumbnail_disk_stats_cache_protects_nested_tiers(self):
        first = thumbnails.cache_stats()

        first["disk"]["tiers"]["sm"]["count"] = 999999
        second = thumbnails.cache_stats()

        self.assertNotEqual(second["disk"]["tiers"]["sm"]["count"], 999999)
