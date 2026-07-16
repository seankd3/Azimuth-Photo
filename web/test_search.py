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

    async def test_resume_embeddings_clears_only_active_model_poison(self):
        source = await self._source("embedding-resume")
        image_id = await self._image(source["id"], "resume-oom.jpg")
        active_config = settings.active_embedding_config()
        other_config = {
            **active_config,
            "model_key": "other-model@main:4",
            "model_id": "other-model",
            "dimension": 4,
        }
        for config in (active_config, other_config):
            await db.poison_embedding_image(
                image_id=image_id,
                embedding_config=config,
                error="CUDA out of memory",
                force=True,
            )

        with (
            unittest.mock.patch.object(
                ai_routes.capabilities,
                "capability_status",
                return_value={"available": True, "optional_missing": []},
            ),
            unittest.mock.patch.object(
                ai_routes,
                "embedding_runtime_status",
                return_value={"ready": True},
            ),
            unittest.mock.patch.object(
                ai_routes,
                "build_ai_status",
                new=unittest.mock.AsyncMock(return_value={}),
            ),
            unittest.mock.patch.object(thumbnails, "start_pregeneration"),
            unittest.mock.patch.object(embedding_worker, "resume_embedding_worker"),
        ):
            response = await ai_routes.api_resume_embeddings()

        self.assertEqual(response["ok"], True)
        conn = await db.get_db()
        try:
            rows = await (
                await conn.execute(
                    "SELECT model_key FROM embedding_scan_images ORDER BY model_key"
                )
            ).fetchall()
        finally:
            await conn.close()
        self.assertEqual([row["model_key"] for row in rows], [other_config["model_key"]])

    async def test_exif_failures_log_image_context_without_failing_request(self):
        source = await self._source("exif-errors")
        image_id = await self._image(source["id"], "broken.jpg")
        old_batch_update = search_routes._batch_update_metadata

        async def fail_backfill(_updates):
            raise RuntimeError("database write failed")

        search_routes._batch_update_metadata = fail_backfill
        try:
            with unittest.mock.patch.object(
                search_routes.photo_metadata,
                "extract_image_metadata",
                side_effect=OSError("decode failed"),
            ), unittest.mock.patch.object(search_routes.log, "exception") as error_log:
                result = await search_routes.api_exif(image_id)
        finally:
            search_routes._batch_update_metadata = old_batch_update
            search_routes._exif_cache.pop(image_id, None)

        self.assertIn("exif", result)
        self.assertEqual(error_log.call_count, 2)
        rendered = [call.args[0] % call.args[1:] for call in error_log.call_args_list]
        self.assertTrue(all(f"image_id={image_id}" in message for message in rendered))

    async def test_text_search_exact_extension_uses_file_type_filter(self):
        conditions, params = db._ranking_filter_parts(text_query="jpg", include_source=False)
        where = " AND ".join(conditions)

        self.assertIn("LOWER(i.file_ext) IN (?, ?)", where)
        self.assertNotIn("i.filename LIKE", where)
        self.assertEqual(params[-2:], ["jpg", ".jpg"])

    async def test_mosaic_diverse_expands_filtered_and_search_results_to_visible_universe(self):
        def candidate(image_id: int) -> dict:
            return {
                "id": image_id,
                "filename": f"filtered-{image_id}.jpg",
                "filepath": f"/photos/filtered-{image_id}.jpg",
                "elo": 1200.0,
                "comparisons": 0,
                "propagated_updates": 0,
                "status": "kept",
                "flag": "unflagged",
                "orientation": "portrait",
                "aspect_ratio": 0.8,
                "date_taken": "",
                "camera_make": "",
                "camera_model": "",
                "lens": "",
                "file_ext": ".jpg",
                "created_at": 1.0,
            }

        filtered_limits = []
        search_calls = []
        old_filtered = compare_service.filtered_visible_ranked_candidates
        old_search = compare_service.search_visible_ranked_candidates
        old_diverse = compare_service.diverse_sample
        old_resolve = compare_service._resolve_library_constraints
        try:
            async def fake_filtered(size: str, *, limit: int, **_kwargs):
                filtered_limits.append((size, limit))
                returned = min(int(limit), 500)
                return [candidate(idx) for idx in range(1, returned + 1)], 600, 500

            async def fake_search(size: str, *, limit: int, search: dict, force_exact_counts=False, **_kwargs):
                search_calls.append((size, limit, bool(force_exact_counts), search.get("text_query")))
                returned = min(int(limit), 300)
                return [candidate(idx) for idx in range(1, returned + 1)], 350, 300

            async def fake_diverse(candidates, count):
                return candidates[:count]

            async def fake_resolve(q: str, **_kwargs):
                return {
                    "active": bool(q),
                    "id_filter": None,
                    "scores": {},
                    "text_query": q,
                    "search_mode": "metadata" if q else "",
                    "ai_unavailable": False,
                    "fallback_reason": "",
                }

            compare_service.filtered_visible_ranked_candidates = fake_filtered
            compare_service.search_visible_ranked_candidates = fake_search
            compare_service.diverse_sample = fake_diverse
            compare_service._resolve_library_constraints = fake_resolve

            filtered = await compare_service.mosaic_next_impl(
                n=4,
                strategy="diverse",
                orientation="portrait",
            )
            searched = await compare_service.mosaic_next_impl(
                n=4,
                strategy="diverse",
                q="sunset",
            )
        finally:
            compare_service.filtered_visible_ranked_candidates = old_filtered
            compare_service.search_visible_ranked_candidates = old_search
            compare_service.diverse_sample = old_diverse
            compare_service._resolve_library_constraints = old_resolve

        self.assertEqual(filtered_limits[0], ("sm", max(compare_service._FILTERED_MOSAIC_WINDOW, 4 * 40)))
        self.assertEqual(filtered_limits[1], ("sm", 500))
        self.assertEqual(filtered["candidate_source"], "filtered_diverse_universe")
        self.assertEqual(search_calls[0], ("sm", max(compare_service._FILTERED_MOSAIC_WINDOW, 4 * 40), True, "sunset"))
        self.assertEqual(search_calls[1], ("sm", 300, True, "sunset"))
        self.assertEqual(searched["candidate_source"], "search_diverse_universe")

    async def test_search_visibility_is_mode_aware(self):
        source = await self._source()
        hidden_best = await self._image(source["id"], "hidden-best.jpg")
        visible_first = await self._image(source["id"], "visible-first.jpg")
        hidden_next = await self._image(source["id"], "hidden-next.jpg")
        visible_second = await self._image(source["id"], "visible-second.jpg")
        await self._cache_entry(visible_first, "sm")
        await self._cache_entry(visible_second, "sm")

        image_ids = [hidden_best, visible_first, hidden_next, visible_second]
        matrix = np.array(
            [
                [0.99, 0.01],
                [0.90, 0.10],
                [0.80, 0.20],
                [0.70, 0.30],
            ],
            dtype=np.float32,
        )

        async def fake_get_matrix(_model_key=None):
            return image_ids, matrix

        def fake_encode_text(_query):
            return np.array([1.0, 0.0], dtype=np.float32)

        elo_propagation.embed_cache.get_matrix = fake_get_matrix
        embedding_worker.encode_text = fake_encode_text

        result = await search_routes.api_search(q="sunset", limit=2)

        self.assertEqual([img["id"] for img in result["images"]], [hidden_best, visible_first])
        self.assertEqual(result["visible_images"], 4)
        self.assertEqual(result["total_images"], 4)
        self.assertEqual(result["pending_thumbnails"], 2)
        self.assertEqual(result["hidden_pending_thumbnails"], 2)
        self.assertFalse(result["images"][0]["preview_ready"])

        with unittest.mock.patch.dict(
            os.environ,
            {"PHOTOARCHIVE_MODE": "satellite", "PHOTOARCHIVE_HUB_URL": "http://stub-hub"},
        ):
            library_service._rankings_response_cache.clear()
            satellite_result = await search_routes.api_search(q="sunset", limit=2)

        self.assertEqual([img["id"] for img in satellite_result["images"]], [hidden_best, visible_first])
        self.assertEqual(satellite_result["visible_images"], 4)
        self.assertEqual(satellite_result["total_images"], 4)
        self.assertEqual(satellite_result["pending_thumbnails"], 2)
        self.assertEqual(satellite_result["hidden_pending_thumbnails"], 2)
        satellite_cards = {card["id"]: card for card in satellite_result["images"]}
        self.assertNotIn("thumb_url", satellite_cards[hidden_best])
        self.assertIn("thumb_url", satellite_cards[visible_first])

    async def test_rankings_search_uses_metadata_fallback_when_ai_is_cold(self):
        source = await self._source()
        visible_match = await self._image(source["id"], "sunset-visible.jpg")
        hidden_match = await self._image(source["id"], "sunset-hidden.jpg")
        visible_miss = await self._image(source["id"], "portrait-visible.jpg")
        await self._cache_entry(visible_match, "sm")
        await self._cache_entry(visible_miss, "sm")

        embedding_worker.encode_text = lambda _query: None

        result = await library_routes.api_rankings(q="sunset", sort="similarity", limit=10)

        self.assertEqual([img["id"] for img in result["images"]], [visible_match, hidden_match])
        self.assertEqual(result["search_mode"], "metadata")
        self.assertTrue(result["ai_unavailable"])
        self.assertEqual(result["visible_images"], 2)
        self.assertEqual(result["total_images"], 2)
        self.assertEqual(result["pending_thumbnails"], 1)
        self.assertEqual(result["hidden_pending_thumbnails"], 1)
        hidden_card = next(img for img in result["images"] if img["id"] == hidden_match)
        self.assertFalse(hidden_card["preview_ready"])

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
        active_source_ids = await db.get_active_source_id_set()
        self.assertEqual(
            await metadata_search.metadata_search_image_ids(
                db.DB_PATH,
                "sunset",
                active_source_ids=active_source_ids,
            ),
            await db.metadata_search_image_ids("sunset"),
        )

    async def test_text_search_resolution_keeps_exact_filter_for_non_empty_fts_ids(self):
        source = await self._source()
        match = await self._image(source["id"], "sunset-visible.jpg")
        await self._image(source["id"], "portrait-visible.jpg")

        embedding_worker.encode_text = lambda _query, _config=None: None
        query_constraints._text_search_resolution_cache.clear()

        result = await app_module.app.state.photoarchive_shell.runtime_services.resolve_text_search("sunset")

        self.assertEqual(result["search_mode"], "metadata")
        self.assertEqual(result["id_filter"], {match})
        self.assertEqual(result["text_query"], "sunset")

    async def test_extension_metadata_search_uses_exact_extension_path(self):
        source = await self._source()
        jpg = await self._image(source["id"], "sunset-visible.jpg")
        raw = await self._image(source["id"], "portrait.raw")
        conn = await db.get_db()
        try:
            await conn.executemany(
                "UPDATE images SET file_ext = ? WHERE id = ?",
                [(".jpg", jpg), (".raw", raw)],
            )
            await conn.commit()
        finally:
            await conn.close()
        for image_id in (jpg, raw):
            await self._cache_entry(image_id, "sm")

        query_constraints._text_search_resolution_cache.clear()
        calls = {"encode": 0}

        def fake_encode_text(_query):
            calls["encode"] += 1
            return None

        embedding_worker.encode_text = fake_encode_text

        result = await library_routes.api_rankings(q="jpg", limit=10)

        self.assertEqual([image["id"] for image in result["images"]], [jpg])
        self.assertEqual(result["total_images"], 1)
        self.assertEqual(calls["encode"], 0)

    async def test_empty_first_page_metadata_search_skips_count_queries(self):
        source = await self._source()
        image_id = await self._image(source["id"], "sunset-visible.jpg")
        await self._cache_entry(image_id, "sm")

        embedding_worker.encode_text = lambda _query: None
        calls = {"count_rankings": 0, "get_rankings": 0}
        old_count_rankings = db.count_rankings
        old_get_rankings = db.get_rankings

        async def counted_count_rankings(*args, **kwargs):
            calls["count_rankings"] += 1
            return await old_count_rankings(*args, **kwargs)

        async def counted_get_rankings(*args, **kwargs):
            calls["get_rankings"] += 1
            return await old_get_rankings(*args, **kwargs)

        db.count_rankings = counted_count_rankings
        db.get_rankings = counted_get_rankings
        try:
            result = await library_routes.api_rankings(q="no-such-visible-photo", limit=10)
        finally:
            db.count_rankings = old_count_rankings
            db.get_rankings = old_get_rankings

        self.assertEqual(result["images"], [])
        self.assertEqual(result["visible_images"], 0)
        self.assertEqual(result["total_images"], 0)
        self.assertEqual(calls["count_rankings"], 0)
        self.assertEqual(calls["get_rankings"], 0)

    async def test_rankings_search_similarity_defaults_but_other_sorts_keep_pool(self):
        source = await self._source()
        best_match = await self._image(source["id"], "landscape-best.jpg", elo=1200)
        rated_match = await self._image(source["id"], "landscape-rated.jpg", elo=1600)
        miss = await self._image(source["id"], "portrait-miss.jpg", elo=1800)
        for image_id in (best_match, rated_match, miss):
            await self._cache_entry(image_id, "sm")

        self._stub_text_search(
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

    async def test_rankings_search_warms_model_without_blocking_when_worker_is_deferred(self):
        source = await self._source()
        match = await self._image(source["id"], "semantic-match.jpg")
        miss = await self._image(source["id"], "semantic-miss.jpg")
        for image_id in (match, miss):
            await self._cache_entry(image_id, "sm")

        image_ids = [match, miss]
        matrix = np.array([[0.90, 0.10], [0.10, 0.90]], dtype=np.float32)
        calls = {"encode": 0, "start": 0}

        async def fake_get_matrix(_model_key=None):
            return image_ids, matrix

        def fake_encode_text(_query):
            calls["encode"] += 1
            if calls["encode"] == 1:
                return None
            return np.array([1.0, 0.0], dtype=np.float32)

        def fake_start_search_model_load():
            calls["start"] += 1
            return True

        elo_propagation.embed_cache.get_matrix = fake_get_matrix
        embedding_worker.encode_text = fake_encode_text
        embedding_worker.start_search_model_load = fake_start_search_model_load
        query_constraints._text_search_resolution_cache.clear()
        library_service._rankings_response_cache.clear()

        cold = await library_routes.api_rankings(q="dog", sort="similarity", limit=10)
        warm = await library_routes.api_rankings(q="dog", sort="similarity", limit=10)

        self.assertEqual(calls, {"encode": 2, "start": 1})
        self.assertEqual(cold["search_mode"], "metadata")
        self.assertTrue(cold["ai_unavailable"])
        self.assertEqual(cold["fallback_reason"], "model_loading")
        self.assertEqual(warm["search_mode"], "embedding")
        self.assertFalse(warm["ai_unavailable"])
        self.assertEqual([img["id"] for img in warm["images"]], [match])

    async def test_normal_search_uses_active_model_preset(self):
        source = await self._source()
        match = await self._image(source["id"], "fast-role-match.jpg")
        miss = await self._image(source["id"], "fast-role-miss.jpg")
        for image_id in (match, miss):
            await self._cache_entry(image_id, "sm")

        settings.save_settings({"embed_model_preset": "qwen3-vl-embedding-8b"})
        active_config = settings.active_embedding_config()
        calls = []
        image_ids = [match, miss]
        matrix = np.array([[0.90, 0.10], [0.10, 0.90]], dtype=np.float32)

        async def fake_get_matrix(model_key=None):
            calls.append(("matrix", model_key))
            return image_ids, matrix

        def fake_encode_text(_query, config=None):
            calls.append(("encode", config["model_key"]))
            return np.array([1.0, 0.0], dtype=np.float32)

        elo_propagation.embed_cache.get_matrix = fake_get_matrix
        embedding_worker.encode_text = fake_encode_text
        query_constraints._text_search_resolution_cache.clear()

        result = await library_routes.api_rankings(q="semantic dog", sort="similarity", limit=10)

        self.assertEqual(result["search_mode"], "embedding")
        self.assertEqual([img["id"] for img in result["images"]], [match])
        self.assertIn(("encode", active_config["model_key"]), calls)
        self.assertIn(("matrix", active_config["model_key"]), calls)
        self.assertEqual(db.active_embedding_model_key(), active_config["model_key"])

    async def test_mosaic_next_search_filters_candidates_and_counts_visibility(self):
        source = await self._source()
        visible_a = await self._image(source["id"], "landscape-a.jpg")
        hidden_match = await self._image(source["id"], "landscape-hidden.jpg")
        visible_b = await self._image(source["id"], "landscape-b.jpg")
        miss = await self._image(source["id"], "portrait-miss.jpg")
        for image_id in (visible_a, visible_b, miss):
            await self._cache_entry(image_id, "sm")

        self._stub_text_search(
            [visible_a, hidden_match, visible_b, miss],
            [0.95, 0.90, 0.80, 0.10],
        )

        result = await compare_routes.mosaic_next(n=5, strategy="random", q="landscapes")
        ids = {img["id"] for img in result["images"]}

        self.assertEqual(ids, {visible_a, visible_b})
        self.assertEqual(result["visible_images"], 2)
        self.assertEqual(result["total_images"], 3)
        self.assertEqual(result["hidden_pending_thumbnails"], 1)
        self.assertEqual(result["stats"]["filtered_pool_visible"], 2)
        self.assertEqual(result["stats"]["filtered_pool_total"], 3)

    async def test_compare_next_search_only_pairs_matching_candidates(self):
        source = await self._source()
        match_a = await self._image(source["id"], "landscape-a.jpg", elo=1500)
        match_b = await self._image(source["id"], "landscape-b.jpg", elo=1400)
        miss = await self._image(source["id"], "portrait-miss.jpg", elo=1300)
        for image_id in (match_a, match_b, miss):
            await self._cache_entry(image_id, "md")

        self._stub_text_search(
            [match_a, match_b, miss],
            [0.95, 0.80, 0.10],
        )

        result = await compare_routes.compare_next(n=2, mode="swiss", q="landscapes")
        pair_ids = {
            image["id"]
            for pair in result["pairs"]
            for image in (pair["left"], pair["right"])
        }

        self.assertEqual(pair_ids, {match_a, match_b})
        self.assertNotIn(miss, pair_ids)
        self.assertEqual(result["visible_images"], 2)
        self.assertEqual(result["total_images"], 2)

    async def test_search_endpoint_uses_metadata_fallback_when_ai_is_cold(self):
        source = await self._source()
        visible_match = await self._image(source["id"], "sunset-visible.jpg")
        hidden_match = await self._image(source["id"], "sunset-hidden.jpg")
        await self._cache_entry(visible_match, "sm")

        embedding_worker.encode_text = lambda _query: None

        result = await search_routes.api_search(q="sunset", limit=10)

        self.assertEqual([img["id"] for img in result["images"]], [visible_match, hidden_match])
        self.assertIsNone(result["images"][0]["similarity"])
        self.assertEqual(result["search_mode"], "metadata")
        self.assertTrue(result["ai_unavailable"])
        self.assertEqual(result["visible_images"], 2)
        self.assertEqual(result["total_images"], 2)
        self.assertEqual(result["pending_thumbnails"], 1)
        self.assertEqual(result["hidden_pending_thumbnails"], 1)
        hidden_card = next(img for img in result["images"] if img["id"] == hidden_match)
        self.assertFalse(hidden_card["preview_ready"])

    async def test_search_metadata_fallback_reuses_response_cache(self):
        source = await self._source()
        visible_match = await self._image(source["id"], "sunset-visible.jpg")
        await self._cache_entry(visible_match, "sm")
        embedding_worker.encode_text = lambda _query: None

        first = await search_routes.api_search(q="sunset", limit=10)
        self.assertEqual([img["id"] for img in first["images"]], [visible_match])

        old_get_rankings = db.get_rankings
        old_count_rankings = db.count_rankings

        async def fail_get_rankings(*_args, **_kwargs):
            raise AssertionError("cached search should not fetch rankings again")

        async def fail_count_rankings(*_args, **_kwargs):
            raise AssertionError("cached search should not recount rankings")

        db.get_rankings = fail_get_rankings
        db.count_rankings = fail_count_rankings
        try:
            second = await search_routes.api_search(q="sunset", limit=10)
        finally:
            db.get_rankings = old_get_rankings
            db.count_rankings = old_count_rankings

        self.assertEqual(second["images"], first["images"])
        self.assertEqual(second["visible_images"], first["visible_images"])
        self.assertEqual(second["total_images"], first["total_images"])

    async def test_search_deep_parameter_is_ignored_and_does_not_queue_query(self):
        source = await self._source()
        visible_match = await self._image(source["id"], "sunset-visible.jpg")
        await self._cache_entry(visible_match, "sm")
        embedding_worker.encode_text = lambda _query: None
        ai_routes._ai_status_response_cache.update({"data": {"stale": True}, "key": ("stale",), "expires": 999999999})
        settings_status._settings_response_cache.update({"data": {"stale": True}, "expires": 999999999})

        result = await search_routes.api_search(q="sunset portrait", deep=True, limit=10)

        self.assertEqual(result["search_mode"], "metadata")
        self.assertNotIn("deep_requested", result)
        self.assertNotIn("deep_search_cached", result)
        self.assertEqual(ai_routes._ai_status_response_cache["data"], {"stale": True})
        self.assertEqual(settings_status._settings_response_cache["data"], {"stale": True})

    async def test_empty_search_deep_parameter_reports_publicly_disabled(self):
        result = await search_routes.api_search(q="", deep=True, limit=10)

        self.assertEqual(result["images"], [])
        self.assertNotIn("deep_requested", result)
        self.assertNotIn("deep_search_cached", result)

    async def test_deep_search_uses_active_embedding_config_instead_of_fast_config(self):
        import embed_cache as embed_cache_module

        old_get_matrix = embed_cache_module.get_matrix
        matrix_calls = []
        fast_config = {
            "model_key": "fast-model",
            "dimension": 2,
        }
        active_config = {
            "model_key": "active-model",
            "dimension": 2,
        }

        async def fake_get_matrix(model_key=None):
            matrix_calls.append(model_key)
            return [1], np.array([[0.95, 0.0]], dtype=np.float32)

        try:
            embed_cache_module.get_matrix = fake_get_matrix
            query_constraints._text_search_resolution_cache.clear()
            fast = await query_constraints.resolve_text_search(
                "garden",
                deep=False,
                encode_text=lambda _encoder, _query, _config: np.array([1.0, 0.0], dtype=np.float32),
                start_model_load=lambda _worker: False,
                extension_search_terms=set(),
                get_settings=lambda: {"search_similarity_threshold": 0.35},
                active_embedding_config=lambda: active_config,
                fast_search_embedding_config=lambda: fast_config,
            )
            deep = await query_constraints.resolve_text_search(
                "garden",
                deep=True,
                encode_text=lambda _encoder, _query, _config: np.array([1.0, 0.0], dtype=np.float32),
                start_model_load=lambda _worker: False,
                extension_search_terms=set(),
                get_settings=lambda: {"search_similarity_threshold": 0.35},
                active_embedding_config=lambda: active_config,
                fast_search_embedding_config=lambda: fast_config,
            )
        finally:
            embed_cache_module.get_matrix = old_get_matrix

        self.assertEqual(matrix_calls, ["fast-model", "active-model"])
        self.assertEqual(fast["id_filter"], {1})
        self.assertEqual(deep["id_filter"], {1})

    async def test_stale_busy_responses_report_deep_disabled(self):
        old_rankings_handler = library_routes._rankings_handler
        old_mosaic_handler = compare_routes._mosaic_next_handler
        old_compare_handler = compare_routes._compare_next_handler

        async def locked_handler(**_kwargs):
            raise sqlite3.OperationalError("database is locked")

        library_routes._rankings_handler = locked_handler
        compare_routes._mosaic_next_handler = locked_handler
        compare_routes._compare_next_handler = locked_handler
        try:
            rankings = await library_routes.api_rankings(q="sunset", deep=True)
            mosaic = await compare_routes.mosaic_next(q="sunset", deep=True)
            compare = await compare_routes.compare_next(q="sunset", deep=True)
        finally:
            library_routes._rankings_handler = old_rankings_handler
            compare_routes._mosaic_next_handler = old_mosaic_handler
            compare_routes._compare_next_handler = old_compare_handler

        for response in (rankings, mosaic, compare):
            self.assertTrue(response["status_stale"])
            self.assertNotIn("deep_requested", response)
            self.assertNotIn("deep_search_cached", response)

    async def test_text_search_resolution_caches_fast_embedding_result(self):
        source = await self._source()
        match = await self._image(source["id"], "cached-fast-match.jpg")
        miss = await self._image(source["id"], "cached-fast-miss.jpg")
        for image_id in (match, miss):
            await self._cache_entry(image_id, "sm")

        calls = []
        active_key = settings.active_embedding_config()["model_key"]

        def fake_encode(query, config=None):
            calls.append((query, (config or {}).get("model_key")))
            vec = np.zeros(settings.active_embedding_config()["dimension"], dtype=np.float32)
            vec[0] = 1.0
            return vec

        async def fake_get_matrix(model_key=None):
            self.assertEqual(model_key, active_key)
            matrix = np.zeros((2, settings.active_embedding_config()["dimension"]), dtype=np.float32)
            matrix[0, 0] = 0.95
            matrix[1, 0] = 0.10
            return [match, miss], matrix

        embedding_worker.encode_text = fake_encode
        elo_propagation.embed_cache.get_matrix = fake_get_matrix
        query_constraints._text_search_resolution_cache.clear()

        first = await library_routes.api_rankings(q="fast cached query", sort="similarity", limit=10)
        query_constraints._text_search_resolution_cache.clear()
        library_service._rankings_response_cache.clear()
        second = await library_routes.api_rankings(q="fast cached query", sort="similarity", limit=10)

        self.assertEqual(len(calls), 1)
        self.assertEqual(first["search_mode"], "embedding")
        self.assertEqual(second["search_mode"], "embedding")
        self.assertEqual([img["id"] for img in second["images"]], [match])

        conn = await db.get_db()
        try:
            cursor = await conn.execute(
                "SELECT query FROM search_query_embeddings WHERE query_key = ?",
                ("fast cached query",),
            )
            row = await cursor.fetchone()
        finally:
            await conn.close()
        self.assertEqual(row["query"], "fast cached query")

    async def test_embedding_search_reuses_rankings_response_cache(self):
        source = await self._source()
        match = await self._image(source["id"], "cached-response-match.jpg")
        miss = await self._image(source["id"], "cached-response-miss.jpg")
        for image_id in (match, miss):
            await self._cache_entry(image_id, "sm")

        def fake_encode(_query, _config=None):
            return np.array([1.0, 0.0], dtype=np.float32)

        active_key = settings.active_embedding_config()["model_key"]

        async def fake_get_matrix(model_key=None):
            self.assertEqual(model_key, active_key)
            return [match, miss], np.array(
                [
                    [0.95, 0.05],
                    [0.10, 0.90],
                ],
                dtype=np.float32,
            )

        embedding_worker.encode_text = fake_encode
        elo_propagation.embed_cache.get_matrix = fake_get_matrix
        library_service._rankings_response_cache.clear()
        query_constraints._text_search_resolution_cache.clear()

        first = await library_routes.api_rankings(q="cached response query", sort="similarity", limit=10)

        old_get_rankings = db.get_rankings
        old_count_rankings = db.count_rankings

        async def fail_get_rankings(*_args, **_kwargs):
            raise AssertionError("cached embedding search should not fetch rankings again")

        async def fail_count_rankings(*_args, **_kwargs):
            raise AssertionError("cached embedding search should not recount rankings")

        db.get_rankings = fail_get_rankings
        db.count_rankings = fail_count_rankings
        try:
            second = await library_routes.api_rankings(q="cached response query", sort="similarity", limit=10)
        finally:
            db.get_rankings = old_get_rankings
            db.count_rankings = old_count_rankings

        self.assertEqual(second["images"], first["images"])
        self.assertEqual(second["search_mode"], "embedding")

    async def test_cached_text_search_resolution_does_not_queue_deep_query(self):
        source = await self._source()
        visible_match = await self._image(source["id"], "repeat-query-visible.jpg")
        await self._cache_entry(visible_match, "sm")
        embedding_worker.encode_text = lambda _query, _config=None: None
        query_constraints._text_search_resolution_cache.clear()

        await library_routes.api_rankings(q="repeat cache query", limit=1, offset=0)
        await library_routes.api_rankings(q="repeat cache query", limit=1, offset=1)

        conn = await db.get_db()
        try:
            cursor = await conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
                ("deep_search_queries",),
            )
            row = await cursor.fetchone()
        finally:
            await conn.close()

        self.assertIsNone(row)

    async def test_search_endpoint_does_not_queue_deep_query_response(self):
        source = await self._source()
        visible_match = await self._image(source["id"], "repeat-api-search-visible.jpg")
        await self._cache_entry(visible_match, "sm")
        embedding_worker.encode_text = lambda _query, _config=None: None
        library_service._rankings_response_cache.clear()

        await search_routes.api_search(q="repeat api query", limit=10)
        await search_routes.api_search(q="repeat api query", limit=10)

        conn = await db.get_db()
        try:
            cursor = await conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'deep_search_queries'"
            )
            row = await cursor.fetchone()
        finally:
            await conn.close()

        self.assertIsNone(row)

    async def test_search_endpoint_metadata_fallback_keeps_exact_filter_for_non_empty_fts_ids(self):
        source = await self._source()
        visible_match = await self._image(source["id"], "sunset-api-visible.jpg")
        await self._image(source["id"], "portrait-api-visible.jpg")
        await self._cache_entry(visible_match, "sm")
        embedding_worker.encode_text = lambda _query, _config=None: None
        library_service._rankings_response_cache.clear()

        seen = []
        old_get_rankings = db.get_rankings
        old_count_rankings = db.count_rankings

        async def checked_get_rankings(*args, **kwargs):
            seen.append(("get", kwargs.get("id_filter"), kwargs.get("text_query")))
            return await old_get_rankings(*args, **kwargs)

        async def checked_count_rankings(*args, **kwargs):
            seen.append(("count", kwargs.get("id_filter"), kwargs.get("text_query")))
            return await old_count_rankings(*args, **kwargs)

        db.get_rankings = checked_get_rankings
        db.count_rankings = checked_count_rankings
        try:
            result = await search_routes.api_search(q="sunset-api", limit=10)
        finally:
            db.get_rankings = old_get_rankings
            db.count_rankings = old_count_rankings

        self.assertEqual([img["id"] for img in result["images"]], [visible_match])
        self.assertTrue(seen)
        for _kind, id_filter, text_query in seen:
            self.assertEqual(id_filter, {visible_match})
            self.assertEqual(text_query, "sunset-api")

    async def test_search_query_is_not_saved_to_legacy_deep_query_queue(self):
        source = await self._source()
        visible_match = await self._image(source["id"], "long-query-visible.jpg")
        await self._cache_entry(visible_match, "sm")
        embedding_worker.encode_text = lambda _query, _config=None: None
        long_query = " ".join(["verylongquery"] * 40)

        result = await search_routes.api_search(q=long_query, limit=10)

        conn = await db.get_db()
        try:
            cursor = await conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'deep_search_queries'"
            )
            row = await cursor.fetchone()
        finally:
            await conn.close()

        self.assertIsNone(row)
        self.assertTrue(result["query"])

    async def test_embedding_batch_listener_invalidates_vector_derived_caches(self):
        search_service._duplicates_cache.update({"key": ("stale",), "data": {"pairs": []}})
        elo_propagation._prediction_cache_key = ("stale",)
        elo_propagation._prediction_cache_counts = {1: 10}

        cache_events.embedding_batch_stored("model", [1])

        self.assertIsNone(search_service._duplicates_cache["key"])
        self.assertIsNone(search_service._duplicates_cache["data"])
        self.assertIsNone(elo_propagation._prediction_cache_key)
        self.assertIsNone(elo_propagation._prediction_cache_counts)

    async def test_embedding_model_change_invalidates_vector_derived_caches(self):
        search_service._duplicates_cache.update({"key": ("stale",), "data": {"pairs": []}})
        elo_propagation._prediction_cache_key = ("stale",)
        elo_propagation._prediction_cache_counts = {1: 10}

        await settings_routes.api_save_settings(JsonRequest({
            "settings_version": settings.SETTINGS_VERSION,
            "embed_model_preset": "qwen3-vl-embedding-2b",
        }))

        self.assertIsNone(search_service._duplicates_cache["key"])
        self.assertIsNone(search_service._duplicates_cache["data"])
        self.assertIsNone(elo_propagation._prediction_cache_key)
        self.assertIsNone(elo_propagation._prediction_cache_counts)

    async def test_search_metadata_prefetch_runs_after_response(self):
        source = await self._source()
        match = await self._image(source["id"], "sunset-visible.jpg")
        await self._cache_entry(match, "sm")
        embedding_worker.encode_text = lambda _query: None
        started = asyncio.Event()
        release = asyncio.Event()

        async def blocking_prefetch(*_args, **_kwargs):
            started.set()
            await release.wait()
            return 0

        thumbnails.prefetch_images = blocking_prefetch
        try:
            result = await asyncio.wait_for(search_routes.api_search(q="sunset", limit=10), timeout=0.5)
            self.assertEqual([img["id"] for img in result["images"]], [match])
            await asyncio.wait_for(started.wait(), timeout=0.5)
        finally:
            release.set()
            await asyncio.sleep(0)

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
            {"PHOTOARCHIVE_MODE": "satellite", "PHOTOARCHIVE_HUB_URL": "http://stub-hub"},
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

        old_get_active_images_by_ids = db.get_active_images_by_ids
        elo_propagation.embed_cache.get_matrix = fake_get_matrix
        search_service._duplicates_cache.update({"key": None, "data": None})
        first_result = await search_routes.api_duplicates(threshold=0.95, limit=10)

        async def fail_get_active_images_by_ids(_ids):
            raise AssertionError("cached duplicate result should avoid refetching images")

        db.get_active_images_by_ids = fail_get_active_images_by_ids
        try:
            second_result = await search_routes.api_duplicates(threshold=0.95, limit=10)
        finally:
            db.get_active_images_by_ids = old_get_active_images_by_ids
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

    async def test_api_settings_does_not_return_deep_search_terms(self):
        response = await settings_routes.api_save_settings(JsonRequest({
            "deep_search_terms": "crane\ncat in cafe window\nCRANE\n",
        }))

        self.assertNotIn("deep_search_terms", response["settings"])

        refreshed = await settings_routes.api_settings()

        self.assertNotIn("deep_search_terms", refreshed["settings"])

    async def test_deep_search_schedule_fields_are_not_in_defaults(self):
        normalized = settings.normalize_settings({})

        self.assertNotIn("deep_search_schedule_enabled", normalized)
        self.assertNotIn("deep_search_schedule_days", normalized)
        self.assertNotIn("deep_search_schedule_start", normalized)
        self.assertNotIn("deep_search_schedule_end", normalized)
        self.assertNotIn("deep_search_schedule_timezone", normalized)

    async def test_search_runtime_settings_invalidate_cached_search_results(self):
        library_service._rankings_response_cache[("stale",)] = {
            "data": {"images": []},
            "expires": time.monotonic() + 100,
        }
        query_constraints._text_search_resolution_cache[("stale", False)] = {
            "data": {"search_mode": "embedding"},
            "expires": time.monotonic() + 100,
        }

        await settings_routes.api_save_settings(JsonRequest({"search_similarity_threshold": 0.55}))

        self.assertFalse(library_service._rankings_response_cache)
        self.assertFalse(query_constraints._text_search_resolution_cache)
