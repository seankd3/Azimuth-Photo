import os
import sqlite3
import sys
import tempfile
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import embed_cache  # noqa: E402
from data.repositories import embeddings as embedding_repository  # noqa: E402


class EmbedCacheTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.old_cache = embed_cache._cache
        self.old_caches = embed_cache._caches
        self.old_active_model_key = embed_cache._active_embedding_model_key
        self.old_db_path = embed_cache._db_path
        self.old_get_embedding_count_sync = embed_cache._get_embedding_count_sync
        self.old_load_embeddings_sync = embed_cache._load_embeddings_sync

        embed_cache._cache = embed_cache._empty_cache()
        embed_cache._caches = {}
        embed_cache.configure(
            active_embedding_model_key=lambda: "fast-model",
            db_path=lambda: "/tmp/photoarchive-test.db",
        )

    async def asyncTearDown(self):
        embed_cache._cache = self.old_cache
        embed_cache._caches = self.old_caches
        embed_cache._active_embedding_model_key = self.old_active_model_key
        embed_cache._db_path = self.old_db_path
        embed_cache._get_embedding_count_sync = self.old_get_embedding_count_sync
        embed_cache._load_embeddings_sync = self.old_load_embeddings_sync

    async def test_matrix_cache_keeps_fast_and_deep_surfaces_warm(self):
        loads = []
        matrices = {
            "fast-model": np.array([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32),
            "deep-model": np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]], dtype=np.float32),
        }

        embed_cache._get_embedding_count_sync = lambda _model_key: 2

        def fake_load_embeddings_sync(_expected_count, model_key):
            loads.append(model_key)
            return [1, 2], matrices[model_key].copy()

        embed_cache._load_embeddings_sync = fake_load_embeddings_sync

        fast_ids, fast_matrix = await embed_cache.get_matrix()
        deep_ids, deep_matrix = await embed_cache.get_matrix("deep-model")
        warm_fast_ids, warm_fast_matrix = await embed_cache.get_matrix()

        self.assertEqual(loads, ["fast-model", "deep-model"])
        self.assertEqual(fast_ids, [1, 2])
        self.assertEqual(deep_ids, [1, 2])
        self.assertEqual(warm_fast_ids, [1, 2])
        self.assertEqual(fast_matrix.shape, (2, 2))
        self.assertEqual(deep_matrix.shape, (2, 3))
        self.assertEqual(warm_fast_matrix.shape, (2, 2))
        self.assertEqual(embed_cache.get_index("deep-model"), {1: 0, 2: 1})

    async def test_add_vectors_updates_model_cache_without_switching_active_view(self):
        matrices = {
            "fast-model": np.array([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32),
            "deep-model": np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]], dtype=np.float32),
        }

        embed_cache._get_embedding_count_sync = lambda _model_key: 2
        embed_cache._load_embeddings_sync = (
            lambda _expected_count, model_key: ([1, 2], matrices[model_key].copy())
        )

        await embed_cache.get_matrix("deep-model")
        await embed_cache.get_matrix()

        embed_cache.add_vectors(
            [(3, np.array([0.0, 0.0, 1.0], dtype=np.float32))],
            model_key="deep-model",
        )

        self.assertEqual(embed_cache._cache["model_key"], "fast-model")
        self.assertNotIn(3, embed_cache.get_index())
        self.assertEqual(embed_cache.get_index("deep-model")[3], 2)
        self.assertEqual(embed_cache.get_vector(3, "deep-model").tolist(), [0.0, 0.0, 1.0])

    async def test_add_vectors_replaces_existing_warm_vector(self):
        embed_cache._get_embedding_count_sync = lambda _model_key: 2
        embed_cache._load_embeddings_sync = (
            lambda _expected_count, _model_key: (
                [1, 2],
                np.array([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32),
            )
        )

        await embed_cache.get_matrix()

        embed_cache.invalidate()
        embed_cache.add_vectors([(1, np.array([0.25, 0.75], dtype=np.float32))])

        self.assertEqual(embed_cache.get_vector(1).tolist(), [0.25, 0.75])
        self.assertEqual(embed_cache.get_vector(2).tolist(), [0.0, 1.0])
        self.assertEqual(embed_cache._cache["count"], 2)

    async def test_get_warm_matrix_ignores_invalidated_cache(self):
        embed_cache._get_embedding_count_sync = lambda _model_key: 2
        embed_cache._load_embeddings_sync = (
            lambda _expected_count, _model_key: (
                [1, 2],
                np.array([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32),
            )
        )

        await embed_cache.get_matrix()
        embed_cache.invalidate()

        image_ids, matrix = embed_cache.get_warm_matrix()

        self.assertIsNone(image_ids)
        self.assertIsNone(matrix)

    async def test_offline_source_embeddings_stay_searchable(self):
        embed_cache._get_embedding_count_sync = self.old_get_embedding_count_sync
        embed_cache._load_embeddings_sync = self.old_load_embeddings_sync

        with tempfile.TemporaryDirectory() as tempdir:
            db_path = os.path.join(tempdir, "photoarchive.db")
            conn = sqlite3.connect(db_path)
            try:
                conn.executescript(
                    """
                    CREATE TABLE catalog_sources (
                        id INTEGER PRIMARY KEY,
                        included INTEGER NOT NULL,
                        online INTEGER NOT NULL
                    );
                    CREATE TABLE images (
                        id INTEGER PRIMARY KEY,
                        source_id INTEGER NOT NULL,
                        missing_at REAL
                    );
                    CREATE TABLE embeddings_by_model (
                        model_key TEXT NOT NULL,
                        image_id INTEGER NOT NULL,
                        embedding BLOB NOT NULL,
                        dimension INTEGER NOT NULL,
                        PRIMARY KEY (model_key, image_id)
                    );
                    """
                )
                conn.execute(
                    "INSERT INTO catalog_sources (id, included, online) VALUES (1, 1, 0)"
                )
                conn.execute(
                    "INSERT INTO images (id, source_id, missing_at) VALUES (7, 1, NULL)"
                )
                conn.execute(
                    "INSERT INTO embeddings_by_model "
                    "(model_key, image_id, embedding, dimension) VALUES (?, ?, ?, ?)",
                    (
                        "fast-model",
                        7,
                        np.array([0.25, 0.75], dtype=np.float32).tobytes(),
                        2,
                    ),
                )
                conn.commit()
            finally:
                conn.close()

            embed_cache.configure(
                active_embedding_model_key=lambda: "fast-model",
                db_path=lambda: db_path,
            )

            self.assertEqual(embed_cache._get_embedding_count_sync("fast-model"), 1)
            image_ids, matrix = embed_cache._load_embeddings_sync(1, "fast-model")

        self.assertEqual(image_ids, [7])
        self.assertGreaterEqual(matrix.shape[0], 1)
        self.assertEqual(matrix.shape[1], 2)
        self.assertEqual(matrix[0].tolist(), [0.25, 0.75])

    async def test_semantic_result_cache_invalidates_when_embedding_count_changes(self):
        with tempfile.TemporaryDirectory() as tempdir:
            db_path = os.path.join(tempdir, "photoarchive.db")
            conn = sqlite3.connect(db_path)
            try:
                conn.executescript(
                    """
                    CREATE TABLE catalog_sources (
                        id INTEGER PRIMARY KEY,
                        included INTEGER NOT NULL,
                        online INTEGER NOT NULL
                    );
                    CREATE TABLE images (
                        id INTEGER PRIMARY KEY,
                        source_id INTEGER NOT NULL,
                        missing_at REAL
                    );
                    CREATE TABLE embeddings_by_model (
                        model_key TEXT NOT NULL,
                        image_id INTEGER NOT NULL,
                        embedding BLOB NOT NULL,
                        dimension INTEGER NOT NULL,
                        PRIMARY KEY (model_key, image_id)
                    );
                    CREATE TABLE semantic_search_result_cache (
                        model_key TEXT NOT NULL,
                        query_key TEXT NOT NULL,
                        query TEXT NOT NULL,
                        threshold_key TEXT NOT NULL,
                        threshold REAL NOT NULL,
                        embedding_count INTEGER NOT NULL,
                        result_count INTEGER NOT NULL,
                        scores_json TEXT NOT NULL,
                        source TEXT NOT NULL DEFAULT 'fast',
                        created_at REAL NOT NULL DEFAULT (strftime('%s', 'now')),
                        updated_at REAL NOT NULL DEFAULT (strftime('%s', 'now')),
                        PRIMARY KEY (model_key, query_key, threshold_key)
                    );
                    """
                )
                conn.execute(
                    "INSERT INTO catalog_sources (id, included, online) VALUES (1, 1, 0)"
                )
                conn.execute(
                    "INSERT INTO images (id, source_id, missing_at) VALUES (7, 1, NULL)"
                )
                conn.execute(
                    "INSERT INTO images (id, source_id, missing_at) VALUES (8, 1, NULL)"
                )
                conn.execute(
                    "INSERT INTO embeddings_by_model "
                    "(model_key, image_id, embedding, dimension) VALUES (?, ?, ?, ?)",
                    (
                        "fast-model",
                        7,
                        np.array([0.25, 0.75], dtype=np.float32).tobytes(),
                        2,
                    ),
                )
                conn.commit()
            finally:
                conn.close()

            stored = await embedding_repository.store_cached_semantic_search_results(
                db_path,
                " Night ",
                "fast-model",
                0.4,
                {7: 0.91},
            )
            cached = await embedding_repository.get_cached_semantic_search_results(
                db_path,
                "night",
                "fast-model",
                0.4,
            )

            self.assertEqual(stored["embedding_count"], 1)
            self.assertEqual(cached["id_filter"], {7})
            self.assertEqual(cached["scores"], {7: 0.91})
            self.assertEqual(cached["source"], "fast")

            conn = sqlite3.connect(db_path)
            try:
                conn.execute(
                    "INSERT INTO embeddings_by_model "
                    "(model_key, image_id, embedding, dimension) VALUES (?, ?, ?, ?)",
                    (
                        "fast-model",
                        8,
                        np.array([0.1, 0.9], dtype=np.float32).tobytes(),
                        2,
                    ),
                )
                conn.commit()
            finally:
                conn.close()

            stale = await embedding_repository.get_cached_semantic_search_results(
                db_path,
                "night",
                "fast-model",
                0.4,
            )

        self.assertIsNone(stale)


if __name__ == "__main__":
    unittest.main()
