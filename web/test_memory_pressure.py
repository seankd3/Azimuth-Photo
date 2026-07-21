"""Memory watermark gates for background bulk work."""

from __future__ import annotations

import asyncio
import unittest
from dataclasses import dataclass
from unittest import mock

from core import memory_pressure
from thumbnails import pregen as thumbnail_pregen
from thumbnails import pregen_worker
from thumbnails.decode_budget import DecodeByteBudget, estimate_decode_bytes


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
        self.assertTrue(over.unload_models)
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
                side_effect=lambda **_kwargs: unload_calls.append("unload") or ["embeddings"],
            ) as unload,
        ):
            pressure = memory_pressure.gate_bulk_work(rss_bytes=hard + 1)

        self.assertTrue(pressure.pause_bulk)
        self.assertTrue(pressure.unload_models)
        self.assertEqual(pressure.level, "hard")
        release.assert_called_once_with()
        unload.assert_called_once_with(force=False)
        self.assertEqual(unload_calls, ["unload"])

    def test_soft_gate_sheds_models_and_logs_rss_delta(self):
        soft = memory_pressure.SOFT_WATERMARK_BYTES
        unload_calls = []

        with (
            mock.patch.object(
                memory_pressure,
                "release_discardable_buffers",
                return_value={"gc": True, "thumbnail_memory": 12},
            ) as release,
            mock.patch.object(
                memory_pressure,
                "request_model_unload",
                side_effect=lambda **_kwargs: unload_calls.append("unload") or ["embeddings"],
            ) as unload,
            mock.patch.object(memory_pressure, "read_rss_bytes", side_effect=[soft + 10, soft // 2]),
        ):
            pressure = memory_pressure.gate_bulk_work(rss_bytes=soft + 10)

        self.assertTrue(pressure.pause_bulk)
        self.assertTrue(pressure.unload_models)
        release.assert_called_once_with()
        unload.assert_called_once_with(force=False)
        self.assertEqual(unload_calls, ["unload"])

    def test_pause_shed_then_lower_rss_resumes(self):
        soft = memory_pressure.SOFT_WATERMARK_BYTES
        resume = memory_pressure.RESUME_WATERMARK_BYTES
        rss_state = {"value": soft + 1024}

        def fake_reader():
            return rss_state["value"]

        memory_pressure.set_rss_reader(fake_reader)
        with (
            mock.patch.object(
                memory_pressure,
                "release_discardable_buffers",
                return_value={"gc": True},
            ) as release,
            mock.patch.object(
                memory_pressure,
                "request_model_unload",
                return_value=["embeddings"],
            ) as unload,
        ):
            paused = memory_pressure.gate_bulk_work()
            self.assertTrue(paused.pause_bulk)
            release.assert_called_once_with()
            unload.assert_called_once_with(force=False)

            # Shed "worked" — RSS falls to the resume watermark.
            rss_state["value"] = resume
            clear = memory_pressure.evaluate_memory_pressure()
            self.assertFalse(clear.pause_bulk)
            self.assertEqual(clear.level, "ok")

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
        with (
            mock.patch.object(
                memory_pressure,
                "release_discardable_buffers",
                return_value={},
            ),
            mock.patch.object(
                memory_pressure,
                "request_model_unload",
                return_value=[],
            ),
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
        with (
            mock.patch.object(
                memory_pressure,
                "release_discardable_buffers",
                return_value={},
            ),
            mock.patch.object(
                memory_pressure,
                "request_model_unload",
                return_value=[],
            ),
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

    def test_effective_prefetch_workers_halves_above_resume(self):
        soft = memory_pressure.SOFT_WATERMARK_BYTES
        resume = memory_pressure.RESUME_WATERMARK_BYTES
        self.assertEqual(
            memory_pressure.effective_prefetch_workers(6, rss_bytes=resume),
            6,
        )
        self.assertEqual(
            memory_pressure.effective_prefetch_workers(6, rss_bytes=resume + 1),
            3,
        )
        self.assertEqual(
            memory_pressure.effective_prefetch_workers(6, rss_bytes=soft + 1),
            3,
        )
        self.assertEqual(
            memory_pressure.effective_prefetch_workers(1, rss_bytes=soft + 1),
            1,
        )

    def test_combined_pressure_trips_on_high_swap_stays_calm_on_modest(self):
        """RSS-only blind spot: low RSS + high swap must trip; normal browse must not."""
        soft = memory_pressure.SOFT_WATERMARK_BYTES
        gib = 1024**3

        # Normal browse: ~1 GiB RSS + modest swap — well under soft.
        calm = memory_pressure.evaluate_memory_pressure(
            pressure_bytes=1 * gib + 200 * 1024 * 1024,
            rss_bytes=1 * gib,
            swap_bytes=200 * 1024 * 1024,
        )
        self.assertFalse(calm.pause_bulk)
        self.assertEqual(calm.level, "ok")
        self.assertEqual(calm.swap_bytes, 200 * 1024 * 1024)

        # Synthetic: RSS alone looks fine, but swap pushes combined over soft.
        rss_only = 1100 * 1024 * 1024
        swap_heavy = soft - rss_only + (64 * 1024 * 1024)
        self.assertLess(rss_only, soft)
        tripped = memory_pressure.evaluate_memory_pressure(
            pressure_bytes=rss_only + swap_heavy,
            rss_bytes=rss_only,
            swap_bytes=swap_heavy,
        )
        self.assertTrue(tripped.pause_bulk)
        self.assertEqual(tripped.level, "soft")
        self.assertTrue(tripped.unload_models)
        self.assertGreaterEqual(tripped.pressure_bytes, soft)

    def test_memory_reader_injection_drives_gate(self):
        soft = memory_pressure.SOFT_WATERMARK_BYTES
        memory_pressure.set_memory_reader(
            lambda: memory_pressure.MemoryReading(
                rss_bytes=900 * 1024 * 1024,
                swap_bytes=soft,
                pressure_bytes=900 * 1024 * 1024 + soft,
                source="injected",
            )
        )
        with (
            mock.patch.object(
                memory_pressure,
                "release_discardable_buffers",
                return_value={"gc": True},
            ),
            mock.patch.object(
                memory_pressure,
                "request_model_unload",
                return_value=["embeddings"],
            ) as unload,
        ):
            pressure = memory_pressure.gate_bulk_work()
        self.assertTrue(pressure.pause_bulk)
        self.assertEqual(pressure.signal, "injected")
        self.assertGreater(pressure.swap_bytes, 0)
        unload.assert_called_once_with(force=False)

    def test_pressure_unload_skips_pinned_search_model(self):
        from core.model_pool import ModelPool, reset_model_pool_for_tests

        clock = {"now": 1000.0}
        unloads: list[str] = []

        def empty_cache():
            return None

        pool = reset_model_pool_for_tests(
            ModelPool(
                vram_budget_bytes=None,
                ram_budget_bytes=None,
                pin_seconds=30.0,
                empty_cache=empty_cache,
                clock=lambda: clock["now"],
            )
        )
        self.addCleanup(
            lambda: reset_model_pool_for_tests(
                ModelPool(
                    vram_budget_bytes=None,
                    ram_budget_bytes=None,
                    pin_seconds=0.0,
                    empty_cache=lambda: None,
                )
            )
        )

        pool.acquire(
            "embeddings",
            load_fn=lambda: "search-model",
            unload_fn=lambda: unloads.append("embeddings"),
            vram_bytes=10,
            ram_bytes=10,
            interactive=True,
        )
        self.assertTrue(pool.is_pinned("embeddings"))

        hard = memory_pressure.HARD_WATERMARK_BYTES
        with mock.patch.object(
            memory_pressure,
            "release_discardable_buffers",
            return_value={},
        ):
            pressure = memory_pressure.gate_bulk_work(rss_bytes=hard + 1)

        self.assertTrue(pressure.pause_bulk)
        self.assertTrue(pressure.unload_models)
        self.assertIn("embeddings", pool.resident_names())
        self.assertEqual(unloads, [])

        # After pin window, the same shed path must clear residency.
        clock["now"] += 31.0
        self.assertFalse(pool.is_pinned("embeddings"))
        with mock.patch.object(
            memory_pressure,
            "release_discardable_buffers",
            return_value={},
        ):
            memory_pressure.gate_bulk_work(rss_bytes=hard + 1)
        self.assertNotIn("embeddings", pool.resident_names())
        self.assertEqual(unloads, ["embeddings"])


class MidBatchPressureAbortTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        memory_pressure.reset_for_tests()
        self.addCleanup(memory_pressure.reset_for_tests)

    async def test_mid_batch_soft_abort_stops_further_decodes(self):
        soft = memory_pressure.SOFT_WATERMARK_BYTES
        rss_state = {"value": soft - 1024}
        memory_pressure.set_rss_reader(lambda: rss_state["value"])

        submissions: list[int] = []

        def fake_generate(filepath, image_id, signatures, **kwargs):
            submissions.append(int(image_id))
            # Cross soft after the first decode lands.
            rss_state["value"] = soft + 1024
            return {"written": 1, "image_id": image_id}

        async def fake_candidates(limit):
            return [
                {
                    "id": i,
                    "source_id": 1,
                    "filepath": f"/tmp/photo_{i}.CR3",
                    "file_size": 40 * 1024 * 1024,
                    "file_modified_at": 1.0,
                    "width": 9504,
                    "height": 6336,
                }
                for i in range(1, 9)
            ]

        async def _priority_empty(limit, processed):
            return [], None

        with (
            mock.patch.object(
                memory_pressure,
                "release_discardable_buffers",
                return_value={"gc": True},
            ),
            mock.patch.object(
                memory_pressure,
                "request_model_unload",
                return_value=[],
            ),
        ):
            result = await pregen_worker.run_pregen_bulk_batch(
                generate_batch=8,
                default_generate_batch=8,
                scan_batch=8,
                thumb_tiers=("sm",),
                full_tier="full",
                disk_allocations={"sm": 10**12, "full": 0},
                is_prefetching=lambda: True,
                is_manual_paused=lambda: False,
                should_pause_for_priority=lambda: False,
                flush_write_queue=lambda: True,
                cache_metadata_backoff_active=lambda: False,
                bulk_tier_budgets=lambda: {"sm": 10**12},
                bulk_tier_room=lambda budgets: {"sm": 10**12},
                full_tier_room=lambda budget: 0,
                pregen_priority_candidate_batch=_priority_empty,
                pregen_bulk_candidate_batch=fake_candidates,
                reset_pregen_bulk_cursor=lambda: None,
                set_priority_scope=lambda label: None,
                bulk_candidate_signatures=lambda row, tier_room, tier_budgets: (
                    {"sm": "sig"},
                    int(row["file_size"]),
                ),
                full_candidate_signature=lambda *args, **kwargs: None,
                prefetch_executor=None,
                generate_thumbnail_set_sync=fake_generate,
                record_pregen_result=lambda result: int(result.get("written") or 0),
                activity_burst_items=2,
                prefetch_workers=1,
            )

        self.assertEqual(result, pregen_worker.PREGEN_PRESSURE)
        self.assertEqual(submissions, [1])
        self.assertTrue(memory_pressure.evaluate_memory_pressure().pause_bulk)


class DecodeBudgetDimensionTests(unittest.IsolatedAsyncioTestCase):
    def test_sixty_mp_raw_charges_at_least_500mb(self):
        # ~60.2MP (typical high-res body).
        estimate = estimate_decode_bytes(
            70 * 1024 * 1024,
            raw=True,
            width=9504,
            height=6336,
        )
        self.assertGreaterEqual(estimate, 500 * 1024 * 1024)

    async def test_budget_refuses_second_60mp_while_first_inflight(self):
        budget = DecodeByteBudget(max_bytes=768 * 1024 * 1024)
        weight = estimate_decode_bytes(raw=True, width=9504, height=6336)
        self.assertGreaterEqual(weight, 500 * 1024 * 1024)

        first = await budget.acquire(weight)
        order: list[str] = []

        async def second():
            order.append("wait")
            await budget.acquire(weight)
            order.append("got")

        task = asyncio.create_task(second())
        await asyncio.sleep(0.05)
        self.assertEqual(order, ["wait"])
        self.assertGreater(budget.used_bytes, 0)
        await budget.release(first)
        await asyncio.wait_for(task, timeout=1.0)
        self.assertEqual(order, ["wait", "got"])


class AdaptiveWatermarkTests(unittest.TestCase):
    def test_fallback_watermarks_are_fractions_of_detected_ram(self):
        total = 16 * memory_pressure._GIB
        with mock.patch.object(memory_pressure, "read_cgroup_limits", return_value=(None, None)), mock.patch.object(
            memory_pressure, "_detect_total_ram_bytes", return_value=total
        ):
            soft, hard = memory_pressure._default_watermarks()
        self.assertEqual(soft, int(total * 0.55))
        self.assertEqual(hard, int(total * 0.72))

    def test_fallback_watermarks_scale_on_64gb_host(self):
        total = 64 * memory_pressure._GIB
        with mock.patch.object(memory_pressure, "read_cgroup_limits", return_value=(None, None)), mock.patch.object(
            memory_pressure, "_detect_total_ram_bytes", return_value=total
        ):
            soft, hard = memory_pressure._default_watermarks()
        self.assertEqual(soft, int(total * 0.55))
        self.assertEqual(hard, int(total * 0.72))
        self.assertGreater(soft, 30 * memory_pressure._GIB)


if __name__ == "__main__":
    unittest.main()
