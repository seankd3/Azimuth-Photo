import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import embed_cache  # noqa: E402


class EmbedCacheTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.old_cache = embed_cache._cache
        self.old_caches = embed_cache._caches
        self.old_active_model_key = embed_cache.db.active_embedding_model_key
        self.old_get_embedding_count_sync = embed_cache._get_embedding_count_sync
        self.old_load_embeddings_sync = embed_cache._load_embeddings_sync

        embed_cache._cache = embed_cache._empty_cache()
        embed_cache._caches = {}

    async def asyncTearDown(self):
        embed_cache._cache = self.old_cache
        embed_cache._caches = self.old_caches
        embed_cache.db.active_embedding_model_key = self.old_active_model_key
        embed_cache._get_embedding_count_sync = self.old_get_embedding_count_sync
        embed_cache._load_embeddings_sync = self.old_load_embeddings_sync

    async def test_matrix_cache_keeps_fast_and_deep_surfaces_warm(self):
        loads = []
        matrices = {
            "fast-model": np.array([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32),
            "deep-model": np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]], dtype=np.float32),
        }

        embed_cache.db.active_embedding_model_key = lambda: "fast-model"
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

        embed_cache.db.active_embedding_model_key = lambda: "fast-model"
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
        embed_cache.db.active_embedding_model_key = lambda: "fast-model"
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


if __name__ == "__main__":
    unittest.main()
