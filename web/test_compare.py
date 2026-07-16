from test_support import *  # noqa: F401,F403
import asyncio
import random

from features.compare import semantic_pairing


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

    async def test_undo_before_propagation_suppresses_action_updates(self):
        source = await self._source()
        winner = await self._image(source["id"], "undo-first-winner.jpg")
        loser = await self._image(source["id"], "undo-first-loser.jpg")
        neighbor = await self._image(source["id"], "undo-first-neighbor.jpg")
        action_id = "undo-before-propagation"
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
        self.assertIsNotNone(await db.undo_last_comparison())

        conn = await db.get_db()
        try:
            updated = await elo_propagation._apply_propagation_deltas(
                conn,
                {neighbor: {"elo": 1200.0, "comparisons": 0, "propagated_updates": 0}},
                {neighbor: 5.0},
                action_id=action_id,
            )
            if updated:
                await conn.commit()
            else:
                await conn.rollback()
        finally:
            await conn.close()

        self.assertEqual(updated, 0)
        neighbor_row = await self._image_row(neighbor)
        self.assertEqual(neighbor_row["elo"], 1200.0)
        self.assertEqual(neighbor_row["propagated_updates"], 0)

    async def test_undo_cannot_commit_between_propagation_check_and_writes(self):
        source = await self._source()
        winner = await self._image(source["id"], "serialized-winner.jpg")
        loser = await self._image(source["id"], "serialized-loser.jpg")
        neighbor = await self._image(source["id"], "serialized-neighbor.jpg")
        action_id = "serialized-propagation"
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

        checked = asyncio.Event()
        resume = asyncio.Event()

        class PausingCursor:
            def __init__(self, cursor):
                self.cursor = cursor

            async def fetchone(self):
                row = await self.cursor.fetchone()
                checked.set()
                await resume.wait()
                return row

        class PausingConnection:
            def __init__(self, conn):
                self.conn = conn

            async def execute(self, sql, parameters=()):
                cursor = await self.conn.execute(sql, parameters)
                if sql.lstrip().startswith("SELECT 1 FROM comparisons"):
                    return PausingCursor(cursor)
                return cursor

            async def executemany(self, sql, parameters):
                return await self.conn.executemany(sql, parameters)

        raw_conn = await db.get_db()
        conn = PausingConnection(raw_conn)

        async def apply_propagation():
            try:
                updated = await elo_propagation._apply_propagation_deltas(
                    conn,
                    {neighbor: {"elo": 1200.0, "comparisons": 0, "propagated_updates": 0}},
                    {neighbor: 5.0},
                    action_id=action_id,
                )
                if updated:
                    await raw_conn.commit()
                else:
                    await raw_conn.rollback()
                return updated
            finally:
                await raw_conn.close()

        propagation_task = asyncio.create_task(apply_propagation())
        await asyncio.wait_for(checked.wait(), timeout=1.0)
        undo_task = asyncio.create_task(db.undo_last_comparison())
        try:
            await asyncio.wait_for(asyncio.shield(undo_task), timeout=0.1)
        except TimeoutError:
            pass
        resume.set()
        await propagation_task
        undo = await asyncio.wait_for(undo_task, timeout=1.0)

        self.assertIsNotNone(undo)
        neighbor_row = await self._image_row(neighbor)
        self.assertEqual(neighbor_row["elo"], 1200.0)
        self.assertEqual(neighbor_row["propagated_updates"], 0)
        conn = await db.get_db()
        try:
            orphan_count = await (await conn.execute(
                "SELECT COUNT(*) AS count FROM propagation_updates WHERE action_id = ?",
                (action_id,),
            )).fetchone()
        finally:
            await conn.close()
        self.assertEqual(orphan_count["count"], 0)

    async def test_direct_undo_keeps_action_retryable_when_a_rating_drifted(self):
        source = await self._source()
        winner = await self._image(source["id"], "relative-undo-winner.jpg")
        loser = await self._image(source["id"], "relative-undo-loser.jpg")
        await db.record_comparison(
            winner,
            loser,
            "swiss",
            1200.0,
            1200.0,
            1210.0,
            1190.0,
            action_id="relative-direct-undo",
        )
        conn = await db.get_db()
        try:
            await conn.execute(
                "UPDATE images SET elo = elo + 7 WHERE id = ?",
                (winner,),
            )
            await conn.commit()
        finally:
            await conn.close()

        undo = await compare_routes.compare_undo()

        self.assertFalse(undo["ok"])
        self.assertTrue(undo["partial"])
        self.assertEqual(undo["comparisons_undone"], 0)
        self.assertEqual(undo["skipped_drift"], [winner])
        unchanged_winner = await self._image_row(winner)
        unchanged_loser = await self._image_row(loser)
        self.assertAlmostEqual(unchanged_winner["elo"], 1217.0)
        self.assertEqual(unchanged_winner["comparisons"], 1)
        self.assertAlmostEqual(unchanged_loser["elo"], 1190.0)
        self.assertEqual(unchanged_loser["comparisons"], 1)

        conn = await db.get_db()
        try:
            comparison_count = await (await conn.execute("SELECT COUNT(*) AS c FROM comparisons")).fetchone()
            await conn.execute("UPDATE images SET elo = 1210 WHERE id = ?", (winner,))
            await conn.commit()
        finally:
            await conn.close()
        self.assertEqual(comparison_count["c"], 1)

        retry = await compare_routes.compare_undo()

        self.assertTrue(retry["ok"])
        self.assertEqual((await self._image_row(winner))["elo"], 1200.0)
        self.assertEqual((await self._image_row(loser))["elo"], 1200.0)

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

    async def test_concurrent_comparisons_compute_from_serialized_ratings(self):
        source = await self._source()
        winner = await self._image(source["id"], "concurrent-winner.jpg")
        loser = await self._image(source["id"], "concurrent-loser.jpg")
        counts = await db.get_catalog_image_counts()

        await asyncio.gather(
            ratings.record_active_comparison(
                db.DB_PATH,
                winner_id=winner,
                loser_id=loser,
                mode="swiss",
                action_id="concurrent-1",
                catalog_counts=counts,
            ),
            ratings.record_active_comparison(
                db.DB_PATH,
                winner_id=winner,
                loser_id=loser,
                mode="swiss",
                action_id="concurrent-2",
                catalog_counts=counts,
            ),
        )

        conn = await db.get_db()
        try:
            image_rows = await (await conn.execute(
                "SELECT id, elo, comparisons FROM images WHERE id IN (?, ?) ORDER BY id",
                (winner, loser),
            )).fetchall()
            comparisons = await (await conn.execute(
                "SELECT elo_before_winner, elo_before_loser FROM comparisons "
                "WHERE action_id IN ('concurrent-1', 'concurrent-2') ORDER BY id",
            )).fetchall()
        finally:
            await conn.close()

        self.assertEqual([row["comparisons"] for row in image_rows], [2, 2])
        self.assertEqual(len(comparisons), 2)
        self.assertNotEqual(
            comparisons[0]["elo_before_winner"],
            comparisons[1]["elo_before_winner"],
        )
        self.assertNotEqual(
            comparisons[0]["elo_before_loser"],
            comparisons[1]["elo_before_loser"],
        )

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

    async def test_mosaic_next_rejects_malformed_ids(self):
        response = await compare_routes.mosaic_next(ids="abc")
        self.assertEqual(response.status_code, 400)

        response = await compare_routes.mosaic_next(ids="")
        self.assertEqual(response.status_code, 400)

        response = await compare_routes.mosaic_next(ids="1,0")
        self.assertEqual(response.status_code, 400)

        response = await compare_routes.mosaic_next(ids="1" * (compare_routes.MAX_SCOPED_IDS_LENGTH + 1))
        self.assertEqual(response.status_code, 400)

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

    async def test_smart_collection_restricts_mosaic_and_duel_to_resolved_images(self):
        source = await self._source()
        first = await self._image(source["id"], "smart-first.jpg", elo=1500)
        second = await self._image(source["id"], "smart-second.jpg", elo=1400)
        outside = await self._image(source["id"], "smart-outside.jpg", elo=1300)
        for image_id in (first, second):
            await db.set_image_flag(image_id, "picked")
        for image_id in (first, second, outside):
            await self._cache_entry(image_id, "sm")
            await self._cache_entry(image_id, "md")
        smart = await db.create_collection(
            name="Picked Refine",
            query=json.dumps({"flag": "picked", "sort": "elo"}),
        )

        mosaic = await compare_routes.mosaic_next(n=2, collection_id=smart["id"])
        duel = await compare_routes.compare_next(n=1, collection_id=smart["id"])
        duel_ids = {
            duel["pairs"][0]["left"]["id"],
            duel["pairs"][0]["right"]["id"],
        }

        self.assertEqual({image["id"] for image in mosaic["images"]}, {first, second})
        self.assertEqual(mosaic["total_images"], 2)
        self.assertEqual(duel_ids, {first, second})
        self.assertEqual(duel["total_images"], 2)
        self.assertNotIn(outside, duel_ids)

    async def test_refine_restricts_mosaic_and_duel_to_import_batch(self):
        source = await self._source()
        first = await self._image(source["id"], "batch-first.jpg", elo=1500)
        second = await self._image(source["id"], "batch-second.jpg", elo=1400)
        outside = await self._image(source["id"], "outside.jpg", elo=1300)
        for image_id in (first, second, outside):
            await self._cache_entry(image_id, "sm")
            await self._cache_entry(image_id, "md")
        conn = await db.get_db()
        try:
            cursor = await conn.execute("INSERT INTO import_batches(name) VALUES ('Refine batch')")
            batch_id = int(cursor.lastrowid)
            await conn.executemany(
                "INSERT INTO import_batch_images(batch_id, image_id, filepath) VALUES (?, ?, ?)",
                [(batch_id, first, "batch-first.jpg"), (batch_id, second, "batch-second.jpg")],
            )
            await conn.commit()
        finally:
            await conn.close()

        mosaic = await compare_routes.mosaic_next(n=2, import_batch=batch_id)
        duel = await compare_routes.compare_next(n=1, import_batch=batch_id)

        self.assertEqual({image["id"] for image in mosaic["images"]}, {first, second})
        duel_ids = {duel["pairs"][0]["left"]["id"], duel["pairs"][0]["right"]["id"]}
        self.assertEqual(duel_ids, {first, second})
        self.assertNotIn(outside, duel_ids)

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

    async def test_compare_next_rejects_malformed_ids(self):
        response = await compare_routes.compare_next(ids="1,bad")
        self.assertEqual(response.status_code, 400)

        response = await compare_routes.compare_next(ids=",,,")
        self.assertEqual(response.status_code, 400)

    async def test_compare_and_mosaic_reject_mixed_malformed_ids(self):
        for route in (compare_routes.compare_next, compare_routes.mosaic_next):
            response = await route(ids="abc,-1,0")
            self.assertEqual(response.status_code, 400)

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

    async def test_refine_semantic_duel_prefers_within_cluster_partner(self):
        source = await self._source()
        sunset_a = await self._image(source["id"], "sunset-a.jpg")
        sunset_b = await self._image(source["id"], "sunset-b.jpg")
        screen_a = await self._image(source["id"], "screen-a.jpg")
        screen_b = await self._image(source["id"], "screen-b.jpg")
        image_ids = [sunset_a, sunset_b, screen_a, screen_b]
        for image_id in image_ids:
            await self._cache_entry(image_id, "sm")

        matrix = np.array(
            [
                [1.0, 0.0],
                [0.96, 0.08],
                [0.0, 1.0],
                [0.08, 0.96],
            ],
            dtype=np.float32,
        )

        async def fake_get_matrix(_model_key=None):
            return image_ids, matrix

        def fake_get_index(_model_key=None):
            return {image_id: idx for idx, image_id in enumerate(image_ids)}

        old_rate = semantic_pairing.SEMANTIC_DUEL_EXPLORATION_RATE
        elo_propagation.embed_cache.get_matrix = fake_get_matrix
        elo_propagation.embed_cache.get_index = fake_get_index
        semantic_pairing.SEMANTIC_DUEL_EXPLORATION_RATE = 0.0
        try:
            random.seed(8)
            result = await compare_routes.mosaic_next(n=2, strategy="random")
        finally:
            semantic_pairing.SEMANTIC_DUEL_EXPLORATION_RATE = old_rate

        selected_ids = {image["id"] for image in result["images"]}
        self.assertEqual(result["pairing"], "semantic")
        self.assertIn(selected_ids, ({sunset_a, sunset_b}, {screen_a, screen_b}))

    async def test_refine_semantic_duel_keeps_exploration_floor(self):
        image_ids = [1, 2, 3, 4]
        candidates = [
            {"id": image_id, "elo": 1200.0, "comparisons": 0, "propagated_updates": 0}
            for image_id in image_ids
        ]
        matrix = np.array(
            [
                [1.0, 0.0],
                [0.98, 0.02],
                [0.0, 1.0],
                [0.02, 0.98],
            ],
            dtype=np.float32,
        )
        context = semantic_pairing.SemanticContext(
            matrix=matrix,
            index_by_id={image_id: idx for idx, image_id in enumerate(image_ids)},
        )

        random.seed(41)
        strategy_draws = 0
        for _ in range(100):
            _sample, pairing_mode = await compare_service._semantic_duel_sample(
                candidates,
                2,
                strategy="random",
                grid_elo=0,
                context=context,
            )
            if pairing_mode == "strategy":
                strategy_draws += 1

        self.assertGreaterEqual(strategy_draws, 10)
        self.assertLessEqual(strategy_draws, 35)

    async def test_refine_mosaic_keeps_strategy_pairing_with_embeddings(self):
        source = await self._source()
        warm_ids = [
            await self._image(source["id"], f"warm-{idx}.jpg")
            for idx in range(3)
        ]
        cool_ids = [
            await self._image(source["id"], f"cool-{idx}.jpg")
            for idx in range(3)
        ]
        image_ids = warm_ids + cool_ids
        for image_id in image_ids:
            await self._cache_entry(image_id, "sm")

        matrix = np.array(
            [
                [1.0, 0.0],
                [0.92, 0.08],
                [0.88, 0.12],
                [0.0, 1.0],
                [0.08, 0.92],
                [0.12, 0.88],
            ],
            dtype=np.float32,
        )

        async def fake_get_matrix(_model_key=None):
            return image_ids, matrix

        def fake_get_index(_model_key=None):
            return {image_id: idx for idx, image_id in enumerate(image_ids)}

        elo_propagation.embed_cache.get_matrix = fake_get_matrix
        elo_propagation.embed_cache.get_index = fake_get_index

        random.seed(12)
        result = await compare_routes.mosaic_next(n=3, strategy="random")
        selected = [image["id"] for image in result["images"]]

        self.assertEqual(result["pairing"], "strategy")
        self.assertEqual(len(selected), 3)

    async def test_refine_mosaic_strategies_remain_distinct_on_synthetic_embeddings(self):
        image_ids = list(range(1, 19))
        candidates = [
            {
                "id": image_id,
                "elo": 1300.0 if image_id >= 10 else 100.0,
                "comparisons": 0 if image_id <= 9 else 20,
                "propagated_updates": 0,
            }
            for image_id in image_ids
        ]
        cluster = np.tile(np.array([[1.0] + [0.0] * 9], dtype=np.float32), (9, 1))
        spread = np.eye(10, dtype=np.float32)[1:]
        matrix = np.vstack([cluster, spread])

        async def fake_get_matrix(_model_key=None):
            return image_ids, matrix

        def fake_get_index(_model_key=None):
            return {image_id: idx for idx, image_id in enumerate(image_ids)}

        elo_propagation.embed_cache.get_matrix = fake_get_matrix
        elo_propagation.embed_cache.get_index = fake_get_index

        random.seed(22)
        diverse, diverse_pairing = await compare_service._refine_sample(
            candidates,
            9,
            strategy="diverse",
        )
        random.seed(22)
        explore, explore_pairing = await compare_service._refine_sample(
            candidates,
            9,
            strategy="explore",
        )
        random.seed(22)
        compete, compete_pairing = await compare_service._refine_sample(
            candidates,
            9,
            strategy="compete",
            grid_elo=1300.0,
        )

        diverse_ids = {image["id"] for image in diverse}
        explore_ids = {image["id"] for image in explore}
        compete_ids = {image["id"] for image in compete}
        diverse_indices = [image_ids.index(image_id) for image_id in diverse_ids]
        similarities = matrix[diverse_indices] @ matrix[diverse_indices].T
        high_similarity_pairs = [
            similarities[left, right]
            for left in range(len(diverse_indices))
            for right in range(left + 1, len(diverse_indices))
        ]
        semantic_neighbor_cells = {
            index
            for left in range(len(diverse_indices))
            for right in range(left + 1, len(diverse_indices))
            if similarities[left, right] > semantic_pairing.MOSAIC_NEIGHBOR_THRESHOLD
            for index in (left, right)
        }

        self.assertEqual((diverse_pairing, explore_pairing, compete_pairing), ("strategy", "strategy", "strategy"))
        self.assertNotEqual(diverse_ids, explore_ids)
        self.assertNotEqual(diverse_ids, compete_ids)
        self.assertNotEqual(explore_ids, compete_ids)
        self.assertTrue(all(sim < semantic_pairing.MOSAIC_DIVERSE_SPREAD_THRESHOLD for sim in high_similarity_pairs))
        self.assertLessEqual(len(semantic_neighbor_cells), semantic_pairing.MOSAIC_DIVERSE_NEIGHBOR_CELL_LIMIT)

    async def test_refine_semantic_unavailable_embeddings_fallback_is_identical(self):
        source = await self._source()
        image_ids = [
            await self._image(source["id"], f"image-{idx}.jpg", elo=1200 + idx)
            for idx in range(6)
        ]
        for image_id in image_ids:
            await self._cache_entry(image_id, "sm")

        settings.save_settings({"refine_semantic_pairing": False})
        random.seed(91)
        expected = await compare_service.mosaic_next_impl(n=4, strategy="random")

        async def missing_matrix(_model_key=None):
            return None, None

        def missing_index(_model_key=None):
            return {}

        settings.save_settings({"refine_semantic_pairing": True})
        compare_service._interaction_response_cache.clear()
        elo_propagation.embed_cache.get_matrix = missing_matrix
        elo_propagation.embed_cache.get_index = missing_index
        random.seed(91)
        actual = await compare_service.mosaic_next_impl(n=4, strategy="random")

        self.assertEqual(actual, expected)
        self.assertEqual(actual["pairing"], "strategy")

    async def test_refine_two_card_mosaic_does_not_reuse_semantic_response_cache(self):
        source = await self._source()
        image_ids = [
            await self._image(source["id"], f"semantic-cache-{idx}.jpg", elo=1200 + idx)
            for idx in range(4)
        ]
        for image_id in image_ids:
            await self._cache_entry(image_id, "sm")

        matrix = np.array(
            [
                [1.0, 0.0],
                [0.96, 0.08],
                [0.0, 1.0],
                [0.08, 0.96],
            ],
            dtype=np.float32,
        )

        async def fake_get_matrix(_model_key=None):
            return image_ids, matrix

        def fake_get_index(_model_key=None):
            return {image_id: idx for idx, image_id in enumerate(image_ids)}

        old_rate = semantic_pairing.SEMANTIC_DUEL_EXPLORATION_RATE
        elo_propagation.embed_cache.get_matrix = fake_get_matrix
        elo_propagation.embed_cache.get_index = fake_get_index
        semantic_pairing.SEMANTIC_DUEL_EXPLORATION_RATE = 0.0
        compare_service._interaction_response_cache.clear()
        try:
            random.seed(8)
            semantic = await compare_service.mosaic_next_impl(n=2, strategy="explore")
            self.assertEqual(semantic["pairing"], "semantic")
            self.assertFalse(compare_service._interaction_response_cache)

            settings.save_settings({"refine_semantic_pairing": False})
            random.seed(8)
            fallback = await compare_service.mosaic_next_impl(n=2, strategy="explore")
        finally:
            semantic_pairing.SEMANTIC_DUEL_EXPLORATION_RATE = old_rate
            compare_service._interaction_response_cache.clear()

        self.assertEqual(fallback["pairing"], "strategy")

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

    async def test_filtered_compare_window_is_smaller_than_default_window(self):
        self.assertLess(compare_service._FILTERED_SWISS_PAIR_WINDOW, compare_service._SWISS_PAIR_WINDOW)
        self.assertGreaterEqual(compare_service._FILTERED_SWISS_PAIR_WINDOW, 256)

    async def test_filtered_mosaic_window_is_bounded_but_not_tiny(self):
        self.assertLess(compare_service._FILTERED_MOSAIC_WINDOW, compare_service._MOSAIC_EXPLORE_WINDOW)
        self.assertGreaterEqual(compare_service._FILTERED_MOSAIC_WINDOW, 128)
        self.assertGreaterEqual(compare_service._MOSAIC_EXPLORE_WINDOW, 768)
