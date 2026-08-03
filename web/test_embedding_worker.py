import asyncio
import os
import sqlite3
import db
import sys
import threading
import time
import unittest
import unittest.mock

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import embedding_worker  # noqa: E402
from core import memory_pressure, work_coordination  # noqa: E402


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
    async def test_embedding_ownership_loss_unloads_before_reentering_waits(self):
        wait_order = []

        async def wait_for_gpu_owner(*_args, **_kwargs):
            wait_order.append("gpu")

        async def wait_for_manual_owner(*_args, **_kwargs):
            wait_order.append("manual")

        with (
            unittest.mock.patch.object(
                work_coordination,
                "lost_ownership",
                return_value=True,
            ),
            unittest.mock.patch.object(embedding_worker, "_unload_model") as unload_model,
            unittest.mock.patch.object(
                work_coordination,
                "wait_for_gpu_turn",
                side_effect=wait_for_gpu_owner,
            ) as wait_for_gpu,
            unittest.mock.patch.object(
                work_coordination,
                "wait_for_manual_turn",
                side_effect=wait_for_manual_owner,
            ) as wait_for_manual,
        ):
            retained = await embedding_worker._renew_embedding_turn()

        self.assertFalse(retained)
        unload_model.assert_called_once_with()
        self.assertEqual(wait_order, ["manual", "gpu"])
        wait_for_gpu.assert_awaited_once_with("embeddings")
        wait_for_manual.assert_awaited_once_with("embeddings")

    async def test_embedding_shutdown_cancels_search_load_and_gpu_executors(self):
        old_embed_executor = embedding_worker._embed_executor
        old_preload_executor = embedding_worker._preload_executor
        fake_embed_executor = unittest.mock.Mock()
        fake_preload_executor = unittest.mock.Mock()
        embedding_worker._embed_executor = fake_embed_executor
        embedding_worker._preload_executor = fake_preload_executor
        load_task = asyncio.create_task(asyncio.Event().wait())
        residency_task = asyncio.create_task(asyncio.Event().wait())
        embedding_worker._search_model_load_task = load_task
        embedding_worker._search_model_residency_task = residency_task
        try:
            await embedding_worker.shutdown_embedding_worker()

            self.assertTrue(load_task.cancelled())
            self.assertTrue(residency_task.cancelled())
            self.assertIsNone(embedding_worker._search_model_load_task)
            self.assertIsNone(embedding_worker._search_model_residency_task)
            fake_embed_executor.shutdown.assert_called_once_with(
                wait=False,
                cancel_futures=True,
            )
            fake_preload_executor.shutdown.assert_called_once_with(
                wait=False,
                cancel_futures=True,
            )
        finally:
            embedding_worker._embed_executor.shutdown(
                wait=False,
                cancel_futures=True,
            )
            embedding_worker._preload_executor.shutdown(
                wait=False,
                cancel_futures=True,
            )
            embedding_worker._embed_executor = old_embed_executor
            embedding_worker._preload_executor = old_preload_executor

    async def test_embedding_immediate_claim_does_not_report_waiting(self):
        observed_states = []

        async def candidates(**_kwargs):
            return [{"id": 1, "filepath": "/test/1.jpg"}]

        async def stop_at_gpu_wait(*_args, **_kwargs):
            observed_states.append(embedding_worker.get_worker_status()["state"])
            raise asyncio.CancelledError

        embedding_worker._get_unembedded_images = candidates
        embedding_worker._model = object()
        embedding_worker._loaded_model_dir = "/tmp/test-model"
        embedding_worker._loaded_model_id = "test-model"
        embedding_worker._loaded_model_revision = "main"

        with (
            unittest.mock.patch.object(
                work_coordination,
                "wait_for_gpu_turn",
                side_effect=stop_at_gpu_wait,
            ),
            unittest.mock.patch.object(
                work_coordination,
                "manual_turn_blocked",
                return_value=False,
            ),
            unittest.mock.patch.object(
                work_coordination,
                "gpu_turn_blocked",
                return_value=False,
            ),
        ):
            with self.assertRaises(asyncio.CancelledError):
                await embedding_worker.run_embedding_worker()

        self.assertNotIn(observed_states[0], {"waiting_for_turn", "waiting_for_gpu"})

    async def test_embedding_reports_manual_wait_only_when_blocked(self):
        async def stop_at_manual_wait(*_args, **_kwargs):
            raise asyncio.CancelledError

        with (
            unittest.mock.patch.object(
                work_coordination,
                "manual_turn_blocked",
                return_value=True,
            ),
            unittest.mock.patch.object(
                work_coordination,
                "wait_for_manual_turn",
                side_effect=stop_at_manual_wait,
            ),
        ):
            with self.assertRaises(asyncio.CancelledError):
                await embedding_worker._wait_for_embedding_turn()

        self.assertEqual(embedding_worker.get_worker_status()["state"], "waiting_for_turn")

    async def test_embedding_cooldown_sleep_reports_waiting_retry(self):
        observed_states = []

        async def candidates(**_kwargs):
            return [{"id": 1, "filepath": "/test/1.jpg"}]

        async def stop_at_sleep(_seconds):
            observed_states.append(embedding_worker.get_worker_status()["state"])
            raise asyncio.CancelledError

        embedding_worker._get_unembedded_images = candidates
        embedding_worker._embed_retry_after[1] = time.time() + 600
        embedding_worker._model = object()
        embedding_worker._loaded_model_dir = "/tmp/test-model"
        embedding_worker._loaded_model_id = "test-model"
        embedding_worker._loaded_model_revision = "main"

        with unittest.mock.patch.object(
            embedding_worker.asyncio,
            "sleep",
            side_effect=stop_at_sleep,
        ):
            with self.assertRaises(asyncio.CancelledError):
                await embedding_worker.run_embedding_worker()

        self.assertEqual(observed_states, ["waiting_retry"])

    async def test_worker_cancellation_releases_gpu_and_manual_owners(self):
        processing_started = asyncio.Event()

        async def candidates(**_kwargs):
            return [{"id": 1, "filepath": "/test/1.jpg"}]

        async def block_processing(*_args, **_kwargs):
            processing_started.set()
            await asyncio.Future()

        embedding_worker._get_unembedded_images = candidates
        embedding_worker._model = object()
        embedding_worker._loaded_model_dir = "/tmp/test-model"
        embedding_worker._loaded_model_id = "test-model"
        embedding_worker._loaded_model_revision = "main"
        for owner in (work_coordination.manual_owner(),):
            if owner:
                work_coordination.release_manual_owner(owner)
        for owner in (work_coordination.gpu_owner(),):
            if owner:
                work_coordination.release_gpu_owner(owner)

        task = None
        try:
            with unittest.mock.patch.object(
                embedding_worker,
                "_process_embedding_candidates",
                side_effect=block_processing,
            ):
                task = asyncio.create_task(embedding_worker.run_embedding_worker())
                await asyncio.wait_for(processing_started.wait(), timeout=1)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task

            self.assertIsNone(work_coordination.manual_owner())
            self.assertIsNone(work_coordination.gpu_owner())
        finally:
            if task is not None and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            work_coordination.release_manual_owner("embeddings")
            work_coordination.release_gpu_owner("embeddings")

    async def test_sqlite_lock_is_retryable_embedding_contention(self):
        self.assertTrue(
            embedding_worker._is_sqlite_locked_error(
                sqlite3.OperationalError("database is locked")
            )
        )
        self.assertFalse(
            embedding_worker._is_sqlite_locked_error(
                RuntimeError("model exploded")
            )
        )

    async def asyncSetUp(self):
        self.old_preload_images = embedding_worker._preload_images
        self.old_embedding_providers = {
            name: getattr(embedding_worker, name)
            for name in (
                "_get_catalog_image_counts",
                "_count_embeddings_for_model",
                "_get_unembedded_images",
                "_store_embeddings_batch",
                "_poison_embedding_image",
                "_get_embedding_count",
            )
        }
        self.old_add_vectors = embedding_worker.embed_cache.add_vectors
        self.old_get_settings = embedding_worker.settings.get_settings
        self.old_active_embedding_config = embedding_worker.settings.active_embedding_config
        self.old_model_files_present = embedding_worker.ai_models.model_files_present
        self.old_missing_model_dependency = embedding_worker._missing_model_dependency
        self.old_load_model = embedding_worker._load_model
        self.old_clear_cuda_cache = embedding_worker._clear_cuda_cache
        self.old_ensure_model_loaded_for_config = embedding_worker._ensure_model_loaded_for_config
        self.old_encode_text = embedding_worker.encode_text
        self.old_worker_status = dict(embedding_worker._worker_status)
        self.old_batch_control = dict(embedding_worker._batch_control)
        self.old_retry_after = dict(embedding_worker._embed_retry_after)
        self.old_manual_pause = embedding_worker._embedding_manual_pause
        self.old_manual_pause_message = embedding_worker._embedding_manual_pause_message
        self.old_pause_reason = embedding_worker._embedding_pause_reason
        self.old_history = list(embedding_worker._embedding_history)
        self.old_model = embedding_worker._model
        self.old_loaded_model_dir = embedding_worker._loaded_model_dir
        self.old_loaded_model_id = embedding_worker._loaded_model_id
        self.old_loaded_model_revision = embedding_worker._loaded_model_revision
        self.old_search_model_load_task = embedding_worker._search_model_load_task
        self.old_search_model_residency_task = embedding_worker._search_model_residency_task
        self.old_model_load_retry_after = embedding_worker._model_load_retry_after
        self.old_model_load_error_key = embedding_worker._model_load_error_key

        self.stored = []
        self.stored_embedding_configs = []
        self.cached = []
        self.cached_model_keys = []
        self.preload_calls = []
        self.missing_ids = set()
        self.poisoned = []

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

        async def fake_count(*_args, **_kwargs):
            return len(self.stored)

        async def fake_poison(**kwargs):
            self.poisoned.append(kwargs)
            return bool(kwargs.get("force"))

        async def fake_empty_dict(*_args, **_kwargs):
            return {}

        async def fake_empty_list(*_args, **_kwargs):
            return []

        async def fake_noop(*_args, **_kwargs):
            return None

        def fake_add_vectors(rows, model_key=None):
            self.cached.extend(rows)
            self.cached_model_keys.append(model_key)

        embedding_worker._preload_images = fake_preload
        db.get_catalog_image_counts = fake_empty_dict
        db.count_embeddings_for_model = fake_count
        db.get_unembedded_images = fake_empty_list
        db.store_embeddings_batch = fake_store
        db.poison_embedding_image = fake_poison
        db.get_embedding_count = fake_count
        embedding_worker.embed_cache.add_vectors = fake_add_vectors
        embedding_worker.settings.get_settings = lambda: {"embed_batch_size": 8}
        embedding_worker.settings.active_embedding_config = lambda: {
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
        embedding_worker._embedding_oom_circuit.reset()
        embedding_worker._embedding_manual_pause = False
        embedding_worker._model = None
        embedding_worker._loaded_model_dir = None
        embedding_worker._loaded_model_id = None
        embedding_worker._loaded_model_revision = None
        embedding_worker._search_model_load_task = None
        embedding_worker._search_model_residency_task = None
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
        for name, value in self.old_embedding_providers.items():
            setattr(embedding_worker, name, value)
        embedding_worker.embed_cache.add_vectors = self.old_add_vectors
        embedding_worker.settings.get_settings = self.old_get_settings
        embedding_worker.settings.active_embedding_config = self.old_active_embedding_config
        embedding_worker.ai_models.model_files_present = self.old_model_files_present
        embedding_worker._missing_model_dependency = self.old_missing_model_dependency
        embedding_worker._load_model = self.old_load_model
        embedding_worker._clear_cuda_cache = self.old_clear_cuda_cache
        embedding_worker._ensure_model_loaded_for_config = self.old_ensure_model_loaded_for_config
        embedding_worker.encode_text = self.old_encode_text
        embedding_worker._worker_status.clear()
        embedding_worker._worker_status.update(self.old_worker_status)
        embedding_worker._batch_control.clear()
        embedding_worker._batch_control.update(self.old_batch_control)
        embedding_worker._embed_retry_after.clear()
        embedding_worker._embed_retry_after.update(self.old_retry_after)
        embedding_worker._embedding_manual_pause = self.old_manual_pause
        embedding_worker._embedding_manual_pause_message = self.old_manual_pause_message
        embedding_worker._embedding_pause_reason = self.old_pause_reason
        embedding_worker._embedding_history.clear()
        embedding_worker._embedding_history.extend(self.old_history)
        task = embedding_worker._search_model_load_task
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        residency_task = embedding_worker._unload_model()
        if residency_task is not None and not residency_task.done():
            await asyncio.gather(residency_task, return_exceptions=True)
        embedding_worker._search_model_load_task = self.old_search_model_load_task
        embedding_worker._search_model_residency_task = self.old_search_model_residency_task
        embedding_worker._model = self.old_model
        embedding_worker._loaded_model_dir = self.old_loaded_model_dir
        embedding_worker._loaded_model_id = self.old_loaded_model_id
        embedding_worker._loaded_model_revision = self.old_loaded_model_revision
        embedding_worker._model_load_retry_after = self.old_model_load_retry_after
        embedding_worker._model_load_error_key = self.old_model_load_error_key
        embedding_worker._embedding_oom_circuit.reset()

    def rows(self, count):
        return [
            {"id": image_id, "filepath": f"/test/{image_id}.jpg"}
            for image_id in range(1, count + 1)
        ]

    def stored_ids(self):
        return [image_id for image_id, _blob in self.stored]

    async def process(self, rows, model=None):
        loop = asyncio.get_running_loop()
        for owner in (work_coordination.manual_owner(),):
            if owner:
                work_coordination.release_manual_owner(owner)
        for owner in (work_coordination.gpu_owner(),):
            if owner:
                work_coordination.release_gpu_owner(owner)
        work_coordination.claim_manual_owner("embeddings")
        work_coordination.claim_gpu_owner("embeddings")
        try:
            return await embedding_worker._process_embedding_candidates(
                loop,
                model or FakeModel(),
                rows,
                batch_pause_seconds=0.0,
            )
        finally:
            work_coordination.release_manual_owner("embeddings")
            work_coordination.release_gpu_owner("embeddings")

    async def test_candidate_window_splits_into_chunks_without_duplicate_stores(self):
        model = FakeModel()

        result = await self.process(self.rows(10), model)

        self.assertEqual(result["stored"], 10)
        self.assertEqual(result["failed"], 0)
        self.assertEqual(result["chunks"], 3)
        self.assertEqual(self.stored_ids(), list(range(1, 11)))
        self.assertEqual(len(set(self.stored_ids())), 10)
        self.assertEqual([len(call) for call in model.calls], [4, 4, 2])

    async def test_candidate_chunks_renew_manual_then_gpu_leases(self):
        wait_order = []

        async def wait_for_manual_owner(*_args, **_kwargs):
            wait_order.append("manual")

        async def wait_for_gpu_owner(*_args, **_kwargs):
            wait_order.append("gpu")

        with (
            unittest.mock.patch.object(
                work_coordination,
                "wait_for_manual_turn",
                side_effect=wait_for_manual_owner,
            ),
            unittest.mock.patch.object(
                work_coordination,
                "wait_for_gpu_turn",
                side_effect=wait_for_gpu_owner,
            ),
        ):
            result = await self.process(self.rows(10))

        self.assertEqual(result["chunks"], 3)
        self.assertEqual(wait_order, ["manual", "gpu"] * 3)

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

    async def test_repeated_single_image_ooms_poison_images_and_open_circuit(self):
        model = FakeModel(oom_above=0)

        result = await self.process(self.rows(3), model)

        self.assertEqual(result["stored"], 0)
        self.assertEqual(result["failed"], 3)
        self.assertEqual([item["image_id"] for item in self.poisoned], [1, 2, 3])
        self.assertEqual([item["force"] for item in self.poisoned], [False, False, True])
        self.assertEqual(sorted(embedding_worker._embed_retry_after), [1, 2])
        self.assertTrue(embedding_worker._embedding_manual_pause)
        self.assertIn("out-of-memory", embedding_worker.get_worker_status()["message"])

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

    async def test_bulk_model_load_starts_search_model_residency(self):
        observed = []

        async def stop_after_model_load(**_kwargs):
            task = embedding_worker._search_model_residency_task
            observed.append((
                embedding_worker.get_worker_status()["state"],
                task is not None and not task.done(),
            ))
            raise asyncio.CancelledError

        embedding_worker._load_model = lambda *_args: object()
        embedding_worker._get_unembedded_images = stop_after_model_load

        with self.assertRaises(asyncio.CancelledError):
            await embedding_worker.run_embedding_worker()

        self.assertEqual(observed, [("resident", True)])

    async def test_search_model_loader_waits_for_fresh_caption_lease(self):
        load_started = threading.Event()

        def fake_load(_model_dir, _model_id, _interactive=False):
            load_started.set()
            return object()

        embedding_worker._load_model = fake_load
        current_manual_owner = work_coordination.manual_owner()
        if current_manual_owner:
            work_coordination.release_manual_owner(current_manual_owner)
        current_gpu_owner = work_coordination.gpu_owner()
        if current_gpu_owner:
            work_coordination.release_gpu_owner(current_gpu_owner)
        work_coordination.claim_manual_owner("captions")
        work_coordination.claim_gpu_owner("captions")
        task = asyncio.create_task(embedding_worker.ensure_model_loaded_for_search())
        try:
            await asyncio.sleep(0.05)

            self.assertFalse(load_started.is_set())
            self.assertFalse(task.done())
            self.assertEqual(work_coordination.manual_owner(), "captions")
            self.assertEqual(work_coordination.gpu_owner(), "captions")

            work_coordination.release_manual_owner("captions")
            work_coordination.release_gpu_owner("captions")
            self.assertTrue(await asyncio.wait_for(task, timeout=2))
            self.assertTrue(load_started.is_set())
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            work_coordination.release_manual_owner("captions")
            work_coordination.release_gpu_owner("captions")

        embedding_worker._unload_model()
        self.assertIsNone(work_coordination.manual_owner())
        self.assertIsNone(work_coordination.gpu_owner())

    async def test_search_model_unload_lets_captions_claim_before_next_heartbeat(self):
        heartbeat_seconds = 0.2
        embedding_worker._load_model = lambda *_args: object()

        with unittest.mock.patch.object(
            work_coordination,
            "LEASE_HEARTBEAT_SECONDS",
            heartbeat_seconds,
        ):
            self.assertTrue(await embedding_worker.ensure_model_loaded_for_search())
            embedding_worker._unload_model()

            await asyncio.wait_for(
                work_coordination.wait_for_manual_turn("captions", poll_seconds=0.001),
                timeout=heartbeat_seconds / 2,
            )
            await asyncio.wait_for(
                work_coordination.wait_for_gpu_turn("captions", poll_seconds=0.001),
                timeout=heartbeat_seconds / 2,
            )

        self.assertEqual(work_coordination.manual_owner(), "captions")
        self.assertEqual(work_coordination.gpu_owner(), "captions")
        work_coordination.release_manual_owner("captions")
        work_coordination.release_gpu_owner("captions")

    async def test_search_model_residency_unloads_after_lease_is_stolen(self):
        embedding_worker._load_model = lambda *_args: object()

        with unittest.mock.patch.object(
            work_coordination,
            "LEASE_HEARTBEAT_SECONDS",
            0.01,
        ):
            self.assertTrue(await embedding_worker.ensure_model_loaded_for_search())
            expired_at = time.time() - work_coordination.OWNER_LEASE_SECONDS - 1
            work_coordination._manual_owner_updated_at = expired_at
            work_coordination._gpu_owner_updated_at = expired_at
            work_coordination.claim_manual_owner("captions")
            work_coordination.claim_gpu_owner("captions")

            async def wait_for_unload():
                while embedding_worker.search_model_ready():
                    await asyncio.sleep(0.001)

            await asyncio.wait_for(wait_for_unload(), timeout=0.1)

        self.assertFalse(embedding_worker.search_model_ready())
        self.assertFalse(embedding_worker.get_worker_status()["ready"])
        self.assertEqual(work_coordination.manual_owner(), "captions")
        self.assertEqual(work_coordination.gpu_owner(), "captions")
        work_coordination.release_manual_owner("captions")
        work_coordination.release_gpu_owner("captions")

    async def test_search_model_residency_renews_both_leases(self):
        embedding_worker._load_model = lambda *_args: object()

        with unittest.mock.patch.object(
            work_coordination,
            "LEASE_HEARTBEAT_SECONDS",
            0.01,
        ):
            self.assertTrue(await embedding_worker.ensure_model_loaded_for_search())
            manual_before = work_coordination._manual_owner_updated_at
            gpu_before = work_coordination._gpu_owner_updated_at

            async def wait_for_renewal():
                while (
                    work_coordination._manual_owner_updated_at <= manual_before
                    or work_coordination._gpu_owner_updated_at <= gpu_before
                ):
                    await asyncio.sleep(0.001)

            await asyncio.wait_for(wait_for_renewal(), timeout=1.0)

        self.assertEqual(work_coordination.manual_owner(), "embeddings")
        self.assertEqual(work_coordination.gpu_owner(), "embeddings")

    async def test_search_model_load_failure_releases_both_owners(self):
        def fail_load(*_args):
            raise RuntimeError("load failed")

        embedding_worker._load_model = fail_load
        try:
            with unittest.mock.patch.object(
                work_coordination,
                "_write_gpu_owner_flag",
            ):
                self.assertFalse(
                    await embedding_worker.ensure_model_loaded_for_search()
                )

                self.assertIsNone(work_coordination.manual_owner())
                self.assertIsNone(work_coordination.gpu_owner())
                self.assertEqual(
                    work_coordination.claim_manual_owner("captions"),
                    "captions",
                )
                self.assertEqual(
                    work_coordination.claim_gpu_owner("captions"),
                    "captions",
                )
        finally:
            work_coordination.release_manual_owner("captions")
            work_coordination.release_gpu_owner("captions")
            work_coordination.release_manual_owner("embeddings")
            work_coordination.release_gpu_owner("embeddings")

    async def test_start_search_model_load_warms_model_in_background_once(self):
        calls = []
        model = object()

        def fake_load(model_dir, model_id, _interactive=False):
            calls.append((model_dir, model_id))
            return model

        embedding_worker._load_model = fake_load

        self.assertTrue(embedding_worker.start_search_model_load())
        task = embedding_worker._search_model_load_task
        self.assertIsNotNone(task)
        self.assertTrue(embedding_worker.start_search_model_load())

        loaded = await task

        self.assertTrue(loaded)
        self.assertEqual(calls, [("/tmp/test-model", "test-model")])
        self.assertTrue(embedding_worker.search_model_ready())
        status = embedding_worker.get_worker_status()
        self.assertEqual(status["state"], "resident")
        self.assertEqual(status["message"], "Search model warm.")

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

    def test_resume_embedding_worker_clears_model_load_backoff(self):
        embedding_worker._note_model_load_failure(
            "/tmp/test-model",
            "test-model",
            "main",
            RuntimeError("CUDA out of memory"),
        )
        self.assertGreater(embedding_worker._model_load_retry_after, time.time())

        embedding_worker.resume_embedding_worker()

        self.assertEqual(embedding_worker._model_load_retry_after, 0.0)
        self.assertIsNone(embedding_worker._model_load_error_key)
        self.assertFalse(embedding_worker._embedding_manual_pause)
        self.assertEqual(embedding_worker.get_worker_status()["state"], "idle")


class EmbeddingIdleUnloadTests(unittest.TestCase):
    def setUp(self):
        from core.model_pool import ModelPool, reset_model_pool_for_tests

        memory_pressure.reset_for_tests()
        self.clock = {"now": 1000.0}
        self.unloads: list[str] = []
        self.pool = reset_model_pool_for_tests(
            ModelPool(
                vram_budget_bytes=None,
                ram_budget_bytes=None,
                pin_seconds=30.0,
                empty_cache=lambda: None,
                clock=lambda: self.clock["now"],
            )
        )
        self.old_model = embedding_worker._model
        self.old_pause = embedding_worker._embedding_manual_pause
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        from core.model_pool import ModelPool, reset_model_pool_for_tests

        embedding_worker._embedding_manual_pause = self.old_pause
        embedding_worker._model = self.old_model
        embedding_worker._unload_model(force=True)
        reset_model_pool_for_tests(
            ModelPool(
                vram_budget_bytes=None,
                ram_budget_bytes=None,
                pin_seconds=0.0,
                empty_cache=lambda: None,
            )
        )
        memory_pressure.reset_for_tests()

    def _load_pinned(self):
        def unload():
            self.unloads.append("embeddings")
            embedding_worker._model = None

        self.pool.acquire(
            "embeddings",
            load_fn=lambda: "search-model",
            unload_fn=unload,
            vram_bytes=10,
            ram_bytes=10,
            interactive=True,
        )
        embedding_worker._model = "search-model"

    def test_pause_unload_blocked_while_search_pinned(self):
        self._load_pinned()
        embedding_worker._embedding_manual_pause = True

        blocked = embedding_worker._maybe_unload_idle_model()
        self.assertFalse(blocked)
        self.assertEqual(embedding_worker._model, "search-model")
        self.assertIn("embeddings", self.pool.resident_names())
        self.assertEqual(self.unloads, [])

        # Pin window ends → pause shed fires.
        self.clock["now"] += 31.0
        shed = embedding_worker._maybe_unload_idle_model()
        self.assertTrue(shed)
        self.assertIsNone(embedding_worker._model)
        self.assertEqual(self.unloads, ["embeddings"])

    def test_idle_ttl_unloads_after_quiet_period(self):
        self._load_pinned()
        embedding_worker._embedding_manual_pause = False
        self.clock["now"] += 31.0  # pin expired
        self.assertFalse(
            embedding_worker._maybe_unload_idle_model(ttl_seconds=120.0)
        )
        self.clock["now"] += 120.0
        self.assertTrue(
            embedding_worker._maybe_unload_idle_model(ttl_seconds=120.0)
        )
        self.assertIsNone(embedding_worker._model)


if __name__ == "__main__":
    unittest.main()
