import asyncio
from datetime import datetime
import inspect
import os
import sqlite3
import sys
import tempfile
import unittest
from zoneinfo import ZoneInfo

import numpy as np
from starlette.requests import Request
from starlette.responses import Response

sys.path.insert(0, os.path.dirname(__file__))
import app as app_module  # noqa: E402
import db  # noqa: E402
import embedding_worker  # noqa: E402
import elo_propagation  # noqa: E402
import scanner  # noqa: E402


class JsonRequest:
    def __init__(self, payload):
        self.payload = payload

    async def json(self):
        return self.payload


class BadJsonRequest:
    async def json(self):
        raise ValueError("bad json")


class HeaderRequest:
    def __init__(self, headers=None):
        self.headers = headers or {}


class BackendRankingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.old_db_path = db.DB_PATH
        self.old_schedule_pairing_propagation = app_module._schedule_pairing_propagation
        self.old_get_matrix = elo_propagation.embed_cache.get_matrix
        self.old_get_index = elo_propagation.embed_cache.get_index
        self.old_get_vector = elo_propagation.embed_cache.get_vector
        self.old_encode_text = embedding_worker.encode_text
        self.old_ensure_model_loaded_for_search = embedding_worker.ensure_model_loaded_for_search
        self.old_embedding_manual_pause = embedding_worker.get_worker_status()["manual_pause"]
        self.old_prefetch_images = app_module.thumbnails.prefetch_images
        self.old_schedule_full_image_cache = app_module.thumbnails.schedule_full_image_cache
        self.old_has_cached_fast = app_module.thumbnails.has_cached_fast
        self.old_fast_disk_path_entry = app_module.thumbnails.fast_disk_path_entry
        self.old_fast_disk_read_entry = app_module.thumbnails.fast_disk_read_entry
        self.old_thumbnail_persistent_conn = app_module.thumbnails._persistent_conn
        self.old_settings_path = app_module.settings.SETTINGS_PATH
        self.old_settings_state = app_module.settings._settings

        db.DB_PATH = os.path.join(self.tempdir.name, "photoarchive-test.db")
        app_module.thumbnails._persistent_conn = None
        app_module.settings.SETTINGS_PATH = os.path.join(self.tempdir.name, "settings.local.json")
        app_module.settings._settings = None
        db.invalidate_stats_cache()
        db.invalidate_cached_image_ids_cache()
        db._invalidate_past_matchups_cache()
        db.clear_filter_options_cache()
        await db.init_db()
        app_module._pairing_cache.update({"data": None, "valid": False})
        app_module._matchups_cache.update({"data": None, "valid": False})
        app_module._visible_matchups_cache.clear()
        app_module._visible_pairing_candidates_cache.clear()
        app_module._interaction_response_cache.clear()
        app_module._invalidate_rankings_cache()
        app_module._clear_folders_cache()
        app_module._invalidate_cache_status_cache()
        app_module._invalidate_settings_response_cache()

        def close_scheduled(coro):
            coro.close()

        async def noop_prefetch(*_args, **_kwargs):
            return 0

        async def no_model_load_for_search():
            return False

        app_module._schedule_pairing_propagation = close_scheduled
        app_module.thumbnails.prefetch_images = noop_prefetch
        embedding_worker.ensure_model_loaded_for_search = no_model_load_for_search

    async def asyncTearDown(self):
        app_module._schedule_pairing_propagation = self.old_schedule_pairing_propagation
        elo_propagation.embed_cache.get_matrix = self.old_get_matrix
        elo_propagation.embed_cache.get_index = self.old_get_index
        elo_propagation.embed_cache.get_vector = self.old_get_vector
        embedding_worker.encode_text = self.old_encode_text
        embedding_worker.ensure_model_loaded_for_search = self.old_ensure_model_loaded_for_search
        if self.old_embedding_manual_pause:
            embedding_worker.pause_embedding_worker()
        else:
            embedding_worker.resume_embedding_worker()
        app_module.thumbnails.prefetch_images = self.old_prefetch_images
        app_module.thumbnails.schedule_full_image_cache = self.old_schedule_full_image_cache
        app_module.thumbnails.has_cached_fast = self.old_has_cached_fast
        app_module.thumbnails.fast_disk_path_entry = self.old_fast_disk_path_entry
        app_module.thumbnails.fast_disk_read_entry = self.old_fast_disk_read_entry
        if app_module.thumbnails._persistent_conn is not None:
            app_module.thumbnails._persistent_conn.close()
        app_module.thumbnails._persistent_conn = self.old_thumbnail_persistent_conn
        app_module.settings.SETTINGS_PATH = self.old_settings_path
        app_module.settings._settings = self.old_settings_state
        db.DB_PATH = self.old_db_path
        db.invalidate_stats_cache()
        db.invalidate_cached_image_ids_cache()
        db._invalidate_past_matchups_cache()
        db.clear_filter_options_cache()
        app_module._visible_matchups_cache.clear()
        app_module._visible_pairing_candidates_cache.clear()
        app_module._interaction_response_cache.clear()
        app_module._invalidate_rankings_cache()
        app_module._clear_folders_cache()
        app_module._invalidate_cache_status_cache()
        app_module._invalidate_settings_response_cache()
        self.tempdir.cleanup()

    async def _source(self, name="catalog", *, online=True):
        path = os.path.join(self.tempdir.name, name)
        os.makedirs(path, exist_ok=True)
        source = await db.add_or_restore_source(path)
        if not online:
            conn = await db.get_db()
            try:
                await conn.execute("UPDATE catalog_sources SET online = 0 WHERE id = ?", (source["id"],))
                await conn.commit()
            finally:
                await conn.close()
            db.invalidate_stats_cache()
        return source

    async def _image(
        self,
        source_id,
        filename,
        *,
        elo=1200.0,
        comparisons=0,
        propagated_updates=0,
        missing_at=None,
    ):
        filepath = os.path.join(self.tempdir.name, f"{source_id}-{filename}")
        conn = await db.get_db()
        try:
            cursor = await conn.execute(
                "INSERT INTO images "
                "(source_id, filename, filepath, elo, comparisons, propagated_updates, status, missing_at) "
                "VALUES (?, ?, ?, ?, ?, ?, 'kept', ?)",
                (source_id, filename, filepath, elo, comparisons, propagated_updates, missing_at),
            )
            await db._update_source_counts(conn, source_id)
            await conn.commit()
            image_id = cursor.lastrowid
        finally:
            await conn.close()
        db.invalidate_stats_cache()
        return image_id

    async def _image_row(self, image_id):
        conn = await db.get_db()
        try:
            cursor = await conn.execute("SELECT * FROM images WHERE id = ?", (image_id,))
            return dict(await cursor.fetchone())
        finally:
            await conn.close()

    async def _cache_entry(self, image_id, size="sm"):
        conn = await db.get_db()
        try:
            now = 12345.0 + int(image_id)
            await conn.execute(
                "INSERT OR REPLACE INTO cache_entries "
                "(cache_root, size, image_id, path, source_signature, size_bytes, last_accessed, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    app_module.thumbnails.SSD_CACHE_DIR,
                    size,
                    image_id,
                    os.path.join(self.tempdir.name, f"{size}-{image_id}.jpg"),
                    f"sig-{size}-{image_id}",
                    123,
                    now,
                    now,
                ),
            )
            await conn.commit()
        finally:
            await conn.close()

    def _stub_text_search(self, image_ids, scores):
        matrix = np.array([[score, 0.0] for score in scores], dtype=np.float32)

        async def fake_get_matrix():
            return image_ids, matrix

        def fake_encode_text(_query):
            return np.array([1.0, 0.0], dtype=np.float32)

        elo_propagation.embed_cache.get_matrix = fake_get_matrix
        embedding_worker.encode_text = fake_encode_text

    async def test_compare_payload_validation_rejects_invalid_and_inactive_images(self):
        source = await self._source()
        a = await self._image(source["id"], "a.jpg")
        b = await self._image(source["id"], "b.jpg")
        offline_source = await self._source("offline", online=False)
        offline = await self._image(offline_source["id"], "offline.jpg")

        response = await app_module.submit_comparison(BadJsonRequest())
        self.assertEqual(response.status_code, 400)

        response = await app_module.mosaic_pick(JsonRequest([]))
        self.assertEqual(response.status_code, 400)

        response = await app_module.submit_comparison(JsonRequest({"winner_id": a, "loser_id": a}))
        self.assertEqual(response.status_code, 400)

        response = await app_module.submit_comparison(JsonRequest({"winner_id": a, "loser_id": offline}))
        self.assertTrue(response["ok"])

        response = await app_module.mosaic_pick(JsonRequest({"winner_id": a, "loser_ids": [b, b]}))
        self.assertEqual(response.status_code, 400)

        response = await app_module.mosaic_pick(JsonRequest({"winner_id": a, "loser_ids": [a]}))
        self.assertEqual(response.status_code, 400)

        response = await app_module.mosaic_pick(JsonRequest({"winner_id": a, "loser_ids": [999999]}))
        self.assertEqual(response.status_code, 400)

    async def test_mosaic_pick_undo_reverts_whole_action(self):
        source = await self._source()
        winner = await self._image(source["id"], "winner.jpg")
        losers = [
            await self._image(source["id"], "loser1.jpg"),
            await self._image(source["id"], "loser2.jpg"),
            await self._image(source["id"], "loser3.jpg"),
        ]

        result = await app_module.mosaic_pick(JsonRequest({"winner_id": winner, "loser_ids": losers}))
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

        undo = await app_module.compare_undo()
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

    async def test_compare_propagation_prefers_deep_embedding_surface(self):
        source = await self._source()
        winner = await self._image(source["id"], "winner.jpg")
        loser = await self._image(source["id"], "loser.jpg")
        deep_neighbor = await self._image(source["id"], "deep-neighbor.jpg")
        fast_neighbor = await self._image(source["id"], "fast-neighbor.jpg")

        image_ids = [winner, loser, deep_neighbor, fast_neighbor]
        deep_matrix = np.array(
            [
                [1.0, 0.0],
                [-1.0, 0.0],
                [0.995, 0.1],
                [0.0, 1.0],
            ],
            dtype=np.float32,
        )
        fast_matrix = np.array(
            [
                [1.0, 0.0],
                [-1.0, 0.0],
                [0.0, 1.0],
                [0.995, 0.1],
            ],
            dtype=np.float32,
        )
        deep_matrix /= np.linalg.norm(deep_matrix, axis=1, keepdims=True)
        fast_matrix /= np.linalg.norm(fast_matrix, axis=1, keepdims=True)
        deep_key = app_module.settings.deep_search_embedding_config()["model_key"]
        calls = []

        async def fake_get_matrix(model_key=None):
            calls.append(model_key)
            return image_ids, deep_matrix if model_key == deep_key else fast_matrix

        def fake_get_index(_model_key=None):
            return {image_id: idx for idx, image_id in enumerate(image_ids)}

        elo_propagation.embed_cache.get_matrix = fake_get_matrix
        elo_propagation.embed_cache.get_index = fake_get_index

        await elo_propagation.propagate_comparison(winner, loser, k=20.0)

        deep_row = await self._image_row(deep_neighbor)
        fast_row = await self._image_row(fast_neighbor)
        self.assertEqual(calls[0], deep_key)
        self.assertGreater(deep_row["elo"], 1200.0)
        self.assertAlmostEqual(fast_row["elo"], 1200.0)

    async def test_mosaic_diverse_prefers_deep_embedding_surface(self):
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
        deep_key = app_module.settings.deep_search_embedding_config()["model_key"]
        calls = []

        async def fake_get_matrix(model_key=None):
            calls.append(model_key)
            return image_ids, matrix

        def fake_get_index(_model_key=None):
            return {image_id: idx for idx, image_id in enumerate(image_ids)}

        def fail_get_warm_matrix():
            raise AssertionError("deep mosaic diversity should not fall through to the active warm matrix")

        old_get_warm_matrix = elo_propagation.embed_cache.get_warm_matrix
        elo_propagation.embed_cache.get_matrix = fake_get_matrix
        elo_propagation.embed_cache.get_index = fake_get_index
        elo_propagation.embed_cache.get_warm_matrix = fail_get_warm_matrix
        try:
            sample = await app_module._diverse_sample(candidates, 2)
        finally:
            elo_propagation.embed_cache.get_warm_matrix = old_get_warm_matrix

        self.assertEqual(calls[0], deep_key)
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

        undo = await app_module.compare_undo()
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
        await app_module.submit_comparison(JsonRequest({"winner_id": winner, "loser_id": loser}))

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

    async def test_pairing_cache_patch_keeps_immediate_candidate_cache_hot(self):
        app_module._pairing_cache.update({
            "valid": True,
            "data": [
                {"id": 1, "elo": 1200.0, "comparisons": 0},
                {"id": 2, "elo": 1300.0, "comparisons": 2},
            ],
        })
        expires = app_module.time.monotonic() + 1.0
        app_module._visible_pairing_candidates_cache["test:md:2:elo"] = {
            "data": [
                {"id": 1, "elo": 1200.0, "comparisons": 0},
                {"id": 2, "elo": 1300.0, "comparisons": 2},
            ],
            "id_set": {1, 2},
            "expires": expires,
        }
        app_module._visible_pairing_candidates_cache["test:sm:2:cache"] = {
            "data": [
                {"id": 3, "elo": 1100.0, "comparisons": 0},
            ],
            "id_set": {3},
            "expires": expires,
        }

        app_module._patch_pairing_cache([(1, 1400.0, 1)])

        self.assertTrue(app_module._pairing_cache["valid"])
        self.assertEqual(app_module._pairing_cache["data"][0]["elo"], 1400.0)
        cached = app_module._visible_pairing_candidates_cache["test:md:2:elo"]
        self.assertEqual([row["id"] for row in cached["data"]], [1, 2])
        self.assertEqual(cached["data"][0]["comparisons"], 1)
        self.assertEqual(cached["id_set"], {1, 2})
        self.assertGreater(cached["expires"], expires)
        self.assertEqual(
            app_module._visible_pairing_candidates_cache["test:sm:2:cache"]["expires"],
            expires,
        )

    async def test_ai_status_counts_only_count_active_embeddings(self):
        active_source = await self._source("active")
        offline_source = await self._source("offline", online=False)
        active = await self._image(active_source["id"], "active.jpg")
        offline = await self._image(offline_source["id"], "offline.jpg")

        conn = await db.get_db()
        try:
            await conn.executemany(
                "INSERT INTO embeddings (image_id, embedding) VALUES (?, ?)",
                [(active, b"active"), (offline, b"offline")],
            )
            await conn.commit()
        finally:
            await conn.close()

        ai_counts = await db.get_ai_status_counts()

        self.assertEqual(ai_counts["total_images"], 1)
        self.assertEqual(ai_counts["embedded"], 1)
        self.assertEqual(await db.get_embedding_count(), 1)

    async def test_fast_search_role_ignores_saved_deep_model_preset(self):
        source = await self._source("active")
        image_id = await self._image(source["id"], "active.jpg")

        fast_key = db.active_embedding_model_key()
        await db.store_embeddings_batch([(image_id, b"fast-vector")])

        self.assertEqual(await db.get_embedding_count(), 1)

        app_module.settings.save_settings({
            "embed_model_preset": "qwen3-vl-embedding-8b",
            "embed_model_dir": "/tmp/stale-2b-path",
        })
        db._invalidate_embedding_count_cache()
        still_fast_key = db.active_embedding_model_key()
        deep_config = app_module.settings.deep_search_embedding_config()

        self.assertEqual(still_fast_key, fast_key)
        self.assertNotEqual(deep_config["model_key"], fast_key)
        self.assertEqual(await db.get_embedding_count(), 1)

        await db.store_embeddings_batch([(image_id, b"deep-vector")], embedding_config=deep_config)

        conn = await db.get_db()
        try:
            cursor = await conn.execute("SELECT embedding FROM embeddings WHERE image_id = ?", (image_id,))
            fast_legacy_row = await cursor.fetchone()
            cursor = await conn.execute(
                "SELECT model_key, embedding, dimension FROM embeddings_by_model WHERE image_id = ?",
                (image_id,),
            )
            rows = {row["model_key"]: row for row in await cursor.fetchall()}
        finally:
            await conn.close()

        self.assertEqual(fast_legacy_row["embedding"], b"fast-vector")
        self.assertEqual(rows[fast_key]["embedding"], b"fast-vector")
        self.assertEqual(rows[fast_key]["dimension"], 2048)
        self.assertEqual(rows[deep_config["model_key"]]["embedding"], b"deep-vector")
        self.assertEqual(rows[deep_config["model_key"]]["dimension"], 4096)
        self.assertEqual(await db.get_embedding_count(), 1)

    async def test_qwen8b_preset_is_authoritative(self):
        saved = app_module.settings.save_settings({
            "embed_model_preset": "qwen3-vl-embedding-8b",
            "embed_model_dir": "/tmp/stale-2b-path",
            "embed_model_dim": 2048,
        })
        presets = {
            preset["key"]: preset
            for preset in app_module.settings.settings_metadata()["embedding_model_presets"]
        }

        self.assertEqual(saved["embed_model_id"], "Qwen/Qwen3-VL-Embedding-8B")
        self.assertEqual(saved["embed_model_dim"], 4096)
        self.assertEqual(saved["embed_model_dir"], presets["qwen3-vl-embedding-8b"]["model_dir"])
        self.assertTrue(app_module.settings.embedding_model_key(saved).endswith(":4096"))

    async def test_init_db_migrates_legacy_comparison_action_id_before_indexes(self):
        original_path = db.DB_PATH
        legacy_path = os.path.join(self.tempdir.name, "legacy-comparisons.db")
        conn = sqlite3.connect(legacy_path)
        try:
            conn.execute(
                "CREATE TABLE comparisons ("
                "id INTEGER PRIMARY KEY, "
                "winner_id INTEGER, "
                "loser_id INTEGER, "
                "mode TEXT, "
                "elo_before_winner REAL, "
                "elo_before_loser REAL, "
                "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP"
                ")"
            )
            conn.commit()
        finally:
            conn.close()

        db.DB_PATH = legacy_path
        db.invalidate_stats_cache()
        try:
            await db.init_db()
            conn = sqlite3.connect(legacy_path)
            try:
                columns = {row[1] for row in conn.execute("PRAGMA table_info(comparisons)")}
                indexes = {row[1] for row in conn.execute("PRAGMA index_list(comparisons)")}
            finally:
                conn.close()
        finally:
            db.DB_PATH = original_path
            db.invalidate_stats_cache()

        self.assertIn("action_id", columns)
        self.assertIn("idx_comparisons_action_id", indexes)

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
            cache_root=app_module.thumbnails.SSD_CACHE_DIR,
        )
        options = await db.get_filter_options()

        self.assertEqual({row["id"] for row in rows}, {plain, dotted})
        self.assertIn({"ext": "jpg", "count": 2}, options["file_types"])
        self.assertNotIn({"ext": ".jpg", "count": 1}, options["file_types"])

    async def test_text_search_exact_extension_uses_file_type_filter(self):
        conditions, params = db._ranking_filter_parts(text_query="jpg", include_source=False)
        where = " AND ".join(conditions)

        self.assertIn("LOWER(i.file_ext) IN (?, ?)", where)
        self.assertNotIn("i.filename LIKE", where)
        self.assertEqual(params[-2:], ["jpg", ".jpg"])

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

        result = await app_module.api_rankings(limit=10, sort="elo")

        self.assertEqual([img["id"] for img in result["images"]], [visible_high, visible_low])
        self.assertEqual(result["visible_images"], 2)
        self.assertEqual(result["total_images"], 3)
        self.assertEqual(result["hidden_pending_thumbnails"], 1)
        self.assertNotIn(hidden, [img["id"] for img in result["images"]])

    async def test_orientation_visible_pairing_pool_counts(self):
        self.assertGreaterEqual(db.VISIBLE_PAIRING_POOL_COUNTS_TTL_SECONDS, 30.0)
        source = await self._source()
        visible_landscape = await self._image(source["id"], "visible-landscape.jpg")
        hidden_landscape = await self._image(source["id"], "hidden-landscape.jpg")
        portrait = await self._image(source["id"], "portrait.jpg")
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
            app_module.thumbnails.SSD_CACHE_DIR,
            "landscape",
        )

        self.assertEqual(counts["active_images"], 2)
        self.assertEqual(counts["visible_images"], 1)

        await self._cache_entry(hidden_landscape, "md")
        db.invalidate_cached_image_ids_cache(app_module.thumbnails.SSD_CACHE_DIR, "md")

        refreshed = await db.get_visible_orientation_pairing_pool_counts(
            "md",
            app_module.thumbnails.SSD_CACHE_DIR,
            "landscape",
        )

        self.assertEqual(refreshed["visible_images"], 2)

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
            cache_root=app_module.thumbnails.SSD_CACHE_DIR,
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
            cache_root=app_module.thumbnails.SSD_CACHE_DIR,
        )
        camera_rows = await db.get_rankings(
            limit=10,
            sort="camera",
            visible_thumb_size="sm",
            cache_root=app_module.thumbnails.SSD_CACHE_DIR,
        )

        self.assertEqual([row["id"] for row in filename_rows], [visible_b, visible_c])
        self.assertEqual([row["id"] for row in camera_rows], [visible_c, visible_b])
        self.assertNotIn(hidden_a, [row["id"] for row in filename_rows + camera_rows])

    async def test_visible_pairing_least_compared_uses_ordered_index(self):
        source = await self._source()
        visible_fresh = await self._image(source["id"], "fresh.jpg", elo=1200, comparisons=0)
        visible_rated = await self._image(source["id"], "rated.jpg", elo=1700, comparisons=3)
        hidden_fresh = await self._image(source["id"], "hidden.jpg", elo=1800, comparisons=0)
        await self._cache_entry(visible_fresh, "sm")
        await self._cache_entry(visible_rated, "sm")

        rows = await db.get_visible_images_for_pairing(
            "sm",
            app_module.thumbnails.SSD_CACHE_DIR,
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
                "AND s.included = 1 AND s.online = 1 AND i.missing_at IS NULL "
                "AND EXISTS ("
                "  SELECT 1 FROM cache_entries c "
                "  WHERE c.cache_root = ? AND c.size = ? AND c.image_id = i.id"
                ") "
                "ORDER BY i.comparisons ASC, i.elo DESC LIMIT 10",
                (app_module.thumbnails.SSD_CACHE_DIR, "sm"),
            ).fetchall()
        finally:
            raw.close()
        plan = " ".join(row[3] for row in plan_rows)
        self.assertIn("idx_images_visible_comparisons_elo", plan)
        self.assertNotIn("TEMP B-TREE", plan)

    async def test_rankings_response_cache_invalidates_after_flag_change(self):
        source = await self._source()
        first = await self._image(source["id"], "first.jpg", elo=1500)
        second = await self._image(source["id"], "second.jpg", elo=1300)
        await self._cache_entry(first, "sm")
        await self._cache_entry(second, "sm")

        initial = await app_module.api_rankings(limit=10, sort="elo", flag="picked")
        self.assertEqual(initial["images"], [])
        self.assertTrue(app_module._rankings_response_cache)

        await app_module.api_set_image_flag(first, JsonRequest({"flag": "picked"}))
        refreshed = await app_module.api_rankings(limit=10, sort="elo", flag="picked")

        self.assertEqual([image["id"] for image in refreshed["images"]], [first])
        self.assertNotIn(second, [image["id"] for image in refreshed["images"]])

    async def test_benchmark_cache_reset_clears_response_caches(self):
        import bench_perf

        source = await self._source()
        image_id = await self._image(source["id"], "first.jpg", elo=1500)
        await self._cache_entry(image_id, "sm")

        await app_module.api_rankings(limit=10, sort="elo")
        await app_module.api_settings()
        self.assertTrue(app_module._rankings_response_cache)
        self.assertIsNotNone(app_module._settings_response_cache["data"])
        self.assertIsNotNone(app_module._ai_status_response_cache["data"])
        app_module._thumbnail_memory_warm_inflight.add("sm:1")

        bench_perf.reset_app_caches()

        self.assertFalse(app_module._rankings_response_cache)
        self.assertFalse(app_module._thumbnail_memory_warm_inflight)
        self.assertIsNone(app_module._settings_response_cache["data"])
        self.assertIsNone(app_module._ai_status_response_cache["data"])

    async def test_rankings_response_cache_returns_independent_image_lists(self):
        source = await self._source()
        image_id = await self._image(source["id"], "first.jpg", elo=1500)
        await self._cache_entry(image_id, "sm")

        first = await app_module.api_rankings(limit=10, sort="elo")
        second = await app_module.api_rankings(limit=10, sort="elo")
        second["images"].clear()
        third = await app_module.api_rankings(limit=10, sort="elo")

        self.assertEqual([image["id"] for image in first["images"]], [image_id])
        self.assertEqual([image["id"] for image in third["images"]], [image_id])

    async def test_visible_ranking_count_uses_short_ttl_cache_and_invalidation(self):
        source = await self._source()
        first = await self._image(source["id"], "first.jpg")
        second = await self._image(source["id"], "second.jpg")
        await self._cache_entry(first, "sm")

        count = await db.count_rankings(
            visible_thumb_size="sm",
            cache_root=app_module.thumbnails.SSD_CACHE_DIR,
        )
        self.assertEqual(count, 1)

        await self._cache_entry(second, "sm")
        stale_count = await db.count_rankings(
            visible_thumb_size="sm",
            cache_root=app_module.thumbnails.SSD_CACHE_DIR,
        )
        self.assertEqual(stale_count, 1)

        db.invalidate_cached_image_ids_cache()
        refreshed_count = await db.count_rankings(
            visible_thumb_size="sm",
            cache_root=app_module.thumbnails.SSD_CACHE_DIR,
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
            cache_root=app_module.thumbnails.SSD_CACHE_DIR,
        )

        self.assertEqual(count, 2)

    async def test_cache_entry_count_uses_short_ttl_until_invalidation(self):
        source = await self._source()
        first = await self._image(source["id"], "cache-count-1.jpg")
        second = await self._image(source["id"], "cache-count-2.jpg")
        await self._cache_entry(first, "sm")

        root = app_module.thumbnails.SSD_CACHE_DIR
        self.assertEqual(await db._cache_entry_count("sm", root), 1)

        await self._cache_entry(second, "sm")
        self.assertEqual(await db._cache_entry_count("sm", root), 1)

        db.invalidate_cached_image_ids_cache(cache_root=root, size="sm")
        self.assertEqual(await db._cache_entry_count("sm", root), 2)

    async def test_catalog_image_counts_cache_invalidates_with_stats(self):
        source = await self._source()

        self.assertEqual(await db.get_catalog_image_counts(), {
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
        self.assertEqual(initial_stats["total_comparisons"], 0)
        self.assertEqual(initial_ai_counts["ranking_signal_count"], 0)

        result = await db.record_active_comparison(first, second, "swiss", action_id="stats-cache-test")
        self.assertIsNotNone(result)
        self.assertIsNotNone(db._stats_cache["data"])

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
        db._stats_inflight_task = None
        db._stats_cache["data"] = stale_stats
        db._stats_cache["expires"] = db._time.time() - 1
        try:
            self.assertIs(await db.get_stats(), stale_stats)
            await asyncio.wait_for(refresh_started.wait(), timeout=1)
            self.assertEqual(calls, 1)

            self.assertIs(await db.get_stats(), stale_stats)
            self.assertEqual(calls, 1)

            refresh_can_finish.set()
            await asyncio.wait_for(db._stats_inflight_task, timeout=1)

            self.assertIs(await db.get_stats(), fresh_stats)
            self.assertEqual(calls, 1)
        finally:
            refresh_can_finish.set()
            task = db._stats_inflight_task
            if task is not None and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            db._stats_inflight_task = None
            db._get_stats_uncached = old_get_stats_uncached
            db.invalidate_stats_cache()

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

        rankings = await app_module.api_rankings(limit=10, sort="elo")
        self.assertEqual([img["id"] for img in rankings["images"]], [active_a, active_b])
        self.assertEqual(rankings["visible_images"], 2)
        self.assertEqual(rankings["total_images"], 2)

        mosaic = await app_module.mosaic_next(n=3, strategy="diverse")
        self.assertNotIn(missing, [img["id"] for img in mosaic["images"]])
        self.assertEqual(mosaic["total_images"], 2)

        compare = await app_module.compare_next(n=2, mode="swiss")
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
            md_cache_root=app_module.thumbnails.SSD_CACHE_DIR,
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
            md_cache_root=app_module.thumbnails.SSD_CACHE_DIR,
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

        rankings = await app_module.api_rankings(limit=10)
        self.assertEqual([image["id"] for image in rankings["images"]], [image_id])
        self.assertEqual(rankings["total_images"], 1)

        self.assertEqual(await db.count_rankings(), 1)
        self.assertEqual([row["id"] for row in await db.get_rankings(limit=10)], [image_id])
        self.assertEqual([row["id"] for row in await db.get_active_images_for_pairing()], [image_id])
        self.assertEqual(await db.get_past_matchups(), set())
        self.assertIsInstance(
            await db.get_date_groups(visible_thumb_size="sm", cache_root=app_module.thumbnails.SSD_CACHE_DIR),
            list,
        )

        markers = await db.get_map_markers(visible_thumb_size="sm", cache_root=app_module.thumbnails.SSD_CACHE_DIR)
        self.assertEqual(markers["total_count"], 1)
        self.assertEqual(markers["gps_total_count"], 0)

        folders = await app_module.api_folders()
        self.assertTrue(folders["folders"])

        collections = await app_module.api_collections()
        self.assertEqual(collections["collections"], [])
        duplicates = await app_module.api_duplicates()
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
        app_module._invalidate_folders_cache()
        result = await app_module.api_folders()

        self.assertEqual(result["root"], source["path"])
        counts = {folder["path"]: folder["count"] for folder in result["folders"]}
        self.assertEqual(counts["."], 1)
        self.assertEqual(counts["Family"], 3)
        self.assertEqual(counts["Family/Trip"], 2)
        self.assertEqual(counts["Family/Trip/Day"], 1)

        shallow = await app_module.api_folders(max_depth=1)
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
        app_module._invalidate_folders_cache()
        result = await app_module.api_folders()

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

        app_module._invalidate_folders_cache()
        result = await app_module.api_folders(max_depth=0)

        self.assertEqual(result["root"], self.tempdir.name)
        counts = {folder["path"]: folder["count"] for folder in result["folders"]}
        self.assertEqual(counts, {"first-source": 2, "second-source": 1})

    async def test_scan_start_invalidates_active_source_cache(self):
        source = await self._source("offline-source", online=False)
        self.assertEqual(await db.get_active_source_id_set(), frozenset())

        await db.mark_source_scan_started(source["id"])

        self.assertIn(source["id"], await db.get_active_source_id_set())

    async def test_purge_source_invalidates_cached_image_id_cache(self):
        source = await self._source("purge-source")
        image_id = await self._image(source["id"], "cached.jpg")
        await self._cache_entry(image_id, "sm")
        db.invalidate_cached_image_ids_cache()
        self.assertIn(
            image_id,
            await db.get_cached_image_id_set("sm", app_module.thumbnails.SSD_CACHE_DIR),
        )

        await db.purge_source_catalog_data(source["id"])

        self.assertNotIn(
            image_id,
            await db.get_cached_image_id_set("sm", app_module.thumbnails.SSD_CACHE_DIR),
        )

    async def test_thumbnail_store_invalidates_cached_image_id_cache(self):
        source = await self._source("thumb-store-source")
        image_id = await self._image(source["id"], "new-cache.jpg")
        app_module.thumbnails._persistent_conn = None
        old_allocations = dict(app_module.thumbnails._disk_allocations)
        app_module.thumbnails._disk_allocations["sm"] = 10_000
        app_module.thumbnails._tier_byte_totals.clear()
        db.invalidate_cached_image_ids_cache()
        try:
            self.assertNotIn(
                image_id,
                await db.get_cached_image_id_set("sm", app_module.thumbnails.SSD_CACHE_DIR),
            )

            app_module.thumbnails._store_disk_entry(
                "sm",
                image_id,
                "sig-sm",
                os.path.join(self.tempdir.name, "new-cache-sm.jpg"),
                123,
            )

            self.assertIn(
                image_id,
                await db.get_cached_image_id_set("sm", app_module.thumbnails.SSD_CACHE_DIR),
            )
        finally:
            app_module.thumbnails._disk_allocations.clear()
            app_module.thumbnails._disk_allocations.update(old_allocations)
            app_module.thumbnails._tier_byte_totals.clear()

    async def test_thumbnail_write_queue_flush_invalidates_cached_image_id_cache(self):
        source = await self._source("thumb-flush-source")
        image_id = await self._image(source["id"], "queued-cache.jpg")
        app_module.thumbnails._persistent_conn = None
        old_allocations = dict(app_module.thumbnails._disk_allocations)
        old_queue = list(app_module.thumbnails._write_queue)
        app_module.thumbnails._disk_allocations["sm"] = 10_000
        app_module.thumbnails._tier_byte_totals.clear()
        with app_module.thumbnails._write_queue_lock:
            app_module.thumbnails._write_queue.clear()
        db.invalidate_cached_image_ids_cache()
        try:
            self.assertNotIn(
                image_id,
                await db.get_cached_image_id_set("sm", app_module.thumbnails.SSD_CACHE_DIR),
            )

            with app_module.thumbnails._write_queue_lock:
                app_module.thumbnails._write_queue.append(
                    (
                        "sm",
                        image_id,
                        "sig-sm",
                        os.path.join(self.tempdir.name, "queued-cache-sm.jpg"),
                        123,
                        app_module.thumbnails._current_time(),
                    )
                )
            self.assertTrue(app_module.thumbnails._flush_write_queue())

            self.assertIn(
                image_id,
                await db.get_cached_image_id_set("sm", app_module.thumbnails.SSD_CACHE_DIR),
            )
        finally:
            app_module.thumbnails._disk_allocations.clear()
            app_module.thumbnails._disk_allocations.update(old_allocations)
            app_module.thumbnails._tier_byte_totals.clear()
            with app_module.thumbnails._write_queue_lock:
                app_module.thumbnails._write_queue.clear()
                app_module.thumbnails._write_queue.extend(old_queue)

    async def test_thumbnail_append_preserves_visible_facet_cache(self):
        root = app_module.thumbnails.SSD_CACHE_DIR
        key = db._facet_cache_key(visible_thumb_size="sm", cache_root=root)
        cached_groups = [{"date": "2026-05", "label": "May 2026", "count": 1}]
        db._date_groups_cache[key] = {"data": cached_groups, "expires": db._time.time() + 30.0}
        db._map_markers_cache[key] = {"data": [{"id": 1}], "expires": db._time.time() + 30.0}

        db.note_cached_image_ids_added(root, "sm", [123])

        self.assertIn(key, db._date_groups_cache)
        self.assertIn(key, db._map_markers_cache)

    async def test_thumbnail_append_preserves_visible_count_cache(self):
        root = app_module.thumbnails.SSD_CACHE_DIR
        key = db._ranking_count_cache_key(visible_thumb_size="sm", cache_root=root)
        db._ranking_count_cache[key] = {"value": 12, "expires": db._time.time() + 30.0}

        db.note_cached_image_ids_added(root, "sm", [123])

        self.assertIn(key, db._ranking_count_cache)

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
            cache_root=app_module.thumbnails.SSD_CACHE_DIR,
        )
        self.assertEqual(date_groups, [])
        markers = await db.get_map_markers(
            visible_thumb_size="sm",
            cache_root=app_module.thumbnails.SSD_CACHE_DIR,
        )
        self.assertEqual(markers["markers"], [])

        await self._cache_entry(image_id, "sm")
        db.invalidate_cached_image_ids_cache(app_module.thumbnails.SSD_CACHE_DIR, "sm")

        date_groups = await db.get_date_groups(
            visible_thumb_size="sm",
            cache_root=app_module.thumbnails.SSD_CACHE_DIR,
        )
        self.assertEqual(date_groups[0]["date"], "2024-01")
        self.assertGreaterEqual(db.FACET_CACHE_TTL_SECONDS, 30.0)
        markers = await db.get_map_markers(
            visible_thumb_size="sm",
            cache_root=app_module.thumbnails.SSD_CACHE_DIR,
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
            cache_root=app_module.thumbnails.SSD_CACHE_DIR,
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
            cache_root=app_module.thumbnails.SSD_CACHE_DIR,
        )

        self.assertEqual(markers["markers"], [])
        self.assertEqual(markers["total_count"], 2)
        self.assertEqual(markers["visible_count"], 1)
        self.assertEqual(markers["gps_total_count"], 0)
        self.assertEqual(markers["hidden_pending_thumbnails"], 0)

    async def test_mosaic_excludes_images_without_sm_but_reports_filtered_total(self):
        source = await self._source()
        first = await self._image(source["id"], "first.jpg", elo=1500)
        hidden = await self._image(source["id"], "hidden.jpg", elo=1400)
        second = await self._image(source["id"], "second.jpg", elo=1300)
        await self._cache_entry(first, "sm")
        await self._cache_entry(second, "sm")

        result = await app_module.mosaic_next(n=3, strategy="diverse")
        ids = [img["id"] for img in result["images"]]

        self.assertEqual(ids, [first, second])
        self.assertNotIn(hidden, ids)
        self.assertEqual(result["visible_images"], 2)
        self.assertEqual(result["total_images"], 3)
        self.assertEqual(result["hidden_pending_thumbnails"], 1)
        self.assertEqual(result["stats"]["filtered_pool_visible"], 2)
        self.assertEqual(result["stats"]["filtered_pool_total"], 3)

    async def test_default_interaction_response_cache_reuses_and_invalidates_on_rating(self):
        source = await self._source()
        first = await self._image(source["id"], "first.jpg", elo=1500)
        second = await self._image(source["id"], "second.jpg", elo=1300)
        third = await self._image(source["id"], "third.jpg", elo=1100)
        for image_id in (first, second, third):
            await self._cache_entry(image_id, "sm")
            await self._cache_entry(image_id, "md")

        mosaic_first = await app_module.mosaic_next(n=3, strategy="diverse")
        self.assertTrue(app_module._interaction_response_cache)
        old_get_visible = db.get_visible_images_for_pairing

        async def fail_visible_pairing(*_args, **_kwargs):
            raise AssertionError("warm default interaction response should be reused")

        db.get_visible_images_for_pairing = fail_visible_pairing
        try:
            mosaic_second = await app_module.mosaic_next(n=3, strategy="diverse")
        finally:
            db.get_visible_images_for_pairing = old_get_visible

        self.assertEqual(mosaic_second["images"], mosaic_first["images"])
        mosaic_second["images"][0]["filename"] = "mutated"
        mosaic_second["stats"]["filtered_pool"] = 999
        mosaic_third = await app_module.mosaic_next(n=3, strategy="diverse")
        self.assertNotEqual(mosaic_third["images"][0]["filename"], "mutated")
        self.assertNotEqual(mosaic_third["stats"]["filtered_pool"], 999)

        await app_module.submit_comparison(
            JsonRequest({"winner_id": first, "loser_id": second})
        )
        self.assertFalse(app_module._interaction_response_cache)

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

        app_module.thumbnails.prefetch_images = blocking_prefetch
        try:
            result = await asyncio.wait_for(
                app_module.mosaic_next(n=2, strategy="random"),
                timeout=0.5,
            )
            self.assertEqual(len(result["images"]), 2)
            await asyncio.wait_for(started.wait(), timeout=0.5)
        finally:
            release.set()
            await asyncio.sleep(0)

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

        app_module.thumbnails.prefetch_images = blocking_prefetch
        try:
            result = await asyncio.wait_for(app_module.api_rankings(limit=2), timeout=0.5)
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

        app_module.thumbnails.prefetch_images = blocking_prefetch
        try:
            result = await asyncio.wait_for(app_module.compare_next(n=1, mode="swiss"), timeout=0.5)
            self.assertEqual(len(result["pairs"]), 1)
            await asyncio.wait_for(started.wait(), timeout=0.5)
        finally:
            release.set()
            await asyncio.sleep(0)

    async def test_thumbnail_memory_warm_reads_cached_sm_md_and_lg(self):
        calls = []
        app_module.thumbnails._last_user_activity = app_module.thumbnails.time.monotonic() - 30.0

        def fake_read(size, image_id, source_signature=None, *, populate_memory=False):
            calls.append((size, image_id, source_signature, populate_memory))
            return ("sig", b"jpeg")

        app_module.thumbnails.fast_disk_read_entry = fake_read

        app_module._schedule_cached_thumbnail_memory_warm(
            [{"id": 10}, {"id": 11}],
            "sm",
            limit=2,
        )
        app_module._schedule_cached_thumbnail_memory_warm(
            [{"id": 20}, {"id": 21}],
            "md",
            limit=2,
        )
        app_module._schedule_cached_thumbnail_memory_warm(
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
        app_module.thumbnails._last_user_activity = app_module.thumbnails.time.monotonic()

        def fake_read(size, image_id, source_signature=None, *, populate_memory=False):
            calls.append((size, image_id, populate_memory))
            return ("sig", b"jpeg")

        app_module.thumbnails.fast_disk_read_entry = fake_read

        app_module._schedule_result_thumbnail_memory_warm(rows)
        await asyncio.sleep(0.05)

        by_size = {}
        for size, image_id, populate_memory in calls:
            self.assertTrue(populate_memory)
            by_size.setdefault(size, []).append(image_id)

        self.assertEqual(by_size.get("sm"), list(range(1001, 1010)))
        self.assertEqual(by_size.get("md"), list(range(1001, 1007)))
        self.assertEqual(by_size.get("lg"), list(range(1001, 1007)))

    async def test_compare_next_excludes_images_without_md_thumbnails(self):
        source = await self._source()
        visible_a = await self._image(source["id"], "visible-a.jpg", elo=1500)
        hidden_a = await self._image(source["id"], "hidden-a.jpg", elo=1450)
        visible_b = await self._image(source["id"], "visible-b.jpg", elo=1400)
        hidden_b = await self._image(source["id"], "hidden-b.jpg", elo=1350)
        await self._cache_entry(visible_a, "md")
        await self._cache_entry(visible_b, "md")

        result = await app_module.compare_next(n=2, mode="swiss")
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

    async def test_visible_pairing_candidates_exclude_offline_cached_images(self):
        active_source = await self._source("pair-active")
        offline_source = await self._source("pair-offline", online=False)
        active = await self._image(active_source["id"], "active.jpg")
        offline = await self._image(offline_source["id"], "offline.jpg")
        await self._cache_entry(active, "md")
        await self._cache_entry(offline, "md")

        rows = await db.get_visible_images_for_pairing(
            "md",
            app_module.thumbnails.SSD_CACHE_DIR,
            include_card_metadata=False,
            limit=10,
        )

        self.assertEqual([row["id"] for row in rows], [active])

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
            app_module.thumbnails.SSD_CACHE_DIR,
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

    async def test_search_skips_uncached_sm_results_and_fills_later_visible_matches(self):
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

        async def fake_get_matrix():
            return image_ids, matrix

        def fake_encode_text(_query):
            return np.array([1.0, 0.0], dtype=np.float32)

        elo_propagation.embed_cache.get_matrix = fake_get_matrix
        embedding_worker.encode_text = fake_encode_text

        result = await app_module.api_search(q="sunset", limit=2)

        self.assertEqual([img["id"] for img in result["images"]], [visible_first, visible_second])
        self.assertEqual(result["visible_images"], 2)
        self.assertEqual(result["total_images"], 4)
        self.assertEqual(result["hidden_pending_thumbnails"], 2)

    async def test_rankings_search_uses_metadata_fallback_when_ai_is_cold(self):
        source = await self._source()
        visible_match = await self._image(source["id"], "sunset-visible.jpg")
        hidden_match = await self._image(source["id"], "sunset-hidden.jpg")
        visible_miss = await self._image(source["id"], "portrait-visible.jpg")
        await self._cache_entry(visible_match, "sm")
        await self._cache_entry(visible_miss, "sm")

        embedding_worker.encode_text = lambda _query: None

        result = await app_module.api_rankings(q="sunset", sort="similarity", limit=10)

        self.assertEqual([img["id"] for img in result["images"]], [visible_match])
        self.assertEqual(result["search_mode"], "metadata")
        self.assertTrue(result["ai_unavailable"])
        self.assertEqual(result["visible_images"], 1)
        self.assertEqual(result["total_images"], 2)
        self.assertEqual(result["hidden_pending_thumbnails"], 1)
        self.assertNotIn(hidden_match, [img["id"] for img in result["images"]])

    async def test_metadata_search_image_ids_uses_active_fts_index(self):
        source = await self._source()
        match = await self._image(source["id"], "sunset-visible.jpg")
        miss = await self._image(source["id"], "portrait-visible.jpg")

        self.assertEqual(await db.metadata_search_image_ids("sunset"), {match})
        self.assertEqual(await db.metadata_search_image_ids("no-such-photo"), set())
        self.assertNotIn(miss, await db.metadata_search_image_ids("sunset"))

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

        app_module._text_search_resolution_cache.clear()
        calls = {"encode": 0}

        def fake_encode_text(_query):
            calls["encode"] += 1
            return None

        embedding_worker.encode_text = fake_encode_text

        result = await app_module.api_rankings(q="jpg", limit=10)

        self.assertEqual([image["id"] for image in result["images"]], [jpg])
        self.assertEqual(result["total_images"], 1)
        self.assertEqual(calls["encode"], 0)

    async def test_rankings_http_cache_hit_returns_preencoded_response(self):
        source = await self._source()
        image_id = await self._image(source["id"], "cached-response.jpg")
        await self._cache_entry(image_id, "sm")

        first = await app_module.api_rankings(limit=1)
        self.assertIsInstance(first, dict)
        self.assertEqual([image["id"] for image in first["images"]], [image_id])

        request = Request({"type": "http", "method": "GET", "path": "/api/rankings", "headers": []})
        second = await app_module.api_rankings(limit=1, request=request)

        self.assertIsInstance(second, Response)
        self.assertEqual(second.media_type, "application/json")
        self.assertIn(b"cached-response.jpg", second.body)

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
            result = await app_module.api_rankings(q="no-such-visible-photo", limit=10)
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

        similarity = await app_module.api_rankings(q="landscapes", sort="similarity", limit=10)
        elo_sorted = await app_module.api_rankings(q="landscapes", sort="elo", limit=10)

        self.assertEqual([img["id"] for img in similarity["images"]], [best_match, rated_match])
        self.assertEqual([img["id"] for img in elo_sorted["images"]], [rated_match, best_match])
        self.assertEqual(similarity["total_images"], 2)
        self.assertEqual(elo_sorted["total_images"], 2)
        self.assertNotIn(miss, [img["id"] for img in elo_sorted["images"]])

    async def test_rankings_search_loads_model_on_demand_when_worker_is_deferred(self):
        source = await self._source()
        match = await self._image(source["id"], "semantic-match.jpg")
        miss = await self._image(source["id"], "semantic-miss.jpg")
        for image_id in (match, miss):
            await self._cache_entry(image_id, "sm")

        image_ids = [match, miss]
        matrix = np.array([[0.90, 0.10], [0.10, 0.90]], dtype=np.float32)
        calls = {"encode": 0, "ensure": 0}

        async def fake_get_matrix():
            return image_ids, matrix

        def fake_encode_text(_query):
            calls["encode"] += 1
            if calls["encode"] == 1:
                return None
            return np.array([1.0, 0.0], dtype=np.float32)

        async def fake_ensure_model_loaded_for_search():
            calls["ensure"] += 1
            return True

        elo_propagation.embed_cache.get_matrix = fake_get_matrix
        embedding_worker.encode_text = fake_encode_text
        embedding_worker.ensure_model_loaded_for_search = fake_ensure_model_loaded_for_search

        result = await app_module.api_rankings(q="dog", sort="similarity", limit=10)

        self.assertEqual([img["id"] for img in result["images"]], [match])
        self.assertEqual(calls, {"encode": 2, "ensure": 1})
        self.assertEqual(result["search_mode"], "embedding")
        self.assertFalse(result["ai_unavailable"])

    async def test_normal_search_uses_fast_2b_role_even_when_saved_preset_is_8b(self):
        source = await self._source()
        match = await self._image(source["id"], "fast-role-match.jpg")
        miss = await self._image(source["id"], "fast-role-miss.jpg")
        for image_id in (match, miss):
            await self._cache_entry(image_id, "sm")

        app_module.settings.save_settings({"embed_model_preset": "qwen3-vl-embedding-8b"})
        fast_config = app_module.settings.fast_search_embedding_config()
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
        app_module._text_search_resolution_cache.clear()

        result = await app_module.api_rankings(q="semantic dog", sort="similarity", limit=10)

        self.assertEqual(result["search_mode"], "embedding")
        self.assertEqual([img["id"] for img in result["images"]], [match])
        self.assertIn(("encode", fast_config["model_key"]), calls)
        self.assertIn(("matrix", None), calls)
        self.assertEqual(db.active_embedding_model_key(), fast_config["model_key"])

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

        result = await app_module.mosaic_next(n=5, strategy="random", q="landscapes")
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

        result = await app_module.compare_next(n=2, mode="swiss", q="landscapes")
        pair_ids = {
            image["id"]
            for pair in result["pairs"]
            for image in (pair["left"], pair["right"])
        }

        self.assertEqual(pair_ids, {match_a, match_b})
        self.assertNotIn(miss, pair_ids)
        self.assertEqual(result["visible_images"], 2)
        self.assertEqual(result["total_images"], 2)

    async def test_metadata_fallback_constrains_compare_and_mosaic_pools(self):
        source = await self._source()
        match_a = await self._image(source["id"], "sunset-a.jpg", elo=1500)
        match_b = await self._image(source["id"], "sunset-b.jpg", elo=1400)
        miss = await self._image(source["id"], "portrait-miss.jpg", elo=1300)
        for image_id in (match_a, match_b, miss):
            await self._cache_entry(image_id, "sm")
            await self._cache_entry(image_id, "md")

        embedding_worker.encode_text = lambda _query: None

        mosaic = await app_module.mosaic_next(n=5, strategy="random", q="sunset")
        compare = await app_module.compare_next(n=2, mode="swiss", q="sunset")
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

    async def test_search_endpoint_uses_metadata_fallback_when_ai_is_cold(self):
        source = await self._source()
        visible_match = await self._image(source["id"], "sunset-visible.jpg")
        hidden_match = await self._image(source["id"], "sunset-hidden.jpg")
        await self._cache_entry(visible_match, "sm")

        embedding_worker.encode_text = lambda _query: None

        result = await app_module.api_search(q="sunset", limit=10)

        self.assertEqual([img["id"] for img in result["images"]], [visible_match])
        self.assertIsNone(result["images"][0]["similarity"])
        self.assertEqual(result["search_mode"], "metadata")
        self.assertTrue(result["ai_unavailable"])
        self.assertEqual(result["visible_images"], 1)
        self.assertEqual(result["total_images"], 2)
        self.assertEqual(result["hidden_pending_thumbnails"], 1)
        self.assertNotIn(hidden_match, [img["id"] for img in result["images"]])

    async def test_search_metadata_fallback_reuses_response_cache(self):
        source = await self._source()
        visible_match = await self._image(source["id"], "sunset-visible.jpg")
        await self._cache_entry(visible_match, "sm")
        embedding_worker.encode_text = lambda _query: None

        first = await app_module.api_search(q="sunset", limit=10)
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
            second = await app_module.api_search(q="sunset", limit=10)
        finally:
            db.get_rankings = old_get_rankings
            db.count_rankings = old_count_rankings

        self.assertEqual(second["images"], first["images"])
        self.assertEqual(second["visible_images"], first["visible_images"])
        self.assertEqual(second["total_images"], first["total_images"])

    async def test_search_logs_deep_search_query_for_later_embedding(self):
        source = await self._source()
        visible_match = await self._image(source["id"], "sunset-visible.jpg")
        await self._cache_entry(visible_match, "sm")
        embedding_worker.encode_text = lambda _query: None

        result = await app_module.api_search(q="sunset portrait", limit=10)
        pending = await db.get_pending_deep_search_queries(
            app_module.settings.deep_search_embedding_config(),
            [],
            limit=10,
        )

        self.assertEqual(result["search_mode"], "metadata")
        self.assertIn("sunset portrait", [row["query"] for row in pending])

    async def test_text_search_resolution_caches_fast_embedding_result(self):
        source = await self._source()
        match = await self._image(source["id"], "cached-fast-match.jpg")
        miss = await self._image(source["id"], "cached-fast-miss.jpg")
        for image_id in (match, miss):
            await self._cache_entry(image_id, "sm")

        calls = []

        def fake_encode(query, config=None):
            calls.append((query, (config or {}).get("model_key")))
            return np.array([1.0, 0.0], dtype=np.float32)

        async def fake_get_matrix(model_key=None):
            self.assertIsNone(model_key)
            return [match, miss], np.array(
                [
                    [0.95, 0.05],
                    [0.10, 0.90],
                ],
                dtype=np.float32,
            )

        embedding_worker.encode_text = fake_encode
        elo_propagation.embed_cache.get_matrix = fake_get_matrix
        app_module._text_search_resolution_cache.clear()

        first = await app_module.api_rankings(q="fast cached query", sort="similarity", limit=10)
        second = await app_module.api_rankings(q="fast cached query", sort="similarity", limit=10)

        self.assertEqual(len(calls), 1)
        self.assertEqual(first["search_mode"], "embedding")
        self.assertEqual(second["search_mode"], "embedding")
        self.assertEqual([img["id"] for img in second["images"]], [match])

    async def test_cached_text_search_resolution_does_not_relog_query_per_page(self):
        source = await self._source()
        visible_match = await self._image(source["id"], "repeat-query-visible.jpg")
        await self._cache_entry(visible_match, "sm")
        embedding_worker.encode_text = lambda _query, _config=None: None
        app_module._text_search_resolution_cache.clear()

        await app_module.api_rankings(q="repeat cache query", limit=1, offset=0)
        await app_module.api_rankings(q="repeat cache query", limit=1, offset=1)

        conn = await db.get_db()
        try:
            cursor = await conn.execute(
                "SELECT use_count FROM deep_search_queries WHERE query_key = ?",
                ("repeat cache query",),
            )
            row = await cursor.fetchone()
        finally:
            await conn.close()

        self.assertIsNotNone(row)
        self.assertEqual(int(row["use_count"]), 1)

    async def test_search_endpoint_does_not_relog_cached_query_response(self):
        source = await self._source()
        visible_match = await self._image(source["id"], "repeat-api-search-visible.jpg")
        await self._cache_entry(visible_match, "sm")
        embedding_worker.encode_text = lambda _query, _config=None: None
        app_module._rankings_response_cache.clear()
        app_module._deep_search_query_record_cache.clear()

        await app_module.api_search(q="repeat api query", limit=10)
        await app_module.api_search(q="repeat api query", limit=10)

        conn = await db.get_db()
        try:
            cursor = await conn.execute(
                "SELECT use_count FROM deep_search_queries WHERE query_key = ?",
                ("repeat api query",),
            )
            row = await cursor.fetchone()
        finally:
            await conn.close()

        self.assertIsNotNone(row)
        self.assertEqual(int(row["use_count"]), 1)

    async def test_search_query_logging_is_length_limited(self):
        source = await self._source()
        visible_match = await self._image(source["id"], "long-query-visible.jpg")
        await self._cache_entry(visible_match, "sm")
        embedding_worker.encode_text = lambda _query, _config=None: None
        app_module._deep_search_query_record_cache.clear()
        long_query = " ".join(["verylongquery"] * 40)

        await app_module.api_search(q=long_query, limit=10)

        conn = await db.get_db()
        try:
            cursor = await conn.execute(
                "SELECT query FROM deep_search_queries ORDER BY updated_at DESC LIMIT 1"
            )
            row = await cursor.fetchone()
        finally:
            await conn.close()

        self.assertIsNotNone(row)
        self.assertLessEqual(len(row["query"]), app_module.settings.MAX_DEEP_SEARCH_TERM_LENGTH)

    async def test_uncached_deep_search_queues_without_loading_active_model(self):
        query = "future semantic cache"

        def fail_encode(_query):
            raise AssertionError("uncached deep search should not encode with the active model")

        async def fail_ensure():
            raise AssertionError("uncached deep search should not load the active model")

        embedding_worker.encode_text = fail_encode
        embedding_worker.ensure_model_loaded_for_search = fail_ensure
        app_module._text_search_resolution_cache.clear()
        app_module._rankings_response_cache.clear()

        rankings = await app_module.api_rankings(q=query, deep=True, sort="similarity", limit=10)
        search = await app_module.api_search(q=query, deep=True, limit=10)
        pending = await db.get_pending_deep_search_queries(
            app_module.settings.deep_search_embedding_config(),
            [],
            limit=10,
        )

        for result in (rankings, search):
            self.assertEqual(result["search_mode"], "metadata")
            self.assertTrue(result["ai_unavailable"])
            self.assertTrue(result["deep_requested"])
            self.assertFalse(result["deep_search_cached"])
            self.assertEqual(result["fallback_reason"], "deep_search_not_cached")
        self.assertIn(query, [row["query"] for row in pending])

    async def test_compare_and_mosaic_deep_search_queue_without_loading_active_model(self):
        query = "deep compare cache"

        def fail_encode(_query, _config=None):
            raise AssertionError("uncached deep interaction search should not encode with the active model")

        async def fail_ensure():
            raise AssertionError("uncached deep interaction search should not load the active model")

        embedding_worker.encode_text = fail_encode
        embedding_worker.ensure_model_loaded_for_search = fail_ensure
        app_module._text_search_resolution_cache.clear()

        mosaic = await app_module.mosaic_next(q=query, deep=True, n=5)
        compare = await app_module.compare_next(q=query, deep=True, n=2)
        pending = await db.get_pending_deep_search_queries(
            app_module.settings.deep_search_embedding_config(),
            [],
            limit=10,
        )

        for result in (mosaic, compare):
            self.assertEqual(result["search_mode"], "metadata")
            self.assertTrue(result["ai_unavailable"])
            self.assertTrue(result["deep_requested"])
            self.assertFalse(result["deep_search_cached"])
            self.assertEqual(result["fallback_reason"], "deep_search_not_cached")
        self.assertIn(query, [row["query"] for row in pending])

    async def test_search_uses_cached_deep_embedding_without_loading_active_model(self):
        source = await self._source()
        match = await self._image(source["id"], "deep-match.jpg")
        miss = await self._image(source["id"], "deep-miss.jpg")
        for image_id in (match, miss):
            await self._cache_entry(image_id, "sm")

        config = app_module.settings.deep_search_embedding_config()
        dimension = int(config["dimension"])
        query_vec = np.zeros((dimension,), dtype=np.float32)
        query_vec[0] = 1.0
        match_vec = np.zeros((dimension,), dtype=np.float32)
        match_vec[0] = 0.95
        miss_vec = np.zeros((dimension,), dtype=np.float32)
        miss_vec[1] = 0.95

        conn = await db.get_db()
        try:
            await db._ensure_embedding_model_row(conn, config)
            await conn.executemany(
                "INSERT OR REPLACE INTO embeddings_by_model "
                "(model_key, image_id, embedding, dimension) VALUES (?, ?, ?, ?)",
                [
                    (config["model_key"], match, match_vec.tobytes(), dimension),
                    (config["model_key"], miss, miss_vec.tobytes(), dimension),
                ],
            )
            await conn.commit()
        finally:
            await conn.close()
        await db.store_deep_search_query_embedding(config, "specific deep query", query_vec.tobytes())

        def fail_encode(_query):
            raise AssertionError("cached deep search should not encode with active model")

        async def fail_ensure():
            raise AssertionError("cached deep search should not load the active model")

        embedding_worker.encode_text = fail_encode
        embedding_worker.ensure_model_loaded_for_search = fail_ensure
        app_module._text_search_resolution_cache.clear()
        app_module._rankings_response_cache.clear()

        result = await app_module.api_search(q="specific deep query", limit=1)

        self.assertEqual(result["search_mode"], "deep_embedding")
        self.assertTrue(result["deep_search_cached"])
        self.assertEqual([img["id"] for img in result["images"]], [match])

    async def test_cached_deep_search_uses_deep_index_mapping_when_fast_cache_is_warm(self):
        source = await self._source()
        match = await self._image(source["id"], "deep-map-match.jpg")
        miss = await self._image(source["id"], "deep-map-miss.jpg")
        for image_id in (match, miss):
            await self._cache_entry(image_id, "sm")

        deep_key = app_module.settings.deep_search_embedding_config()["model_key"]
        image_ids = [match, miss]
        similarities = np.array([0.95, 0.10], dtype=np.float32)
        old_resolve_cached_deep_search = app_module._resolve_cached_deep_search

        async def fake_resolve_cached_deep_search(_query):
            return {
                "id_filter": {match, miss},
                "scores": {match: 0.95, miss: 0.10},
                "image_ids": image_ids,
                "similarities": similarities,
                "search_mode": "deep_embedding",
                "deep_model_key": deep_key,
            }

        def fake_get_index(model_key=None):
            if model_key == deep_key:
                return {match: 0, miss: 1}
            return {match: 1, miss: 0}

        app_module._resolve_cached_deep_search = fake_resolve_cached_deep_search
        elo_propagation.embed_cache.get_index = fake_get_index
        try:
            result = await app_module.api_search(q="specific deep query", deep=True, limit=1)
        finally:
            app_module._resolve_cached_deep_search = old_resolve_cached_deep_search

        self.assertEqual(result["search_mode"], "deep_embedding")
        self.assertEqual([img["id"] for img in result["images"]], [match])

    async def test_storing_deep_query_embedding_invalidates_cached_fallback_search(self):
        source = await self._source()
        match = await self._image(source["id"], "later-deep-match.jpg")
        miss = await self._image(source["id"], "later-deep-miss.jpg")
        for image_id in (match, miss):
            await self._cache_entry(image_id, "sm")

        query = "deep ready later"
        config = app_module.settings.deep_search_embedding_config()
        dimension = int(config["dimension"])
        query_vec = np.zeros((dimension,), dtype=np.float32)
        query_vec[0] = 1.0
        match_vec = np.zeros((dimension,), dtype=np.float32)
        match_vec[0] = 0.95
        miss_vec = np.zeros((dimension,), dtype=np.float32)
        miss_vec[1] = 0.95
        await db.store_embeddings_batch(
            [
                (match, match_vec.tobytes()),
                (miss, miss_vec.tobytes()),
            ],
            embedding_config=config,
        )

        app_module._rankings_response_cache.clear()
        app_module._text_search_resolution_cache.clear()

        first = await app_module.api_search(q=query, deep=True, limit=1)

        self.assertEqual(first["search_mode"], "metadata")
        self.assertEqual(first["fallback_reason"], "deep_search_not_cached")
        self.assertTrue(app_module._rankings_response_cache)
        app_module._text_search_resolution_cache[("stale", True)] = {
            "data": {"search_mode": "metadata"},
            "expires": app_module.time.monotonic() + 300,
        }

        await db.store_deep_search_query_embedding(config, query, query_vec.tobytes())

        self.assertFalse(app_module._rankings_response_cache)
        self.assertFalse(app_module._text_search_resolution_cache)

        second = await app_module.api_search(q=query, deep=True, limit=1)

        self.assertEqual(second["search_mode"], "deep_embedding")
        self.assertTrue(second["deep_search_cached"])
        self.assertEqual([img["id"] for img in second["images"]], [match])

    async def test_storing_image_embeddings_invalidates_cached_deep_search_resolution(self):
        source = await self._source()
        first = await self._image(source["id"], "first-deep-vector.jpg")
        second = await self._image(source["id"], "second-deep-vector.jpg")

        query = "expanding deep index"
        config = app_module.settings.deep_search_embedding_config()
        dimension = int(config["dimension"])
        query_vec = np.zeros((dimension,), dtype=np.float32)
        query_vec[0] = 1.0
        first_vec = np.zeros((dimension,), dtype=np.float32)
        first_vec[0] = 0.80
        second_vec = np.zeros((dimension,), dtype=np.float32)
        second_vec[0] = 0.95

        await db.store_deep_search_query_embedding(config, query, query_vec.tobytes())
        await db.store_embeddings_batch([(first, first_vec.tobytes())], embedding_config=config)
        app_module._text_search_resolution_cache.clear()

        first_resolution = await app_module._resolve_text_search(query, deep=True)

        self.assertEqual(first_resolution["id_filter"], {first})
        self.assertTrue(app_module._text_search_resolution_cache)

        await db.store_embeddings_batch([(second, second_vec.tobytes())], embedding_config=config)

        self.assertFalse(app_module._text_search_resolution_cache)

        second_resolution = await app_module._resolve_text_search(query, deep=True)

        self.assertEqual(second_resolution["search_mode"], "deep_embedding")
        self.assertEqual(second_resolution["id_filter"], {first, second})
        self.assertGreater(second_resolution["scores"][second], second_resolution["scores"][first])

    async def test_embedding_batch_listener_invalidates_vector_derived_caches(self):
        app_module._duplicates_cache.update({"key": ("stale",), "data": {"pairs": []}})
        app_module._collections_cache.update({"key": ("stale",), "data": {"collections": []}})
        elo_propagation._prediction_cache_key = ("stale",)
        elo_propagation._prediction_cache_counts = {1: 10}

        app_module._embedding_batch_stored("model", [1])

        self.assertIsNone(app_module._duplicates_cache["key"])
        self.assertIsNone(app_module._duplicates_cache["data"])
        self.assertIsNone(app_module._collections_cache["key"])
        self.assertIsNone(app_module._collections_cache["data"])
        self.assertIsNone(elo_propagation._prediction_cache_key)
        self.assertIsNone(elo_propagation._prediction_cache_counts)

    async def test_embedding_model_change_invalidates_vector_derived_caches(self):
        app_module._duplicates_cache.update({"key": ("stale",), "data": {"pairs": []}})
        app_module._collections_cache.update({"key": ("stale",), "data": {"collections": []}})
        elo_propagation._prediction_cache_key = ("stale",)
        elo_propagation._prediction_cache_counts = {1: 10}

        await app_module.api_save_settings(JsonRequest({
            "embed_model_preset": "custom",
            "embed_model_id": "Local/Test-Embedding",
            "embed_model_revision": "main",
            "embed_model_dir": os.path.join(self.tempdir.name, "test-embedding-model"),
            "embed_model_dim": 128,
        }))

        self.assertIsNone(app_module._duplicates_cache["key"])
        self.assertIsNone(app_module._duplicates_cache["data"])
        self.assertIsNone(app_module._collections_cache["key"])
        self.assertIsNone(app_module._collections_cache["data"])
        self.assertIsNone(elo_propagation._prediction_cache_key)
        self.assertIsNone(elo_propagation._prediction_cache_counts)

    async def test_deep_image_index_is_independent_from_active_model_index(self):
        source = await self._source()
        image_id = await self._image(source["id"], "needs-deep-index.jpg")
        active_vec = np.ones((2048,), dtype=np.float32)
        await db.store_embeddings_batch([(image_id, active_vec.tobytes())])

        deep_config = app_module.settings.deep_search_embedding_config()
        pending_before = await db.get_unembedded_images(
            limit=10,
            embedding_config=deep_config,
        )

        self.assertEqual([row["id"] for row in pending_before], [image_id])

        deep_vec = np.ones((int(deep_config["dimension"]),), dtype=np.float32)
        await db.store_embeddings_batch(
            [(image_id, deep_vec.tobytes())],
            embedding_config=deep_config,
        )
        pending_after = await db.get_unembedded_images(
            limit=10,
            embedding_config=deep_config,
        )

        self.assertEqual(pending_after, [])

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

        app_module.thumbnails.prefetch_images = blocking_prefetch
        try:
            result = await asyncio.wait_for(app_module.api_search(q="sunset", limit=10), timeout=0.5)
            self.assertEqual([img["id"] for img in result["images"]], [match])
            await asyncio.wait_for(started.wait(), timeout=0.5)
        finally:
            release.set()
            await asyncio.sleep(0)

    async def test_similar_skips_uncached_sm_results_and_fills_later_visible_matches(self):
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

        async def fake_get_matrix():
            return image_ids, matrix

        def fake_get_index():
            return {image_id: idx for idx, image_id in enumerate(image_ids)}

        def fake_get_vector(image_id):
            return matrix[fake_get_index()[image_id]]

        elo_propagation.embed_cache.get_matrix = fake_get_matrix
        elo_propagation.embed_cache.get_index = fake_get_index
        elo_propagation.embed_cache.get_vector = fake_get_vector

        result = await app_module.api_similar(source_image, limit=2)

        self.assertEqual([img["id"] for img in result["images"]], [visible_first, visible_second])
        self.assertEqual(result["visible_images"], 2)
        self.assertEqual(result["total_images"], 4)
        self.assertEqual(result["hidden_pending_thumbnails"], 2)

    async def test_duplicates_reuses_cached_result_for_same_embedding_surface(self):
        source = await self._source()
        first = await self._image(source["id"], "dup-a.jpg", elo=1300)
        second = await self._image(source["id"], "dup-b.jpg", elo=1250)
        for image_id in (first, second):
            await self._cache_entry(image_id, "sm")

        image_ids = [first, second]
        matrix = np.array([[1.0, 0.0], [1.0, 0.0]], dtype=np.float32)

        async def fake_get_matrix():
            return image_ids, matrix

        old_get_active_images_by_ids = db.get_active_images_by_ids
        elo_propagation.embed_cache.get_matrix = fake_get_matrix
        app_module._duplicates_cache.update({"key": None, "data": None})
        first_result = await app_module.api_duplicates(threshold=0.95, limit=10)

        async def fail_get_active_images_by_ids(_ids):
            raise AssertionError("cached duplicate result should avoid refetching images")

        db.get_active_images_by_ids = fail_get_active_images_by_ids
        try:
            second_result = await app_module.api_duplicates(threshold=0.95, limit=10)
        finally:
            db.get_active_images_by_ids = old_get_active_images_by_ids
            app_module._duplicates_cache.update({"key": None, "data": None})

        self.assertEqual(first_result, second_result)
        self.assertEqual(first_result["visible_pairs"], 1)

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

        app_module.thumbnails.prefetch_images = fake_prefetch
        app_module.thumbnails.schedule_full_image_cache = fake_schedule_full

        result = await app_module.warm_images(JsonRequest({
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

    async def test_warm_images_skips_already_cached_thumbnail_ids(self):
        source = await self._source()
        cached = await self._image(source["id"], "cached.jpg")
        uncached = await self._image(source["id"], "uncached.jpg")
        await self._cache_entry(cached, "md")
        prefetch_calls = []

        async def fake_prefetch(rows, tier, limit=None, hot=False):
            prefetch_calls.append({
                "tier": tier,
                "ids": [row["id"] for row in rows],
                "limit": limit,
                "hot": hot,
            })
            return len(rows)

        app_module.thumbnails.prefetch_images = fake_prefetch

        result = await app_module.warm_images(JsonRequest({"tiers": {"md": [cached, uncached]}}))

        self.assertEqual(result["scheduled"], {"md": 1})
        self.assertEqual(prefetch_calls, [{
            "tier": "md",
            "ids": [uncached],
            "limit": 1,
            "hot": True,
        }])

    async def test_warm_images_is_best_effort_when_thumbnail_cache_is_locked(self):
        source = await self._source()
        image_id = await self._image(source["id"], "locked.jpg")

        async def locked_prefetch(*_args, **_kwargs):
            raise sqlite3.OperationalError("database is locked")

        app_module.thumbnails.prefetch_images = locked_prefetch

        result = await app_module.warm_images(JsonRequest({"tiers": {"md": [image_id]}}))

        self.assertEqual(result, {"scheduled": {"md": 0}, "images": 1})

    async def test_warm_images_is_best_effort_when_full_cache_is_locked(self):
        source = await self._source()
        image_id = await self._image(source["id"], "locked-full.jpg")

        async def locked_full(*_args, **_kwargs):
            raise sqlite3.OperationalError("database is locked")

        app_module.thumbnails.schedule_full_image_cache = locked_full

        result = await app_module.warm_images(JsonRequest({"tiers": {"full": [image_id]}}))

        self.assertEqual(result, {"scheduled": {"full": 0}, "images": 1})

    async def test_media_status_reports_cached_tiers_without_image_lookup(self):
        def fake_has_cached_fast(size, image_id):
            return image_id == 42 and size == "md"

        def fake_fast_disk_path_entry(size, image_id):
            if image_id == 42 and size == app_module.thumbnails.FULL_TIER:
                return ("sig", "/tmp/full.jpg")
            return None

        app_module.thumbnails.has_cached_fast = fake_has_cached_fast
        app_module.thumbnails.fast_disk_path_entry = fake_fast_disk_path_entry

        result = await app_module.image_media_status(42)

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

        old_has_cached_fast = app_module.thumbnails.has_cached_fast
        old_fast_disk_path_entry = app_module.thumbnails.fast_disk_path_entry
        app_module.thumbnails.has_cached_fast = fake_has_cached_fast
        app_module.thumbnails.fast_disk_path_entry = fake_fast_disk_path_entry
        try:
            result = await app_module.images_media_status(JsonRequest({"ids": [42, "42", "bad", 43]}))
        finally:
            app_module.thumbnails.has_cached_fast = old_has_cached_fast
            app_module.thumbnails.fast_disk_path_entry = old_fast_disk_path_entry

        self.assertEqual([status["id"] for status in result["statuses"]], [42, 43])
        self.assertTrue(result["statuses"][0]["tiers"]["sm"]["cached"])
        self.assertFalse(result["statuses"][1]["tiers"]["sm"]["cached"])

    async def test_thumbnail_matching_disk_etag_returns_304_without_reading_file(self):
        old_memory_get = app_module.thumbnails._memory_get_entry_fast
        old_path_entry = app_module.thumbnails.fast_disk_path_entry
        old_read_entry = app_module.thumbnails.fast_disk_read_entry

        def fake_memory_get(_size, _image_id):
            return None

        def fake_path_entry(size, image_id):
            self.assertEqual(size, "sm")
            self.assertEqual(image_id, 42)
            return ("sig-42", "/tmp/unused.jpg")

        def fail_read(*_args, **_kwargs):
            raise AssertionError("matching ETag should not read thumbnail bytes")

        app_module.thumbnails._memory_get_entry_fast = fake_memory_get
        app_module.thumbnails.fast_disk_path_entry = fake_path_entry
        app_module.thumbnails.fast_disk_read_entry = fail_read
        try:
            response = await app_module.serve_thumbnail(
                HeaderRequest({"if-none-match": '"sig-42"'}),
                "sm",
                42,
            )
        finally:
            app_module.thumbnails._memory_get_entry_fast = old_memory_get
            app_module.thumbnails.fast_disk_path_entry = old_path_entry
            app_module.thumbnails.fast_disk_read_entry = old_read_entry

        self.assertEqual(response.status_code, 304)

    async def test_cached_lg_thumbnail_uses_file_response_without_reading_bytes(self):
        old_memory_get = app_module.thumbnails._memory_get_entry_fast
        old_path_entry = app_module.thumbnails.fast_disk_path_entry
        old_read_entry = app_module.thumbnails.fast_disk_read_entry
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

        app_module.thumbnails._memory_get_entry_fast = fake_memory_get
        app_module.thumbnails.fast_disk_path_entry = fake_path_entry
        app_module.thumbnails.fast_disk_read_entry = fail_read
        try:
            response = await app_module.serve_thumbnail(HeaderRequest(), "lg", 42, cached=True)
        finally:
            app_module.thumbnails._memory_get_entry_fast = old_memory_get
            app_module.thumbnails.fast_disk_path_entry = old_path_entry
            app_module.thumbnails.fast_disk_read_entry = old_read_entry

        self.assertIsInstance(response, app_module.FileResponse)
        self.assertEqual(response.headers.get("etag"), '"sig-42"')

    async def test_cached_full_image_uses_file_response_without_image_lookup(self):
        old_path_entry = app_module.thumbnails.fast_disk_path_entry
        old_get_image = db.get_image_by_id
        full_path = os.path.join(self.tempdir.name, "full.jpg")
        with open(full_path, "wb") as f:
            f.write(b"jpeg")

        def fake_path_entry(size, image_id):
            self.assertEqual(size, app_module.thumbnails.FULL_TIER)
            self.assertEqual(image_id, 42)
            return ("full-sig-42", full_path)

        async def fail_get_image(_image_id):
            raise AssertionError("cached full image should not hit image lookup")

        app_module.thumbnails.fast_disk_path_entry = fake_path_entry
        db.get_image_by_id = fail_get_image
        try:
            response = await app_module.serve_full_image(HeaderRequest(), 42, app_module.BackgroundTasks())
        finally:
            app_module.thumbnails.fast_disk_path_entry = old_path_entry
            db.get_image_by_id = old_get_image

        self.assertIsInstance(response, app_module.FileResponse)
        self.assertEqual(response.headers.get("etag"), '"full-sig-42"')

    async def test_cached_full_image_matching_etag_returns_304_without_image_lookup(self):
        old_path_entry = app_module.thumbnails.fast_disk_path_entry
        old_get_image = db.get_image_by_id

        def fake_path_entry(size, image_id):
            self.assertEqual(size, app_module.thumbnails.FULL_TIER)
            self.assertEqual(image_id, 42)
            return ("full-sig-42", "/tmp/unused-full.jpg")

        async def fail_get_image(_image_id):
            raise AssertionError("matching full ETag should not hit image lookup")

        app_module.thumbnails.fast_disk_path_entry = fake_path_entry
        db.get_image_by_id = fail_get_image
        try:
            response = await app_module.serve_full_image(
                HeaderRequest({"if-none-match": '"full-sig-42"'}),
                42,
                app_module.BackgroundTasks(),
            )
        finally:
            app_module.thumbnails.fast_disk_path_entry = old_path_entry
            db.get_image_by_id = old_get_image

        self.assertEqual(response.status_code, 304)

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
                "root": app_module.thumbnails.SSD_CACHE_DIR,
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

        old_cache_stats = app_module.thumbnails.cache_stats
        old_pregen_status = app_module.thumbnails.get_pregen_status
        old_recommendations = app_module._cache_recommendations
        try:
            app_module.thumbnails.cache_stats = lambda: cache_stats
            app_module.thumbnails.get_pregen_status = fake_pregen_status
            app_module._cache_recommendations = (
                lambda _cache, eligible, total, browser, estimates=None: {
                    "eligible_images": eligible,
                    "total_images": total,
                    "browser_original_images": browser,
                    "tiers": {},
                }
            )

            result = await app_module.build_cache_status(ahead=0)
        finally:
            app_module.thumbnails.cache_stats = old_cache_stats
            app_module.thumbnails.get_pregen_status = old_pregen_status
            app_module._cache_recommendations = old_recommendations

        self.assertEqual(captured, {"target_total": 2, "original_total": 1})
        self.assertGreaterEqual(app_module._cache_status_cache_ttl_seconds, 30.0)
        self.assertGreaterEqual(app_module._browser_original_count_cache_ttl_seconds, 30.0)
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
        archive_estimates = {"needed_bytes": {app_module.thumbnails.FULL_TIER: 10000}}

        result = app_module.thumbnails._original_cache_status(
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

        self.assertEqual(app_module._cache_status_ttl(result), 1.0)

    async def test_cache_status_ttl_is_short_while_running(self):
        result = {
            "pregen": {
                "state": "running",
                "enabled": True,
                "manual_pause": False,
                "active_phase": "previews",
            }
        }

        self.assertEqual(app_module._cache_status_ttl(result), 2.0)

    async def test_cache_status_caps_ahead_window(self):
        result = await app_module.build_cache_status(
            ahead=app_module._cache_status_ahead_limit + 100
        )

        self.assertEqual(result["window"], app_module._cache_status_ahead_limit)

    async def test_cache_pregen_status_reuses_cached_status_builder(self):
        first = await app_module.build_cache_status(ahead=0)
        result = await app_module.cache_pregen_status()

        self.assertEqual(result["state"], first["pregen"]["state"])
        self.assertIn("preview", result)

    async def test_interaction_cache_warmup_starts_quickly_after_startup(self):
        self.assertLessEqual(app_module.INTERACTION_CACHE_WARMUP_DELAY_SECONDS, 0.05)
        self.assertGreaterEqual(app_module._visible_pairing_candidates_cache_ttl_seconds, 5.0)

    async def test_light_startup_warmup_does_not_cold_load_deep_diverse_mosaic(self):
        startup_source = inspect.getsource(app_module.startup)
        light_warmup = startup_source.split(
            "async def _warm_light_startup_caches():",
            1,
        )[1].split("async def _wait_for_background_window", 1)[0]

        self.assertNotIn('strategy="diverse"', light_warmup)
        self.assertIn('strategy="explore"', light_warmup)

    async def test_library_cross_view_warmup_does_not_request_diverse_mosaic(self):
        with open(os.path.join(os.path.dirname(__file__), "static", "app.js"), encoding="utf-8") as fh:
            script = fh.read()
        warmup = script.split("function scheduleCrossViewWarmup(fromView)", 1)[1].split(
            "function scheduleLibraryNeighborWarmup",
            1,
        )[0]

        self.assertIn("const warmStrategy = mosaicStrategy === 'diverse' ? 'explore' : mosaicStrategy;", warmup)
        self.assertIn("strategy: warmStrategy", warmup)
        self.assertNotIn("strategy: mosaicStrategy", warmup)

    async def test_filtered_compare_window_is_smaller_than_default_window(self):
        self.assertLess(app_module._FILTERED_SWISS_PAIR_WINDOW, app_module._SWISS_PAIR_WINDOW)
        self.assertGreaterEqual(app_module._FILTERED_SWISS_PAIR_WINDOW, 256)

    async def test_filtered_mosaic_window_is_bounded_but_not_tiny(self):
        self.assertLess(app_module._FILTERED_MOSAIC_WINDOW, app_module._MOSAIC_EXPLORE_WINDOW)
        self.assertGreaterEqual(app_module._FILTERED_MOSAIC_WINDOW, 128)
        self.assertGreaterEqual(app_module._MOSAIC_EXPLORE_WINDOW, 768)

    async def test_shutdown_cancels_tracked_background_tasks(self):
        cancelled = asyncio.Event()

        async def waits_forever():
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        task = app_module._track_background_task(waits_forever())
        self.assertIn(task, app_module._BACKGROUND_TASKS)
        await asyncio.sleep(0)

        await app_module.shutdown()

        self.assertTrue(cancelled.is_set())
        self.assertTrue(task.cancelled())
        self.assertEqual(app_module._BACKGROUND_TASKS, set())

    async def test_ui_settings_returns_default_loupe_cache_status(self):
        result = await app_module.api_ui_settings()

        self.assertEqual(result, {"settings": {"show_loupe_cache_status": True}})

    async def test_loupe_cache_status_setting_persists(self):
        saved = app_module.settings.save_settings({"show_loupe_cache_status": False})
        self.assertFalse(saved["show_loupe_cache_status"])

        reloaded = app_module.settings.load_settings(force=True)
        result = await app_module.api_ui_settings()

        self.assertFalse(reloaded["show_loupe_cache_status"])
        self.assertEqual(result, {"settings": {"show_loupe_cache_status": False}})

    async def test_deep_search_terms_are_normalized_and_persisted(self):
        saved = app_module.settings.save_settings({
            "deep_search_terms": [
                " Crane ",
                "crane",
                "",
                "  black   and   white portraits  ",
            ]
        })

        self.assertEqual(saved["deep_search_terms"], ["Crane", "black and white portraits"])

        reloaded = app_module.settings.load_settings(force=True)

        self.assertEqual(reloaded["deep_search_terms"], ["Crane", "black and white portraits"])

    async def test_api_settings_returns_deep_search_terms(self):
        response = await app_module.api_save_settings(JsonRequest({
            "deep_search_terms": "crane\ncat in cafe window\nCRANE\n",
        }))

        self.assertEqual(response["settings"]["deep_search_terms"], ["crane", "cat in cafe window"])

        refreshed = await app_module.api_settings()

        self.assertEqual(refreshed["settings"]["deep_search_terms"], ["crane", "cat in cafe window"])

    async def test_deep_search_schedule_defaults_to_weekday_central_work_hours(self):
        normalized = app_module.settings.normalize_settings({})

        self.assertTrue(normalized["deep_search_schedule_enabled"])
        self.assertEqual(normalized["deep_search_schedule_days"], ["mon", "tue", "wed", "thu", "fri"])
        self.assertEqual(normalized["deep_search_schedule_start"], "07:00")
        self.assertEqual(normalized["deep_search_schedule_end"], "16:00")
        self.assertEqual(normalized["deep_search_schedule_timezone"], "America/Chicago")

    async def test_background_work_mode_defaults_and_normalizes(self):
        normalized = app_module.settings.normalize_settings({})
        self.assertEqual(normalized["background_work_mode"], "balanced")

        saved = app_module.settings.save_settings({"background_work_mode": "MAX"})
        self.assertEqual(saved["background_work_mode"], "max")

        fallback = app_module.settings.save_settings({"background_work_mode": "turbo"})
        self.assertEqual(fallback["background_work_mode"], "balanced")

    async def test_settings_metadata_lists_background_work_modes(self):
        metadata = app_module.settings.settings_metadata()
        modes = metadata["background_work_modes"]

        self.assertEqual([mode["key"] for mode in modes], ["browse", "balanced", "max"])
        self.assertTrue(all(mode.get("label") for mode in modes))

    async def test_deep_search_schedule_status_uses_weekday_central_window(self):
        config = app_module.settings.normalize_settings({})

        monday_morning = datetime(2026, 5, 18, 8, 0, tzinfo=ZoneInfo("America/Chicago"))
        monday_evening = datetime(2026, 5, 18, 17, 0, tzinfo=ZoneInfo("America/Chicago"))
        saturday_morning = datetime(2026, 5, 16, 8, 0, tzinfo=ZoneInfo("America/Chicago"))

        self.assertTrue(app_module.settings.deep_search_schedule_status(config, monday_morning)["active"])
        self.assertEqual(
            app_module.settings.deep_search_schedule_status(config, monday_evening)["reason"],
            "outside_time_window",
        )
        self.assertEqual(
            app_module.settings.deep_search_schedule_status(config, saturday_morning)["reason"],
            "outside_selected_days",
        )

    async def test_deep_search_schedule_is_normalized_and_persisted(self):
        saved = app_module.settings.save_settings({
            "deep_search_schedule_enabled": "true",
            "deep_search_schedule_days": ["Friday", "monday", "bad", "fri"],
            "deep_search_schedule_start": "7:00",
            "deep_search_schedule_end": "16:00:00",
            "deep_search_schedule_timezone": "CST",
        })

        self.assertTrue(saved["deep_search_schedule_enabled"])
        self.assertEqual(saved["deep_search_schedule_days"], ["mon", "fri"])
        self.assertEqual(saved["deep_search_schedule_start"], "07:00")
        self.assertEqual(saved["deep_search_schedule_end"], "16:00")
        self.assertEqual(saved["deep_search_schedule_timezone"], "America/Chicago")

        reloaded = app_module.settings.load_settings(force=True)

        self.assertEqual(reloaded["deep_search_schedule_days"], ["mon", "fri"])
        self.assertEqual(reloaded["deep_search_schedule_timezone"], "America/Chicago")

    async def test_ai_status_reports_fast_and_deep_embedding_indexes(self):
        source = await self._source()
        image_id = await self._image(source["id"], "dual-index.jpg")
        fast_config = app_module.settings.fast_search_embedding_config()
        deep_config = app_module.settings.deep_search_embedding_config()

        await db.store_embeddings_batch([(image_id, b"fast-vector")], embedding_config=fast_config)
        await db.store_embeddings_batch([(image_id, b"deep-vector")], embedding_config=deep_config)
        await db.record_deep_search_query("queued only")
        await db.store_deep_search_query_embedding(deep_config, "ready query", b"query-vector")
        app_module._invalidate_ai_status_response_cache()

        status = await app_module.build_ai_status(force=True)
        indexes = status["embedding_indexes"]
        deep_queries = {item["query"]: item for item in indexes["deep"]["queries"]}

        self.assertEqual(indexes["fast"]["model_key"], fast_config["model_key"])
        self.assertEqual(indexes["fast"]["dimension"], 2048)
        self.assertEqual(indexes["fast"]["embedded"], 1)
        self.assertEqual(indexes["fast"]["remaining"], 0)
        self.assertEqual(indexes["deep"]["model_key"], deep_config["model_key"])
        self.assertEqual(indexes["deep"]["dimension"], 4096)
        self.assertEqual(indexes["deep"]["embedded"], 1)
        self.assertEqual(indexes["deep"]["remaining"], 0)
        self.assertFalse(deep_queries["queued only"]["cached"])
        self.assertTrue(deep_queries["ready query"]["cached"])

    async def test_ai_status_reports_deep_model_install_progress_on_deep_index(self):
        fast_config = app_module.settings.fast_search_embedding_config()
        deep_config = app_module.settings.deep_search_embedding_config()
        install_state = {
            "running": True,
            "status": "downloading",
            "message": "Downloading Qwen/Qwen3-VL-Embedding-8B",
            "model_id": deep_config["model_id"],
            "revision": deep_config["revision"],
            "model_dir": deep_config["model_dir"],
            "started_at": 1.0,
            "finished_at": None,
            "last_error": "",
        }
        old_get_model_status = app_module.ai_models.get_model_status

        def fake_get_model_status(config=None):
            cfg = config or fast_config
            return {
                "model_id": cfg["model_id"],
                "revision": cfg["revision"],
                "model_dir": cfg["model_dir"],
                "dimension": int(cfg["dimension"]),
                "model_key": cfg["model_key"],
                "installed": False,
                "install": dict(install_state),
            }

        app_module.ai_models.get_model_status = fake_get_model_status
        app_module._invalidate_ai_status_response_cache()
        try:
            status = await app_module.build_ai_status(force=True)
        finally:
            app_module.ai_models.get_model_status = old_get_model_status
            app_module._invalidate_ai_status_response_cache()

        indexes = status["embedding_indexes"]
        self.assertFalse(indexes["fast"]["installing"])
        self.assertEqual(indexes["fast"]["install_status"], "idle")
        self.assertTrue(indexes["deep"]["installing"])
        self.assertEqual(indexes["deep"]["install_status"], "downloading")
        self.assertIn("8B", indexes["deep"]["install_message"])

    async def test_install_model_reports_conflict_when_other_model_is_downloading(self):
        fast_config = app_module.settings.fast_search_embedding_config()
        old_start_model_install = app_module.ai_models.start_model_install
        install_state = {
            "running": True,
            "status": "downloading",
            "message": "Downloading fast model",
            "model_id": fast_config["model_id"],
            "revision": fast_config["revision"],
            "model_dir": fast_config["model_dir"],
            "started_at": 1.0,
            "finished_at": None,
            "last_error": "",
        }

        app_module.ai_models.start_model_install = lambda _config: dict(install_state)
        try:
            response = await app_module.api_install_ai_model(role="deep")
        finally:
            app_module.ai_models.start_model_install = old_start_model_install

        body = app_module.json.loads(response.body)
        self.assertEqual(response.status_code, 409)
        self.assertFalse(body["ok"])
        self.assertIn("already running", body["error"])

    async def test_defer_ai_on_startup_setting_controls_embedding_pause(self):
        await app_module.api_save_settings(JsonRequest({"defer_ai_on_startup": False}))
        self.assertFalse(embedding_worker.get_worker_status()["manual_pause"])

        response = await app_module.api_save_settings(JsonRequest({"defer_ai_on_startup": True}))

        self.assertTrue(response["settings"]["defer_ai_on_startup"])
        self.assertTrue(response["ai_status"]["embedding_manual_pause"])
        self.assertEqual(response["ai_status"]["worker_message"], "AI work deferred by startup setting.")

    async def test_settings_reads_do_not_expose_cached_state(self):
        first = app_module.settings.get_settings()
        first["thumb_quality"] = 40
        first["deep_search_terms"].append("mutated")

        second = app_module.settings.get_settings()

        self.assertNotEqual(second["thumb_quality"], 40)
        self.assertNotIn("mutated", second["deep_search_terms"])

    async def test_template_context_versions_static_assets(self):
        context = app_module._template_context(HeaderRequest())

        self.assertIn("static_version", context)
        self.assertTrue(str(context["static_version"]).isdigit())

    async def test_api_settings_cache_returns_independent_responses_and_invalidates(self):
        first = await app_module.api_settings()
        self.assertIsNotNone(app_module._settings_response_cache["data"])

        second = await app_module.api_settings()
        second["settings"] = {"thumb_quality": 40}
        third = await app_module.api_settings()
        self.assertNotEqual(third["settings"], {"thumb_quality": 40})

        target_quality = 80 if third["settings"]["thumb_quality"] != 80 else 79
        await app_module.api_save_settings(JsonRequest({"thumb_quality": target_quality}))
        refreshed = await app_module.api_settings()

        self.assertEqual(refreshed["settings"]["thumb_quality"], target_quality)

    async def test_api_settings_cache_protects_nested_responses(self):
        first = await app_module.api_settings()
        self.assertIsNotNone(app_module._settings_response_cache["data"])

        first["settings"]["thumb_quality"] = 40
        first["cache_stats"]["disk"]["tiers"]["sm"]["count"] = 999999
        first["catalog"]["sources"].append({"id": 999999})

        second = await app_module.api_settings()

        self.assertNotEqual(second["settings"]["thumb_quality"], 40)
        self.assertNotEqual(second["cache_stats"]["disk"]["tiers"]["sm"]["count"], 999999)
        self.assertNotIn({"id": 999999}, second["catalog"]["sources"])

    async def test_api_settings_returns_stale_cache_while_refreshing(self):
        stale = {
            "settings": {"thumb_quality": 40},
            "cache_stats": {},
            "model_status": {},
            "ai_status": {},
            "catalog": {},
            **app_module.settings.settings_metadata(),
        }
        fresh = {
            "settings": {"thumb_quality": 80},
            "cache_stats": {},
            "model_status": {},
            "ai_status": {},
            "catalog": {},
            **app_module.settings.settings_metadata(),
        }
        app_module._settings_response_cache["data"] = stale
        app_module._settings_response_cache["expires"] = 0
        old_build_settings_response = app_module._build_settings_response

        async def fake_build_settings_response():
            await asyncio.sleep(0)
            return fresh

        app_module._build_settings_response = fake_build_settings_response
        try:
            response = await app_module.api_settings()
            self.assertEqual(response["settings"]["thumb_quality"], 40)
            await asyncio.sleep(0.01)
            self.assertEqual(
                app_module._settings_response_cache["data"]["settings"]["thumb_quality"],
                80,
            )
        finally:
            app_module._build_settings_response = old_build_settings_response

    async def test_cache_status_invalidation_expires_settings_without_dropping_stale_data(self):
        first = await app_module.api_settings()
        self.assertIsNotNone(app_module._settings_response_cache["data"])
        self.assertGreater(app_module._settings_response_cache["expires"], 0)

        app_module._invalidate_cache_status_cache()

        self.assertIsNotNone(app_module._settings_response_cache["data"])
        self.assertEqual(app_module._settings_response_cache["expires"], 0)

        second = await app_module.api_settings()
        self.assertEqual(second["settings"], first["settings"])
        for _ in range(20):
            if not app_module._settings_response_refreshing:
                break
            await asyncio.sleep(0.01)

    async def test_cache_status_cache_protects_nested_responses(self):
        first = await app_module.build_cache_status(ahead=0)
        self.assertTrue(app_module._cache_status_cache)

        first["disk"]["tiers"]["sm"]["count"] = 999999
        first["pregen"]["phases"]["sm"]["count"] = 999999
        first["system_resources"]["disk"]["free_bytes"] = -1

        second = await app_module.build_cache_status(ahead=0)

        self.assertNotEqual(second["disk"]["tiers"]["sm"]["count"], 999999)
        self.assertNotEqual(second["pregen"]["phases"]["sm"]["count"], 999999)
        self.assertNotEqual(second["system_resources"]["disk"]["free_bytes"], -1)

    async def test_search_runtime_settings_invalidate_cached_search_results(self):
        app_module._rankings_response_cache[("stale",)] = {
            "data": {"images": []},
            "expires": app_module.time.monotonic() + 100,
        }
        app_module._text_search_resolution_cache[("stale", False)] = {
            "data": {"search_mode": "embedding"},
            "expires": app_module.time.monotonic() + 100,
        }

        await app_module.api_save_settings(JsonRequest({"search_similarity_threshold": 0.55}))

        self.assertFalse(app_module._rankings_response_cache)
        self.assertFalse(app_module._text_search_resolution_cache)

    async def test_thumbnail_disk_stats_cache_protects_nested_tiers(self):
        first = app_module.thumbnails.cache_stats()

        first["disk"]["tiers"]["sm"]["count"] = 999999
        second = app_module.thumbnails.cache_stats()

        self.assertNotEqual(second["disk"]["tiers"]["sm"]["count"], 999999)

    async def test_ai_status_response_cache_protects_nested_responses(self):
        first = await app_module.build_ai_status()
        self.assertIsNotNone(app_module._ai_status_response_cache["data"])

        first["last_batch_stage_seconds"]["db"] = 999999
        first["governor"]["reason"] = "mutated"

        second = await app_module.build_ai_status()

        self.assertNotEqual(second["last_batch_stage_seconds"].get("db"), 999999)
        self.assertNotEqual(second["governor"].get("reason"), "mutated")

    async def test_ai_status_counts_reuse_warm_stats_cache(self):
        db._stats_cache["data"] = {
            "active_images": 42,
            "rated_images": 7,
            "direct_comparison_rows": 5,
            "ranking_signal_count": 9,
            "imported_ranking_without_history": 2,
        }
        db._stats_cache["expires"] = db._time.time() + db.STATS_CACHE_TTL_SECONDS
        db._ai_status_counts_cache["data"] = None
        db._ai_status_counts_cache["expires"] = 0

        counts = await db.get_ai_status_counts()

        self.assertEqual(counts["total_images"], 42)
        self.assertEqual(counts["rated_images"], 7)
        self.assertEqual(counts["direct_comparison_rows"], 5)
        self.assertEqual(counts["ranking_signal_count"], 9)
        self.assertEqual(counts["imported_ranking_without_history"], 2)

    async def test_ai_status_counts_reuse_stale_stats_cache_while_refreshing(self):
        db._stats_cache["data"] = {
            "active_images": 42,
            "rated_images": 7,
            "direct_comparison_rows": 5,
            "ranking_signal_count": 9,
            "imported_ranking_without_history": 2,
        }
        db._stats_cache["expires"] = db._time.time() - 1
        db._ai_status_counts_cache["data"] = None
        db._ai_status_counts_cache["expires"] = 0
        db._embedding_count_cache["value"] = 11
        db._embedding_count_cache["expires"] = db._time.time() + db.EMBEDDING_COUNT_CACHE_TTL_SECONDS
        db._stats_inflight_task = None
        started = asyncio.Event()
        release = asyncio.Event()
        old_get_stats_uncached = db._get_stats_uncached

        async def fake_get_stats_uncached():
            started.set()
            await release.wait()
            return db._stats_cache["data"]

        db._get_stats_uncached = fake_get_stats_uncached
        try:
            counts = await db.get_ai_status_counts()
            await asyncio.wait_for(started.wait(), timeout=1)

            self.assertEqual(counts["embedded"], 11)
            self.assertEqual(counts["total_images"], 42)
            self.assertEqual(counts["rated_images"], 7)
            self.assertEqual(counts["direct_comparison_rows"], 5)
            self.assertEqual(counts["ranking_signal_count"], 9)
            self.assertEqual(counts["imported_ranking_without_history"], 2)
        finally:
            release.set()
            task = db._stats_inflight_task
            if task is not None and not task.done():
                await task
            db._stats_inflight_task = None
            db._get_stats_uncached = old_get_stats_uncached
            db.invalidate_stats_cache()


if __name__ == "__main__":
    unittest.main()
