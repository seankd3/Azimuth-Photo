from test_support import *  # noqa: F401,F403

import caption_worker
import contextlib
import unittest.mock
from fastapi.testclient import TestClient
from data.repositories import captions
from features.captions import routes as caption_routes
from features.search.fusion import reciprocal_rank_fusion
from workers.caption_health import CaptionOomCircuit


class CaptionTests(BackendTestCase):
    def test_caption_oom_circuit_opens_after_repeated_minimum_batch_failures(self):
        circuit = CaptionOomCircuit(threshold=3)

        self.assertFalse(circuit.record_failure())
        self.assertFalse(circuit.record_failure())
        self.assertTrue(circuit.record_failure())
        circuit.reset()
        self.assertEqual(circuit.consecutive_failures, 0)

    async def test_caption_worker_pauses_after_repeated_minimum_batch_ooms(self):
        old_dependencies = (
            caption_worker._count_images_needing_captions,
            caption_worker._get_images_needing_captions,
            caption_worker._store_caption_result,
        )
        old_pause = caption_worker._caption_manual_pause
        old_pause_message = caption_worker._caption_manual_pause_message
        old_status = dict(caption_worker._status)
        stored_errors = 0
        third_error = asyncio.Event()

        async def count_pending(**_kwargs):
            return 1

        async def next_image(**_kwargs):
            return [{"id": 7, "cache_path": "/tmp/caption-oom.jpg"}]

        async def store_error(**kwargs):
            nonlocal stored_errors
            self.assertEqual(kwargs["status"], "error")
            stored_errors += 1
            if stored_errors == 3:
                third_error.set()

        async def ready_for_work(*_args, **_kwargs):
            return None

        def raise_oom(*_args, **_kwargs):
            raise RuntimeError("CUDA out of memory")

        config = {
            "model_id": "test-caption-model",
            "model_key": "test-caption-model@main",
            "model_dir": "/tmp/test-caption-model",
            "quantization": "none",
            "prompt_version": "test-v1",
            "batch_size": 1,
        }
        caption_worker.configure(
            count_images_needing_captions=count_pending,
            get_images_needing_captions=next_image,
            store_caption_result=store_error,
        )
        caption_worker._caption_manual_pause = False
        caption_worker._caption_manual_pause_message = ""
        caption_worker._oom_circuit.reset()
        task = None
        try:
            with (
                unittest.mock.patch.object(settings, "get_settings", return_value={
                    "caption_scan_enabled": True,
                    "ssd_cache_dir": "/tmp",
                }),
                unittest.mock.patch.object(settings, "active_caption_config", return_value=config),
                unittest.mock.patch.object(ai_models, "model_files_present", return_value=True),
                unittest.mock.patch.object(caption_worker, "_load_model", return_value=None),
                unittest.mock.patch.object(caption_worker, "_caption_cached_preview", side_effect=raise_oom),
                unittest.mock.patch.object(caption_worker, "_clear_cuda_cache", return_value=None),
                unittest.mock.patch.object(caption_worker, "_unload_model", return_value=None),
                unittest.mock.patch.object(
                    work_coordination,
                    "wait_for_gpu_turn",
                    side_effect=ready_for_work,
                ),
                unittest.mock.patch.object(
                    work_coordination,
                    "wait_for_manual_turn",
                    side_effect=ready_for_work,
                ),
            ):
                task = asyncio.create_task(caption_worker.run_caption_worker())
                await asyncio.wait_for(third_error.wait(), timeout=2)
                await asyncio.sleep(0)
                self.assertTrue(caption_worker.manual_pause_active())
                self.assertIn("out-of-memory", caption_worker.get_worker_status()["message"])
        finally:
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            (
                caption_worker._count_images_needing_captions,
                caption_worker._get_images_needing_captions,
                caption_worker._store_caption_result,
            ) = old_dependencies
            caption_worker._caption_manual_pause = old_pause
            caption_worker._caption_manual_pause_message = old_pause_message
            caption_worker._status.clear()
            caption_worker._status.update(old_status)
            caption_worker._oom_circuit.reset()

    async def test_caption_control_failure_returns_actionable_error_without_internal_detail(self):
        secret = "/home/sean/private/model.bin"
        with unittest.mock.patch.object(
            caption_worker,
            "pause_caption_worker",
            side_effect=RuntimeError(f"failed reading {secret}"),
        ), unittest.mock.patch.object(caption_routes.log, "exception") as error_log:
            response = await caption_routes.api_pause_captions()

        payload = json.loads(response.body)
        self.assertEqual(response.status_code, 503)
        self.assertNotIn(secret, str(payload))
        self.assertIn("try again", payload["detail"])
        error_log.assert_called_once()

    async def test_caption_store_and_fts_update_delete_round_trip(self):
        source = await self._source()
        image_id = await self._image(source["id"], "wedding.jpg")

        config = settings.active_caption_config()
        await db.store_caption_result(
            image_id=image_id,
            caption_config=config,
            caption="A bride holds a white bouquet in warm window light.",
            tags=["wedding", "bride", "bouquet"],
            status="done",
        )
        matches = await db.caption_search_ranked_image_ids("bride bouquet", caption_config=config)
        self.assertEqual([image_id for image_id, _score in matches], [image_id])

        await db.store_caption_result(
            image_id=image_id,
            caption_config=config,
            caption="A night sky filled with bright stars over a dark field.",
            tags=["stars", "night sky"],
            status="done",
        )
        old_matches = await db.caption_search_ranked_image_ids("bride bouquet", caption_config=config)
        new_matches = await db.caption_search_ranked_image_ids("night stars", caption_config=config)
        self.assertEqual(old_matches, [])
        self.assertEqual([image_id for image_id, _score in new_matches], [image_id])

        conn = await db.get_db()
        try:
            await conn.execute(
                "DELETE FROM image_captions WHERE image_id = ? AND model_key = ?",
                (image_id, config["model_key"]),
            )
            await conn.commit()
        finally:
            await conn.close()
        deleted_matches = await db.caption_search_ranked_image_ids("night stars", caption_config=config)
        self.assertEqual(deleted_matches, [])

    async def test_caption_ledger_retries_error_rows_after_backoff(self):
        source = await self._source()
        first = await self._image(source["id"], "picked.jpg")
        second = await self._image(source["id"], "newest.jpg")
        await self._cache_entry(first, "md")
        await self._cache_entry(second, "md")
        config = settings.active_caption_config()

        pending = await db.get_images_needing_captions(
            limit=10,
            caption_config=config,
            cache_root=thumbnails.SSD_CACHE_DIR,
        )
        self.assertEqual({row["id"] for row in pending}, {first, second})

        await db.store_caption_result(
            image_id=first,
            caption_config=config,
            caption="",
            tags=[],
            status="error",
            error="decode failed",
        )
        await db.store_caption_result(
            image_id=second,
            caption_config=config,
            caption="A searchable caption.",
            tags=["searchable"],
            status="done",
        )

        self.assertEqual(
            await db.count_images_needing_captions(
                caption_config=config,
                cache_root=thumbnails.SSD_CACHE_DIR,
            ),
            0,
        )
        counts = await db.get_caption_status_counts(caption_config=config)
        self.assertEqual(counts["done"], 1)
        self.assertEqual(counts["error"], 1)

        conn = await db.get_db()
        try:
            await conn.execute(
                "UPDATE caption_scan_images SET scanned_at = ? WHERE image_id = ? AND model_key = ?",
                (time.time() - captions.ERROR_RETRY_AFTER_SECONDS - 1, first, config["model_key"]),
            )
            await conn.commit()
        finally:
            await conn.close()

        retry_ready = await db.get_images_needing_captions(
            limit=10,
            caption_config=config,
            cache_root=thumbnails.SSD_CACHE_DIR,
        )
        self.assertEqual([row["id"] for row in retry_ready], [first])
        self.assertEqual(
            await db.count_images_needing_captions(
                caption_config=config,
                cache_root=thumbnails.SSD_CACHE_DIR,
            ),
            1,
        )

    def test_caption_parse_fallback_uses_raw_text(self):
        parsed = caption_worker.parse_caption_response("not json but still useful")
        self.assertEqual(parsed["caption"], "not json but still useful")
        self.assertEqual(parsed["tags"], [])
        self.assertFalse(parsed["parsed"])

    def test_rrf_fusion_prefers_cross_channel_matches(self):
        scores = reciprocal_rank_fusion({
            "metadata": [1, 2, 3],
            "captions": [2, 4],
            "embedding": [3, 2, 5],
        })
        ordered = [image_id for image_id, _score in sorted(scores.items(), key=lambda item: item[1], reverse=True)]
        self.assertEqual(ordered[:2], [2, 3])

    async def test_text_search_can_fuse_caption_metadata_and_embeddings(self):
        source = await self._source()
        caption_only = await self._image(source["id"], "quiet.jpg")
        metadata_hit = await self._image(source["id"], "sunset-water.jpg")
        embedding_hit = await self._image(source["id"], "semantic.jpg")
        for image_id in (caption_only, metadata_hit, embedding_hit):
            await self._cache_entry(image_id, "sm")

        config = settings.active_caption_config()
        await db.store_caption_result(
            image_id=caption_only,
            caption_config=config,
            caption="A golden sunset reflected across calm water.",
            tags=["sunset", "water", "gold"],
            status="done",
        )
        self._stub_text_search([embedding_hit], [0.90])

        result = await search_routes.api_search(q="sunset water", limit=10)

        self.assertEqual(result["search_mode"], "fused")
        self.assertEqual(set(result["search_sources"]), {"captions", "embedding", "metadata"})
        result_ids = {image["id"] for image in result["images"]}
        self.assertTrue({caption_only, metadata_hit, embedding_hit}.issubset(result_ids))

    async def test_tags_route_and_tag_scope_filters(self):
        source = await self._source()
        wedding = await self._image(source["id"], "wedding.jpg")
        travel = await self._image(source["id"], "travel.jpg")
        await self._cache_entry(wedding, "sm")
        await self._cache_entry(travel, "sm")
        config = settings.active_caption_config()
        await db.store_caption_result(
            image_id=wedding,
            caption_config=config,
            caption="A small ceremony beside a garden path.",
            tags=["wedding", "garden"],
            status="done",
        )
        await db.store_caption_result(
            image_id=travel,
            caption_config=config,
            caption="A street scene with market umbrellas.",
            tags=["travel", "market"],
            status="done",
        )

        def probe():
            client = TestClient(app_module.app)
            tags = client.get("/api/tags", params={"q": "wed", "limit": 10})
            rankings = client.get("/api/rankings", params={"tag": "wedding", "limit": 10})
            counts = client.get("/api/counts", params={"tag": "wedding"})
            groups = client.get("/api/date-groups", params={"tag": "wedding"})
            return tags, rankings, counts, groups

        tags, rankings, counts, groups = await asyncio.to_thread(probe)
        self.assertEqual(tags.status_code, 200)
        self.assertEqual(tags.json()["tags"], [{"tag": "wedding", "count": 1}])
        self.assertEqual([image["id"] for image in rankings.json()["images"]], [wedding])
        self.assertTrue(rankings.json()["images"][0]["has_caption"])
        self.assertEqual(counts.json()["total"], 1)
        self.assertEqual(groups.json()["groups"][0]["count"], 1)

    async def test_owner_caption_edit_persists_fts_and_blocks_worker_overwrite(self):
        source = await self._source()
        image_id = await self._image(source["id"], "portrait.jpg")
        config = settings.active_caption_config()
        await db.store_caption_result(
            image_id=image_id,
            caption_config=config,
            caption="A neutral generated caption.",
            tags=["portrait"],
            status="done",
        )

        def edit():
            client = TestClient(app_module.app)
            return client.post(
                f"/api/image/{image_id}/caption",
                json={"caption": "Sean edited this into a moonlit harbor frame.", "tags": ["harbor", "moonlit"]},
            )

        response = await asyncio.to_thread(edit)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["caption"]["user_edited"])
        self.assertEqual(response.json()["caption"]["tags"], ["harbor", "moonlit"])
        matches = await db.caption_search_ranked_image_ids("moonlit harbor", caption_config=config)
        self.assertEqual([image_id for image_id, _score in matches], [image_id])

        await db.store_caption_result(
            image_id=image_id,
            caption_config=config,
            caption="Worker tried to replace the owner edit.",
            tags=["worker"],
            status="done",
        )
        saved = await db.get_image_caption(image_id, caption_config=config)
        self.assertEqual(saved["caption"], "Sean edited this into a moonlit harbor frame.")
        self.assertEqual(saved["tags"], ["harbor", "moonlit"])

        def read():
            client = TestClient(app_module.app)
            return client.get(f"/api/image/{image_id}/caption")

        readback = await asyncio.to_thread(read)
        self.assertEqual(readback.status_code, 200)
        self.assertEqual(readback.json()["caption"], "Sean edited this into a moonlit harbor frame.")
