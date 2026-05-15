import asyncio
import os
import sys
import time
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import embedding_worker  # noqa: E402


class FakeImage:
    def __init__(self, image_id):
        self.image_id = image_id
        self.closed = False

    def close(self):
        self.closed = True


class FakeModel:
    def __init__(self, *, oom_above=None):
        self.oom_above = oom_above
        self.calls = []

    def encode(self, images, normalize_embeddings=True):
        ids = [image.image_id for image in images]
        self.calls.append(ids)
        if self.oom_above is not None and len(images) > self.oom_above:
            raise RuntimeError("CUDA out of memory while allocating test tensor")
        return np.array([[float(image_id), 0.0, 0.0, 0.0] for image_id in ids], dtype=np.float32)


class EmbeddingWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.old_preload_images = embedding_worker._preload_images
        self.old_store_embeddings_batch = embedding_worker.db.store_embeddings_batch
        self.old_get_embedding_count = embedding_worker.db.get_embedding_count
        self.old_add_vectors = embedding_worker.embed_cache.add_vectors
        self.old_get_settings = embedding_worker.settings.get_settings
        self.old_fast_search_embedding_config = embedding_worker.settings.fast_search_embedding_config
        self.old_model_files_present = embedding_worker.ai_models.model_files_present
        self.old_missing_model_dependency = embedding_worker._missing_model_dependency
        self.old_load_model = embedding_worker._load_model
        self.old_clear_cuda_cache = embedding_worker._clear_cuda_cache
        self.old_worker_status = dict(embedding_worker._worker_status)
        self.old_batch_control = dict(embedding_worker._batch_control)
        self.old_retry_after = dict(embedding_worker._embed_retry_after)
        self.old_manual_pause = embedding_worker._embedding_manual_pause
        self.old_history = list(embedding_worker._embedding_history)
        self.old_model = embedding_worker._model
        self.old_loaded_model_dir = embedding_worker._loaded_model_dir
        self.old_loaded_model_id = embedding_worker._loaded_model_id
        self.old_loaded_model_revision = embedding_worker._loaded_model_revision
        self.old_model_load_retry_after = embedding_worker._model_load_retry_after
        self.old_model_load_error_key = embedding_worker._model_load_error_key

        self.stored = []
        self.stored_embedding_configs = []
        self.cached = []
        self.cached_model_keys = []
        self.preload_calls = []
        self.missing_ids = set()

        def fake_preload(image_refs):
            self.preload_calls.append([image_id for image_id, _path in image_refs])
            valid = []
            valid_indices = []
            errors = [None] * len(image_refs)
            for index, (image_id, _path) in enumerate(image_refs):
                if image_id in self.missing_ids:
                    errors[index] = "md thumbnail not cached yet"
                    continue
                valid.append(FakeImage(image_id))
                valid_indices.append(index)
            return valid, valid_indices, errors

        async def fake_store(rows, embedding_config=None):
            self.stored.extend(rows)
            self.stored_embedding_configs.append(embedding_config)

        async def fake_count():
            return len(self.stored)

        def fake_add_vectors(rows, model_key=None):
            self.cached.extend(rows)
            self.cached_model_keys.append(model_key)

        embedding_worker._preload_images = fake_preload
        embedding_worker.db.store_embeddings_batch = fake_store
        embedding_worker.db.get_embedding_count = fake_count
        embedding_worker.embed_cache.add_vectors = fake_add_vectors
        embedding_worker.settings.get_settings = lambda: {"embed_batch_size": 8}
        embedding_worker.settings.fast_search_embedding_config = lambda: {
            "model_key": "fast-test-model@main:4",
            "model_id": "test-model",
            "revision": "main",
            "dimension": 4,
            "model_dir": "/tmp/test-model",
            "embed_model_id": "test-model",
            "embed_model_revision": "main",
            "embed_model_dim": 4,
            "embed_model_dir": "/tmp/test-model",
            "embed_batch_size": 8,
        }
        embedding_worker.ai_models.model_files_present = lambda _model_dir: True
        embedding_worker._missing_model_dependency = lambda: None
        embedding_worker._clear_cuda_cache = lambda: None

        embedding_worker._embedding_history.clear()
        embedding_worker._embed_retry_after.clear()
        embedding_worker._embedding_manual_pause = False
        embedding_worker._model = None
        embedding_worker._loaded_model_dir = None
        embedding_worker._loaded_model_id = None
        embedding_worker._loaded_model_revision = None
        embedding_worker._model_load_retry_after = 0.0
        embedding_worker._model_load_error_key = None
        embedding_worker._batch_control.update({
            "active_batch_size": 4,
            "successful_batches": 0,
            "oom_backoffs": 0,
            "last_oom_at": None,
            "growth_paused_until": None,
        })
        embedding_worker._worker_status.update({
            "last_batch_size": 0,
            "last_batch_seconds": 0.0,
            "last_embedded_at": None,
            "session_embedded": 0,
            "session_started_at": None,
            "session_embed_seconds": 0.0,
            "session_wall_seconds": 0.0,
            "recent_images_per_min": 0.0,
            "recent_wall_images_per_min": 0.0,
            "overall_images_per_min": 0.0,
            "overall_wall_images_per_min": 0.0,
            "last_batch_failures": 0,
            "last_batch_stage_seconds": {},
            "last_candidate_query_seconds": 0.012,
            "oom_backoffs": 0,
        })

    async def asyncTearDown(self):
        embedding_worker._preload_images = self.old_preload_images
        embedding_worker.db.store_embeddings_batch = self.old_store_embeddings_batch
        embedding_worker.db.get_embedding_count = self.old_get_embedding_count
        embedding_worker.embed_cache.add_vectors = self.old_add_vectors
        embedding_worker.settings.get_settings = self.old_get_settings
        embedding_worker.settings.fast_search_embedding_config = self.old_fast_search_embedding_config
        embedding_worker.ai_models.model_files_present = self.old_model_files_present
        embedding_worker._missing_model_dependency = self.old_missing_model_dependency
        embedding_worker._load_model = self.old_load_model
        embedding_worker._clear_cuda_cache = self.old_clear_cuda_cache
        embedding_worker._worker_status.clear()
        embedding_worker._worker_status.update(self.old_worker_status)
        embedding_worker._batch_control.clear()
        embedding_worker._batch_control.update(self.old_batch_control)
        embedding_worker._embed_retry_after.clear()
        embedding_worker._embed_retry_after.update(self.old_retry_after)
        embedding_worker._embedding_manual_pause = self.old_manual_pause
        embedding_worker._embedding_history.clear()
        embedding_worker._embedding_history.extend(self.old_history)
        embedding_worker._model = self.old_model
        embedding_worker._loaded_model_dir = self.old_loaded_model_dir
        embedding_worker._loaded_model_id = self.old_loaded_model_id
        embedding_worker._loaded_model_revision = self.old_loaded_model_revision
        embedding_worker._model_load_retry_after = self.old_model_load_retry_after
        embedding_worker._model_load_error_key = self.old_model_load_error_key

    def rows(self, count):
        return [
            {"id": image_id, "filepath": f"/test/{image_id}.jpg"}
            for image_id in range(1, count + 1)
        ]

    def stored_ids(self):
        return [image_id for image_id, _blob in self.stored]

    async def process(self, rows, model=None):
        loop = asyncio.get_running_loop()
        return await embedding_worker._process_embedding_candidates(
            loop,
            model or FakeModel(),
            rows,
            batch_pause_seconds=0.0,
        )

    async def test_candidate_window_splits_into_chunks_without_duplicate_stores(self):
        model = FakeModel()

        result = await self.process(self.rows(10), model)

        self.assertEqual(result["stored"], 10)
        self.assertEqual(result["failed"], 0)
        self.assertEqual(result["chunks"], 3)
        self.assertEqual(self.stored_ids(), list(range(1, 11)))
        self.assertEqual(len(set(self.stored_ids())), 10)
        self.assertEqual([len(call) for call in model.calls], [4, 4, 2])

    async def test_preload_encode_pipeline_preserves_result_ordering(self):
        result = await self.process(self.rows(6))

        stored_values = [
            float(np.frombuffer(blob, dtype=np.float32)[0])
            for _image_id, blob in self.stored
        ]
        self.assertEqual(result["stored"], 6)
        self.assertEqual(self.stored_ids(), [1, 2, 3, 4, 5, 6])
        self.assertEqual(stored_values, [1.0, 2.0, 3.0, 4.0, 5.0, 6.0])
        self.assertEqual(self.preload_calls[0], [1, 2, 3, 4])
        self.assertEqual(self.preload_calls[1], [5, 6])

    async def test_manual_pause_after_preload_stops_before_gpu_encode(self):
        model = FakeModel()

        def pausing_preload(image_refs):
            self.preload_calls.append([image_id for image_id, _path in image_refs])
            embedding_worker._embedding_manual_pause = True
            return (
                [FakeImage(image_id) for image_id, _path in image_refs],
                list(range(len(image_refs))),
                [None] * len(image_refs),
            )

        embedding_worker._preload_images = pausing_preload

        result = await self.process(self.rows(4), model)

        self.assertEqual(result["stored"], 0)
        self.assertEqual(result["chunks"], 0)
        self.assertEqual(model.calls, [])
        self.assertEqual(self.preload_calls, [[1, 2, 3, 4]])

    async def test_warm_cache_patch_failure_does_not_fail_committed_batch(self):
        def fail_add_vectors(_rows, model_key=None):
            raise RuntimeError("warm cache is stale")

        embedding_worker.embed_cache.add_vectors = fail_add_vectors

        result = await self.process(self.rows(3))

        self.assertEqual(result["stored"], 3)
        self.assertEqual(result["failed"], 0)
        self.assertEqual(self.stored_ids(), [1, 2, 3])

    async def test_image_embeddings_are_coerced_to_configured_dimension(self):
        embedding_worker.settings.get_settings = lambda: {
            "embed_batch_size": 8,
            "embed_model_dim": 2,
        }

        result = await self.process(self.rows(2))
        stored_vectors = [
            np.frombuffer(blob, dtype=np.float32)
            for _image_id, blob in self.stored
        ]

        self.assertEqual(result["stored"], 2)
        self.assertEqual([vec.shape[0] for vec in stored_vectors], [2, 2])
        self.assertEqual(stored_vectors[0].tolist(), [1.0, 0.0])
        self.assertEqual(stored_vectors[1].tolist(), [1.0, 0.0])

    async def test_cuda_oom_retries_smaller_chunks_and_reduces_active_batch(self):
        model = FakeModel(oom_above=2)

        result = await self.process(self.rows(5), model)
        status = embedding_worker.get_worker_status()

        self.assertEqual(result["stored"], 5)
        self.assertEqual(result["failed"], 0)
        self.assertEqual(self.stored_ids(), [1, 2, 3, 4, 5])
        self.assertEqual(model.calls[0], [1, 2, 3, 4])
        self.assertEqual([len(call) for call in model.calls[1:]], [2, 2, 1])
        self.assertEqual(status["active_batch_size"], 2)
        self.assertEqual(status["oom_backoffs"], 1)
        self.assertIsNotNone(status["batch_growth_paused_until"])

    async def test_failed_image_retry_cooldown_still_works(self):
        self.missing_ids = {2}

        result = await self.process(self.rows(3))

        self.assertEqual(result["stored"], 2)
        self.assertEqual(result["failed"], 1)
        self.assertEqual(self.stored_ids(), [1, 3])
        self.assertNotIn(1, embedding_worker._embed_retry_after)
        self.assertGreater(embedding_worker._embed_retry_after[2], time.time())
        self.assertEqual(embedding_worker.get_worker_status()["last_batch_failures"], 1)

    async def test_timing_and_status_fields_are_populated(self):
        await self.process(self.rows(2))

        status = embedding_worker.get_worker_status()
        stages = status["last_batch_stage_seconds"]

        self.assertEqual(status["active_batch_size"], 4)
        self.assertEqual(status["target_batch_size"], 8)
        self.assertGreater(status["recent_wall_images_per_min"], 0)
        self.assertEqual(status["last_candidate_query_seconds"], 0.012)
        for key in ("candidate_query", "preload", "encode", "store", "pause", "wall"):
            self.assertIn(key, stages)
        self.assertIn("oom_backoffs", status)

    async def test_search_model_loader_returns_fast_when_dependency_missing(self):
        called = False

        def fail_load(*_args, **_kwargs):
            nonlocal called
            called = True
            raise AssertionError("model load should not run without dependencies")

        embedding_worker.settings.get_settings = lambda: {
            "embed_model_id": "test-model",
            "embed_model_revision": "main",
            "embed_model_dir": "/tmp/test-model",
        }
        embedding_worker._missing_model_dependency = lambda: "torch"
        embedding_worker._load_model = fail_load

        loaded = await embedding_worker.ensure_model_loaded_for_search()

        self.assertFalse(loaded)
        self.assertFalse(called)
        status = embedding_worker.get_worker_status()
        self.assertEqual(status["state"], "error")
        self.assertIn("torch", status["last_error"])

    async def test_missing_dependency_blocks_background_model_load(self):
        embedding_worker.settings.get_settings = lambda: {
            "embed_model_id": "test-model",
            "embed_model_revision": "main",
            "embed_model_dir": "/tmp/test-model",
        }
        embedding_worker._missing_model_dependency = lambda: "torch"

        blocked = embedding_worker._block_model_load_for_missing_dependency(
            "/tmp/test-model",
            "test-model",
            "main",
        )

        self.assertTrue(blocked)
        self.assertEqual(
            embedding_worker._model_load_error_key,
            ("/tmp/test-model", "test-model", "main"),
        )
        self.assertGreater(embedding_worker._model_load_retry_after, time.time())
        self.assertIn("torch", embedding_worker.get_worker_status()["last_error"])


if __name__ == "__main__":
    unittest.main()
