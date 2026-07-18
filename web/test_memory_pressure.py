"""Memory watermark gates for background bulk work."""

from __future__ import annotations

import unittest
from dataclasses import dataclass
from unittest import mock

from core import memory_pressure
from thumbnails import pregen as thumbnail_pregen


@dataclass(frozen=True)
class _FakeDecision:
    mode: str
    intensity: float
    pause: bool
    sleep_seconds: float
    thumbnail_batch_size: int
    thumbnail_pause_seconds: float
    embedding_pause_seconds: float
    reason: str
    checked_at: float


class MemoryPressureTests(unittest.TestCase):
    def setUp(self):
        memory_pressure.reset_for_tests()
        self.addCleanup(memory_pressure.reset_for_tests)

    def test_soft_watermark_pauses_bulk_and_resumes_below_hysteresis(self):
        soft = memory_pressure.SOFT_WATERMARK_BYTES
        resume = memory_pressure.RESUME_WATERMARK_BYTES

        over = memory_pressure.evaluate_memory_pressure(rss_bytes=soft + 1)
        self.assertTrue(over.pause_bulk)
        self.assertEqual(over.level, "soft")
        self.assertFalse(over.unload_models)
        self.assertEqual(over.message, "Paused: memory pressure")

        still = memory_pressure.evaluate_memory_pressure(rss_bytes=resume + 1)
        self.assertTrue(still.pause_bulk)
        self.assertEqual(still.level, "soft")

        clear = memory_pressure.evaluate_memory_pressure(rss_bytes=resume)
        self.assertFalse(clear.pause_bulk)
        self.assertEqual(clear.level, "ok")

    def test_hard_watermark_requests_model_unload(self):
        hard = memory_pressure.HARD_WATERMARK_BYTES
        unload_calls = []

        with (
            mock.patch.object(
                memory_pressure,
                "release_discardable_buffers",
                return_value={"gc": True},
            ) as release,
            mock.patch.object(
                memory_pressure,
                "request_model_unload",
                side_effect=lambda: unload_calls.append("unload") or ["embeddings"],
            ) as unload,
        ):
            pressure = memory_pressure.gate_bulk_work(rss_bytes=hard + 1)

        self.assertTrue(pressure.pause_bulk)
        self.assertTrue(pressure.unload_models)
        self.assertEqual(pressure.level, "hard")
        release.assert_called_once_with()
        unload.assert_called_once_with()
        self.assertEqual(unload_calls, ["unload"])

    def test_apply_to_decision_sets_honest_pause_reason(self):
        soft = memory_pressure.SOFT_WATERMARK_BYTES
        decision = _FakeDecision(
            mode="manual",
            intensity=1.0,
            pause=False,
            sleep_seconds=0.0,
            thumbnail_batch_size=16,
            thumbnail_pause_seconds=0.25,
            embedding_pause_seconds=0.25,
            reason="manual background work",
            checked_at=1.0,
        )
        with mock.patch.object(
            memory_pressure,
            "release_discardable_buffers",
            return_value={},
        ):
            paused = memory_pressure.apply_to_decision(decision, rss_bytes=soft + 1)

        self.assertTrue(paused.pause)
        self.assertEqual(paused.reason, "memory pressure")
        self.assertEqual(paused.mode, "paused")
        self.assertGreaterEqual(paused.sleep_seconds, 2.0)
        self.assertEqual(paused.thumbnail_batch_size, 0)

    def test_pregen_background_decision_inherits_shared_gate(self):
        import thumbnails

        soft = memory_pressure.SOFT_WATERMARK_BYTES
        with mock.patch.object(
            memory_pressure,
            "release_discardable_buffers",
            return_value={},
        ):
            memory_pressure.set_rss_reader(lambda: soft + 1024)
            decision = thumbnails._pregen_background_decision()

        self.assertIsInstance(decision, thumbnail_pregen.BackgroundDecision)
        self.assertTrue(decision.pause)
        self.assertEqual(decision.reason, "memory pressure")

    def test_hard_unload_then_lazy_reload_path_clears_model(self):
        import embedding_worker

        hard = memory_pressure.HARD_WATERMARK_BYTES
        embedding_worker._model = object()
        embedding_worker._loaded_model_dir = "/tmp/model"
        embedding_worker._loaded_model_id = "test"
        embedding_worker._loaded_model_revision = "main"
        self.addCleanup(embedding_worker._unload_model)

        with mock.patch.object(
            memory_pressure,
            "release_discardable_buffers",
            return_value={},
        ):
            memory_pressure.gate_bulk_work(rss_bytes=hard + 1)

        self.assertIsNone(embedding_worker._model)
        self.assertIsNone(embedding_worker._loaded_model_dir)


if __name__ == "__main__":
    unittest.main()
