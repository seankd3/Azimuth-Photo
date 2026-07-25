"""Interactive isolation — status stays fast under a burst of cold decodes."""

from __future__ import annotations

import asyncio
import time
import unittest
from unittest import mock

from thumbnails import jobs as thumbnail_jobs
from thumbnails.decode_budget import estimate_decode_bytes


class OnDemandIsolationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        thumbnail_jobs.reset_on_demand_semaphore_for_tests(2)

    async def test_on_demand_decodes_queue_behind_semaphore(self):
        inflight_peaks: list[int] = []
        current = 0
        lock = asyncio.Lock()

        async def slow_job(*_args, **_kwargs):
            nonlocal current
            async with lock:
                current += 1
                inflight_peaks.append(current)
            await asyncio.sleep(0.05)
            async with lock:
                current -= 1
            return b"ok"

        async def one(image_id: int):
            return await thumbnail_jobs.ensure_thumbnail_with_executor(
                f"cold-{image_id}.tif",
                "sm",
                image_id,
                "executor",
                note_activity=True,
                note_user_activity=lambda: None,
                build_source_signature=lambda fp, size, iid: f"{fp}:{size}:{iid}",
                memory_get=lambda *_a: None,
                fast_disk_read_entry=lambda *_a: None,
                read_disk_thumbnail=lambda *_a: None,
                source_missing=lambda _fp: False,
                inflight={},
                run_thumbnail_job=slow_job,
            )

        results = await asyncio.gather(*[one(i) for i in range(20)])
        self.assertEqual(results, [b"ok"] * 20)
        self.assertLessEqual(max(inflight_peaks), 2)

    async def test_status_probe_p95_under_20_cold_tiff_decodes(self):
        """Regression: status endpoint stays <100ms p95 while 20 cold TIFFs decode."""

        async def slow_tiff_job(*_args, **_kwargs):
            await asyncio.sleep(0.4)
            return b"tiff"

        async def status_probe() -> float:
            started = time.perf_counter()
            # Stand in for /api/cache/pregen/status — pure async, no decode pool.
            await asyncio.sleep(0)
            return (time.perf_counter() - started) * 1000.0

        decode_tasks = [
            asyncio.create_task(
                thumbnail_jobs.ensure_thumbnail_with_executor(
                    f"cold-{i}.tif",
                    "sm",
                    10_000 + i,
                    "executor",
                    note_activity=True,
                    note_user_activity=lambda: None,
                    build_source_signature=lambda fp, size, iid: f"{fp}:{size}:{iid}",
                    memory_get=lambda *_a: None,
                    fast_disk_read_entry=lambda *_a: None,
                    read_disk_thumbnail=lambda *_a: None,
                    source_missing=lambda _fp: False,
                    inflight={},
                    run_thumbnail_job=slow_tiff_job,
                )
            )
            for i in range(20)
        ]
        # Let the burst grab the semaphore slots.
        await asyncio.sleep(0.02)

        samples: list[float] = []
        for _ in range(30):
            samples.append(await status_probe())
            await asyncio.sleep(0.01)

        await asyncio.gather(*decode_tasks)
        samples_sorted = sorted(samples)
        # Nearest-rank p95.
        p95 = samples_sorted[max(0, int(round(0.95 * len(samples_sorted))) - 1)]
        self.assertLess(
            p95,
            100.0,
            f"status p95={p95:.1f}ms during 20 cold TIFF decodes (samples={samples[:5]}…)",
        )


class EmbeddedDecodeBudgetTests(unittest.TestCase):
    def test_embedded_raw_is_about_ten_times_cheaper_than_demosaic(self):
        demosaic = estimate_decode_bytes(raw=True, width=9504, height=6336)
        embedded = estimate_decode_bytes(
            raw=True,
            embedded_preview=True,
            width=9504,
            height=6336,
        )
        self.assertGreaterEqual(demosaic, 500 * 1024 * 1024)
        self.assertLessEqual(embedded, demosaic // 8)
        # Still floors at the minimum weight.
        self.assertGreaterEqual(embedded, 16 * 1024 * 1024)

    def test_embedded_allows_more_concurrent_60mp_at_768mib(self):
        budget_cap = 768 * 1024 * 1024
        demosaic = estimate_decode_bytes(raw=True, width=9504, height=6336)
        embedded = estimate_decode_bytes(
            raw=True,
            embedded_preview=True,
            width=9504,
            height=6336,
        )
        self.assertEqual(budget_cap // demosaic, 1)
        self.assertGreaterEqual(budget_cap // embedded, 8)

    def test_dng_is_not_in_embedded_preview_extensions(self):
        from thumbnails.config import EMBEDDED_PREVIEW_EXTENSIONS, RAW_EXTENSIONS

        self.assertIn(".dng", RAW_EXTENSIONS)
        self.assertNotIn(".dng", EMBEDDED_PREVIEW_EXTENSIONS)
        self.assertIn(".cr3", EMBEDDED_PREVIEW_EXTENSIONS)


class PregenStallWatchdogTests(unittest.IsolatedAsyncioTestCase):
    async def test_stall_watchdog_cancels_stuck_batch_and_resets_budget(self):
        from thumbnails import pregen_worker
        from thumbnails.decode_budget import bulk_decode_budget

        old_poll = pregen_worker.PREGEN_STALL_WATCHDOG_POLL_SECONDS
        pregen_worker.PREGEN_STALL_WATCHDOG_POLL_SECONDS = 0.05
        status = {
            "state": "paused",
            "started_at": None,
            "last_generated_at": None,
            "message": "",
        }
        events: list[str] = []
        prefetching = True

        async def hang_forever(generate_batch=None):
            events.append("batch-start")
            await asyncio.Event().wait()
            return 0

        def set_state(state, message="", **_kwargs):
            status["state"] = state
            status["message"] = message
            if state == "running" and status["started_at"] is None:
                status["started_at"] = time.time() - 10.0
            events.append(f"state:{state}")

        async def sleeper(seconds):
            await asyncio.sleep(min(float(seconds), 0.05))

        held = await bulk_decode_budget.acquire(400 * 1024 * 1024)
        resets: list[str] = []

        async def stop_soon():
            await asyncio.sleep(0.35)
            nonlocal prefetching
            prefetching = False

        stopper = asyncio.create_task(stop_soon())
        try:
            await pregen_worker.run_prefetch_worker_loop(
                is_prefetching=lambda: prefetching,
                is_manual_paused=lambda: False,
                is_manual_mode=lambda: True,
                pregen_on_idle=lambda: True,
                cache_target_total=lambda: asyncio.sleep(0, result=10),
                current_monotonic=time.monotonic,
                set_pregen_state=set_state,
                sleep=sleeper,
                flush_write_queue=lambda: True,
                flush_orientation_updates=lambda: asyncio.sleep(0),
                should_pause_for_priority=lambda: False,
                background_decision=lambda: mock.Mock(
                    pause=False,
                    reason="manual",
                    mode="manual",
                    sleep_seconds=0.0,
                    thumbnail_pause_seconds=0.0,
                ),
                generate_batch_for_decision=lambda _d: 8,
                pregen_status=status,
                disk_allocations={"sm": 64 * 1024 * 1024, "md": 0, "lg": 0, "full": 0},
                full_tier="full",
                background_tier_budget=lambda size: 64 * 1024 * 1024 if size == "sm" else 0,
                run_pregen_bulk_batch=hang_forever,
                run_full_warm_batch=lambda **_k: asyncio.sleep(0, result=0),
                get_pregen_status=lambda _t: {
                    "phases": {"sm": {"count": 0, "total": 10}},
                    "originals": {"count": 0, "utilization_pct": 0.0},
                },
                no_progress_scan_limit=lambda: 12,
                batch_pause_seconds=lambda: 0.01,
                stall_watchdog_seconds=0.1,
                reset_prefetch_executor=lambda: resets.append("executor"),
            )
        finally:
            await stopper
            await bulk_decode_budget.release(held)
            pregen_worker.PREGEN_STALL_WATCHDOG_POLL_SECONDS = old_poll

        self.assertIn("batch-start", events)
        self.assertTrue(
            any(e.startswith("state:waiting") for e in events),
            events,
        )
        self.assertEqual(bulk_decode_budget.used_bytes, 0)
        self.assertGreaterEqual(len(resets), 1)

    async def test_stall_watchdog_spares_batch_with_recent_progress(self):
        """Slow-but-working batches must not be cancelled by the stall watchdog."""
        from thumbnails import pregen_worker
        from thumbnails.decode_budget import bulk_decode_budget

        old_poll = pregen_worker.PREGEN_STALL_WATCHDOG_POLL_SECONDS
        pregen_worker.PREGEN_STALL_WATCHDOG_POLL_SECONDS = 0.05
        now = time.time()
        status = {
            "state": "paused",
            "started_at": now - 30.0,
            # last successful write is old — would false-positive without progress heartbeat
            "last_generated_at": now - 30.0,
            "last_progress_at": now,  # decode actively making progress
            "message": "",
        }
        events: list[str] = []
        prefetching = True
        cancel_reasons: list[str] = []
        stall_trips: list[str] = []

        async def slow_working_batch(generate_batch=None):
            events.append("batch-start")
            # Keep progress fresh while "demosaicing" — beats every 50ms,
            # well inside the 400ms stall window.
            for _ in range(10):
                status["last_progress_at"] = time.time()
                await asyncio.sleep(0.05)
            events.append("batch-done")
            return 2

        def set_state(state, message="", **_kwargs):
            status["state"] = state
            status["message"] = message
            if "stalled batch" in message:
                stall_trips.append(message)
            if state == "running" and status["started_at"] is None:
                status["started_at"] = time.time()
            events.append(f"state:{state}")

        async def sleeper(seconds):
            await asyncio.sleep(min(float(seconds), 0.05))

        async def stop_soon():
            await asyncio.sleep(0.7)
            nonlocal prefetching
            prefetching = False

        stopper = asyncio.create_task(stop_soon())
        try:
            await pregen_worker.run_prefetch_worker_loop(
                is_prefetching=lambda: prefetching,
                is_manual_paused=lambda: False,
                is_manual_mode=lambda: True,
                pregen_on_idle=lambda: True,
                cache_target_total=lambda: asyncio.sleep(0, result=10),
                current_monotonic=time.monotonic,
                set_pregen_state=set_state,
                sleep=sleeper,
                flush_write_queue=lambda: True,
                flush_orientation_updates=lambda: asyncio.sleep(0),
                should_pause_for_priority=lambda: False,
                background_decision=lambda: mock.Mock(
                    pause=False,
                    reason="manual",
                    mode="manual",
                    sleep_seconds=0.0,
                    thumbnail_pause_seconds=0.0,
                ),
                generate_batch_for_decision=lambda _d: 8,
                pregen_status=status,
                disk_allocations={"sm": 64 * 1024 * 1024, "md": 0, "lg": 0, "full": 0},
                full_tier="full",
                background_tier_budget=lambda size: 64 * 1024 * 1024 if size == "sm" else 0,
                run_pregen_bulk_batch=slow_working_batch,
                run_full_warm_batch=lambda **_k: asyncio.sleep(0, result=0),
                get_pregen_status=lambda _t: {
                    "phases": {"sm": {"count": 0, "total": 10}},
                    "originals": {"count": 0, "utilization_pct": 0.0},
                },
                no_progress_scan_limit=lambda: 12,
                batch_pause_seconds=lambda: 0.01,
                # Stall window >> progress heartbeat; old last_generated_at alone
                # would false-positive without last_progress_at.
                stall_watchdog_seconds=0.4,
                reset_prefetch_executor=lambda: cancel_reasons.append("executor"),
            )
        finally:
            await stopper
            pregen_worker.PREGEN_STALL_WATCHDOG_POLL_SECONDS = old_poll

        self.assertIn("batch-start", events)
        self.assertIn("batch-done", events)
        self.assertEqual(
            cancel_reasons,
            [],
            "watchdog must not reset executor for a progressing batch",
        )
        self.assertEqual(stall_trips, [], "watchdog must not declare a stall")
        self.assertEqual(bulk_decode_budget.used_bytes, 0)


if __name__ == "__main__":
    unittest.main()
