from test_support import *  # noqa: F401,F403
import embed_cache
from features.library import taste as taste_service


class LibraryTests(BackendTestCase):
    async def _embedding(self, image_id, values):
        config = settings.active_embedding_config()
        vector = np.asarray(values, dtype=np.float32)
        conn = await db.get_db()
        try:
            await conn.execute(
                "INSERT OR IGNORE INTO embedding_models "
                "(model_key, model_id, revision, dimension) VALUES (?, ?, ?, ?)",
                (
                    config["model_key"],
                    config["model_id"],
                    config["revision"],
                    len(vector),
                ),
            )
            await conn.execute(
                "INSERT OR REPLACE INTO embeddings_by_model "
                "(model_key, image_id, embedding, dimension) VALUES (?, ?, ?, ?)",
                (config["model_key"], image_id, vector.tobytes(), len(vector)),
            )
            await conn.commit()
        finally:
            await conn.close()
        embed_cache.invalidate()
        taste_service.invalidate_taste_cache()

    async def _comparison_rows(self, rows):
        conn = await db.get_db()
        try:
            await conn.executemany(
                "INSERT INTO comparisons "
                "(winner_id, loser_id, mode, elo_before_winner, elo_before_loser, action_id) "
                "VALUES (?, ?, 'swiss', 1200, 1200, ?)",
                [(winner, loser, f"taste-{idx}") for idx, (winner, loser) in enumerate(rows)],
            )
            await conn.commit()
        finally:
            await conn.close()
        cache_events.invalidate_rankings_cache()
        taste_service.invalidate_taste_cache()

    async def test_stats_preserve_imported_ranking_without_history(self):
        source = await self._source()
        await self._image(source["id"], "imported-a.jpg", elo=1300.0, comparisons=5)
        await self._image(source["id"], "imported-b.jpg", elo=1190.0, comparisons=2, propagated_updates=1)
        await self._image(source["id"], "unranked.jpg")

        stats = await db.get_stats()

        self.assertGreaterEqual(db.STATS_CACHE_TTL_SECONDS, 30.0)
        self.assertEqual(stats["direct_comparison_rows"], 0)
        self.assertEqual(stats["rated_images"], 2)
        self.assertEqual(stats["imported_ranking_without_history"], 7)
        self.assertEqual(stats["propagated_update_count"], 1)
        self.assertEqual(stats["ranking_signal_count"], 8)
        self.assertEqual(stats["total_comparisons"], 8)

        ai_counts = await db.get_ai_status_counts()
        self.assertGreaterEqual(db.AI_STATUS_COUNTS_CACHE_TTL_SECONDS, 30.0)
        self.assertEqual(ai_counts["total_images"], stats["active_images"])
        self.assertEqual(ai_counts["rated_images"], stats["rated_images"])
        self.assertEqual(ai_counts["direct_comparison_rows"], stats["direct_comparison_rows"])
        self.assertEqual(ai_counts["imported_ranking_without_history"], stats["imported_ranking_without_history"])
        self.assertEqual(ai_counts["ranking_signal_count"], stats["ranking_signal_count"])

    async def test_stats_count_direct_history_when_all_catalog_images_are_active(self):
        source = await self._source()
        winner = await self._image(source["id"], "winner.jpg")
        loser = await self._image(source["id"], "loser.jpg")
        await compare_routes.submit_comparison(JsonRequest({"winner_id": winner, "loser_id": loser}))

        stats = await db.get_stats()

        self.assertEqual(stats["direct_comparison_rows"], 1)
        self.assertEqual(stats["direct_catalog_comparison_rows"], 1)
        self.assertEqual(stats["imported_ranking_without_history"], 0)
        self.assertEqual(stats["ranking_signal_count"], 1)
        self.assertEqual(stats["total_comparisons"], 1)

        ai_counts = await db.get_ai_status_counts()
        self.assertEqual(ai_counts["total_images"], stats["active_images"])
        self.assertEqual(ai_counts["rated_images"], stats["rated_images"])
        self.assertEqual(ai_counts["direct_comparison_rows"], stats["direct_comparison_rows"])
        self.assertEqual(ai_counts["imported_ranking_without_history"], stats["imported_ranking_without_history"])
        self.assertEqual(ai_counts["ranking_signal_count"], stats["ranking_signal_count"])

    async def test_stats_exclude_missing_comparison_rows_without_importing_valid_endpoints(self):
        source = await self._source()
        first = await self._image(source["id"], "first.jpg", comparisons=2)
        second = await self._image(source["id"], "second.jpg", comparisons=2)
        missing = await self._image(source["id"], "missing.jpg", comparisons=2, missing_at=123.0)
        conn = await db.get_db()
        try:
            await conn.executemany(
                "INSERT INTO comparisons "
                "(winner_id, loser_id, mode, elo_before_winner, elo_before_loser, action_id) "
                "VALUES (?, ?, 'swiss', 1200, 1200, ?)",
                [
                    (first, second, "valid-row"),
                    (first, missing, "missing-loser"),
                    (missing, second, "missing-winner"),
                ],
            )
            await conn.commit()
        finally:
            await conn.close()
        db.invalidate_stats_cache()

        stats = await db.get_stats()

        self.assertEqual(stats["direct_catalog_comparison_rows"], 3)
        self.assertEqual(stats["direct_comparison_rows"], 1)
        self.assertEqual(stats["imported_ranking_without_history"], 0)
        self.assertEqual(stats["ranking_signal_count"], 1)
        self.assertEqual(stats["total_comparisons"], 1)

    async def test_full_stats_repository_matches_facade_payload(self):
        source = await self._source()
        winner = await self._image(source["id"], "winner.jpg", comparisons=1)
        loser = await self._image(source["id"], "loser.jpg", comparisons=1, propagated_updates=1)
        picked = await self._image(source["id"], "picked.jpg", elo=1300.0)
        rejected = await self._image(source["id"], "rejected.jpg")
        conn = await db.get_db()
        try:
            await conn.execute("UPDATE images SET flag = 'picked' WHERE id = ?", (picked,))
            await conn.execute("UPDATE images SET flag = 'rejected' WHERE id = ?", (rejected,))
            await conn.execute(
                "INSERT INTO comparisons "
                "(winner_id, loser_id, mode, elo_before_winner, elo_before_loser, action_id) "
                "VALUES (?, ?, 'swiss', 1200, 1200, 'repo-facade-parity')",
                (winner, loser),
            )
            await conn.commit()
        finally:
            await conn.close()
        db.invalidate_stats_cache()

        repository_stats = await stats_repository.full_stats(db.DB_PATH)
        db.invalidate_stats_cache()
        facade_stats = await db._get_stats_uncached()
        cached_stats = await db.get_stats()

        self.assertEqual(facade_stats, repository_stats)
        self.assertIs(db._stats_cache["data"], facade_stats)
        self.assertIs(cached_stats, facade_stats)
        self.assertEqual(
            list(facade_stats.keys()),
            [
                "total_images",
                "active_images",
                "total_catalog_images",
                "removed_images",
                "offline_images",
                "kept",
                "maybe",
                "picked",
                "rejected",
                "total_comparisons",
                "total_catalog_comparisons",
                "direct_comparison_rows",
                "direct_catalog_comparison_rows",
                "rated_images",
                "ranking_signal_count",
                "catalog_ranking_signal_count",
                "propagated_update_count",
                "imported_ranking_without_history",
            ],
        )

    async def test_rankings_query_plan_uses_active_sort_indexes(self):
        source = await self._source()
        first = await self._image(source["id"], "a.jpg", elo=1500)
        second = await self._image(source["id"], "b.jpg", elo=1300)
        conn = await db.get_db()
        try:
            await conn.execute(
                "UPDATE images SET date_taken = ?, file_modified_at = ?, file_size = ?, "
                "width = ?, height = ?, camera_make = ?, camera_model = ? WHERE id = ?",
                ("2024-01-02 03:04:05", 1700000000.0, 200, 4000, 3000, "Fuji", "X-T5", first),
            )
            await conn.execute(
                "UPDATE images SET date_taken = ?, file_modified_at = ?, file_size = ?, "
                "width = ?, height = ?, camera_make = ?, camera_model = ? WHERE id = ?",
                ("2023-01-02 03:04:05", 1600000000.0, 100, 2000, 1000, "Canon", "R5", second),
            )
            await conn.commit()
        finally:
            await conn.close()

        expected = {
            "elo": "idx_images_active_elo",
            "date_taken": "idx_images_active_date_taken_sort_desc",
            "date_modified": "idx_images_active_modified_sort_desc",
            "file_size": "idx_images_active_file_size_sort_desc",
            "resolution": "idx_images_active_resolution_sort_desc",
            "camera": "idx_images_active_camera_sort_asc",
        }
        raw = sqlite3.connect(db.DB_PATH)
        try:
            for sort, index_name in expected.items():
                rows = await db.get_rankings(limit=10, sort=sort)
                self.assertTrue(rows)
                conditions, params = db._ranking_filter_parts()
                image_source = db._ranking_image_source(sort, id_filter=None, text_query="")
                sql = (
                    f"SELECT i.id FROM {image_source} "
                    "JOIN catalog_sources s ON s.id = i.source_id "
                    f"WHERE {' AND '.join(conditions)} "
                    f"ORDER BY {db.RANKING_SORTS[sort]} LIMIT 10"
                )
                plan_rows = raw.execute(f"EXPLAIN QUERY PLAN {sql}", params).fetchall()
                plan = " | ".join(row[3] for row in plan_rows)
                self.assertIn(index_name, plan)
                self.assertNotIn("USE TEMP B-TREE", plan)
        finally:
            raw.close()

    async def test_orientation_rankings_use_orientation_elo_index(self):
        source = await self._source()
        await self._image(source["id"], "landscape.jpg", elo=1500)
        await self._image(source["id"], "portrait.jpg", elo=1300)
        conn = await db.get_db()
        try:
            await conn.execute(
                "UPDATE images SET orientation = 'landscape' WHERE filename = 'landscape.jpg'"
            )
            await conn.execute(
                "UPDATE images SET orientation = 'portrait' WHERE filename = 'portrait.jpg'"
            )
            await conn.commit()
        finally:
            await conn.close()

        rows = await db.get_rankings(limit=10, sort="elo", orientation="landscape")
        self.assertEqual([row["filename"] for row in rows], ["landscape.jpg"])
        conditions, params = db._ranking_filter_parts(orientation="landscape")
        image_source = db._ranking_image_source(
            "elo",
            orientation="landscape",
            id_filter=None,
            text_query="",
        )
        raw = sqlite3.connect(db.DB_PATH)
        try:
            plan_rows = raw.execute(
                "EXPLAIN QUERY PLAN "
                f"SELECT i.id FROM {image_source} "
                "JOIN catalog_sources s ON s.id = i.source_id "
                f"WHERE {' AND '.join(conditions)} "
                "ORDER BY i.elo DESC LIMIT 10",
                params,
            ).fetchall()
            plan = " | ".join(row[3] for row in plan_rows)
        finally:
            raw.close()
        self.assertIn("idx_images_active_visible_orientation_elo", plan)
        self.assertNotIn("idx_images_active_elo", plan)

    async def test_filter_options_query_plan_uses_metadata_indexes(self):
        source = await self._source()
        first = await self._image(source["id"], "a.jpg")
        second = await self._image(source["id"], "b.png")
        conn = await db.get_db()
        try:
            await conn.execute(
                "UPDATE images SET date_taken = ?, file_ext = ?, camera_make = ?, "
                "camera_model = ?, lens = ? WHERE id = ?",
                ("2024-01-02", "jpg", "Fuji", "X-T5", "35mm", first),
            )
            await conn.execute(
                "UPDATE images SET file_ext = ?, camera_make = ?, camera_model = ?, "
                "lens = ? WHERE id = ?",
                ("png", "Canon", "R5", "50mm", second),
            )
            await conn.commit()
        finally:
            await conn.close()
        db.invalidate_stats_cache()

        options = await db.get_filter_options()
        self.assertEqual(options["years"][0]["year"], "2024")
        self.assertEqual(options["undated"], 1)
        self.assertGreaterEqual(db.FILTER_OPTIONS_CACHE_TTL_SECONDS, 300.0)

    async def test_filter_options_repository_matches_facade_with_cache(self):
        source = await self._source()
        removed_source = await self._source("removed-filter-options")
        active = await self._image(source["id"], "active.jpg")
        removed = await self._image(removed_source["id"], "removed.png")
        conn = await db.get_db()
        try:
            await conn.execute(
                "UPDATE images SET date_taken = ?, file_ext = ?, camera_make = ?, "
                "camera_model = ?, lens = ? WHERE id = ?",
                ("2024-01-02", "jpg", "Fuji", "X-T5", "35mm", active),
            )
            await conn.execute(
                "UPDATE images SET date_taken = ?, file_ext = ?, camera_make = ?, "
                "camera_model = ?, lens = ? WHERE id = ?",
                ("2023-01-02", "png", "Canon", "R5", "50mm", removed),
            )
            await conn.execute(
                "UPDATE catalog_sources SET included = 0 WHERE id = ?",
                (removed_source["id"],),
            )
            await db._update_source_counts(conn, removed_source["id"])
            await conn.commit()
        finally:
            await conn.close()
        db.invalidate_stats_cache()
        db.clear_filter_options_cache()

        repository_result = await filter_options_repository.filter_options(
            db.DB_PATH,
            catalog_counts=await db.get_catalog_image_counts(),
            active_source_ids=sorted(await db.get_active_source_id_set()),
        )
        facade_result = await db.get_filter_options()

        self.assertEqual(facade_result, repository_result)
        self.assertEqual(facade_result["years"], [{"year": "2024", "count": 1}])
        self.assertEqual(facade_result["file_types"], [{"ext": "jpg", "count": 1}])
        self.assertEqual(facade_result["cameras"], [{"camera": "Fuji X-T5", "count": 1}])
        self.assertEqual(facade_result["lenses"], [{"lens": "35mm", "count": 1}])
        self.assertEqual(db._filter_options_cache["data"], facade_result)

        original_filter_options = filter_options_repository.filter_options

        async def fail_on_uncached_read(*_args, **_kwargs):
            raise AssertionError("cached facade result should not hit repository")

        filter_options_repository.filter_options = fail_on_uncached_read
        try:
            self.assertEqual(await db.get_filter_options(), facade_result)
        finally:
            filter_options_repository.filter_options = original_filter_options

    async def test_rankings_repository_matches_facade_visible_cache_paths(self):
        source = await self._source()
        alpha = await self._image(source["id"], "alpha.jpg", elo=1300)
        beta = await self._image(source["id"], "beta.jpg", elo=1400)
        gamma = await self._image(source["id"], "gamma.jpg", elo=1500)
        for image_id in (alpha, beta):
            await self._cache_entry(image_id, "sm")
        db.invalidate_cached_image_ids_cache()
        db.invalidate_stats_cache()
        filters = {
            "sort": "filename",
            "visible_thumb_size": "sm",
            "cache_root": thumbnails.SSD_CACHE_DIR,
        }

        repository_rows = await rankings.rankings(
            db.DB_PATH,
            catalog_counts=await db.get_catalog_image_counts(),
            limit=10,
            offset=0,
            use_cache_first_visible=True,
            **filters,
        )
        facade_rows = await db.get_rankings(limit=10, offset=0, **filters)

        self.assertEqual([dict(row) for row in facade_rows], [dict(row) for row in repository_rows])
        self.assertEqual([row["id"] for row in facade_rows], [alpha, beta])

        id_filtered_facade_rows = await db.get_rankings(
            limit=10,
            offset=0,
            **filters,
            id_filter={beta, gamma},
        )
        id_filtered_repository_rows = await rankings.rankings(
            db.DB_PATH,
            catalog_counts=await db.get_catalog_image_counts(),
            limit=10,
            offset=0,
            sort="filename",
            id_filter={beta},
        )
        self.assertEqual(
            [dict(row) for row in id_filtered_facade_rows],
            [dict(row) for row in id_filtered_repository_rows],
        )
        self.assertEqual([row["id"] for row in id_filtered_facade_rows], [beta])

    async def test_file_type_filters_match_dotted_and_plain_extensions(self):
        source = await self._source()
        plain = await self._image(source["id"], "plain.jpg")
        dotted = await self._image(source["id"], "dotted.jpg")
        png = await self._image(source["id"], "other.png")
        conn = await db.get_db()
        try:
            for image_id, ext in ((plain, "jpg"), (dotted, ".jpg"), (png, "png")):
                await conn.execute("UPDATE images SET file_ext = ? WHERE id = ?", (ext, image_id))
            await conn.commit()
        finally:
            await conn.close()
        for image_id in (plain, dotted, png):
            await self._cache_entry(image_id, "md")
        db.invalidate_stats_cache()

        rows = await db.get_rankings(
            limit=10,
            file_type="jpg",
            visible_thumb_size="md",
            cache_root=thumbnails.SSD_CACHE_DIR,
        )
        options = await db.get_filter_options()

        self.assertEqual({row["id"] for row in rows}, {plain, dotted})
        self.assertIn({"ext": "jpg", "count": 2}, options["file_types"])
        self.assertNotIn({"ext": ".jpg", "count": 1}, options["file_types"])

    async def test_flag_filters_use_normalized_indexed_values(self):
        source = await self._source()
        null_flag = await self._image(source["id"], "legacy-null.jpg")
        empty_flag = await self._image(source["id"], "legacy-empty.jpg")
        rejected = await self._image(source["id"], "legacy-rejected.jpg")
        picked = await self._image(source["id"], "picked.jpg")
        conn = await db.get_db()
        try:
            await conn.execute("UPDATE images SET flag = NULL WHERE id = ?", (null_flag,))
            await conn.execute("UPDATE images SET flag = '' WHERE id = ?", (empty_flag,))
            await conn.execute(
                "UPDATE images SET status = 'rejected', flag = '' WHERE id = ?",
                (rejected,),
            )
            await conn.execute("UPDATE images SET flag = 'picked' WHERE id = ?", (picked,))
            await conn.commit()
        finally:
            await conn.close()

        await db.init_db()
        db.invalidate_stats_cache()

        self.assertEqual((await self._image_row(null_flag))["flag"], "unflagged")
        self.assertEqual((await self._image_row(empty_flag))["flag"], "unflagged")
        self.assertEqual((await self._image_row(rejected))["flag"], "rejected")
        self.assertEqual((await self._image_row(picked))["flag"], "picked")
        self.assertEqual(await db.count_rankings(flag="unflagged"), 2)
        self.assertEqual(await db.count_rankings(flag="rejected"), 1)
        self.assertEqual(await db.count_rankings(flag="picked"), 1)

        conditions, params = db._ranking_filter_parts(flag="unflagged", include_source=False)
        raw = sqlite3.connect(db.DB_PATH)
        try:
            plan_rows = raw.execute(
                f"EXPLAIN QUERY PLAN SELECT COUNT(*) FROM images i "
                f"WHERE {' AND '.join(conditions)}",
                params,
            ).fetchall()
            plan = " | ".join(row[3] for row in plan_rows)
        finally:
            raw.close()
        self.assertIn("idx_images_flag", plan)

    async def test_orientation_count_uses_count_index(self):
        source = await self._source()
        await self._image(source["id"], "landscape.jpg")
        await self._image(source["id"], "portrait.jpg")
        conn = await db.get_db()
        try:
            await conn.execute(
                "UPDATE images SET orientation = 'landscape' WHERE filename = 'landscape.jpg'"
            )
            await conn.execute(
                "UPDATE images SET orientation = 'portrait' WHERE filename = 'portrait.jpg'"
            )
            await conn.commit()
        finally:
            await conn.close()
        db.invalidate_stats_cache()

        self.assertEqual(await db.count_rankings(orientation="landscape"), 1)
        conditions, params = db._ranking_filter_parts(orientation="landscape", include_source=False)
        raw = sqlite3.connect(db.DB_PATH)
        try:
            plan_rows = raw.execute(
                f"EXPLAIN QUERY PLAN SELECT COUNT(*) FROM images i "
                f"WHERE {' AND '.join(conditions)}",
                params,
            ).fetchall()
            plan = " | ".join(row[3] for row in plan_rows)
        finally:
            raw.close()
        self.assertIn("idx_images_active_orientation_count", plan)

    async def test_rankings_returns_only_sm_cached_images_with_visible_total_counts(self):
        source = await self._source()
        visible_high = await self._image(source["id"], "visible-high.jpg", elo=1500)
        hidden = await self._image(source["id"], "hidden.jpg", elo=1400)
        visible_low = await self._image(source["id"], "visible-low.jpg", elo=1300)
        await self._cache_entry(visible_high, "sm")
        await self._cache_entry(visible_low, "sm")

        result = await library_routes.api_rankings(limit=10, sort="elo")

        self.assertEqual([img["id"] for img in result["images"]], [visible_high, visible_low])
        self.assertEqual(result["visible_images"], 2)
        self.assertEqual(result["total_images"], 3)
        self.assertEqual(result["hidden_pending_thumbnails"], 1)
        self.assertNotIn(hidden, [img["id"] for img in result["images"]])

    async def test_taste_vector_scores_winner_like_embeddings_above_loser_like(self):
        source = await self._source()
        winners = [await self._image(source["id"], f"winner-{idx}.jpg", comparisons=1) for idx in range(3)]
        losers = [await self._image(source["id"], f"loser-{idx}.jpg", comparisons=1) for idx in range(3)]
        winner_like = await self._image(source["id"], "winner-like.jpg")
        loser_like = await self._image(source["id"], "loser-like.jpg")
        for image_id in winners + [winner_like]:
            await self._embedding(image_id, [1.0, 0.0])
        for image_id in losers + [loser_like]:
            await self._embedding(image_id, [-1.0, 0.0])
        await self._comparison_rows(list(zip(winners, losers)) + [(winners[0], losers[0]), (winners[1], losers[1])])
        for image_id in (winner_like, loser_like):
            await self._cache_entry(image_id, "sm")

        vector = await taste_service.taste_vector()
        result = await library_routes.api_rankings(limit=10, sort="taste")

        self.assertTrue(vector["available"])
        self.assertEqual(vector["comparison_count"], 5)
        self.assertEqual(vector["embedded_winner_count"], 5)
        self.assertEqual(vector["embedded_loser_count"], 5)
        self.assertEqual([img["id"] for img in result["images"][:2]], [winner_like, loser_like])
        self.assertGreater(result["images"][0]["taste_score"], result["images"][1]["taste_score"])
        self.assertTrue(result["taste_available"])
        self.assertEqual(result["taste_signal_count"], 5)

    async def test_taste_vector_skips_missing_embeddings_and_reports_thresholds(self):
        source = await self._source()
        winner_a = await self._image(source["id"], "winner-a.jpg", comparisons=1)
        winner_b = await self._image(source["id"], "winner-b.jpg", comparisons=1)
        missing_winner = await self._image(source["id"], "winner-missing.jpg", comparisons=1)
        loser_a = await self._image(source["id"], "loser-a.jpg", comparisons=1)
        loser_b = await self._image(source["id"], "loser-b.jpg", comparisons=1)
        missing_loser = await self._image(source["id"], "loser-missing.jpg", comparisons=1)
        await self._embedding(winner_a, [1.0, 0.0])
        await self._embedding(winner_b, [1.0, 0.0])
        await self._embedding(loser_a, [-1.0, 0.0])
        await self._embedding(loser_b, [-1.0, 0.0])
        await self._comparison_rows([
            (winner_a, loser_a),
            (winner_b, loser_b),
            (missing_winner, loser_a),
            (winner_a, missing_loser),
            (missing_winner, missing_loser),
        ])

        vector = await taste_service.taste_vector()

        self.assertTrue(vector["available"])
        self.assertEqual(vector["embedded_winner_count"], 3)
        self.assertEqual(vector["embedded_loser_count"], 3)

    async def test_taste_unavailable_returns_empty_without_elo_fallback(self):
        source = await self._source()
        high = await self._image(source["id"], "high.jpg", elo=1800)
        low = await self._image(source["id"], "low.jpg", elo=1000)
        for image_id in (high, low):
            await self._cache_entry(image_id, "sm")

        result = await library_routes.api_rankings(limit=10, sort="taste")

        self.assertEqual(result["images"], [])
        self.assertFalse(result["taste_available"])
        self.assertIn("direct comparisons", result["fallback_reason"])
        self.assertNotEqual([img["id"] for img in result["images"]], [high, low])

    async def test_taste_sort_preserves_filters_and_only_uses_uncompared_when_explicit(self):
        source = await self._source()
        winners = [await self._image(source["id"], f"winner-{idx}.jpg", comparisons=1) for idx in range(3)]
        losers = [await self._image(source["id"], f"loser-{idx}.jpg", comparisons=1) for idx in range(3)]
        ranked_match = await self._image(source["id"], "ranked-match.jpg", comparisons=1)
        unranked_match = await self._image(source["id"], "unranked-match.jpg")
        rejected_match = await self._image(source["id"], "rejected-match.jpg")
        opposite = await self._image(source["id"], "opposite.jpg")
        for image_id in winners + [ranked_match, unranked_match, rejected_match]:
            await self._embedding(image_id, [1.0, 0.0])
        for image_id in losers + [opposite]:
            await self._embedding(image_id, [-1.0, 0.0])
        await self._comparison_rows(list(zip(winners, losers)) + [(winners[0], losers[0]), (winners[1], losers[1])])
        conn = await db.get_db()
        try:
            await conn.execute("UPDATE images SET flag = 'picked' WHERE id = ?", (ranked_match,))
            await conn.execute("UPDATE images SET flag = 'rejected' WHERE id = ?", (rejected_match,))
            await conn.commit()
        finally:
            await conn.close()
        for image_id in (ranked_match, unranked_match, rejected_match, opposite):
            await self._cache_entry(image_id, "sm")
        db.invalidate_stats_cache()
        cache_events.invalidate_rankings_cache()

        all_taste = await library_routes.api_rankings(limit=20, sort="taste")
        unranked = await library_routes.api_rankings(limit=20, sort="taste", compared="uncompared")
        picked = await library_routes.api_rankings(limit=20, sort="taste", flag="picked")

        self.assertIn(ranked_match, [img["id"] for img in all_taste["images"]])
        self.assertIn(unranked_match, [img["id"] for img in all_taste["images"]])
        self.assertEqual([img["id"] for img in unranked["images"]], [unranked_match, rejected_match, opposite])
        self.assertEqual([img["id"] for img in picked["images"]], [ranked_match])

    async def test_taste_cache_invalidates_when_comparison_or_embedding_count_changes(self):
        source = await self._source()
        winner_a = await self._image(source["id"], "winner-a.jpg", comparisons=1)
        winner_b = await self._image(source["id"], "winner-b.jpg", comparisons=1)
        loser_a = await self._image(source["id"], "loser-a.jpg", comparisons=1)
        loser_b = await self._image(source["id"], "loser-b.jpg", comparisons=1)
        extra = await self._image(source["id"], "extra.jpg")
        for image_id, vector in (
            (winner_a, [1.0, 0.0]),
            (winner_b, [1.0, 0.0]),
            (loser_a, [-1.0, 0.0]),
            (loser_b, [-1.0, 0.0]),
        ):
            await self._embedding(image_id, vector)
        await self._comparison_rows([
            (winner_a, loser_a),
            (winner_b, loser_b),
            (winner_a, loser_b),
            (winner_b, loser_a),
            (winner_a, loser_a),
        ])

        first = await taste_service.taste_vector()
        second = await taste_service.taste_vector()
        await self._comparison_rows([(winner_b, loser_b)])
        third = await taste_service.taste_vector()
        await self._embedding(extra, [1.0, 0.0])
        fourth = await taste_service.taste_vector()

        self.assertEqual(first["comparison_count"], second["comparison_count"])
        self.assertEqual(third["comparison_count"], first["comparison_count"] + 1)
        self.assertNotEqual(taste_service._cache["key"][3], first["comparison_count"])
        self.assertEqual(fourth["comparison_count"], third["comparison_count"])
        self.assertEqual(taste_service._cache["key"][2], 5)

    async def test_visible_orientation_rankings_filter_cached_images(self):
        source = await self._source()
        visible_high = await self._image(source["id"], "visible-high.jpg", elo=1500)
        hidden_high = await self._image(source["id"], "hidden-high.jpg", elo=1450)
        visible_low = await self._image(source["id"], "visible-low.jpg", elo=1300)
        portrait = await self._image(source["id"], "portrait.jpg", elo=1600)
        conn = await db.get_db()
        try:
            await conn.executemany(
                "UPDATE images SET orientation = ? WHERE id = ?",
                [
                    ("landscape", visible_high),
                    ("landscape", hidden_high),
                    ("landscape", visible_low),
                    ("portrait", portrait),
                ],
            )
            await conn.commit()
        finally:
            await conn.close()
        db.invalidate_stats_cache()
        await self._cache_entry(visible_high, "md")
        await self._cache_entry(visible_low, "md")
        await self._cache_entry(portrait, "md")

        rows = await db.get_rankings(
            limit=10,
            sort="elo",
            orientation="landscape",
            visible_thumb_size="md",
            cache_root=thumbnails.SSD_CACHE_DIR,
        )

        self.assertEqual([row["id"] for row in rows], [visible_high, visible_low])
        self.assertNotIn(hidden_high, [row["id"] for row in rows])

    async def test_sparse_visible_rankings_sort_cached_subset(self):
        source = await self._source()
        visible_b = await self._image(source["id"], "b-visible.jpg", elo=1300)
        hidden_a = await self._image(source["id"], "a-hidden.jpg", elo=1600)
        visible_c = await self._image(source["id"], "c-visible.jpg", elo=1200)
        conn = await db.get_db()
        try:
            await conn.executemany(
                "UPDATE images SET camera_make = ?, camera_model = ? WHERE id = ?",
                [
                    ("Sony", "A7", visible_b),
                    ("Canon", "R5", hidden_a),
                    ("Fuji", "X-T5", visible_c),
                ],
            )
            await conn.commit()
        finally:
            await conn.close()
        db.invalidate_stats_cache()
        await self._cache_entry(visible_b, "sm")
        await self._cache_entry(visible_c, "sm")

        filename_rows = await db.get_rankings(
            limit=10,
            sort="filename",
            visible_thumb_size="sm",
            cache_root=thumbnails.SSD_CACHE_DIR,
        )
        camera_rows = await db.get_rankings(
            limit=10,
            sort="camera",
            visible_thumb_size="sm",
            cache_root=thumbnails.SSD_CACHE_DIR,
        )

        self.assertEqual([row["id"] for row in filename_rows], [visible_b, visible_c])
        self.assertEqual([row["id"] for row in camera_rows], [visible_c, visible_b])
        self.assertNotIn(hidden_a, [row["id"] for row in filename_rows + camera_rows])

    async def test_rankings_response_cache_invalidates_after_flag_change(self):
        source = await self._source()
        first = await self._image(source["id"], "first.jpg", elo=1500)
        second = await self._image(source["id"], "second.jpg", elo=1300)
        await self._cache_entry(first, "sm")
        await self._cache_entry(second, "sm")

        initial = await library_routes.api_rankings(limit=10, sort="elo", flag="picked")
        self.assertEqual(initial["images"], [])
        self.assertTrue(library_service._rankings_response_cache)

        await settings_routes.api_set_image_flag(first, JsonRequest({"flag": "picked"}))
        refreshed = await library_routes.api_rankings(limit=10, sort="elo", flag="picked")

        self.assertEqual([image["id"] for image in refreshed["images"]], [first])
        self.assertNotIn(second, [image["id"] for image in refreshed["images"]])

    async def test_owner_cache_reset_clears_response_caches(self):
        source = await self._source()
        image_id = await self._image(source["id"], "first.jpg", elo=1500)
        await self._cache_entry(image_id, "sm")

        await library_routes.api_rankings(limit=10, sort="elo")
        await settings_routes.api_settings()
        self.assertTrue(library_service._rankings_response_cache)
        self.assertIsNotNone(settings_status._settings_response_cache["data"])
        self.assertIsNotNone(ai_routes._ai_status_response_cache["data"])
        media_warm._thumbnail_memory_warm_inflight.add("sm:1")

        db.invalidate_stats_cache()
        db.invalidate_cached_image_ids_cache()
        db.clear_filter_options_cache()
        compare_service._pairing_cache.update({"data": None, "valid": False})
        compare_service._matchups_cache.update({"data": None, "valid": False})
        compare_service._visible_matchups_cache.clear()
        compare_service._visible_pairing_candidates_cache.clear()
        compare_service._visible_pairing_candidates_refreshing.clear()
        library_service._rankings_response_cache.clear()
        query_constraints._text_search_resolution_cache.clear()
        compare_service._interaction_response_cache.clear()
        media_warm._thumbnail_memory_warm_inflight.clear()
        settings_status.invalidate_settings_response_cache()
        ai_routes.invalidate_ai_status_response_cache()
        cache_status_service.invalidate_cache_status_cache()
        catalog_routes.clear_folders_cache()

        self.assertFalse(library_service._rankings_response_cache)
        self.assertFalse(media_warm._thumbnail_memory_warm_inflight)
        self.assertIsNone(settings_status._settings_response_cache["data"])
        self.assertIsNone(ai_routes._ai_status_response_cache["data"])

    async def test_rankings_response_cache_returns_independent_image_lists(self):
        source = await self._source()
        image_id = await self._image(source["id"], "first.jpg", elo=1500)
        await self._cache_entry(image_id, "sm")

        first = await library_routes.api_rankings(limit=10, sort="elo")
        second = await library_routes.api_rankings(limit=10, sort="elo")
        second["images"].clear()
        third = await library_routes.api_rankings(limit=10, sort="elo")

        self.assertEqual([image["id"] for image in first["images"]], [image_id])
        self.assertEqual([image["id"] for image in third["images"]], [image_id])

    async def test_visible_ranking_count_uses_short_ttl_cache_and_invalidation(self):
        source = await self._source()
        first = await self._image(source["id"], "first.jpg")
        second = await self._image(source["id"], "second.jpg")
        await self._cache_entry(first, "sm")

        count = await db.count_rankings(
            visible_thumb_size="sm",
            cache_root=thumbnails.SSD_CACHE_DIR,
        )
        self.assertEqual(count, 1)

        await self._cache_entry(second, "sm")
        stale_count = await db.count_rankings(
            visible_thumb_size="sm",
            cache_root=thumbnails.SSD_CACHE_DIR,
        )
        self.assertEqual(stale_count, 1)

        db.invalidate_cached_image_ids_cache()
        refreshed_count = await db.count_rankings(
            visible_thumb_size="sm",
            cache_root=thumbnails.SSD_CACHE_DIR,
        )
        self.assertEqual(refreshed_count, 2)

    async def test_visible_ranking_count_includes_offline_cached_images(self):
        active_source = await self._source("active")
        offline_source = await self._source("offline", online=False)
        active = await self._image(active_source["id"], "active.jpg")
        offline = await self._image(offline_source["id"], "offline.jpg")
        await self._cache_entry(active, "sm")
        await self._cache_entry(offline, "sm")

        count = await db.count_rankings(
            visible_thumb_size="sm",
            cache_root=thumbnails.SSD_CACHE_DIR,
        )

        self.assertEqual(count, 2)

    async def test_catalog_image_counts_cache_invalidates_with_stats(self):
        source = await self._source()

        self.assertEqual(await db.get_catalog_image_counts(), {
            "total_catalog_images": 0,
            "active_images": 0,
            "removed_images": 0,
            "offline_images": 0,
        })
        self.assertEqual(await stats_repository.catalog_image_counts_cached(db.DB_PATH), {
            "total_catalog_images": 0,
            "active_images": 0,
            "removed_images": 0,
            "offline_images": 0,
        })

        await self._image(source["id"], "counted.jpg")

        self.assertEqual(await db.get_catalog_image_counts(), {
            "total_catalog_images": 1,
            "active_images": 1,
            "removed_images": 0,
            "offline_images": 0,
        })
        self.assertEqual(await stats_repository.catalog_image_counts_cached(db.DB_PATH), {
            "total_catalog_images": 1,
            "active_images": 1,
            "removed_images": 0,
            "offline_images": 0,
        })

    async def test_filtered_ranking_count_cache_reuses_until_invalidation(self):
        source = await self._source()
        image_id = await self._image(source["id"], "picked-later.jpg")

        count = await db.count_rankings(flag="picked")
        self.assertEqual(count, 0)

        conn = await db.get_db()
        try:
            await conn.execute("UPDATE images SET flag = 'picked' WHERE id = ?", (image_id,))
            await conn.commit()
        finally:
            await conn.close()

        stale_count = await db.count_rankings(flag="picked")
        self.assertEqual(stale_count, 0)

        db.invalidate_stats_cache()
        refreshed_count = await db.count_rankings(flag="picked")
        self.assertEqual(refreshed_count, 1)

    async def test_flag_updates_invalidate_ranking_count_cache(self):
        source = await self._source()
        first = await self._image(source["id"], "first.jpg")
        second = await self._image(source["id"], "second.jpg")

        self.assertEqual(await db.count_rankings(flag="picked"), 0)
        await db.set_image_flag(first, "picked")
        self.assertEqual(await db.count_rankings(flag="picked"), 1)

        await db.batch_set_image_flags([second], "picked")
        self.assertEqual(await db.count_rankings(flag="picked"), 2)

    async def test_date_histogram_route_counts_months_undated_and_total(self):
        source = await self._source()
        january_first = await self._image(source["id"], "january-first.jpg")
        january_second = await self._image(source["id"], "january-second.jpg")
        february = await self._image(source["id"], "february.jpg")
        march = await self._image(source["id"], "march.jpg")
        await self._image(source["id"], "undated.jpg")
        conn = await db.get_db()
        try:
            await conn.executemany(
                "UPDATE images SET date_taken = ? WHERE id = ?",
                [
                    ("2025-01-03 10:00:00", january_first),
                    ("2025-01-20 18:30:00", january_second),
                    ("2025-02-14 08:15:00", february),
                    ("2025-03-01 22:00:00", march),
                ],
            )
            await conn.commit()
        finally:
            await conn.close()
        db.invalidate_stats_cache()

        response = await library_routes.api_date_histogram()

        self.assertEqual(response["months"], [
            {"month": "2025-03", "count": 1},
            {"month": "2025-02", "count": 1},
            {"month": "2025-01", "count": 2},
        ])
        self.assertEqual(response["undated"], 1)
        self.assertEqual(response["total"], 5)

    async def test_counts_route_counts_total_picked_and_rejected(self):
        source = await self._source()
        picked = await self._image(source["id"], "picked.jpg")
        rejected_first = await self._image(source["id"], "rejected-first.jpg")
        rejected_second = await self._image(source["id"], "rejected-second.jpg")
        await self._image(source["id"], "unflagged.jpg")
        conn = await db.get_db()
        try:
            await conn.execute("UPDATE images SET flag = 'picked' WHERE id = ?", (picked,))
            await conn.execute(
                "UPDATE images SET flag = 'rejected' WHERE id IN (?, ?)",
                (rejected_first, rejected_second),
            )
            await conn.commit()
        finally:
            await conn.close()
        db.invalidate_stats_cache()

        response = await library_routes.api_counts()

        self.assertEqual(response, {"total": 4, "picked": 1, "rejected": 2})

    async def test_date_taken_filter_accepts_year_month_and_undated(self):
        source = await self._source()
        november = await self._image(source["id"], "november.jpg")
        november_late = await self._image(source["id"], "november-late.jpg")
        december = await self._image(source["id"], "december.jpg")
        undated = await self._image(source["id"], "undated.jpg")
        conn = await db.get_db()
        try:
            await conn.executemany(
                "UPDATE images SET date_taken = ? WHERE id = ?",
                [
                    ("2024-11-01 00:00:00", november),
                    ("2024-11-30 23:59:59", november_late),
                    ("2024-12-01 00:00:00", december),
                    (None, undated),
                ],
            )
            await conn.commit()
        finally:
            await conn.close()
        db.invalidate_stats_cache()

        year_rows = await db.get_rankings(limit=10, sort="date_taken", date_taken="2024")
        month_rows = await db.get_rankings(limit=10, sort="date_taken", date_taken="2024-11")
        undated_rows = await db.get_rankings(limit=10, sort="date_taken", date_taken="undated")

        self.assertEqual({row["id"] for row in year_rows}, {november, november_late, december})
        self.assertEqual({row["id"] for row in month_rows}, {november, november_late})
        self.assertEqual([row["id"] for row in undated_rows], [undated])
        self.assertEqual(await db.count_rankings(date_taken="2024-11"), 2)

    async def test_expired_stats_cache_returns_stale_while_refreshing(self):
        stale_stats = {"total_images": 1, "active_images": 1}
        fresh_stats = {"total_images": 2, "active_images": 2}
        refresh_started = asyncio.Event()
        refresh_can_finish = asyncio.Event()
        calls = 0
        old_get_stats_uncached = db._get_stats_uncached

        async def fake_get_stats_uncached():
            nonlocal calls
            calls += 1
            refresh_started.set()
            await refresh_can_finish.wait()
            db._stats_cache["data"] = fresh_stats
            db._stats_cache["expires"] = db._time.time() + db.STATS_CACHE_TTL_SECONDS
            return fresh_stats

        db._get_stats_uncached = fake_get_stats_uncached
        stats_repository._stats_inflight_task = None
        db._stats_inflight_task = None
        db._stats_cache["data"] = stale_stats
        db._stats_cache["expires"] = db._time.time() - 1
        try:
            self.assertIs(await db.get_stats(), stale_stats)
            await asyncio.wait_for(refresh_started.wait(), timeout=1)
            self.assertEqual(calls, 1)
            self.assertIs(db._stats_inflight_task, stats_repository._stats_inflight_task)

            self.assertIs(await db.get_stats(), stale_stats)
            self.assertEqual(calls, 1)

            refresh_can_finish.set()
            await asyncio.wait_for(stats_repository._stats_inflight_task, timeout=1)

            self.assertIs(await db.get_stats(), fresh_stats)
            self.assertEqual(calls, 1)
        finally:
            refresh_can_finish.set()
            task = stats_repository._stats_inflight_task
            if task is not None and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            stats_repository._stats_inflight_task = None
            db._stats_inflight_task = None
            db._get_stats_uncached = old_get_stats_uncached
            db.invalidate_stats_cache()

    async def test_date_groups_return_stale_cache_while_refreshing(self):
        source = await self._source()
        image_id = await self._image(source["id"], "dated.jpg")
        conn = await db.get_db()
        try:
            await conn.execute("UPDATE images SET date_taken = ? WHERE id = ?", ("2025-02-03", image_id))
            await conn.commit()
        finally:
            await conn.close()
        db.invalidate_stats_cache()

        key = db._facet_cache_key()
        stale = [{"date": "1999-01", "label": "January 1999", "count": 1}]
        db._date_groups_cache[key] = {"data": stale, "expires": db._time.time() - 1.0}

        result = await db.get_date_groups()
        self.assertEqual(result, stale)

        for _ in range(50):
            if key not in db._date_groups_refreshing:
                break
            await asyncio.sleep(0.01)

        self.assertNotIn(key, db._date_groups_refreshing)
        refreshed = db._date_groups_cache[key]["data"]
        self.assertEqual(refreshed[0]["date"], "2025-02")

    async def test_date_groups_empty_catalog_keeps_facade_cache_empty(self):
        cache_key = db._facet_cache_key()

        self.assertEqual(
            await rankings.date_groups(
                db.DB_PATH,
                catalog_counts=await db.get_catalog_image_counts(),
            ),
            [],
        )
        self.assertEqual(await db.get_date_groups(), [])
        self.assertNotIn(cache_key, db._date_groups_cache)

    async def test_date_groups_repository_matches_facade_with_visible_filters(self):
        source = await self._source()
        feb = await self._image(source["id"], "feb.jpg")
        jan = await self._image(source["id"], "jan.jpg")
        no_date = await self._image(source["id"], "no-date.jpg")
        uncached_jan = await self._image(source["id"], "uncached-jan.jpg")
        conn = await db.get_db()
        try:
            await conn.executemany(
                "UPDATE images SET date_taken = ? WHERE id = ?",
                [
                    ("2024-02-03", feb),
                    ("2024-01-02", jan),
                    (None, no_date),
                    ("2024-01-04", uncached_jan),
                ],
            )
            await conn.commit()
        finally:
            await conn.close()
        db.invalidate_stats_cache()
        root = thumbnails.SSD_CACHE_DIR
        for image_id in (feb, jan, no_date):
            await self._cache_entry(image_id, "sm")

        filters = {"visible_thumb_size": "sm", "cache_root": root}
        repository_result = await rankings.date_groups(
            db.DB_PATH,
            catalog_counts=await db.get_catalog_image_counts(),
            **filters,
        )
        db._date_groups_cache.clear()
        facade_result = await db.get_date_groups(**filters)

        self.assertEqual(facade_result, repository_result)
        self.assertEqual(
            facade_result,
            [
                {"date": "2024-02", "label": "February 2024", "count": 1},
                {"date": "2024-01", "label": "January 2024", "count": 1},
                {"date": "", "label": "No Date", "count": 1},
            ],
        )
        cache_key = db._facet_cache_key(**filters)
        self.assertEqual(db._date_groups_cache[cache_key]["data"], facade_result)

        original_date_groups = rankings.date_groups

        async def fail_on_uncached_read(*_args, **_kwargs):
            raise AssertionError("cached facade result should not hit repository")

        rankings.date_groups = fail_on_uncached_read
        try:
            self.assertEqual(await db.get_date_groups(**filters), facade_result)
        finally:
            rankings.date_groups = original_date_groups

        id_filter = {jan, no_date, uncached_jan}
        id_filtered = await db.get_date_groups(**filters, id_filter=id_filter)
        repository_id_filtered = await rankings.date_groups(
            db.DB_PATH,
            catalog_counts=await db.get_catalog_image_counts(),
            **filters,
            id_filter=id_filter,
        )
        self.assertEqual(id_filtered, repository_id_filtered)
        self.assertEqual(
            id_filtered,
            [
                {"date": "2024-01", "label": "January 2024", "count": 1},
                {"date": "", "label": "No Date", "count": 1},
            ],
        )

    async def test_map_markers_repository_matches_facade_with_visible_filters(self):
        source = await self._source()
        visible_gps = await self._image(source["id"], "visible-gps.jpg")
        visible_no_gps = await self._image(source["id"], "visible-no-gps.jpg")
        hidden_gps = await self._image(source["id"], "hidden-gps.jpg")
        conn = await db.get_db()
        try:
            await conn.executemany(
                "UPDATE images SET latitude = ?, longitude = ? WHERE id = ?",
                [
                    (45.0, -93.0, visible_gps),
                    (None, None, visible_no_gps),
                    (46.0, -94.0, hidden_gps),
                ],
            )
            await conn.commit()
        finally:
            await conn.close()
        db.invalidate_stats_cache()
        root = thumbnails.SSD_CACHE_DIR
        for image_id in (visible_gps, visible_no_gps):
            await self._cache_entry(image_id, "sm")

        filters = {"visible_thumb_size": "sm", "cache_root": root}
        repository_result = await rankings.map_markers(
            db.DB_PATH,
            catalog_counts=await db.get_catalog_image_counts(),
            total_count=await db.count_rankings(),
            visible_total_count=await db.count_rankings(**filters),
            **filters,
        )
        db._map_markers_cache.clear()
        facade_result = await db.get_map_markers(**filters)

        self.assertEqual(facade_result, repository_result)
        self.assertEqual([marker["id"] for marker in facade_result["markers"]], [visible_gps])
        self.assertEqual(facade_result["total_count"], 3)
        self.assertEqual(facade_result["visible_count"], 2)
        self.assertEqual(facade_result["gps_total_count"], 2)
        self.assertEqual(facade_result["hidden_pending_thumbnails"], 1)
        cache_key = db._facet_cache_key(**filters)
        self.assertEqual(db._map_markers_cache[cache_key]["data"], facade_result)

        original_map_markers = rankings.map_markers

        async def fail_on_uncached_read(*_args, **_kwargs):
            raise AssertionError("cached facade result should not hit repository")

        rankings.map_markers = fail_on_uncached_read
        try:
            self.assertEqual(await db.get_map_markers(**filters), facade_result)
        finally:
            rankings.map_markers = original_map_markers

    async def test_rescan_marks_missing_files_and_restores_seen_files(self):
        source = await self._source("scan-source")
        first_path = os.path.join(source["path"], "first.jpg")
        second_path = os.path.join(source["path"], "second.jpg")
        for path in (first_path, second_path):
            with open(path, "wb") as f:
                f.write(b"not-a-real-jpeg")

        await scanner.scan_folder(source["path"], source_id=source["id"])
        stats = await db.get_stats()
        self.assertEqual(stats["total_images"], 2)

        os.remove(second_path)
        await scanner.scan_folder(source["path"], source_id=source["id"])

        rows = await db.get_rankings(limit=10)
        self.assertEqual([row["filename"] for row in rows], ["first.jpg"])
        conn = await db.get_db()
        try:
            cursor = await conn.execute(
                "SELECT missing_at FROM images WHERE filepath = ?",
                (second_path,),
            )
            missing = await cursor.fetchone()
        finally:
            await conn.close()
        self.assertIsNotNone(missing["missing_at"])
        stats = await db.get_stats()
        self.assertEqual(stats["total_images"], 1)
        self.assertEqual(stats["total_catalog_images"], 2)

        with open(second_path, "wb") as f:
            f.write(b"back")
        await scanner.scan_folder(source["path"], source_id=source["id"])

        rows = await db.get_rankings(limit=10, sort="filename")
        self.assertEqual([row["filename"] for row in rows], ["first.jpg", "second.jpg"])
        restored = await self._image_row(rows[1]["id"])
        self.assertIsNone(restored["missing_at"])

    async def test_missing_images_are_excluded_from_active_views_and_workers(self):
        source = await self._source()
        active_a = await self._image(source["id"], "active-a.jpg", elo=1500)
        missing = await self._image(source["id"], "missing.jpg", elo=1400, missing_at=12345.0)
        active_b = await self._image(source["id"], "active-b.jpg", elo=1300)
        await self._cache_entry(active_a, "sm")
        await self._cache_entry(missing, "sm")
        await self._cache_entry(active_b, "sm")
        await self._cache_entry(active_a, "md")
        await self._cache_entry(missing, "md")
        await self._cache_entry(active_b, "md")

        rankings = await library_routes.api_rankings(limit=10, sort="elo")
        self.assertEqual([img["id"] for img in rankings["images"]], [active_a, active_b])
        self.assertEqual(rankings["visible_images"], 2)
        self.assertEqual(rankings["total_images"], 2)

        mosaic = await compare_routes.mosaic_next(n=3, strategy="diverse")
        self.assertNotIn(missing, [img["id"] for img in mosaic["images"]])
        self.assertEqual(mosaic["total_images"], 2)

        compare = await compare_routes.compare_next(n=2, mode="swiss")
        pair_ids = {
            image["id"]
            for pair in compare["pairs"]
            for image in (pair["left"], pair["right"])
        }
        self.assertEqual(pair_ids, {active_a, active_b})

        self.assertNotIn(missing, await db.get_active_images_by_ids([active_a, missing, active_b]))
        self.assertEqual(len(await db.get_unembedded_images(limit=10)), 2)
        md_ready = await db.get_unembedded_images(
            limit=10,
            md_cache_root=thumbnails.SSD_CACHE_DIR,
        )
        self.assertEqual([row["id"] for row in md_ready], [active_a, active_b])
        self.assertEqual(
            [row["filepath"] for row in md_ready],
            [
                os.path.join(self.tempdir.name, f"md-{active_a}.jpg"),
                os.path.join(self.tempdir.name, f"md-{active_b}.jpg"),
            ],
        )
        sm_ready = await db.get_unembedded_images(
            limit=10,
            md_cache_root=thumbnails.SSD_CACHE_DIR,
            cache_size="sm",
        )
        self.assertEqual(
            [row["filepath"] for row in sm_ready],
            [
                os.path.join(self.tempdir.name, f"sm-{active_a}.jpg"),
                os.path.join(self.tempdir.name, f"sm-{active_b}.jpg"),
            ],
        )
        stats = await db.get_stats()
        self.assertEqual(stats["total_images"], 2)
        self.assertEqual(stats["total_catalog_images"], 3)

    async def test_offline_sources_remain_browseable_from_cache(self):
        source = await self._source(online=False)
        image_id = await self._image(source["id"], "offline.jpg")
        await self._cache_entry(image_id, "sm")
        await self._cache_entry(image_id, "md")

        rankings = await library_routes.api_rankings(limit=10)
        self.assertEqual([image["id"] for image in rankings["images"]], [image_id])
        self.assertEqual(rankings["total_images"], 1)

        self.assertEqual(await db.count_rankings(), 1)
        self.assertEqual([row["id"] for row in await db.get_rankings(limit=10)], [image_id])
        self.assertEqual([row["id"] for row in await db.get_active_images_for_pairing()], [image_id])
        self.assertEqual(await db.get_past_matchups(), set())
        self.assertIsInstance(
            await db.get_date_groups(visible_thumb_size="sm", cache_root=thumbnails.SSD_CACHE_DIR),
            list,
        )

        markers = await db.get_map_markers(visible_thumb_size="sm", cache_root=thumbnails.SSD_CACHE_DIR)
        self.assertEqual(markers["total_count"], 1)
        self.assertEqual(markers["gps_total_count"], 0)

        folders = await catalog_routes.api_folders()
        self.assertTrue(folders["folders"])

        collections = await search_routes.api_collections()
        self.assertEqual(collections["collections"], [])
        duplicates = await search_routes.api_duplicates()
        self.assertEqual(duplicates["pairs"], [])
        self.assertEqual(duplicates["total_pairs"], 0)

    async def test_folder_tree_uses_catalog_source_root(self):
        source = await self._source("folder-source")
        nested = os.path.join(source["path"], "Family", "Trip")
        deep_nested = os.path.join(nested, "Day")
        os.makedirs(nested, exist_ok=True)
        os.makedirs(deep_nested, exist_ok=True)
        files = [
            os.path.join(source["path"], "cover.jpg"),
            os.path.join(source["path"], "Family", "portrait.jpg"),
            os.path.join(nested, "view.jpg"),
            os.path.join(deep_nested, "detail.jpg"),
        ]
        for path in files:
            with open(path, "wb") as f:
                f.write(b"image")

        await scanner.scan_folder(source["path"], source_id=source["id"])
        catalog_routes.invalidate_folders_cache()
        result = await catalog_routes.api_folders()

        self.assertEqual(result["root"], source["path"])
        counts = {folder["path"]: folder["count"] for folder in result["folders"]}
        self.assertEqual(counts["."], 1)
        self.assertEqual(counts["Family"], 3)
        self.assertEqual(counts["Family/Trip"], 2)
        self.assertEqual(counts["Family/Trip/Day"], 1)

        shallow = await catalog_routes.api_folders(max_depth=1)
        shallow_counts = {folder["path"]: folder["count"] for folder in shallow["folders"]}
        self.assertEqual(shallow_counts["."], 1)
        self.assertEqual(shallow_counts["Family"], 3)
        self.assertEqual(shallow_counts["Family/Trip"], 2)
        self.assertNotIn("Family/Trip/Day", shallow_counts)

    async def test_folder_tree_counts_flat_source_without_nested_fetch(self):
        source = await self._source("flat-source")
        files = [
            os.path.join(source["path"], "one.jpg"),
            os.path.join(source["path"], "two.jpg"),
        ]
        for path in files:
            with open(path, "wb") as f:
                f.write(b"image")

        await scanner.scan_folder(source["path"], source_id=source["id"])
        catalog_routes.invalidate_folders_cache()
        result = await catalog_routes.api_folders()

        self.assertEqual(result["root"], source["path"])
        self.assertEqual(result["folders"], [{"path": ".", "count": 2, "depth": 0}])
        raw = sqlite3.connect(db.DB_PATH)
        try:
            prefix = source["path"].rstrip(os.sep) + os.sep
            plan_rows = raw.execute(
                "EXPLAIN QUERY PLAN SELECT COUNT(*) FROM images "
                "WHERE source_id = ? AND missing_at IS NULL AND filepath = ? || filename",
                (source["id"], prefix),
            ).fetchall()
            plan = " | ".join(row[3] for row in plan_rows)
        finally:
            raw.close()
        self.assertIn("idx_images_source_missing_filepath_filename", plan)

    async def test_source_level_folder_tree_uses_catalog_source_counts(self):
        first = await self._source("first-source")
        second = await self._source("second-source")
        for source, names in (
            (first, ("one.jpg", "nested/two.jpg")),
            (second, ("three.jpg",)),
        ):
            for name in names:
                path = os.path.join(source["path"], name)
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "wb") as f:
                    f.write(b"image")
            await scanner.scan_folder(source["path"], source_id=source["id"])

        catalog_routes.invalidate_folders_cache()
        result = await catalog_routes.api_folders(max_depth=0)

        self.assertEqual(result["root"], self.tempdir.name)
        counts = {folder["path"]: folder["count"] for folder in result["folders"]}
        self.assertEqual(counts, {"first-source": 2, "second-source": 1})

    async def test_scan_start_invalidates_active_source_cache(self):
        source = await self._source("offline-source", online=False)
        self.assertEqual(await db.get_active_source_id_set(), frozenset({source["id"]}))
        self.assertEqual(
            await catalog_repository.active_source_id_set_cached(
                db.DB_PATH,
                ttl_seconds=db.ACTIVE_SOURCE_IDS_TTL_SECONDS,
            ),
            frozenset({source["id"]}),
        )

        await db.mark_source_scan_started(source["id"])

        self.assertIn(source["id"], await db.get_active_source_id_set())
        self.assertIn(
            source["id"],
            await catalog_repository.active_source_id_set_cached(
                db.DB_PATH,
                ttl_seconds=db.ACTIVE_SOURCE_IDS_TTL_SECONDS,
            ),
        )

    async def test_catalog_summary_cache_invalidates_with_source_and_stats_changes(self):
        first_source = await self._source("first")
        await self._image(first_source["id"], "first.jpg")

        first_summary = await db.get_catalog_summary()
        first_light_summary = await db.get_catalog_light_summary()
        self.assertEqual(len(first_summary["sources"]), 1)
        self.assertEqual(len(first_light_summary["sources"]), 1)
        self.assertEqual(first_summary["stats"]["total_catalog_images"], 1)
        self.assertEqual(first_light_summary["stats"]["total_catalog_images"], 1)

        second_source = await self._source("second")
        second_summary = await db.get_catalog_summary()
        second_light_summary = await db.get_catalog_light_summary()
        self.assertEqual(len(second_summary["sources"]), 2)
        self.assertEqual(len(second_light_summary["sources"]), 2)
        self.assertEqual(second_summary["stats"]["total_catalog_images"], 1)
        self.assertEqual(second_light_summary["stats"]["total_catalog_images"], 1)

        await self._image(second_source["id"], "second.jpg")
        updated_summary = await db.get_catalog_summary()
        updated_light_summary = await db.get_catalog_light_summary()
        self.assertEqual(len(updated_summary["sources"]), 2)
        self.assertEqual(len(updated_light_summary["sources"]), 2)
        self.assertEqual(updated_summary["stats"]["total_catalog_images"], 2)
        self.assertEqual(updated_light_summary["stats"]["total_catalog_images"], 2)

    async def test_catalog_summary_facades_share_repository_caches(self):
        source = await self._source("repository-cache")
        await self._image(source["id"], "repository-cache.jpg")

        direct_summary = await catalog_repository.catalog_summary_cached(
            db.DB_PATH,
            get_stats=db.get_stats,
            refresh_source_online_states=db.refresh_source_online_states,
            ttl_seconds=db.CATALOG_CACHE_TTL_SECONDS,
        )
        direct_light_summary = await catalog_repository.catalog_light_summary_cached(
            db.DB_PATH,
            get_catalog_image_counts=db.get_catalog_image_counts,
            refresh_source_online_states=db.refresh_source_online_states,
            ttl_seconds=db.CATALOG_CACHE_TTL_SECONDS,
        )

        self.assertIs(db._catalog_sources_cache, catalog_repository._catalog_sources_cache)
        self.assertIs(db._catalog_summary_cache, catalog_repository._catalog_summary_cache)
        self.assertIs(db._catalog_light_summary_cache, catalog_repository._catalog_light_summary_cache)
        self.assertIs(await db.get_catalog_summary(), direct_summary)
        self.assertIs(await db.get_catalog_light_summary(), direct_light_summary)

        db._invalidate_catalog_cache()
        self.assertIsNone(catalog_repository._catalog_sources_cache["data"])
        self.assertIsNone(catalog_repository._catalog_summary_cache["data"])
        self.assertIsNone(catalog_repository._catalog_light_summary_cache["data"])

    async def test_visible_facet_caches_invalidate_when_cached_ids_change(self):
        source = await self._source()
        image_id = await self._image(source["id"], "dated.jpg")
        conn = await db.get_db()
        try:
            await conn.execute(
                "UPDATE images SET date_taken = ?, latitude = ?, longitude = ? WHERE id = ?",
                ("2024-01-02", 45.0, -93.0, image_id),
            )
            await conn.commit()
        finally:
            await conn.close()
        db.invalidate_stats_cache()

        date_groups = await db.get_date_groups(
            visible_thumb_size="sm",
            cache_root=thumbnails.SSD_CACHE_DIR,
        )
        self.assertEqual(date_groups, [])
        markers = await db.get_map_markers(
            visible_thumb_size="sm",
            cache_root=thumbnails.SSD_CACHE_DIR,
        )
        self.assertEqual(markers["markers"], [])

        await self._cache_entry(image_id, "sm")
        db.invalidate_cached_image_ids_cache(thumbnails.SSD_CACHE_DIR, "sm")

        date_groups = await db.get_date_groups(
            visible_thumb_size="sm",
            cache_root=thumbnails.SSD_CACHE_DIR,
        )
        self.assertEqual(date_groups[0]["date"], "2024-01")
        self.assertGreaterEqual(db.FACET_CACHE_TTL_SECONDS, 30.0)
        markers = await db.get_map_markers(
            visible_thumb_size="sm",
            cache_root=thumbnails.SSD_CACHE_DIR,
        )
        self.assertEqual([marker["id"] for marker in markers["markers"]], [image_id])

    async def test_map_markers_report_hidden_pending_thumbnails_on_all_active_catalog(self):
        source = await self._source()
        visible = await self._image(source["id"], "visible-gps.jpg")
        hidden = await self._image(source["id"], "hidden-gps.jpg")
        conn = await db.get_db()
        try:
            await conn.executemany(
                "UPDATE images SET latitude = ?, longitude = ? WHERE id = ?",
                [(45.0, -93.0, visible), (46.0, -94.0, hidden)],
            )
            await conn.commit()
        finally:
            await conn.close()
        db.invalidate_stats_cache()
        await self._cache_entry(visible, "sm")

        markers = await db.get_map_markers(
            visible_thumb_size="sm",
            cache_root=thumbnails.SSD_CACHE_DIR,
        )

        self.assertEqual([marker["id"] for marker in markers["markers"]], [visible])
        self.assertEqual(markers["total_count"], 2)
        self.assertEqual(markers["visible_count"], 1)
        self.assertEqual(markers["gps_total_count"], 2)
        self.assertEqual(markers["hidden_pending_thumbnails"], 1)

    async def test_map_markers_without_gps_preserve_visible_totals(self):
        source = await self._source()
        visible = await self._image(source["id"], "visible.jpg")
        await self._image(source["id"], "hidden.jpg")
        await self._cache_entry(visible, "sm")

        markers = await db.get_map_markers(
            visible_thumb_size="sm",
            cache_root=thumbnails.SSD_CACHE_DIR,
        )

        self.assertEqual(markers["markers"], [])
        self.assertEqual(markers["total_count"], 2)
        self.assertEqual(markers["visible_count"], 1)
        self.assertEqual(markers["gps_total_count"], 0)
        self.assertEqual(markers["hidden_pending_thumbnails"], 0)

    async def test_rankings_prefetch_runs_after_response(self):
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
            result = await asyncio.wait_for(library_routes.api_rankings(limit=2), timeout=0.5)
            self.assertEqual(len(result["images"]), 2)
            await asyncio.wait_for(started.wait(), timeout=0.5)
        finally:
            release.set()
            await asyncio.sleep(0)

    async def test_rankable_image_id_set_uses_repository_with_facade_cache(self):
        db._invalidate_rankable_image_ids_cache()
        initial = await rankings.rankable_image_id_set(db.DB_PATH)
        source = await self._source()
        kept = await self._image(source["id"], "rankable-kept.jpg")
        maybe = await self._image(source["id"], "rankable-maybe.jpg")
        missing = await self._image(source["id"], "rankable-missing.jpg", missing_at=123.0)
        rejected = await self._image(source["id"], "rankable-rejected.jpg")
        excluded_source = await self._source("rankable-excluded")
        excluded = await self._image(excluded_source["id"], "rankable-excluded.jpg")
        conn = await db.get_db()
        try:
            await conn.execute("UPDATE images SET status = 'maybe' WHERE id = ?", (maybe,))
            await conn.execute("UPDATE images SET status = 'rejected' WHERE id = ?", (rejected,))
            await conn.execute("UPDATE catalog_sources SET included = 0 WHERE id = ?", (excluded_source["id"],))
            await conn.commit()
        finally:
            await conn.close()
        db._invalidate_active_source_ids_cache()
        db._invalidate_rankable_image_ids_cache()

        expected = initial | frozenset({kept, maybe})
        self.assertEqual(await db.get_rankable_image_id_set(), expected)
        self.assertEqual(await rankings.rankable_image_id_set_cached(db.DB_PATH), expected)
        self.assertEqual(await rankings.rankable_image_id_set(db.DB_PATH), expected)
        self.assertNotIn(missing, await db.get_rankable_image_id_set())
        self.assertNotIn(rejected, await db.get_rankable_image_id_set())
        self.assertNotIn(excluded, await db.get_rankable_image_id_set())

        conn = await db.get_db()
        try:
            cursor = await conn.execute(
                "INSERT INTO images (source_id, filename, filepath, status) VALUES (?, ?, ?, 'kept')",
                (
                    source["id"],
                    "rankable-new-kept.jpg",
                    os.path.join(self.tempdir.name, "rankable-new-kept.jpg"),
                ),
            )
            await conn.commit()
            new_kept = cursor.lastrowid
        finally:
            await conn.close()
        self.assertEqual(await db.get_rankable_image_id_set(), expected)
        self.assertEqual(await rankings.rankable_image_id_set_cached(db.DB_PATH), expected)
        db._invalidate_rankable_image_ids_cache()
        self.assertEqual(await db.get_rankable_image_id_set(), expected | frozenset({new_kept}))
        self.assertEqual(
            await rankings.rankable_image_id_set_cached(db.DB_PATH),
            expected | frozenset({new_kept}),
        )

    async def test_count_rankings_with_id_filter_uses_repository_with_facade(self):
        source = await self._source()
        picked_kept = await self._image(source["id"], "count-picked-kept.jpg")
        picked_maybe = await self._image(source["id"], "count-picked-maybe.jpg")
        picked_missing = await self._image(source["id"], "count-picked-missing.jpg", missing_at=123.0)
        picked_rejected = await self._image(source["id"], "count-picked-rejected.jpg")
        unflagged = await self._image(source["id"], "count-unflagged.jpg")
        excluded_source = await self._source("count-excluded")
        picked_excluded = await self._image(excluded_source["id"], "count-picked-excluded.jpg")
        conn = await db.get_db()
        try:
            await conn.execute(
                "UPDATE images SET flag = 'picked' WHERE id IN (?, ?, ?, ?, ?)",
                (picked_kept, picked_maybe, picked_missing, picked_rejected, picked_excluded),
            )
            await conn.execute("UPDATE images SET status = 'maybe' WHERE id = ?", (picked_maybe,))
            await conn.execute("UPDATE images SET status = 'rejected' WHERE id = ?", (picked_rejected,))
            await conn.execute("UPDATE catalog_sources SET included = 0 WHERE id = ?", (excluded_source["id"],))
            await db._update_source_counts(conn, source["id"])
            await db._update_source_counts(conn, excluded_source["id"])
            await conn.commit()
        finally:
            await conn.close()
        db.invalidate_stats_cache()

        id_values = [
            picked_kept,
            picked_kept,
            picked_maybe,
            picked_missing,
            picked_rejected,
            picked_excluded,
            unflagged,
            *range(1_000_000, 1_000_905),
        ]
        conditions, params = db._ranking_filter_parts(flag="picked")
        conn = await db.get_db()
        try:
            repository_count = await rankings.count_rankings_with_id_filter_on_conn(
                conn,
                conditions,
                params,
                id_values,
            )
            facade_count = await db._count_rankings_with_id_filter(
                conn,
                conditions,
                params,
                id_values,
            )
        finally:
            await conn.close()

        self.assertEqual(repository_count, 2)
        self.assertEqual(facade_count, repository_count)
        self.assertEqual(await db.count_rankings(flag="picked", id_filter=set(id_values)), repository_count)

    async def test_count_rankings_repository_matches_facade_visible_paths(self):
        source = await self._source()
        alpha = await self._image(source["id"], "count-alpha.jpg")
        beta = await self._image(source["id"], "count-beta.jpg")
        gamma = await self._image(source["id"], "count-gamma.jpg")
        for image_id in (alpha, beta):
            await self._cache_entry(image_id, "sm")
        conn = await db.get_db()
        try:
            await conn.execute("UPDATE images SET flag = 'picked' WHERE id IN (?, ?, ?)", (alpha, beta, gamma))
            await conn.commit()
        finally:
            await conn.close()
        db.invalidate_cached_image_ids_cache()
        db.invalidate_stats_cache()
        db._ranking_count_cache.clear()

        filters = {
            "visible_thumb_size": "sm",
            "cache_root": thumbnails.SSD_CACHE_DIR,
        }
        counts = await db.get_catalog_image_counts()

        self.assertEqual(
            await rankings.count_rankings_uncached(
                db.DB_PATH,
                catalog_counts=counts,
                **filters,
            ),
            await db.count_rankings(**filters),
        )

        id_filter = {beta, gamma}
        cached_visible_ids = set(await db.get_cached_image_id_set("sm", thumbnails.SSD_CACHE_DIR))
        self.assertEqual(
            await rankings.count_rankings_uncached(
                db.DB_PATH,
                catalog_counts=counts,
                flag="picked",
                id_filter=id_filter,
                cached_visible_ids=cached_visible_ids,
                **filters,
            ),
            await db.count_rankings(flag="picked", id_filter=id_filter, **filters),
        )
        self.assertEqual(await db.count_rankings(flag="picked", id_filter=id_filter, **filters), 1)

    async def test_rankings_http_cache_hit_returns_preencoded_response(self):
        source = await self._source()
        image_id = await self._image(source["id"], "cached-response.jpg")
        await self._cache_entry(image_id, "sm")

        first = await library_routes.api_rankings(limit=1)
        self.assertIsInstance(first, dict)
        self.assertEqual([image["id"] for image in first["images"]], [image_id])

        request = Request({"type": "http", "method": "GET", "path": "/api/rankings", "headers": []})
        second = await library_routes.api_rankings(limit=1, request=request)

        self.assertIsInstance(second, Response)
        self.assertEqual(second.media_type, "application/json")
        self.assertIn(b"cached-response.jpg", second.body)
