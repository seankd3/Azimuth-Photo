from test_support import *  # noqa: F401,F403


class CompareTests(BackendTestCase):
    async def test_compare_payload_validation_rejects_invalid_and_inactive_images(self):
        source = await self._source()
        a = await self._image(source["id"], "a.jpg")
        b = await self._image(source["id"], "b.jpg")
        offline_source = await self._source("offline", online=False)
        offline = await self._image(offline_source["id"], "offline.jpg")

        response = await compare_routes.submit_comparison(BadJsonRequest())
        self.assertEqual(response.status_code, 400)

        response = await compare_routes.mosaic_pick(JsonRequest([]))
        self.assertEqual(response.status_code, 400)

        response = await compare_routes.submit_comparison(JsonRequest({"winner_id": a, "loser_id": a}))
        self.assertEqual(response.status_code, 400)

        response = await compare_routes.submit_comparison(JsonRequest({"winner_id": a, "loser_id": offline}))
        self.assertTrue(response["ok"])

        response = await compare_routes.mosaic_pick(JsonRequest({"winner_id": a, "loser_ids": [b, b]}))
        self.assertEqual(response.status_code, 400)

        response = await compare_routes.mosaic_pick(JsonRequest({"winner_id": a, "loser_ids": [a]}))
        self.assertEqual(response.status_code, 400)

        response = await compare_routes.mosaic_pick(JsonRequest({"winner_id": a, "loser_ids": [999999]}))
        self.assertEqual(response.status_code, 400)

    async def test_mosaic_pick_undo_reverts_whole_action(self):
        source = await self._source()
        winner = await self._image(source["id"], "winner.jpg")
        losers = [
            await self._image(source["id"], "loser1.jpg"),
            await self._image(source["id"], "loser2.jpg"),
            await self._image(source["id"], "loser3.jpg"),
        ]

        result = await compare_routes.mosaic_pick(JsonRequest({"winner_id": winner, "loser_ids": losers}))
        self.assertTrue(result["ok"])
        self.assertEqual(result["pairs_recorded"], 3)
        self.assertTrue(result["action_id"])

        conn = await db.get_db()
        try:
            cursor = await conn.execute("SELECT COUNT(*) AS c, COUNT(DISTINCT action_id) AS actions FROM comparisons")
            counts = await cursor.fetchone()
        finally:
            await conn.close()
        self.assertEqual(counts["c"], 3)
        self.assertEqual(counts["actions"], 1)

        undo = await compare_routes.compare_undo()
        self.assertTrue(undo["ok"])
        self.assertEqual(undo["comparisons_undone"], 3)

        conn = await db.get_db()
        try:
            cursor = await conn.execute("SELECT COUNT(*) AS c FROM comparisons")
            remaining = await cursor.fetchone()
        finally:
            await conn.close()
        self.assertEqual(remaining["c"], 0)

        for image_id in [winner] + losers:
            row = await self._image_row(image_id)
            self.assertAlmostEqual(row["elo"], 1200.0)
            self.assertEqual(row["comparisons"], 0)

    async def test_propagation_updates_separate_counter(self):
        source = await self._source()
        winner = await self._image(source["id"], "winner.jpg")
        loser = await self._image(source["id"], "loser.jpg")
        neighbor = await self._image(source["id"], "neighbor.jpg")

        image_ids = [winner, loser, neighbor]
        matrix = np.array(
            [
                [1.0, 0.0],
                [-1.0, 0.0],
                [0.995, 0.1],
            ],
            dtype=np.float32,
        )
        matrix /= np.linalg.norm(matrix, axis=1, keepdims=True)

        async def fake_get_matrix(_model_key=None):
            return image_ids, matrix

        def fake_get_index(_model_key=None):
            return {image_id: idx for idx, image_id in enumerate(image_ids)}

        elo_propagation.embed_cache.get_matrix = fake_get_matrix
        elo_propagation.embed_cache.get_index = fake_get_index

        await elo_propagation.propagate_comparison(winner, loser, k=20.0)

        row = await self._image_row(neighbor)
        self.assertEqual(row["comparisons"], 0)
        self.assertEqual(row["propagated_updates"], 1)
        self.assertGreater(row["elo"], 1200.0)

    async def test_compare_propagation_uses_active_embedding_surface(self):
        source = await self._source()
        winner = await self._image(source["id"], "winner.jpg")
        loser = await self._image(source["id"], "loser.jpg")
        active_neighbor = await self._image(source["id"], "active-neighbor.jpg")
        off_axis_neighbor = await self._image(source["id"], "off-axis-neighbor.jpg")

        image_ids = [winner, loser, active_neighbor, off_axis_neighbor]
        matrix = np.array(
            [
                [1.0, 0.0],
                [-1.0, 0.0],
                [0.995, 0.1],
                [0.0, 1.0],
            ],
            dtype=np.float32,
        )
        matrix /= np.linalg.norm(matrix, axis=1, keepdims=True)
        active_key = settings.active_embedding_config()["model_key"]
        calls = []

        async def fake_get_matrix(model_key=None):
            calls.append(model_key)
            return image_ids, matrix

        def fake_get_index(_model_key=None):
            return {image_id: idx for idx, image_id in enumerate(image_ids)}

        elo_propagation.embed_cache.get_matrix = fake_get_matrix
        elo_propagation.embed_cache.get_index = fake_get_index

        await elo_propagation.propagate_comparison(winner, loser, k=20.0)

        active_row = await self._image_row(active_neighbor)
        off_axis_row = await self._image_row(off_axis_neighbor)
        self.assertEqual(calls[0], active_key)
        self.assertGreater(active_row["elo"], 1200.0)
        self.assertAlmostEqual(off_axis_row["elo"], 1200.0)

    async def test_mosaic_diverse_uses_active_embedding_surface(self):
        image_ids = [1, 2, 3, 4]
        candidates = [
            {"id": image_id, "comparisons": 0}
            for image_id in image_ids
        ]
        matrix = np.array(
            [
                [1.0, 0.0],
                [0.0, 1.0],
                [-1.0, 0.0],
                [0.0, -1.0],
            ],
            dtype=np.float32,
        )
        active_key = settings.active_embedding_config()["model_key"]
        calls = []

        async def fake_get_matrix(model_key=None):
            calls.append(model_key)
            return image_ids, matrix

        def fake_get_index(_model_key=None):
            return {image_id: idx for idx, image_id in enumerate(image_ids)}

        def fail_get_warm_matrix():
            raise AssertionError("diverse mosaic should use the active keyed matrix")

        old_get_warm_matrix = elo_propagation.embed_cache.get_warm_matrix
        elo_propagation.embed_cache.get_matrix = fake_get_matrix
        elo_propagation.embed_cache.get_index = fake_get_index
        elo_propagation.embed_cache.get_warm_matrix = fail_get_warm_matrix
        try:
            sample = await compare_service.diverse_sample(candidates, 2)
        finally:
            elo_propagation.embed_cache.get_warm_matrix = old_get_warm_matrix

        self.assertEqual(calls[0], active_key)
        self.assertEqual(len(sample), 2)

    async def test_undo_reverts_action_scoped_propagation_updates(self):
        source = await self._source()
        winner = await self._image(source["id"], "winner.jpg")
        loser = await self._image(source["id"], "loser.jpg")
        neighbor = await self._image(source["id"], "neighbor.jpg")
        action_id = "undo-propagation-test"

        image_ids = [winner, loser, neighbor]
        matrix = np.array(
            [
                [1.0, 0.0],
                [-1.0, 0.0],
                [0.995, 0.1],
            ],
            dtype=np.float32,
        )
        matrix /= np.linalg.norm(matrix, axis=1, keepdims=True)

        async def fake_get_matrix(_model_key=None):
            return image_ids, matrix

        def fake_get_index(_model_key=None):
            return {image_id: idx for idx, image_id in enumerate(image_ids)}

        elo_propagation.embed_cache.get_matrix = fake_get_matrix
        elo_propagation.embed_cache.get_index = fake_get_index

        await db.record_comparison(
            winner,
            loser,
            "swiss",
            1200.0,
            1200.0,
            1210.0,
            1190.0,
            action_id=action_id,
        )
        await elo_propagation.propagate_comparison(winner, loser, k=20.0, action_id=action_id)

        propagated = await self._image_row(neighbor)
        self.assertEqual(propagated["propagated_updates"], 1)
        self.assertGreater(propagated["elo"], 1200.0)

        undo = await compare_routes.compare_undo()
        self.assertTrue(undo["ok"])
        self.assertEqual(undo["comparisons_undone"], 1)
        self.assertEqual(undo["propagations_undone"], 1)

        restored_neighbor = await self._image_row(neighbor)
        self.assertAlmostEqual(restored_neighbor["elo"], 1200.0)
        self.assertEqual(restored_neighbor["propagated_updates"], 0)

        restored_winner = await self._image_row(winner)
        restored_loser = await self._image_row(loser)
        self.assertAlmostEqual(restored_winner["elo"], 1200.0)
        self.assertAlmostEqual(restored_loser["elo"], 1200.0)
        self.assertEqual(restored_winner["comparisons"], 0)
        self.assertEqual(restored_loser["comparisons"], 0)

        conn = await db.get_db()
        try:
            cursor = await conn.execute("SELECT COUNT(*) AS c FROM propagation_updates")
            remaining = await cursor.fetchone()
        finally:
            await conn.close()
        self.assertEqual(remaining["c"], 0)

    async def test_pairing_cache_patch_keeps_immediate_candidate_cache_hot(self):
        compare_service._pairing_cache.update({
            "valid": True,
            "data": [
                {"id": 1, "elo": 1200.0, "comparisons": 0},
                {"id": 2, "elo": 1300.0, "comparisons": 2},
            ],
        })
        expires = time.monotonic() + 1.0
        compare_service._visible_pairing_candidates_cache["test:md:2:elo"] = {
            "data": [
                {"id": 1, "elo": 1200.0, "comparisons": 0},
                {"id": 2, "elo": 1300.0, "comparisons": 2},
            ],
            "id_set": {1, 2},
            "expires": expires,
        }
        compare_service._visible_pairing_candidates_cache["test:sm:2:cache"] = {
            "data": [
                {"id": 3, "elo": 1100.0, "comparisons": 0},
            ],
            "id_set": {3},
            "expires": expires,
        }

        compare_service.patch_pairing_cache([(1, 1400.0, 1)])

        self.assertTrue(compare_service._pairing_cache["valid"])
        self.assertEqual(compare_service._pairing_cache["data"][0]["elo"], 1400.0)
        cached = compare_service._visible_pairing_candidates_cache["test:md:2:elo"]
        self.assertEqual([row["id"] for row in cached["data"]], [1, 2])
        self.assertEqual(cached["data"][0]["comparisons"], 1)
        self.assertEqual(cached["id_set"], {1, 2})
        self.assertGreater(cached["expires"], expires)
        self.assertEqual(
            compare_service._visible_pairing_candidates_cache["test:sm:2:cache"]["expires"],
            expires,
        )

    async def test_orientation_visible_pairing_pool_counts(self):
        self.assertGreaterEqual(db.VISIBLE_PAIRING_POOL_COUNTS_TTL_SECONDS, 30.0)
        self.assertIs(
            db._visible_pairing_pool_counts_cache,
            ratings._visible_pairing_pool_counts_cache,
        )
        source = await self._source()
        visible_landscape = await self._image(source["id"], "visible-landscape.jpg")
        hidden_landscape = await self._image(source["id"], "hidden-landscape.jpg")
        portrait = await self._image(source["id"], "portrait.jpg")
        cache_root = thumbnails.SSD_CACHE_DIR
        conn = await db.get_db()
        try:
            await conn.executemany(
                "UPDATE images SET orientation = ? WHERE id = ?",
                [
                    ("landscape", visible_landscape),
                    ("landscape", hidden_landscape),
                    ("portrait", portrait),
                ],
            )
            await conn.commit()
        finally:
            await conn.close()
        db.invalidate_stats_cache()
        await self._cache_entry(visible_landscape, "md")
        await self._cache_entry(portrait, "md")

        counts = await db.get_visible_orientation_pairing_pool_counts(
            "md",
            cache_root,
            "landscape",
        )
        repository_counts = await ratings.visible_orientation_pairing_pool_counts(
            db.DB_PATH,
            catalog_counts=await db.get_catalog_image_counts(),
            size="md",
            cache_root=cache_root,
            orientation="landscape",
        )
        default_repository_counts = await ratings.visible_pairing_pool_counts(
            db.DB_PATH,
            catalog_counts=await db.get_catalog_image_counts(),
            size="md",
            cache_root=cache_root,
        )

        self.assertEqual(counts["active_images"], 2)
        self.assertEqual(counts["visible_images"], 1)
        self.assertEqual(counts, repository_counts)
        self.assertEqual(
            await db.get_visible_pairing_pool_counts("md", cache_root),
            default_repository_counts,
        )
        self.assertIn(
            (cache_root, "md", "orientation", "landscape"),
            ratings._visible_pairing_pool_counts_cache,
        )
        self.assertIn((cache_root, "md"), ratings._visible_pairing_pool_counts_cache)

        await self._cache_entry(hidden_landscape, "md")
        db.invalidate_cached_image_ids_cache(cache_root, "md")
        self.assertNotIn(
            (cache_root, "md", "orientation", "landscape"),
            ratings._visible_pairing_pool_counts_cache,
        )
        self.assertNotIn((cache_root, "md"), ratings._visible_pairing_pool_counts_cache)

        refreshed = await db.get_visible_orientation_pairing_pool_counts(
            "md",
            cache_root,
            "landscape",
        )

        self.assertEqual(refreshed["visible_images"], 2)

    async def test_visible_pairing_least_compared_uses_ordered_index(self):
        source = await self._source()
        visible_fresh = await self._image(source["id"], "fresh.jpg", elo=1200, comparisons=0)
        visible_rated = await self._image(source["id"], "rated.jpg", elo=1700, comparisons=3)
        hidden_fresh = await self._image(source["id"], "hidden.jpg", elo=1800, comparisons=0)
        await self._cache_entry(visible_fresh, "sm")
        await self._cache_entry(visible_rated, "sm")

        rows = await db.get_visible_images_for_pairing(
            "sm",
            thumbnails.SSD_CACHE_DIR,
            include_card_metadata=False,
            limit=10,
            order="least_compared",
        )

        self.assertEqual([row["id"] for row in rows], [visible_fresh, visible_rated])
        self.assertNotIn(hidden_fresh, [row["id"] for row in rows])

        raw = sqlite3.connect(db.DB_PATH)
        try:
            plan_rows = raw.execute(
                "EXPLAIN QUERY PLAN "
                "SELECT i.id FROM images i INDEXED BY idx_images_visible_comparisons_elo "
                "JOIN catalog_sources s ON s.id = i.source_id "
                "WHERE i.status IN ('kept', 'maybe') "
                "AND s.included = 1 AND i.missing_at IS NULL "
                "AND EXISTS ("
                "  SELECT 1 FROM cache_entries c "
                "  WHERE c.cache_root = ? AND c.size = ? AND c.image_id = i.id"
                ") "
                "ORDER BY i.comparisons ASC, i.elo DESC LIMIT 10",
                (thumbnails.SSD_CACHE_DIR, "sm"),
            ).fetchall()
        finally:
            raw.close()
        plan = " ".join(row[3] for row in plan_rows)
        self.assertIn("idx_images_visible_comparisons_elo", plan)
        self.assertNotIn("TEMP B-TREE", plan)

    async def test_pairing_row_repositories_match_facades(self):
        source = await self._source()
        offline_source = await self._source("pairing-offline", online=False)
        excluded_source = await self._source("pairing-excluded")
        active = await self._image(source["id"], "pair-active.jpg", elo=1500)
        uncached = await self._image(source["id"], "pair-uncached.jpg", elo=1400)
        offline = await self._image(offline_source["id"], "pair-offline.jpg", elo=1300)
        excluded = await self._image(excluded_source["id"], "pair-excluded.jpg", elo=1200)
        for image_id in (active, offline, excluded):
            await self._cache_entry(image_id, "sm")
        conn = await db.get_db()
        try:
            await conn.execute("UPDATE catalog_sources SET included = 0 WHERE id = ?", (excluded_source["id"],))
            await db._update_source_counts(conn, excluded_source["id"])
            await conn.commit()
        finally:
            await conn.close()
        db.invalidate_stats_cache()

        counts = await db.get_catalog_image_counts()
        active_repository_rows = await ratings.active_images_for_pairing(
            db.DB_PATH,
            catalog_counts=counts,
        )
        active_facade_rows = await db.get_active_images_for_pairing()
        self.assertEqual(
            [dict(row) for row in active_facade_rows],
            [dict(row) for row in active_repository_rows],
        )
        self.assertEqual({row["id"] for row in active_facade_rows}, {active, uncached, offline})

        visible_repository_rows = await ratings.visible_images_for_pairing(
            db.DB_PATH,
            "sm",
            thumbnails.SSD_CACHE_DIR,
            include_card_metadata=False,
            order="cache",
        )
        visible_facade_rows = await db.get_visible_images_for_pairing(
            "sm",
            thumbnails.SSD_CACHE_DIR,
            include_card_metadata=False,
            order="cache",
        )
        self.assertEqual(
            [dict(row) for row in visible_facade_rows],
            [dict(row) for row in visible_repository_rows],
        )
        self.assertEqual([row["id"] for row in visible_facade_rows], [active, offline])

    async def test_rating_updates_preserve_unaffected_ranking_count_cache(self):
        source = await self._source()
        first = await self._image(source["id"], "first.jpg")
        second = await self._image(source["id"], "second.jpg")

        self.assertEqual(await db.count_rankings(), 2)
        self.assertEqual(await db.count_rankings(compared="compared"), 0)
        unfiltered_key = db._ranking_count_cache_key()
        compared_key = db._ranking_count_cache_key(compared="compared")
        self.assertIn(unfiltered_key, db._ranking_count_cache)
        self.assertIn(compared_key, db._ranking_count_cache)

        result = await db.record_active_comparison(first, second, "swiss", action_id="cache-test")
        self.assertIsNotNone(result)

        self.assertIn(unfiltered_key, db._ranking_count_cache)
        self.assertNotIn(compared_key, db._ranking_count_cache)
        self.assertEqual(await db.count_rankings(), 2)
        self.assertEqual(await db.count_rankings(compared="compared"), 2)

    async def test_past_matchups_cache_reuses_until_rating_write(self):
        source = await self._source()
        first = await self._image(source["id"], "first.jpg")
        second = await self._image(source["id"], "second.jpg")

        result = await db.record_active_comparison(first, second, "swiss", action_id="matchup-cache-test")
        self.assertIsNotNone(result)
        first_matchups = await db.get_past_matchups()
        self.assertEqual(first_matchups, {(min(first, second), max(first, second))})
        self.assertIsNotNone(db._past_matchups_cache["data"])

        second_matchups = await db.get_past_matchups()
        self.assertEqual(second_matchups, first_matchups)
        third = await self._image(source["id"], "third.jpg")
        result = await db.record_active_comparison(first, third, "swiss", action_id="matchup-cache-test-2")
        self.assertIsNotNone(result)
        self.assertIsNone(db._past_matchups_cache["data"])

    async def test_direct_rating_writes_patch_warm_stats_caches(self):
        source = await self._source()
        first = await self._image(source["id"], "first.jpg")
        second = await self._image(source["id"], "second.jpg")
        third = await self._image(source["id"], "third.jpg")

        initial_stats = await db.get_stats()
        initial_ai_counts = await db.get_ai_status_counts()
        self.assertIs(db._ai_status_counts_cache, stats_repository._ai_status_counts_cache)
        self.assertEqual(initial_stats["total_comparisons"], 0)
        self.assertEqual(initial_ai_counts["ranking_signal_count"], 0)

        result = await db.record_active_comparison(first, second, "swiss", action_id="stats-cache-test")
        self.assertIsNotNone(result)
        self.assertIsNotNone(db._stats_cache["data"])
        self.assertEqual(stats_repository._ai_status_counts_cache["data"]["ranking_signal_count"], 1)

        compared_stats = await db.get_stats()
        compared_ai_counts = await db.get_ai_status_counts()
        self.assertEqual(compared_stats["total_comparisons"], 1)
        self.assertEqual(compared_stats["direct_comparison_rows"], 1)
        self.assertEqual(compared_stats["rated_images"], 2)
        self.assertEqual(compared_ai_counts["ranking_signal_count"], 1)
        self.assertEqual(compared_ai_counts["rated_images"], 2)

        mosaic = await db.record_active_mosaic_pick(first, [second, third], "stats-cache-mosaic")
        self.assertTrue(mosaic["ok"])

        mosaic_stats = await db.get_stats()
        mosaic_ai_counts = await db.get_ai_status_counts()
        self.assertEqual(mosaic_stats["total_comparisons"], 3)
        self.assertEqual(mosaic_stats["direct_comparison_rows"], 3)
        self.assertEqual(mosaic_stats["rated_images"], 3)
        self.assertEqual(mosaic_ai_counts["ranking_signal_count"], 3)
        self.assertEqual(mosaic_ai_counts["rated_images"], 3)

    async def test_rating_updates_preserve_unaffected_facet_caches(self):
        source = await self._source()
        first = await self._image(source["id"], "first.jpg")
        second = await self._image(source["id"], "second.jpg")
        conn = await db.get_db()
        try:
            await conn.executemany(
                "UPDATE images SET date_taken = ?, latitude = ?, longitude = ? WHERE id = ?",
                [
                    ("2024-01-02", 45.0, -93.0, first),
                    ("2024-01-03", 46.0, -94.0, second),
                ],
            )
            await conn.commit()
        finally:
            await conn.close()
        db.invalidate_stats_cache()

        await db.get_date_groups()
        await db.get_date_groups(compared="compared")
        await db.get_map_markers()
        await db.get_map_markers(compared="compared")
        default_key = db._facet_cache_key()
        compared_key = db._facet_cache_key(compared="compared")
        self.assertIn(default_key, db._date_groups_cache)
        self.assertIn(compared_key, db._date_groups_cache)
        self.assertIn(default_key, db._map_markers_cache)
        self.assertIn(compared_key, db._map_markers_cache)

        result = await db.record_active_comparison(first, second, "swiss", action_id="facet-cache-test")
        self.assertIsNotNone(result)

        self.assertIn(default_key, db._date_groups_cache)
        self.assertNotIn(compared_key, db._date_groups_cache)
        self.assertIn(default_key, db._map_markers_cache)
        self.assertNotIn(compared_key, db._map_markers_cache)

    async def test_mosaic_excludes_images_without_sm_but_reports_filtered_total(self):
        source = await self._source()
        first = await self._image(source["id"], "first.jpg", elo=1500)
        hidden = await self._image(source["id"], "hidden.jpg", elo=1400)
        second = await self._image(source["id"], "second.jpg", elo=1300)
        await self._cache_entry(first, "sm")
        await self._cache_entry(second, "sm")

        result = await compare_routes.mosaic_next(n=3, strategy="diverse")
        ids = [img["id"] for img in result["images"]]

        self.assertEqual(ids, [first, second])
        self.assertNotIn(hidden, ids)
        self.assertEqual(result["visible_images"], 2)
        self.assertEqual(result["total_images"], 3)
        self.assertEqual(result["hidden_pending_thumbnails"], 1)
        self.assertEqual(result["stats"]["filtered_pool_visible"], 2)
        self.assertEqual(result["stats"]["filtered_pool_total"], 3)

    async def test_mosaic_next_restricts_pool_by_ids(self):
        source = await self._source()
        first = await self._image(source["id"], "first.jpg", elo=1500)
        second = await self._image(source["id"], "second.jpg", elo=1400)
        third = await self._image(source["id"], "third.jpg", elo=1300)
        for image_id in (first, second, third):
            await self._cache_entry(image_id, "sm")

        result = await compare_routes.mosaic_next(n=2, ids=f"{first},{third}")

        self.assertEqual({image["id"] for image in result["images"]}, {first, third})
        self.assertEqual(result["total_images"], 2)
        self.assertEqual(result["visible_images"], 2)

    async def test_mosaic_next_restricts_pool_by_collection_id(self):
        source = await self._source()
        first = await self._image(source["id"], "first.jpg", elo=1500)
        second = await self._image(source["id"], "second.jpg", elo=1400)
        outside = await self._image(source["id"], "outside.jpg", elo=1300)
        for image_id in (first, second, outside):
            await self._cache_entry(image_id, "sm")
        collection = await db.create_collection(name="Refine", image_ids=[first, second])

        result = await compare_routes.mosaic_next(n=2, collection_id=collection["id"])

        self.assertEqual({image["id"] for image in result["images"]}, {first, second})
        self.assertNotIn(outside, {image["id"] for image in result["images"]})
        self.assertEqual(result["total_images"], 2)

    async def test_mosaic_next_scoped_tiny_pool_returns_not_enough_shape(self):
        source = await self._source()
        first = await self._image(source["id"], "first.jpg", elo=1500)
        second = await self._image(source["id"], "second.jpg", elo=1400)
        for image_id in (first, second):
            await self._cache_entry(image_id, "sm")

        result = await compare_routes.mosaic_next(n=2, ids=str(first))

        self.assertEqual(result["images"], [])
        self.assertEqual(result["total_images"], 1)
        self.assertEqual(result["visible_images"], 1)
        self.assertEqual(result["stats"]["filtered_pool_total"], 1)

    async def test_compare_next_restricts_pool_by_ids(self):
        source = await self._source()
        first = await self._image(source["id"], "first.jpg", elo=1500)
        second = await self._image(source["id"], "second.jpg", elo=1400)
        outside = await self._image(source["id"], "outside.jpg", elo=1300)
        for image_id in (first, second, outside):
            await self._cache_entry(image_id, "md")

        result = await compare_routes.compare_next(n=1, ids=f"{first},{second}")
        pair_ids = {
            result["pairs"][0]["left"]["id"],
            result["pairs"][0]["right"]["id"],
        }

        self.assertEqual(pair_ids, {first, second})
        self.assertNotIn(outside, pair_ids)
        self.assertEqual(result["total_images"], 2)

    async def test_default_interaction_response_cache_reuses_and_invalidates_on_rating(self):
        source = await self._source()
        first = await self._image(source["id"], "first.jpg", elo=1500)
        second = await self._image(source["id"], "second.jpg", elo=1300)
        third = await self._image(source["id"], "third.jpg", elo=1100)
        for image_id in (first, second, third):
            await self._cache_entry(image_id, "sm")
            await self._cache_entry(image_id, "md")

        mosaic_first = await compare_routes.mosaic_next(n=3, strategy="explore")
        self.assertTrue(compare_service._interaction_response_cache)
        old_get_visible = db.get_visible_images_for_pairing

        async def fail_visible_pairing(*_args, **_kwargs):
            raise AssertionError("warm default interaction response should be reused")

        db.get_visible_images_for_pairing = fail_visible_pairing
        try:
            mosaic_second = await compare_routes.mosaic_next(n=3, strategy="explore")
        finally:
            db.get_visible_images_for_pairing = old_get_visible

        self.assertEqual(mosaic_second["images"], mosaic_first["images"])
        mosaic_second["images"][0]["filename"] = "mutated"
        mosaic_second["stats"]["filtered_pool"] = 999
        mosaic_third = await compare_routes.mosaic_next(n=3, strategy="explore")
        self.assertNotEqual(mosaic_third["images"][0]["filename"], "mutated")
        self.assertNotEqual(mosaic_third["stats"]["filtered_pool"], 999)

        await compare_routes.submit_comparison(
            JsonRequest({"winner_id": first, "loser_id": second})
        )
        self.assertFalse(compare_service._interaction_response_cache)

    async def test_mosaic_explore_uses_least_compared_default_pool(self):
        def candidate(image_id: int, comparisons: int) -> dict:
            return {
                "id": image_id,
                "filename": f"image-{image_id}.jpg",
                "filepath": f"/photos/image-{image_id}.jpg",
                "elo": 1200.0 + image_id,
                "comparisons": comparisons,
                "propagated_updates": 0,
                "status": "kept",
                "flag": "unflagged",
                "orientation": "landscape",
                "aspect_ratio": 1.5,
                "date_taken": "",
                "camera_make": "",
                "camera_model": "",
                "lens": "",
                "file_ext": ".jpg",
                "created_at": 1.0,
            }

        calls = []
        old_default = compare_service.default_visible_pairing_candidates
        old_pool = compare_service._get_visible_pairing_pool_counts
        try:
            async def fake_default(size: str, **kwargs):
                calls.append((size, kwargs))
                return [
                    candidate(1, 0),
                    candidate(2, 0),
                    candidate(3, 5),
                    candidate(4, 6),
                ]

            async def fake_pool(_size, _cache_root):
                return {"active_images": 4, "visible_images": 4}

            compare_service.default_visible_pairing_candidates = fake_default
            compare_service._get_visible_pairing_pool_counts = fake_pool
            compare_service._interaction_response_cache.clear()

            result = await compare_service.mosaic_next_impl(n=2, strategy="explore")
        finally:
            compare_service.default_visible_pairing_candidates = old_default
            compare_service._get_visible_pairing_pool_counts = old_pool
            compare_service._interaction_response_cache.clear()

        self.assertEqual(calls[0][0], "sm")
        self.assertEqual(calls[0][1].get("order"), "least_compared")
        self.assertEqual(result["candidate_source"], "default_explore_least_compared")
        self.assertEqual({image["id"] for image in result["images"]}, {1, 2})

    async def test_mosaic_explore_reports_direct_uncompared_pool_stats(self):
        source = await self._source()
        first = await self._image(source["id"], "first.jpg")
        second = await self._image(source["id"], "second.jpg")
        third = await self._image(source["id"], "third.jpg")
        for image_id in (first, second, third):
            await self._cache_entry(image_id, "sm")

        await compare_routes.submit_comparison(
            JsonRequest({"winner_id": first, "loser_id": second})
        )

        result = await compare_routes.mosaic_next(n=2, strategy="explore")
        stats = result["stats"]

        self.assertEqual(stats["pool_metric"], "direct_uncompared")
        self.assertEqual(stats["direct_uncompared_total"], 1)
        self.assertEqual(stats["direct_uncompared_visible"], 1)
        self.assertEqual(stats["direct_uncompared_pool_total"], 3)
        self.assertEqual(stats["direct_uncompared_pool_visible"], 3)

    async def test_lowest_comparison_candidate_pool_accepts_sqlite_rows(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        try:
            conn.execute(
                "CREATE TABLE candidates ("
                "id INTEGER, elo REAL, comparisons INTEGER, propagated_updates INTEGER"
                ")"
            )
            conn.executemany(
                "INSERT INTO candidates VALUES (?, ?, ?, ?)",
                [
                    (1, 1210.0, 4, 0),
                    (2, 1200.0, 0, 1),
                    (3, 1220.0, 0, 0),
                    (4, 1230.0, 2, 0),
                ],
            )
            rows = conn.execute("SELECT * FROM candidates").fetchall()
        finally:
            conn.close()

        pool = compare_service.lowest_comparison_candidate_pool(rows, 2)

        self.assertEqual({row["id"] for row in pool}, {2, 3})

    async def test_mosaic_diverse_uses_full_default_visible_universe_without_response_cache(self):
        def candidate(image_id: int) -> dict:
            return {
                "id": image_id,
                "filename": f"image-{image_id}.jpg",
                "filepath": f"/photos/image-{image_id}.jpg",
                "elo": 1200.0 + image_id,
                "comparisons": 0,
                "propagated_updates": 0,
                "status": "kept",
                "flag": "unflagged",
                "orientation": "landscape",
                "aspect_ratio": 1.5,
                "date_taken": "",
                "camera_make": "",
                "camera_model": "",
                "lens": "",
                "file_ext": ".jpg",
                "created_at": 1.0,
            }

        calls = []
        old_default = compare_service.default_visible_pairing_candidates
        old_diverse = compare_service.diverse_sample
        old_pool = compare_service._get_visible_pairing_pool_counts
        try:
            async def fake_default(size: str, **kwargs):
                calls.append((size, kwargs))
                return [candidate(idx) for idx in range(1, 8)]

            async def fake_diverse(candidates, count):
                return candidates[:count]

            async def fake_pool(_size, _cache_root):
                return {"active_images": 7, "visible_images": 7}

            compare_service.default_visible_pairing_candidates = fake_default
            compare_service.diverse_sample = fake_diverse
            compare_service._get_visible_pairing_pool_counts = fake_pool
            compare_service._interaction_response_cache.clear()

            first = await compare_service.mosaic_next_impl(n=3, strategy="diverse")
            second = await compare_service.mosaic_next_impl(n=3, strategy="diverse")
        finally:
            compare_service.default_visible_pairing_candidates = old_default
            compare_service.diverse_sample = old_diverse
            compare_service._get_visible_pairing_pool_counts = old_pool
            compare_service._interaction_response_cache.clear()

        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0][0], "sm")
        self.assertIsNone(calls[0][1].get("limit"))
        self.assertEqual(calls[0][1].get("order"), "cache")
        self.assertFalse(calls[0][1].get("include_card_metadata"))
        self.assertEqual(first["candidate_source"], "default_diverse_universe")
        self.assertEqual(second["candidate_source"], "default_diverse_universe")
        self.assertFalse(first["cache_hit"])
        self.assertFalse(second["cache_hit"])

    async def test_mosaic_prefetch_runs_after_response(self):
        source = await self._source()
        first = await self._image(source["id"], "first.jpg", elo=1500)
        second = await self._image(source["id"], "second.jpg", elo=1300)
        await self._cache_entry(first, "sm")
        await self._cache_entry(second, "sm")
        started = asyncio.Event()
        release = asyncio.Event()

        async def blocking_prefetch(*_args, **_kwargs):
            started.set()
            await release.wait()
            return 0

        thumbnails.prefetch_images = blocking_prefetch
        try:
            result = await asyncio.wait_for(
                compare_routes.mosaic_next(n=2, strategy="random"),
                timeout=0.5,
            )
            self.assertEqual(len(result["images"]), 2)
            await asyncio.wait_for(started.wait(), timeout=0.5)
        finally:
            release.set()
            await asyncio.sleep(0)

    async def test_compare_prefetch_runs_after_response(self):
        source = await self._source()
        first = await self._image(source["id"], "first.jpg", elo=1500)
        second = await self._image(source["id"], "second.jpg", elo=1300)
        await self._cache_entry(first, "md")
        await self._cache_entry(second, "md")
        started = asyncio.Event()
        release = asyncio.Event()

        async def blocking_prefetch(*_args, **_kwargs):
            started.set()
            await release.wait()
            return 0

        thumbnails.prefetch_images = blocking_prefetch
        try:
            result = await asyncio.wait_for(compare_routes.compare_next(n=1, mode="swiss"), timeout=0.5)
            self.assertEqual(len(result["pairs"]), 1)
            await asyncio.wait_for(started.wait(), timeout=0.5)
        finally:
            release.set()
            await asyncio.sleep(0)

    async def test_compare_next_excludes_images_without_md_thumbnails(self):
        source = await self._source()
        visible_a = await self._image(source["id"], "visible-a.jpg", elo=1500)
        hidden_a = await self._image(source["id"], "hidden-a.jpg", elo=1450)
        visible_b = await self._image(source["id"], "visible-b.jpg", elo=1400)
        hidden_b = await self._image(source["id"], "hidden-b.jpg", elo=1350)
        await self._cache_entry(visible_a, "md")
        await self._cache_entry(visible_b, "md")

        result = await compare_routes.compare_next(n=2, mode="swiss")
        pair_ids = {
            image["id"]
            for pair in result["pairs"]
            for image in (pair["left"], pair["right"])
        }

        self.assertEqual(pair_ids, {visible_a, visible_b})
        self.assertNotIn(hidden_a, pair_ids)
        self.assertNotIn(hidden_b, pair_ids)
        self.assertEqual(result["visible_images"], 2)
        self.assertEqual(result["total_images"], 4)
        self.assertEqual(result["hidden_pending_thumbnails"], 2)

    async def test_visible_pairing_candidates_include_offline_cached_images(self):
        active_source = await self._source("pair-active")
        offline_source = await self._source("pair-offline", online=False)
        active = await self._image(active_source["id"], "active.jpg")
        offline = await self._image(offline_source["id"], "offline.jpg")
        await self._cache_entry(active, "md")
        await self._cache_entry(offline, "md")

        rows = await db.get_visible_images_for_pairing(
            "md",
            thumbnails.SSD_CACHE_DIR,
            include_card_metadata=False,
            limit=10,
        )

        self.assertEqual([row["id"] for row in rows], [active, offline])

    async def test_visible_past_matchups_only_include_visible_pairs(self):
        source = await self._source()
        visible_a = await self._image(source["id"], "visible-a.jpg")
        visible_b = await self._image(source["id"], "visible-b.jpg")
        hidden = await self._image(source["id"], "hidden.jpg")
        await self._cache_entry(visible_a, "md")
        await self._cache_entry(visible_b, "md")
        await db.record_comparison(
            visible_a, visible_b, "swiss",
            1200.0, 1200.0, 1210.0, 1190.0,
        )
        await db.record_comparison(
            visible_a, hidden, "swiss",
            1210.0, 1200.0, 1220.0, 1190.0,
        )

        matchups = await db.get_visible_past_matchups(
            "md",
            thumbnails.SSD_CACHE_DIR,
        )

        self.assertIn((visible_a, visible_b), matchups)
        self.assertNotIn((min(visible_a, hidden), max(visible_a, hidden)), matchups)

    async def test_candidate_past_matchups_use_only_candidate_pairs(self):
        source = await self._source()
        first = await self._image(source["id"], "first.jpg")
        second = await self._image(source["id"], "second.jpg")
        outside = await self._image(source["id"], "outside.jpg")
        await db.record_comparison(
            first, second, "swiss",
            1200.0, 1200.0, 1210.0, 1190.0,
        )
        await db.record_comparison(
            first, outside, "swiss",
            1210.0, 1200.0, 1220.0, 1190.0,
        )

        matchups = await db.get_past_matchups_for_image_ids([first, second])

        self.assertEqual(matchups, {(min(first, second), max(first, second))})

    async def test_metadata_fallback_constrains_compare_and_mosaic_pools(self):
        source = await self._source()
        match_a = await self._image(source["id"], "sunset-a.jpg", elo=1500)
        match_b = await self._image(source["id"], "sunset-b.jpg", elo=1400)
        miss = await self._image(source["id"], "portrait-miss.jpg", elo=1300)
        for image_id in (match_a, match_b, miss):
            await self._cache_entry(image_id, "sm")
            await self._cache_entry(image_id, "md")

        embedding_worker.encode_text = lambda _query: None

        mosaic = await compare_routes.mosaic_next(n=5, strategy="random", q="sunset")
        compare = await compare_routes.compare_next(n=2, mode="swiss", q="sunset")
        mosaic_ids = {img["id"] for img in mosaic["images"]}
        compare_ids = {
            image["id"]
            for pair in compare["pairs"]
            for image in (pair["left"], pair["right"])
        }

        self.assertEqual(mosaic_ids, {match_a, match_b})
        self.assertEqual(compare_ids, {match_a, match_b})
        self.assertEqual(mosaic["search_mode"], "metadata")
        self.assertEqual(compare["search_mode"], "metadata")
        self.assertTrue(mosaic["ai_unavailable"])
        self.assertTrue(compare["ai_unavailable"])

    async def test_library_cross_view_warmup_does_not_request_diverse_mosaic(self):
        base_dir = os.path.dirname(__file__)
        with open(
            os.path.join(base_dir, "static", "js", "legacy", "app.js"),
            encoding="utf-8",
        ) as fh:
            script = fh.read()
        with open(
            os.path.join(base_dir, "static", "js", "warmup_neighbors.js"),
            encoding="utf-8",
        ) as fh:
            warmup_neighbors = fh.read()
        warmup = script.split("function scheduleCrossViewWarmup(fromView)", 1)[1].split(
            "function scheduleLibraryNeighborWarmup",
            1,
        )[0]

        self.assertIn("neighborWarmups.scheduleCrossViewWarmup(fromView)", warmup)
        self.assertIn("const warmStrategy = strategy === 'diverse' ? 'explore' : strategy;", warmup_neighbors)
        self.assertIn("strategy: warmStrategy", warmup_neighbors)
        self.assertNotIn("strategy: getMosaicStrategy()", warmup_neighbors)

    async def test_filtered_compare_window_is_smaller_than_default_window(self):
        self.assertLess(compare_service._FILTERED_SWISS_PAIR_WINDOW, compare_service._SWISS_PAIR_WINDOW)
        self.assertGreaterEqual(compare_service._FILTERED_SWISS_PAIR_WINDOW, 256)

    async def test_filtered_mosaic_window_is_bounded_but_not_tiny(self):
        self.assertLess(compare_service._FILTERED_MOSAIC_WINDOW, compare_service._MOSAIC_EXPLORE_WINDOW)
        self.assertGreaterEqual(compare_service._FILTERED_MOSAIC_WINDOW, 128)
        self.assertGreaterEqual(compare_service._MOSAIC_EXPLORE_WINDOW, 768)
