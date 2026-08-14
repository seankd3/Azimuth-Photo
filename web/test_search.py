from test_support import *  # noqa: F401,F403
import unittest.mock


class SearchTests(BackendTestCase):
    async def test_embedding_image_is_poisoned_only_after_three_ooms(self):
        source = await self._source("embedding-poison")
        image_id = await self._image(source["id"], "oom.jpg")
        config = settings.active_embedding_config()

        before = await db.get_unembedded_images(
            limit=10,
            embedding_config=config,
        )
        self.assertEqual([row["id"] for row in before], [image_id])

        await db.poison_embedding_image(
            image_id=image_id,
            embedding_config=config,
            error="CUDA out of memory",
        )

        after_first = await db.get_unembedded_images(
            limit=10,
            embedding_config=config,
        )
        self.assertEqual([row["id"] for row in after_first], [image_id])
        await db.poison_embedding_image(
            image_id=image_id,
            embedding_config=config,
            error="CUDA out of memory",
        )
        await db.poison_embedding_image(
            image_id=image_id,
            embedding_config=config,
            error="CUDA out of memory",
        )

        after_third = await db.get_unembedded_images(
            limit=10,
            embedding_config=config,
        )
        self.assertEqual(after_third, [])
        conn = await db.get_db()
        try:
            row = await (
                await conn.execute(
                    "SELECT status, attempts FROM embedding_scan_images "
                    "WHERE model_key = ? AND image_id = ?",
                    (config["model_key"], image_id),
                )
            ).fetchone()
        finally:
            await conn.close()
        self.assertEqual(dict(row), {"status": "poisoned", "attempts": 3})

    async def test_metadata_search_image_ids_uses_active_fts_index(self):
        source = await self._source()
        match = await self._image(source["id"], "sunset-visible.jpg")
        miss = await self._image(source["id"], "portrait-visible.jpg")
        missing_match = await self._image(source["id"], "sunset-missing.jpg", missing_at=123.0)
        offline_source = await self._source("offline-metadata", online=False)
        offline_match = await self._image(offline_source["id"], "sunset-offline.jpg")
        rejected_match = await self._image(source["id"], "sunset-rejected.jpg")
        conn = await db.get_db()
        try:
            await conn.execute("UPDATE images SET status = 'rejected' WHERE id = ?", (rejected_match,))
            await conn.commit()
        finally:
            await conn.close()

        self.assertEqual(await db.metadata_search_image_ids("sunset"), {match, offline_match})
        self.assertEqual(await db.metadata_search_image_ids("no-such-photo"), set())
        self.assertNotIn(miss, await db.metadata_search_image_ids("sunset"))
        self.assertNotIn(missing_match, await db.metadata_search_image_ids("sunset"))
        self.assertNotIn(rejected_match, await db.metadata_search_image_ids("sunset"))
        self.assertIsNone(await db.metadata_search_image_ids("su"))
        self.assertIsNone(await db.metadata_search_image_ids("sunset", max_results=1))
        active_source_ids = await catalog_repository.active_source_id_set_cached(db.DB_PATH)
        self.assertEqual(
            await metadata_search.metadata_search_image_ids(
                db.DB_PATH,
                "sunset",
                active_source_ids=active_source_ids,
            ),
            await db.metadata_search_image_ids("sunset"),
        )


    async def test_rankings_search_similarity_defaults_but_other_sorts_keep_pool(self):
        source = await self._source()
        best_match = await self._image(source["id"], "landscape-best.jpg", elo=1200)
        rated_match = await self._image(source["id"], "landscape-rated.jpg", elo=1600)
        miss = await self._image(source["id"], "portrait-miss.jpg", elo=1800)
        for image_id in (best_match, rated_match, miss):
            await self._cache_entry(image_id, "sm")

        await self._stub_text_search(
            [best_match, rated_match, miss],
            [0.92, 0.70, 0.10],
        )

        similarity = await library_routes.api_rankings(q="landscapes", sort="similarity", limit=10)
        elo_sorted = await library_routes.api_rankings(q="landscapes", sort="elo", limit=10)

        self.assertEqual([img["id"] for img in similarity["images"]], [best_match, rated_match])
        self.assertEqual([img["id"] for img in elo_sorted["images"]], [rated_match, best_match])
        self.assertEqual(similarity["total_images"], 2)
        self.assertEqual(elo_sorted["total_images"], 2)
        self.assertNotIn(miss, [img["id"] for img in elo_sorted["images"]])

    async def test_mosaic_next_search_filters_candidates_and_counts_visibility(self):
        source = await self._source()
        visible_a = await self._image(source["id"], "landscape-a.jpg")
        hidden_match = await self._image(source["id"], "landscape-hidden.jpg")
        visible_b = await self._image(source["id"], "landscape-b.jpg")
        miss = await self._image(source["id"], "portrait-miss.jpg")
        for image_id in (visible_a, visible_b, miss):
            # Both tiers: a wave this size is gated on sm, a duel on md, and
            # this test is about search filtering rather than tier choice.
            await self._cache_entry(image_id, "sm")
            await self._cache_entry(image_id, "md")

        await self._stub_text_search(
            [visible_a, hidden_match, visible_b, miss],
            [0.95, 0.90, 0.80, 0.10],
        )

        result = await compare_routes.mosaic_next(n=5, strategy="random", q="landscapes")
        ids = {img["id"] for img in result["images"]}

        self.assertEqual(ids, {visible_a, visible_b})
        self.assertEqual(result["visible_images"], 2)
        self.assertEqual(result["total_images"], 3)
        self.assertEqual(result["stats"]["filtered_pool_visible"], 2)
        self.assertEqual(result["stats"]["filtered_pool_total"], 3)


    async def test_empty_search_deep_parameter_reports_publicly_disabled(self):
        result = await search_routes.api_search(q="", deep=True, limit=10)

        self.assertEqual(result["images"], [])
        self.assertNotIn("deep_requested", result)
        self.assertNotIn("deep_search_cached", result)

    async def test_embedding_batch_listener_invalidates_vector_derived_caches(self):
        search_service._duplicates_cache.update({"key": ("stale",), "data": {"pairs": []}})

        cache_events.embedding_batch_stored("model", [1])

        self.assertIsNone(search_service._duplicates_cache["key"])
        self.assertIsNone(search_service._duplicates_cache["data"])

    async def test_similar_visibility_is_mode_aware(self):
        source = await self._source()
        source_image = await self._image(source["id"], "source.jpg")
        hidden_best = await self._image(source["id"], "hidden-best.jpg")
        visible_first = await self._image(source["id"], "visible-first.jpg")
        hidden_next = await self._image(source["id"], "hidden-next.jpg")
        visible_second = await self._image(source["id"], "visible-second.jpg")
        await self._cache_entry(visible_first, "sm")
        await self._cache_entry(visible_second, "sm")

        image_ids = [source_image, hidden_best, visible_first, hidden_next, visible_second]
        matrix = np.array(
            [
                [1.00, 0.00],
                [0.99, 0.01],
                [0.90, 0.10],
                [0.80, 0.20],
                [0.70, 0.30],
            ],
            dtype=np.float32,
        )

        async def fake_get_matrix(_model_key=None):
            return image_ids, matrix

        def fake_get_index():
            return {image_id: idx for idx, image_id in enumerate(image_ids)}

        def fake_get_vector(image_id):
            return matrix[fake_get_index()[image_id]]

        elo_propagation.embed_cache.get_matrix = fake_get_matrix
        elo_propagation.embed_cache.get_index = fake_get_index
        elo_propagation.embed_cache.get_vector = fake_get_vector

        result = await search_routes.api_similar(source_image, limit=2)

        self.assertEqual([img["id"] for img in result["images"]], [visible_first, visible_second])
        self.assertEqual(result["visible_images"], 2)
        self.assertEqual(result["total_images"], 4)
        self.assertEqual(result["hidden_pending_thumbnails"], 2)
        self.assertTrue(all(card["preview_ready"] for card in result["images"]))

        with unittest.mock.patch.dict(
            os.environ,
            {"AZIMUTH_MODE": "satellite", "AZIMUTH_HUB_URL": "http://stub-hub"},
        ):
            satellite_result = await search_routes.api_similar(source_image, limit=2)

        self.assertEqual([img["id"] for img in satellite_result["images"]], [hidden_best, visible_first])
        self.assertEqual(satellite_result["visible_images"], 4)
        self.assertEqual(satellite_result["total_images"], 4)
        self.assertEqual(satellite_result["hidden_pending_thumbnails"], 0)
        satellite_cards = {card["id"]: card for card in satellite_result["images"]}
        self.assertFalse(satellite_cards[hidden_best]["preview_ready"])
        self.assertTrue(satellite_cards[visible_first]["preview_ready"])
        self.assertNotIn("thumb_url", satellite_cards[hidden_best])
        self.assertIn("thumb_url", satellite_cards[visible_first])

    async def test_duplicates_reuses_cached_result_for_same_embedding_surface(self):
        source = await self._source()
        first = await self._image(source["id"], "dup-a.jpg", elo=1300)
        second = await self._image(source["id"], "dup-b.jpg", elo=1250)
        for image_id in (first, second):
            await self._cache_entry(image_id, "sm")

        image_ids = [first, second]
        matrix = np.array([[1.0, 0.0], [1.0, 0.0]], dtype=np.float32)

        async def fake_get_matrix(_model_key=None):
            return image_ids, matrix

        old_get_active_images_by_ids = image_repository.get_active_images_by_ids
        elo_propagation.embed_cache.get_matrix = fake_get_matrix
        search_service._duplicates_cache.update({"key": None, "data": None})
        first_result = await search_routes.api_duplicates(threshold=0.95, limit=10)

        async def fail_get_active_images_by_ids(_db_path, _ids):
            raise AssertionError("cached duplicate result should avoid refetching images")

        image_repository.get_active_images_by_ids = fail_get_active_images_by_ids
        try:
            second_result = await search_routes.api_duplicates(threshold=0.95, limit=10)
        finally:
            image_repository.get_active_images_by_ids = old_get_active_images_by_ids
            search_service._duplicates_cache.update({"key": None, "data": None})

        self.assertEqual(first_result, second_result)
        self.assertEqual(first_result["visible_pairs"], 1)

    async def test_deep_search_terms_are_not_persisted(self):
        saved = settings.save_settings({
            "deep_search_terms": [
                " Crane ",
                "crane",
                "",
                "  black   and   white portraits  ",
            ]
        })

        self.assertNotIn("deep_search_terms", saved)

        reloaded = settings.load_settings(force=True)

        self.assertNotIn("deep_search_terms", reloaded)

    async def test_deep_search_schedule_fields_are_not_in_defaults(self):
        normalized = settings.normalize_settings({})

        self.assertNotIn("deep_search_schedule_enabled", normalized)
        self.assertNotIn("deep_search_schedule_days", normalized)
        self.assertNotIn("deep_search_schedule_start", normalized)
        self.assertNotIn("deep_search_schedule_end", normalized)
        self.assertNotIn("deep_search_schedule_timezone", normalized)

