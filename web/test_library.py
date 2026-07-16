from test_support import *  # noqa: F401,F403
import embed_cache
import shutil
import unittest.mock
from fastapi.testclient import TestClient
from features.collections import routes as collection_routes
from features.library import taste as taste_service
from features.sync import hashing as sync_hashing


class LibraryTests(BackendTestCase):
    def _set_taste_blend(self, *, enabled=True, min_signal=1):
        settings.save_settings({
            **settings.get_settings(),
            "ranking_taste_blend": enabled,
            "taste_blend_min_signal": min_signal,
        })
        cache_events.invalidate_rankings_cache()
        taste_service.invalidate_taste_cache()

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

    async def test_filter_options_respect_folder_scope(self):
        source = await self._source()
        beach = await self._image(source["id"], "Beach/one.jpg")
        city = await self._image(source["id"], "City/two.jpg")
        conn = await db.get_db()
        try:
            await conn.execute(
                "UPDATE images SET camera_make = ?, camera_model = ? WHERE id = ?",
                ("Fuji", "X-T5", beach),
            )
            await conn.execute(
                "UPDATE images SET camera_make = ?, camera_model = ? WHERE id = ?",
                ("Canon", "R5", city),
            )
            await conn.commit()
        finally:
            await conn.close()
        db.invalidate_stats_cache()

        options = await db.get_filter_options(folder=os.path.join(source["path"], "Beach"))

        self.assertEqual(options["cameras"], [{"camera": "Fuji X-T5", "count": 1}])

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

        def repository_shape(row):
            data = dict(row)
            data.pop("has_caption", None)
            data.pop("caption_tags", None)
            return data

        self.assertEqual([repository_shape(row) for row in facade_rows], [dict(row) for row in repository_rows])
        self.assertEqual([row["has_caption"] for row in facade_rows], [False, False])
        self.assertEqual([row["caption_tags"] for row in facade_rows], [[], []])
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
            [repository_shape(row) for row in id_filtered_facade_rows],
            [dict(row) for row in id_filtered_repository_rows],
        )
        self.assertEqual([row["has_caption"] for row in id_filtered_facade_rows], [False])
        self.assertEqual([row["caption_tags"] for row in id_filtered_facade_rows], [[]])
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

    async def test_rankings_visibility_is_mode_aware(self):
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

        with unittest.mock.patch.dict(
            os.environ,
            {"PHOTOARCHIVE_MODE": "satellite", "PHOTOARCHIVE_HUB_URL": "http://stub-hub"},
        ):
            library_service._rankings_response_cache.clear()
            satellite_result = await library_routes.api_rankings(limit=10, sort="elo")

        self.assertEqual(
            [img["id"] for img in satellite_result["images"]],
            [visible_high, hidden, visible_low],
        )
        self.assertEqual(satellite_result["visible_images"], 3)
        self.assertEqual(satellite_result["total_images"], 3)
        self.assertEqual(satellite_result["hidden_pending_thumbnails"], 0)
        satellite_cards = {card["id"]: card for card in satellite_result["images"]}
        self.assertIn("thumb_url", satellite_cards[visible_high])
        self.assertNotIn("thumb_url", satellite_cards[hidden])

    async def test_all_photos_includes_stale_hub_mirror_rows_across_dates(self):
        """All Photos must surface hub:// mirror rows even when denormalized counts drifted to 0.

        Taste blend caps the fetch at visible_images from SUM(active_image_count). A
        satellite with stale hub:// counters would otherwise show only local/recent imports.
        """
        import time as _time

        from data.repositories import catalog as catalog_repository

        conn = await db.get_db()
        try:
            await conn.execute(
                "INSERT INTO catalog_sources "
                "(path, display_name, included, online, image_count, active_image_count, "
                "created_at, last_seen_at) VALUES (?, ?, 1, 1, 3, 3, ?, ?)",
                ("/tmp/allphotos-local", "Local imports", _time.time(), _time.time()),
            )
            local_id = int((await (await conn.execute("SELECT last_insert_rowid()")).fetchone())[0])
            await conn.execute(
                "INSERT INTO catalog_sources "
                "(path, display_name, included, online, image_count, active_image_count, "
                "created_at, last_seen_at) VALUES (?, ?, 1, 1, 0, 0, ?, ?)",
                (catalog_repository.HUB_MIRROR_SOURCE_PATH, "Hub library", _time.time(), _time.time()),
            )
            hub_id = int((await (await conn.execute("SELECT last_insert_rowid()")).fetchone())[0])
            for index in range(3):
                await conn.execute(
                    "INSERT INTO images "
                    "(source_id, filename, filepath, status, file_ext, elo, date_taken, "
                    "hub_remote, comparisons) "
                    "VALUES (?, ?, ?, 'kept', '.jpg', ?, ?, 0, 0)",
                    (
                        local_id,
                        f"local-{index}.jpg",
                        f"/tmp/allphotos-local/local-{index}.jpg",
                        1500 + index,
                        f"2026-07-12T1{index}:00:00",
                    ),
                )
            for index in range(24):
                year = 2020 + (index // 12)
                month = (index % 12) + 1
                await conn.execute(
                    "INSERT INTO images "
                    "(source_id, filename, filepath, status, file_ext, elo, date_taken, "
                    "hub_remote, hub_image_id, comparisons) "
                    "VALUES (?, ?, ?, 'kept', '.jpg', ?, ?, 1, ?, 0)",
                    (
                        hub_id,
                        f"hub-{index}.jpg",
                        f"/hub/archive/hub-{index}.jpg",
                        1100 + index,
                        f"{year}-{month:02d}-15T12:00:00",
                        9000 + index,
                    ),
                )
            await conn.commit()
        finally:
            await conn.close()

        db.invalidate_stats_cache()
        library_service._rankings_response_cache.clear()
        library_service._blended_rankings_order_cache.clear()
        self._set_taste_blend(enabled=True, min_signal=1)

        async def forced_blend(_db_sort):
            return {
                "active": True,
                "cache_key": ("allphotos", "forced-blend"),
                "scores": {image_id: float(image_id) for image_id in range(1, 200)},
            }

        with unittest.mock.patch.dict(
            os.environ,
            {"PHOTOARCHIVE_MODE": "satellite", "PHOTOARCHIVE_HUB_URL": "http://stub-hub"},
        ), unittest.mock.patch.object(
            library_service,
            "_ranking_taste_blend_context",
            new=forced_blend,
        ):
            result = await library_routes.api_rankings(limit=100, offset=0, sort="elo")

        self.assertEqual(result["visible_images"], 27)
        self.assertEqual(result["total_images"], 27)
        self.assertEqual(len(result["images"]), 27)
        months = {
            str(card.get("date_taken") or "")[:7]
            for card in result["images"]
            if card.get("date_taken")
        }
        self.assertIn("2020-01", months)
        self.assertIn("2021-12", months)
        self.assertIn("2026-07", months)
        counts = await db.get_catalog_image_counts()
        self.assertEqual(counts["active_images"], 27)
        self.assertEqual(counts["total_catalog_images"], 27)

    async def test_hub_source_scope_filters_by_source_instead_of_remote_filepath(self):
        import time as _time

        from data.repositories import catalog as catalog_repository

        conn = await db.get_db()
        try:
            await conn.execute(
                "INSERT INTO catalog_sources "
                "(path, display_name, included, online, image_count, active_image_count, "
                "created_at, last_seen_at) VALUES (?, 'Hub library', 1, 1, 2, 2, ?, ?)",
                (catalog_repository.HUB_MIRROR_SOURCE_PATH, _time.time(), _time.time()),
            )
            hub_id = int((await (await conn.execute("SELECT last_insert_rowid()")).fetchone())[0])
            await conn.executemany(
                "INSERT INTO images "
                "(source_id, filename, filepath, status, elo, hub_remote, hub_image_id) "
                "VALUES (?, ?, ?, 'kept', 1200, 1, ?)",
                [
                    (hub_id, "one.jpg", "/remote/library/one.jpg", 101),
                    (hub_id, "two.jpg", "/another/root/two.jpg", 102),
                ],
            )
            await conn.commit()
        finally:
            await conn.close()

        db.invalidate_stats_cache()
        library_service._rankings_response_cache.clear()
        with unittest.mock.patch.dict(
            os.environ,
            {"PHOTOARCHIVE_MODE": "satellite", "PHOTOARCHIVE_HUB_URL": "http://stub-hub"},
        ):
            result = await library_routes.api_rankings(
                limit=10,
                folder=catalog_repository.HUB_MIRROR_SOURCE_PATH,
            )

        self.assertEqual(result["visible_images"], 2)
        self.assertEqual({card["filename"] for card in result["images"]}, {"one.jpg", "two.jpg"})

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

    async def test_taste_blend_warm_pages_reuse_predictions_and_precomputed_order(self):
        self._set_taste_blend(enabled=True, min_signal=1)
        source = await self._source()
        config = settings.active_embedding_config()
        conn = await db.get_db()
        try:
            await conn.execute(
                "INSERT OR IGNORE INTO embedding_models "
                "(model_key, model_id, revision, dimension) VALUES (?, ?, ?, ?)",
                (config["model_key"], config["model_id"], config["revision"], 2),
            )
            for index in range(160):
                cursor = await conn.execute(
                    "INSERT INTO images "
                    "(source_id, filename, filepath, elo, comparisons, status) "
                    "VALUES (?, ?, ?, ?, ?, 'kept')",
                    (
                        source["id"],
                        f"perf-taste-{index:03d}.jpg",
                        os.path.join(self.tempdir.name, f"perf-taste-{index:03d}.jpg"),
                        1200 + (index % 20),
                        1 if index < 6 else 0,
                    ),
                )
                image_id = int(cursor.lastrowid)
                direction = 1.0 if index < 3 or index % 2 == 0 else -1.0
                vector = np.asarray([direction, 0.0], dtype=np.float32)
                await conn.execute(
                    "INSERT INTO embeddings_by_model "
                    "(model_key, image_id, embedding, dimension) VALUES (?, ?, ?, ?)",
                    (config["model_key"], image_id, vector.tobytes(), 2),
                )
                await conn.execute(
                    "INSERT INTO cache_entries "
                    "(cache_root, size, image_id, path, source_signature, size_bytes, last_accessed, created_at) "
                    "VALUES (?, 'sm', ?, ?, ?, 1, 1, 1)",
                    (
                        thumbnails.SSD_CACHE_DIR,
                        image_id,
                        os.path.join(self.tempdir.name, f"sm-{image_id}.jpg"),
                        f"perf-{image_id}",
                    ),
                )
            cursor = await conn.execute(
                "SELECT id FROM images WHERE filename LIKE 'perf-taste-%' ORDER BY id"
            )
            ids = [int(row["id"]) for row in await cursor.fetchall()]
            await conn.executemany(
                "INSERT INTO comparisons "
                "(winner_id, loser_id, mode, elo_before_winner, elo_before_loser, action_id) "
                "VALUES (?, ?, 'swiss', 1200, 1200, ?)",
                [(ids[index % 3], ids[3 + (index % 3)], f"perf-{index}") for index in range(5)],
            )
            await db._update_source_counts(conn, source["id"])
            await conn.commit()
        finally:
            await conn.close()

        db.invalidate_stats_cache()
        db.invalidate_cached_image_ids_cache()
        embed_cache.invalidate()
        taste_service.invalidate_taste_cache()
        cache_events.invalidate_rankings_cache()
        compute_calls = 0
        ranking_limits = []
        original_compute = taste_service._compute_scaled_scores
        original_get_rankings = library_service._get_rankings

        def counted_compute(*args, **kwargs):
            nonlocal compute_calls
            compute_calls += 1
            return original_compute(*args, **kwargs)

        async def counted_get_rankings(**kwargs):
            ranking_limits.append(int(kwargs.get("limit") or 0))
            return await original_get_rankings(**kwargs)

        taste_service._compute_scaled_scores = counted_compute
        library_service._get_rankings = counted_get_rankings
        try:
            first = await library_routes.api_rankings(limit=20, offset=0, sort="elo")
            library_service._rankings_response_cache.clear()
            second = await library_routes.api_rankings(limit=20, offset=20, sort="elo")
        finally:
            taste_service._compute_scaled_scores = original_compute
            library_service._get_rankings = original_get_rankings

        self.assertEqual(len(first["images"]), 20)
        self.assertEqual(len(second["images"]), 20)
        self.assertEqual(compute_calls, 1)
        self.assertEqual(sum(limit > 20 for limit in ranking_limits), 1)

    async def test_taste_blend_uses_confidence_weighted_display_score_for_elo_sort(self):
        self._set_taste_blend(enabled=True, min_signal=1)
        source = await self._source()
        winners = [await self._image(source["id"], f"winner-{idx}.jpg", comparisons=1) for idx in range(3)]
        losers = [await self._image(source["id"], f"loser-{idx}.jpg", comparisons=1) for idx in range(3)]
        measured = await self._image(source["id"], "measured.jpg", elo=1500, comparisons=10)
        predicted = await self._image(source["id"], "predicted.jpg", elo=1000, comparisons=0)
        for image_id in winners + [predicted]:
            await self._embedding(image_id, [1.0, 0.0])
        for image_id in losers + [measured]:
            await self._embedding(image_id, [-1.0, 0.0])
        await self._comparison_rows(list(zip(winners, losers)) + [(winners[0], losers[0]), (winners[1], losers[1])])
        await self._cache_entry(measured, "sm")
        await self._cache_entry(predicted, "sm")

        result = await library_routes.api_rankings(limit=10, sort="elo")
        cards = {image["id"]: image for image in result["images"]}

        self.assertEqual([image["id"] for image in result["images"][:2]], [predicted, measured])
        self.assertAlmostEqual(cards[measured]["display_score"], 1500.0, places=1)
        self.assertAlmostEqual(cards[predicted]["display_score"], 1600.0, places=1)
        self.assertEqual(cards[measured]["rank_basis"], "measured")
        self.assertEqual(cards[predicted]["rank_basis"], "predicted")
        self.assertEqual(cards[measured]["taste_weight"], 0.0)
        self.assertEqual(cards[predicted]["taste_weight"], 1.0)

    async def test_taste_blend_signal_floor_disables_global_blend(self):
        self._set_taste_blend(enabled=True, min_signal=6)
        source = await self._source()
        winners = [await self._image(source["id"], f"floor-winner-{idx}.jpg", comparisons=1) for idx in range(3)]
        losers = [await self._image(source["id"], f"floor-loser-{idx}.jpg", comparisons=1) for idx in range(3)]
        high_elo = await self._image(source["id"], "high-elo.jpg", elo=1500, comparisons=0)
        taste_match = await self._image(source["id"], "taste-match.jpg", elo=1000, comparisons=0)
        for image_id in winners + [taste_match]:
            await self._embedding(image_id, [1.0, 0.0])
        for image_id in losers + [high_elo]:
            await self._embedding(image_id, [-1.0, 0.0])
        await self._comparison_rows(list(zip(winners, losers)) + [(winners[0], losers[0]), (winners[1], losers[1])])
        await self._cache_entry(high_elo, "sm")
        await self._cache_entry(taste_match, "sm")

        result = await library_routes.api_rankings(limit=10, sort="elo")

        self.assertEqual([image["id"] for image in result["images"][:2]], [high_elo, taste_match])
        self.assertNotIn("display_score", result["images"][0])
        self.assertNotIn("taste_weight", result["images"][0])

    async def test_taste_blend_unavailable_keeps_pure_elo_response(self):
        self._set_taste_blend(enabled=True, min_signal=1)
        source = await self._source()
        high = await self._image(source["id"], "unavailable-high.jpg", elo=1500)
        low = await self._image(source["id"], "unavailable-low.jpg", elo=1000)
        await self._cache_entry(high, "sm")
        await self._cache_entry(low, "sm")

        result = await library_routes.api_rankings(limit=10, sort="elo")

        self.assertEqual([image["id"] for image in result["images"]], [high, low])
        self.assertNotIn("display_score", result["images"][0])

    async def test_taste_blend_rank_basis_thresholds(self):
        self.assertEqual(taste_service.rank_basis(taste_service.elo_confidence(0)), "predicted")
        self.assertEqual(taste_service.rank_basis(taste_service.elo_confidence(2)), "predicted")
        self.assertEqual(taste_service.rank_basis(taste_service.elo_confidence(5)), "blended")
        self.assertEqual(taste_service.rank_basis(taste_service.elo_confidence(8)), "measured")
        self.assertEqual(taste_service.rank_basis(taste_service.elo_confidence(10)), "measured")

    async def test_taste_blend_off_flag_matches_pure_elo_response(self):
        self._set_taste_blend(enabled=False, min_signal=1)
        source = await self._source()
        winners = [await self._image(source["id"], f"off-winner-{idx}.jpg", comparisons=1) for idx in range(3)]
        losers = [await self._image(source["id"], f"off-loser-{idx}.jpg", comparisons=1) for idx in range(3)]
        high_elo = await self._image(source["id"], "off-high.jpg", elo=1500)
        taste_match = await self._image(source["id"], "off-taste-match.jpg", elo=1000)
        for image_id in winners + [taste_match]:
            await self._embedding(image_id, [1.0, 0.0])
        for image_id in losers + [high_elo]:
            await self._embedding(image_id, [-1.0, 0.0])
        await self._comparison_rows(list(zip(winners, losers)) + [(winners[0], losers[0]), (winners[1], losers[1])])
        await self._cache_entry(high_elo, "sm")
        await self._cache_entry(taste_match, "sm")

        pure = await library_routes.api_rankings(limit=10, sort="elo")
        old_taste_vector = taste_service.taste_vector

        async def fail_taste_vector():
            raise AssertionError("taste vector should not be read when ranking_taste_blend is off")

        taste_service.taste_vector = fail_taste_vector
        library_service._rankings_response_cache.clear()
        try:
            without_taste = await library_routes.api_rankings(limit=10, sort="elo")
        finally:
            taste_service.taste_vector = old_taste_vector

        self.assertEqual(
            {key: value for key, value in without_taste.items() if key != "latency_ms"},
            {key: value for key, value in pure.items() if key != "latency_ms"},
        )
        self.assertEqual([image["id"] for image in pure["images"][:2]], [high_elo, taste_match])
        self.assertNotIn("display_score", pure["images"][0])

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

    async def test_collection_scoped_rankings_sort_and_compose_with_flag(self):
        source = await self._source()
        lower = await self._image(source["id"], "collection-lower.jpg", elo=1200)
        picked = await self._image(source["id"], "collection-picked.jpg", elo=1500)
        outside = await self._image(source["id"], "outside.jpg", elo=1800)
        await db.set_image_flag(picked, "picked")
        await db.set_image_flag(outside, "picked")
        collection = await db.create_collection(name="Timeline scope", image_ids=[lower, picked])
        await self._cache_entry(lower, "sm")
        await self._cache_entry(picked, "sm")

        scoped = await library_routes.api_rankings(
            limit=10,
            sort="elo",
            collection_id=collection["id"],
        )
        self.assertEqual([image["id"] for image in scoped["images"]], [picked, lower])
        self.assertEqual(scoped["total_images"], 2)
        self.assertEqual(await db.count_rankings(collection_id=collection["id"]), 2)

        picked_only = await library_routes.api_rankings(
            limit=10,
            sort="elo",
            flag="picked",
            collection_id=collection["id"],
        )
        self.assertEqual([image["id"] for image in picked_only["images"]], [picked])
        self.assertEqual(picked_only["total_images"], 1)
        self.assertEqual(await db.count_rankings(flag="picked", collection_id=collection["id"]), 1)

    async def test_smart_collection_scope_resolves_rankings_and_timeline_histogram(self):
        source = await self._source()
        picked = await self._image(source["id"], "smart-picked.jpg", elo=1500)
        await self._image(source["id"], "smart-outside.jpg", elo=1800)
        await db.set_image_flag(picked, "picked")
        await self._cache_entry(picked, "sm")
        conn = await db.get_db()
        try:
            await conn.execute(
                "UPDATE images SET date_taken = ? WHERE id = ?",
                ("2025-04-12 09:30:00", picked),
            )
            await conn.commit()
        finally:
            await conn.close()
        smart = await collection_routes.api_create_collection(
            collection_routes.CreateCollectionBody(
                name="Picked smart scope",
                query={"flag": "picked", "sort": "elo"},
            )
        )

        scoped = await library_routes.api_rankings(
            limit=10,
            sort="elo",
            collection_id=smart["collection"]["id"],
        )
        histogram = await library_routes.api_date_histogram(
            collection_id=smart["collection"]["id"],
        )

        self.assertEqual([image["id"] for image in scoped["images"]], [picked])
        self.assertEqual(scoped["total_images"], 1)
        self.assertEqual(histogram["months"], [{"month": "2025-04", "count": 1, "cover_id": picked}])
        self.assertEqual(histogram["total"], 1)

    async def test_smart_collection_map_markers_compose_with_flag_filter(self):
        source = await self._source()
        scoped_picked = await self._image(source["id"], "smart-map-picked.jpg")
        scoped_rejected = await self._image(source["id"], "smart-map-rejected.jpg")
        outside_picked = await self._image(source["id"], "outside-map-picked.jpg")
        conn = await db.get_db()
        try:
            await conn.executemany(
                "UPDATE images SET latitude = ?, longitude = ?, camera_make = ?, camera_model = ?, flag = ? WHERE id = ?",
                [
                    (45.0, -93.0, "Fuji", "X-T5", "picked", scoped_picked),
                    (46.0, -94.0, "Fuji", "X-T5", "rejected", scoped_rejected),
                    (47.0, -95.0, "Canon", "R5", "picked", outside_picked),
                ],
            )
            await conn.commit()
        finally:
            await conn.close()
        await self._cache_entry(scoped_picked, "sm")
        smart = await collection_routes.api_create_collection(
            collection_routes.CreateCollectionBody(
                name="Fuji map scope",
                query={"camera": "Fuji X-T5", "sort": "elo"},
            )
        )

        grid = await library_routes.api_rankings(
            limit=10,
            flag="picked",
            collection_id=smart["collection"]["id"],
        )
        markers = await library_routes.api_map_markers(
            flag="picked",
            collection_id=smart["collection"]["id"],
        )

        self.assertEqual([image["id"] for image in grid["images"]], [scoped_picked])
        self.assertEqual([marker["id"] for marker in markers["markers"]], [scoped_picked])

    async def test_smart_collection_scope_constrains_filter_options(self):
        source = await self._source()
        picked = await self._image(source["id"], "smart-filter-picked.jpg")
        outside = await self._image(source["id"], "smart-filter-outside.jpg")
        await db.set_image_flag(picked, "picked")
        conn = await db.get_db()
        try:
            await conn.execute(
                "UPDATE images SET camera_make = 'Fuji', camera_model = 'X-T5' WHERE id = ?",
                (picked,),
            )
            await conn.execute(
                "UPDATE images SET camera_make = 'Canon', camera_model = 'R5' WHERE id = ?",
                (outside,),
            )
            await conn.commit()
        finally:
            await conn.close()
        smart = await collection_routes.api_create_collection(
            collection_routes.CreateCollectionBody(
                name="Picked camera",
                query={"flag": "picked", "sort": "elo"},
            )
        )

        options = await library_routes.api_filter_options(
            collection_id=smart["collection"]["id"],
        )

        self.assertEqual(options["cameras"], [{"camera": "Fuji X-T5", "count": 1}])

    async def test_static_and_smart_collection_scopes_omit_archive_sort_quality(self):
        source = await self._source()
        image_id = await self._image(source["id"], "collection-quality.jpg")
        await db.set_image_flag(image_id, "picked")
        await self._cache_entry(image_id, "sm")
        static = await db.create_collection(name="Static quality", image_ids=[image_id])
        smart = await collection_routes.api_create_collection(
            collection_routes.CreateCollectionBody(
                name="Smart quality",
                query={"flag": "picked", "sort": "elo"},
            )
        )

        static_page = await library_routes.api_rankings(collection_id=static["id"])
        smart_page = await library_routes.api_rankings(
            collection_id=smart["collection"]["id"],
        )

        self.assertNotIn("sort_quality", static_page)
        self.assertNotIn("sort_quality", smart_page)

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
            await conn.execute("UPDATE images SET elo = ? WHERE id = ?", (1700, january_first))
            await conn.commit()
        finally:
            await conn.close()
        db.invalidate_stats_cache()

        response = await library_routes.api_date_histogram()

        self.assertEqual(response["months"], [
            {"month": "2025-03", "count": 1, "cover_id": march},
            {"month": "2025-02", "count": 1, "cover_id": february},
            {"month": "2025-01", "count": 2, "cover_id": january_first},
        ])
        self.assertEqual(response["undated"], 1)
        self.assertEqual(response["total"], 5)

    async def test_date_histogram_works_without_optional_month_index(self):
        source = await self._source()
        image_id = await self._image(source["id"], "indexed-later.jpg")
        conn = await db.get_db()
        try:
            await conn.execute(
                "UPDATE images SET date_taken = ? WHERE id = ?",
                ("2025-01-03 10:00:00", image_id),
            )
            await conn.execute("DROP INDEX IF EXISTS idx_images_active_month_source")
            await conn.commit()
        finally:
            await conn.close()

        response = await library_routes.api_date_histogram(stacks="expanded")

        self.assertEqual(response["months"], [{"month": "2025-01", "count": 1, "cover_id": image_id}])
        self.assertEqual(response["total"], 1)

    async def test_date_histogram_caches_and_clears_with_facet_invalidation(self):
        source = await self._source()
        image_id = await self._image(source["id"], "cached-hist.jpg")
        conn = await db.get_db()
        try:
            await conn.execute(
                "UPDATE images SET date_taken = ? WHERE id = ?",
                ("2025-04-03 10:00:00", image_id),
            )
            await conn.commit()
        finally:
            await conn.close()
        db.invalidate_stats_cache()
        db._date_histogram_cache.clear()

        first = await library_routes.api_date_histogram()
        cache_key = db._facet_cache_key()
        self.assertIn(cache_key, db._date_histogram_cache)
        self.assertEqual(db._date_histogram_cache[cache_key]["data"], first)

        second = await library_routes.api_date_histogram()
        self.assertEqual(second, first)

        cache_events.invalidate_facet_caches()
        self.assertNotIn(cache_key, db._date_histogram_cache)

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
                "SELECT filepath, missing_at FROM images WHERE source_id = ? ORDER BY filepath",
                (source["id"],),
            )
            scan_rows = await cursor.fetchall()
        finally:
            await conn.close()
        self.assertEqual(
            [(row["filepath"], row["missing_at"] is not None) for row in scan_rows],
            [(first_path, False), (second_path, True)],
        )
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

    async def test_rescan_cascades_master_availability_to_virtual_copy_only(self):
        source = await self._source("scan-vc-source")
        filepath = os.path.join(source["path"], "master.jpg")
        anchor_path = os.path.join(source["path"], "anchor.jpg")
        with open(filepath, "wb") as handle:
            handle.write(b"master")
        with open(anchor_path, "wb") as handle:
            handle.write(b"anchor")
        await scanner.scan_folder(source["path"], source_id=source["id"])

        conn = await db.get_db()
        try:
            master = await (await conn.execute(
                "SELECT id FROM images WHERE filepath = ? AND vc_of IS NULL",
                (filepath,),
            )).fetchone()
            cursor = await conn.execute(
                "INSERT INTO images "
                "(source_id, filename, filepath, status, file_ext, file_size, file_modified_at, missing_at, vc_of) "
                "VALUES (?, 'copy-name.jpg', ?, 'kept', '.copy', 999, 1, NULL, ?)",
                (source["id"], filepath, master["id"]),
            )
            copy_id = cursor.lastrowid
            await conn.commit()
        finally:
            await conn.close()

        os.remove(filepath)
        await scanner.scan_folder(source["path"], source_id=source["id"])

        missing_master = await self._image_row(master["id"])
        copy = await self._image_row(copy_id)
        self.assertIsNotNone(missing_master["missing_at"])
        self.assertEqual(copy["missing_at"], missing_master["missing_at"])
        self.assertEqual(copy["filename"], "copy-name.jpg")
        self.assertEqual(copy["file_ext"], ".copy")
        self.assertEqual(copy["file_size"], 999)
        self.assertEqual(copy["file_modified_at"], 1.0)

        with open(filepath, "wb") as handle:
            handle.write(b"master")
        await scanner.scan_folder(source["path"], source_id=source["id"])

        restored_copy = await self._image_row(copy_id)
        self.assertIsNone((await self._image_row(master["id"]))["missing_at"])
        self.assertIsNone(restored_copy["missing_at"])
        self.assertEqual(restored_copy["filename"], "copy-name.jpg")
        self.assertEqual(restored_copy["file_ext"], ".copy")
        self.assertEqual(restored_copy["file_size"], 999)
        self.assertEqual(restored_copy["file_modified_at"], 1.0)

    async def test_rescan_hashes_all_rematch_candidates_before_writing(self):
        source = await self._source("scan-rematch-lock-source")
        new_paths = [
            os.path.join(source["path"], "new-first.jpg"),
            os.path.join(source["path"], "new-second.jpg"),
        ]
        contents = [b"first", b"other"]
        for path, content in zip(new_paths, contents):
            with open(path, "wb") as handle:
                handle.write(content)

        conn = await db.get_db()
        try:
            for index, (path, content) in enumerate(zip(new_paths, contents)):
                await conn.execute(
                    "INSERT INTO images "
                    "(source_id, filename, filepath, status, file_size, file_modified_at, "
                    "content_hash, missing_at) VALUES (?, ?, ?, 'kept', ?, 1, ?, 100)",
                    (
                        source["id"],
                        f"old-{index}.jpg",
                        os.path.join(source["path"], f"old-{index}.jpg"),
                        len(content),
                        sync_hashing.compute_content_hash(path),
                    ),
                )
            await conn.commit()
        finally:
            await conn.close()

        real_compute_content_hash = sync_hashing.compute_content_hash
        lock_probes = 0

        def compute_hash_with_write_probe(path):
            nonlocal lock_probes
            probe = sqlite3.connect(db.DB_PATH, timeout=0.05)
            try:
                probe.execute("BEGIN IMMEDIATE")
                probe.rollback()
                lock_probes += 1
            finally:
                probe.close()
            return real_compute_content_hash(path)

        rows = [
            (os.path.basename(path), path, ".jpg", len(content), 1)
            for path, content in zip(new_paths, contents)
        ]
        with unittest.mock.patch.object(
            sync_hashing,
            "compute_content_hash",
            side_effect=compute_hash_with_write_probe,
        ):
            await catalog_repository.insert_images_batch(
                db.DB_PATH,
                rows,
                source_id=source["id"],
            )

        self.assertEqual(lock_probes, 2)

    async def test_rescan_does_not_rematch_when_new_paths_claim_same_missing_image(self):
        source = await self._source("scan-rematch-collision-source")
        old_path = os.path.join(source["path"], "old.jpg")
        new_paths = [
            os.path.join(source["path"], "new-first.jpg"),
            os.path.join(source["path"], "new-second.jpg"),
        ]
        for path in new_paths:
            with open(path, "wb") as handle:
                handle.write(b"shared identity")

        conn = await db.get_db()
        try:
            cursor = await conn.execute(
                "INSERT INTO images "
                "(source_id, filename, filepath, status, file_size, file_modified_at, "
                "content_hash, missing_at) VALUES (?, 'old.jpg', ?, 'kept', ?, 1, ?, 100)",
                (
                    source["id"],
                    old_path,
                    len(b"shared identity"),
                    sync_hashing.compute_content_hash(new_paths[0]),
                ),
            )
            missing_id = int(cursor.lastrowid)
            await conn.commit()
        finally:
            await conn.close()

        await catalog_repository.insert_images_batch(
            db.DB_PATH,
            [
                (os.path.basename(path), path, ".jpg", len(b"shared identity"), 1)
                for path in new_paths
            ],
            source_id=source["id"],
        )

        conn = await db.get_db()
        try:
            rows = await (await conn.execute(
                "SELECT id, filepath, missing_at FROM images WHERE source_id = ? ORDER BY id",
                (source["id"],),
            )).fetchall()
        finally:
            await conn.close()

        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["id"], missing_id)
        self.assertEqual(rows[0]["filepath"], old_path)
        self.assertEqual(rows[0]["missing_at"], 100.0)
        self.assertEqual({row["filepath"] for row in rows[1:]}, set(new_paths))
        self.assertTrue(all(row["missing_at"] is None for row in rows[1:]))

    async def test_rescan_rematches_renamed_file_by_content_identity(self):
        source = await self._source("scan-rename-source")
        old_path = os.path.join(source["path"], "old-name.jpg")
        anchor_path = os.path.join(source["path"], "anchor.jpg")
        with open(old_path, "wb") as handle:
            handle.write(b"irreplaceable ranked photo")
        with open(anchor_path, "wb") as handle:
            handle.write(b"keeps scan nonempty")
        await scanner.scan_folder(source["path"], source_id=source["id"])

        conn = await db.get_db()
        try:
            original = await (await conn.execute(
                "SELECT id FROM images WHERE filepath = ? AND vc_of IS NULL",
                (old_path,),
            )).fetchone()
            image_id = int(original["id"])
            await conn.execute(
                "UPDATE images SET elo = 1675, comparisons = 42, content_hash = ? WHERE id = ?",
                (sync_hashing.compute_content_hash(old_path), image_id),
            )
            copy = await conn.execute(
                "INSERT INTO images "
                "(source_id, filename, filepath, status, elo, vc_of) "
                "VALUES (?, 'copy-label.jpg', ?, 'kept', 1490, ?)",
                (source["id"], old_path, image_id),
            )
            copy_id = int(copy.lastrowid)
            await conn.commit()
        finally:
            await conn.close()

        holding_path = os.path.join(self.tempdir.name, "holding.jpg")
        os.rename(old_path, holding_path)
        await scanner.scan_folder(source["path"], source_id=source["id"])
        self.assertIsNotNone((await self._image_row(image_id))["missing_at"])

        renamed_path = os.path.join(source["path"], "new-name.jpg")
        os.rename(holding_path, renamed_path)
        await scanner.scan_folder(source["path"], source_id=source["id"])

        rematched = await self._image_row(image_id)
        self.assertEqual(rematched["filepath"], renamed_path)
        self.assertEqual(rematched["filename"], "new-name.jpg")
        self.assertEqual(rematched["elo"], 1675.0)
        self.assertEqual(rematched["comparisons"], 42)
        self.assertIsNone(rematched["missing_at"])
        rematched_copy = await self._image_row(copy_id)
        self.assertEqual(rematched_copy["filepath"], renamed_path)
        self.assertEqual(rematched_copy["filename"], "copy-label.jpg")
        self.assertEqual(rematched_copy["elo"], 1490.0)
        conn = await db.get_db()
        try:
            count = await (await conn.execute(
                "SELECT COUNT(*) AS count FROM images WHERE source_id = ? AND vc_of IS NULL",
                (source["id"],),
            )).fetchone()
        finally:
            await conn.close()
        self.assertEqual(count["count"], 2)

    async def test_rescan_rematches_moved_file_by_unambiguous_metadata(self):
        source = await self._source("scan-move-source")
        first_dir = os.path.join(source["path"], "first")
        second_dir = os.path.join(source["path"], "second")
        os.makedirs(first_dir)
        os.makedirs(second_dir)
        old_path = os.path.join(first_dir, "same-name.jpg")
        anchor_path = os.path.join(source["path"], "anchor.jpg")
        with open(old_path, "wb") as handle:
            handle.write(b"metadata identity")
        with open(anchor_path, "wb") as handle:
            handle.write(b"anchor")
        await scanner.scan_folder(source["path"], source_id=source["id"])

        conn = await db.get_db()
        try:
            original = await (await conn.execute(
                "SELECT id FROM images WHERE filepath = ?",
                (old_path,),
            )).fetchone()
            image_id = int(original["id"])
            await conn.execute("UPDATE images SET elo = 1540 WHERE id = ?", (image_id,))
            await conn.commit()
        finally:
            await conn.close()

        holding_path = os.path.join(self.tempdir.name, "same-name.jpg")
        os.rename(old_path, holding_path)
        await scanner.scan_folder(source["path"], source_id=source["id"])
        new_path = os.path.join(second_dir, "same-name.jpg")
        os.rename(holding_path, new_path)
        await scanner.scan_folder(source["path"], source_id=source["id"])

        rematched = await self._image_row(image_id)
        self.assertEqual(rematched["filepath"], new_path)
        self.assertEqual(rematched["elo"], 1540.0)
        self.assertIsNone(rematched["missing_at"])

    async def test_rescan_does_not_guess_between_ambiguous_missing_rows(self):
        source = await self._source("scan-ambiguous-source")
        new_path = os.path.join(source["path"], "new", "same-name.jpg")
        os.makedirs(os.path.dirname(new_path))
        with open(new_path, "wb") as handle:
            handle.write(b"same")
        modified_at = os.stat(new_path).st_mtime
        conn = await db.get_db()
        try:
            for folder in ("old-a", "old-b"):
                await conn.execute(
                    "INSERT INTO images "
                    "(source_id, filename, filepath, status, file_size, file_modified_at, missing_at) "
                    "VALUES (?, 'same-name.jpg', ?, 'kept', 4, ?, 100)",
                    (source["id"], os.path.join(source["path"], folder, "same-name.jpg"), modified_at),
                )
            await conn.commit()
        finally:
            await conn.close()

        await catalog_repository.insert_images_batch(
            db.DB_PATH,
            [("same-name.jpg", new_path, ".jpg", 4, modified_at)],
            source_id=source["id"],
        )

        conn = await db.get_db()
        try:
            rows = await (await conn.execute(
                "SELECT id, filepath, missing_at FROM images WHERE source_id = ? ORDER BY id",
                (source["id"],),
            )).fetchall()
        finally:
            await conn.close()
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[-1]["filepath"], new_path)
        self.assertIsNone(rows[-1]["missing_at"])

    async def test_rescan_preserves_photos_in_real_and_fenced_directories(self):
        source = await self._source("scan-junk-fence")
        visible_path = os.path.join(source["path"], "visible.jpg")
        fenced_dir = os.path.join(source["path"], "PreviewCache")
        os.makedirs(fenced_dir)
        fenced_path = os.path.join(fenced_dir, "previously-indexed.jpg")
        real_directory_names = ("Backups", "Presets", "Luminar", ".favorites")
        real_paths = []
        for directory_name in real_directory_names:
            directory = os.path.join(source["path"], directory_name)
            os.makedirs(directory)
            real_paths.append(os.path.join(directory, "keeper.jpg"))
        for filepath in (visible_path, fenced_path, *real_paths):
            with open(filepath, "wb") as handle:
                handle.write(b"photo")

        real_image_ids = [
            await self._image(source["id"], os.path.join(directory_name, "keeper.jpg"))
            for directory_name in real_directory_names
        ]
        fenced_id = await self._image(
            source["id"],
            os.path.join("PreviewCache", "previously-indexed.jpg"),
        )

        await scanner.scan_folder(source["path"], source_id=source["id"])

        for image_id in real_image_ids:
            self.assertIsNone((await self._image_row(image_id))["missing_at"])
        self.assertIsNone((await self._image_row(fenced_id))["missing_at"])

    async def test_empty_online_source_scan_preserves_existing_images_and_warns(self):
        source = await self._source("scan-empty-online")
        filepaths = [
            os.path.join(source["path"], "first.jpg"),
            os.path.join(source["path"], "second.jpg"),
        ]
        for filepath in filepaths:
            with open(filepath, "wb") as handle:
                handle.write(b"photo")
        await scanner.scan_folder(source["path"], source_id=source["id"])

        for filepath in filepaths:
            os.remove(filepath)
        await scanner.scan_folder(source["path"], source_id=source["id"])

        conn = await db.get_db()
        try:
            rows = await (await conn.execute(
                "SELECT filepath, missing_at FROM images WHERE source_id = ? ORDER BY filepath",
                (source["id"],),
            )).fetchall()
        finally:
            await conn.close()

        self.assertEqual([row["filepath"] for row in rows], filepaths)
        self.assertTrue(all(row["missing_at"] is None for row in rows))
        self.assertIn("scan found no files", scanner.scan_state["warning"].lower())

    async def test_scan_preserves_catalog_when_source_goes_offline_before_finalize(self):
        source = await self._source("scan-offline")
        filepath = os.path.join(source["path"], "preserve.jpg")
        with open(filepath, "wb") as handle:
            handle.write(b"photo")
        await scanner.scan_folder(source["path"], source_id=source["id"])
        shutil.rmtree(source["path"])

        with unittest.mock.patch.object(catalog_routes.log, "error") as error_log:
            await catalog_routes._run_scan(source["path"], int(source["id"]))

        conn = await db.get_db()
        try:
            image = await (await conn.execute(
                "SELECT missing_at FROM images WHERE filepath = ?", (filepath,)
            )).fetchone()
            refreshed_source = await (await conn.execute(
                "SELECT online FROM catalog_sources WHERE id = ?", (source["id"],)
            )).fetchone()
        finally:
            await conn.close()

        self.assertIsNone(image["missing_at"])
        self.assertEqual(refreshed_source["online"], 0)
        self.assertIn("existing catalog entries were preserved", scanner.scan_state["error"])
        error_log.assert_called_once()

    async def test_scan_quarantines_zero_byte_file_and_logs_only_first_detection(self):
        source = await self._source("scan-zero")
        filepath = os.path.join(source["path"], "empty.jpg")
        open(filepath, "wb").close()

        with unittest.mock.patch.object(db.log, "warning") as warning:
            await scanner.scan_folder(source["path"], source_id=source["id"])
            await scanner.scan_folder(source["path"], source_id=source["id"])

        conn = await db.get_db()
        try:
            image = await (await conn.execute(
                "SELECT id, missing_at FROM images WHERE filepath = ?", (filepath,)
            )).fetchone()
        finally:
            await conn.close()

        self.assertIsNotNone(image)
        self.assertIsNotNone(image["missing_at"])
        self.assertEqual(warning.call_count, 1)
        self.assertIn(f"image_id={image['id']}", warning.call_args.args[0] % warning.call_args.args[1:])

    async def test_catalog_browse_returns_safe_actionable_error_and_logs_internal_detail(self):
        secret = "/mnt/private/permission-detail"
        with unittest.mock.patch.object(
            catalog_routes.os,
            "scandir",
            side_effect=OSError(secret),
        ), unittest.mock.patch.object(catalog_routes.log, "exception") as error_log:
            result = await catalog_routes.api_catalog_browse(self.tempdir.name)

        self.assertNotIn(secret, result["error"])
        self.assertIn("check that the source is connected", result["error"])
        error_log.assert_called_once()

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

    async def test_folder_tree_payload_assembles_source_hierarchy(self):
        sources = [
            {
                "id": 1,
                "path": "/archive/main",
                "display_name": "Main Archive",
                "online": 1,
                "active_image_count": 6,
            },
            {
                "id": 2,
                "path": "/archive/offline",
                "display_name": "Offline Archive",
                "online": 0,
                "active_image_count": 1,
            },
        ]
        counts = {
            1: {
                "/archive/main": 1,
                "/archive/main/Family": 2,
                "/archive/main/Family/Trip": 2,
                "/archive/main/Family/Trip/Day": 1,
            },
            2: {
                "/archive/offline/Scans": 1,
            },
        }

        result = catalog_routes.build_folder_tree_payload_from_rows(sources, counts, max_depth=2)

        main = result["sources"][0]
        self.assertEqual(main["display_name"], "Main Archive")
        self.assertTrue(main["online"])
        self.assertTrue(main["reveal_available"])
        self.assertEqual(main["count"], 1)
        self.assertEqual(main["total_count"], 6)
        family = main["folders"][0]
        self.assertEqual(family["path"], "/archive/main/Family")
        self.assertEqual(family["count"], 2)
        self.assertEqual(family["total_count"], 5)
        trip = family["children"][0]
        self.assertEqual(trip["path"], "/archive/main/Family/Trip")
        self.assertEqual(trip["count"], 2)
        self.assertEqual(trip["total_count"], 3)
        self.assertEqual(trip["children"], [])

        offline = result["sources"][1]
        self.assertFalse(offline["online"])
        self.assertEqual(offline["folders"][0]["name"], "Scans")

    async def test_folder_tree_marks_hub_mirrors_non_revealable(self):
        result = catalog_routes.build_folder_tree_payload_from_rows(
            [{"id": 7, "path": "hub://", "display_name": "Hub library", "online": 1}],
            {7: {"hub://Family": 2}},
        )

        source = result["sources"][0]
        self.assertEqual(source["path"], "hub://")
        self.assertFalse(source["reveal_available"])
        self.assertFalse(source["folders"][0]["reveal_available"])
        self.assertEqual(source["folders"][0]["source_id"], 7)

    async def test_absolute_nested_folder_scope_matches_subtree(self):
        source = await self._source("scope-source")
        paths = [
            os.path.join(source["path"], "Family", "root.jpg"),
            os.path.join(source["path"], "Family", "Trip", "wide.jpg"),
            os.path.join(source["path"], "Family", "Trip", "Day", "detail.jpg"),
            os.path.join(source["path"], "Family", "Other", "aside.jpg"),
        ]
        for path in paths:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as f:
                f.write(b"image")

        await scanner.scan_folder(source["path"], source_id=source["id"])
        parent = os.path.join(source["path"], "Family")
        nested = os.path.join(source["path"], "Family", "Trip")

        parent_count = await db.count_rankings(folder=parent)
        nested_count = await db.count_rankings(folder=nested)
        nested_rows = await db.get_rankings(limit=10, sort="filename", folder=nested)

        self.assertEqual(parent_count, 4)
        self.assertEqual(nested_count, 2)
        self.assertLess(nested_count, parent_count)
        self.assertEqual([row["filename"] for row in nested_rows], ["detail.jpg", "wide.jpg"])

    async def test_single_folder_filter_shape_regression(self):
        folder = "/archive/Family"

        string_conditions, string_params = rankings.ranking_filter_parts(folder=folder)
        list_conditions, list_params = rankings.ranking_filter_parts(folder=[folder])

        self.assertEqual(list_conditions, string_conditions)
        self.assertEqual(list_params, string_params)

    async def test_multi_folder_scope_ors_rankings_counts_groups_histogram_and_export(self):
        source = await self._source("multi-folder-source")
        files = [
            ("Alpha", "one.jpg"),
            ("Alpha", "two.jpg"),
            ("Beta", "three.jpg"),
            ("Gamma", "four.jpg"),
        ]
        for folder_name, filename in files:
            path = os.path.join(source["path"], folder_name, filename)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as f:
                f.write(b"image")

        await scanner.scan_folder(source["path"], source_id=source["id"])
        alpha = os.path.join(source["path"], "Alpha")
        beta = os.path.join(source["path"], "Beta")

        alpha_count = await db.count_rankings(folder=alpha)
        beta_count = await db.count_rankings(folder=beta)
        multi_count = await db.count_rankings(folder=[alpha, beta])
        rows = await db.get_rankings(limit=10, sort="filename", folder=[alpha, beta])
        for row in rows:
            await self._cache_entry(row["id"], "sm")
        cache_events.invalidate_rankings_cache()
        db.invalidate_cached_image_ids_cache()
        counts = await db.scope_counts(folder=[alpha, beta])
        histogram = await db.date_histogram(folder=[alpha, beta])
        groups = await db.get_date_groups(folder=[alpha, beta])

        self.assertEqual(alpha_count, 2)
        self.assertEqual(beta_count, 1)
        self.assertEqual(multi_count, alpha_count + beta_count)
        self.assertEqual(counts["total"], multi_count)
        self.assertEqual(histogram["total"], multi_count)
        self.assertEqual(sum(group["count"] for group in groups), multi_count)
        self.assertEqual([row["filename"] for row in rows], ["one.jpg", "three.jpg", "two.jpg"])

        def probe():
            client = TestClient(app_module.app)
            try:
                params = [("folder", alpha), ("folder", beta), ("sort", "filename"), ("limit", "10")]
                rankings_response = client.get("/api/rankings", params=params)
                counts_response = client.get("/api/counts", params=[("folder", alpha), ("folder", beta)])
                histogram_response = client.get("/api/date-histogram", params=[("folder", alpha), ("folder", beta)])
                groups_response = client.get("/api/date-groups", params=[("folder", alpha), ("folder", beta)])
                export_response = client.get("/api/export", params=params + [("format", "json")])
                return rankings_response, counts_response, histogram_response, groups_response, export_response
            finally:
                client.close()

        rankings_response, counts_response, histogram_response, groups_response, export_response = await asyncio.to_thread(probe)

        self.assertEqual(rankings_response.status_code, 200)
        self.assertEqual(rankings_response.json()["total_images"], multi_count)
        self.assertEqual(counts_response.status_code, 200)
        self.assertEqual(counts_response.json()["total"], multi_count)
        self.assertEqual(histogram_response.status_code, 200)
        self.assertEqual(histogram_response.json()["total"], multi_count)
        self.assertEqual(groups_response.status_code, 200)
        self.assertEqual(sum(group["count"] for group in groups_response.json()["groups"]), multi_count)
        self.assertEqual(export_response.status_code, 200)
        self.assertEqual([row["filename"] for row in export_response.json()], ["one.jpg", "three.jpg", "two.jpg"])

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
