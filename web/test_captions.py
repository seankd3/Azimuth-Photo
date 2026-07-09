from test_support import *  # noqa: F401,F403

import caption_worker
from data.repositories import captions
from features.search.fusion import reciprocal_rank_fusion


class CaptionTests(BackendTestCase):
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
