import os
import sqlite3
import sys
import tempfile
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import embed_cache  # noqa: E402


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
            db_path=lambda: "/tmp/azimuth-test.db",
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
            db_path = os.path.join(tempdir, "azimuth.db")
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

    async def test_snapshot_load_uses_file_backed_mmap(self):
        old_snapshot_dir = embed_cache.SNAPSHOT_DIR
        old_signature = embed_cache._embedding_source_signature
        with tempfile.TemporaryDirectory() as tempdir:
            embed_cache.SNAPSHOT_DIR = tempdir
            try:
                embed_cache._get_embedding_count_sync = lambda _model_key: 2
                embed_cache._embedding_source_signature = lambda _model_key: [
                    "fast-model", 2, 2, "", 3
                ]
                ids = [10, 11]
                matrix = np.array([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32)
                embed_cache._save_snapshot_sync(ids, matrix, "fast-model")

                loaded_ids, loaded_matrix = embed_cache._load_snapshot_sync(2, "fast-model")

                self.assertEqual(loaded_ids, ids)
                self.assertIsInstance(loaded_matrix, np.memmap)
                self.assertEqual(loaded_matrix.tolist(), matrix.tolist())

                embed_cache._caches["fast-model"] = {
                    "image_ids": loaded_ids,
                    "id_to_idx": {10: 0, 11: 1},
                    "matrix": loaded_matrix,
                    "count": 2,
                    "checked_at": 0.0,
                    "model_key": "fast-model",
                }
                embed_cache.add_vectors(
                    [(12, np.array([0.0, 1.0], dtype=np.float32))],
                    model_key="fast-model",
                )
                warm = embed_cache._caches["fast-model"]["matrix"]
                self.assertFalse(isinstance(warm, np.memmap))
                self.assertTrue(warm.flags.writeable)
                self.assertEqual(embed_cache.get_vector(12, "fast-model").tolist(), [0.0, 1.0])
            finally:
                embed_cache.SNAPSHOT_DIR = old_snapshot_dir
                embed_cache._embedding_source_signature = old_signature
