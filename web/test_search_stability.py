import unittest
from unittest import mock

import numpy as np

import embed_cache
from core import query_constraints


class SearchStabilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_committed_search_waits_for_cold_model_then_returns_semantic_result(self):
        ready = False
        encode_calls = 0

        def encode(_encoder, _query, _config):
            nonlocal encode_calls
            encode_calls += 1
            return np.array([1.0, 0.0], dtype=np.float32) if ready else None

        async def ensure(_worker):
            nonlocal ready
            ready = True
            return True

        async def matrix(_model_key=None):
            return [10, 20], np.array([[0.9, 0.1], [0.1, 0.9]], dtype=np.float32)

        with mock.patch.object(embed_cache, "get_matrix", matrix):
            result = await query_constraints.resolve_text_search(
                "red kite",
                deep=True,
                encode_text=encode,
                ensure_model_loaded=ensure,
                start_model_load=lambda _worker: False,
                extension_search_terms=set(),
                get_settings=lambda: {"search_similarity_threshold": 0.35},
                active_embedding_config=lambda: {
                    "model_key": "semantic-model",
                    "dimension": 2,
                },
            )

        self.assertEqual(encode_calls, 2)
        self.assertEqual(result["search_mode"], "embedding")
        self.assertEqual(result["id_filter"], {10})
        self.assertFalse(result["ai_unavailable"])

    async def test_preview_search_keeps_fast_fallback_without_waiting(self):
        ensure_calls = 0

        async def ensure(_worker):
            nonlocal ensure_calls
            ensure_calls += 1
            return True

        async def metadata_ids(_query):
            return {7}

        result = await query_constraints.resolve_text_search(
            "red kite preview",
            deep=False,
            encode_text=lambda _encoder, _query, _config: None,
            ensure_model_loaded=ensure,
            start_model_load=lambda _worker: True,
            apply_metadata_ids=lambda result, query: query_constraints.apply_metadata_search_ids(
                result,
                query,
                metadata_search_image_ids=metadata_ids,
            ),
            extension_search_terms=set(),
            get_settings=lambda: {"search_similarity_threshold": 0.35},
            active_embedding_config=lambda: {
                "model_key": "semantic-model",
                "dimension": 2,
            },
        )

        self.assertEqual(ensure_calls, 0)
        self.assertEqual(result["search_mode"], "metadata")
        self.assertEqual(result["id_filter"], {7})
        self.assertTrue(result["ai_unavailable"])


if __name__ == "__main__":
    unittest.main()
