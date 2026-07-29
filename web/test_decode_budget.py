"""Decode-byte budget for bulk preview warming."""

from __future__ import annotations

import asyncio
import time
import unittest

from thumbnails.decode_budget import DecodeByteBudget, estimate_decode_bytes


class DecodeBudgetTests(unittest.IsolatedAsyncioTestCase):
    def test_estimate_raw_is_heavier_than_jpeg(self):
        raw = estimate_decode_bytes(50 * 1024 * 1024, raw=True)
        jpeg = estimate_decode_bytes(50 * 1024 * 1024, raw=False)
        self.assertGreater(raw, jpeg)
        self.assertGreaterEqual(raw, 200 * 1024 * 1024)

    def test_estimate_prefers_dimensions_over_file_size(self):
        # Tiny file size would under-weight; dimensions must dominate.
        by_dims = estimate_decode_bytes(
            8 * 1024 * 1024,
            raw=True,
            width=9504,
            height=6336,
        )
        by_file = estimate_decode_bytes(8 * 1024 * 1024, raw=True)
        self.assertGreater(by_dims, by_file)
        self.assertGreaterEqual(by_dims, 500 * 1024 * 1024)

    async def test_budget_serializes_oversized_inflight_decodes(self):
        budget = DecodeByteBudget(max_bytes=100 * 1024 * 1024)
        order: list[str] = []

        async def worker(name: str, size: int):
            async with budget.hold(size):
                order.append(f"{name}:start")
                await asyncio.sleep(0.05)
                order.append(f"{name}:end")

        await asyncio.gather(
            worker("a", 80 * 1024 * 1024),
            worker("b", 80 * 1024 * 1024),
        )
        self.assertEqual(len(order), 4)
        self.assertTrue(order[0].endswith(":start"))
        self.assertEqual(order[1], order[0].replace(":start", ":end"))
        self.assertTrue(order[2].endswith(":start"))
        self.assertEqual(order[3], order[2].replace(":start", ":end"))
        self.assertEqual(budget.used_bytes, 0)

    def test_estimate_embedded_raw_is_cheaper_than_demosaic(self):
        demosaic = estimate_decode_bytes(raw=True, width=9504, height=6336)
        embedded = estimate_decode_bytes(
            raw=True,
            embedded_preview=True,
            width=9504,
            height=6336,
        )
        self.assertGreaterEqual(demosaic, 500 * 1024 * 1024)
        self.assertLessEqual(embedded, demosaic // 8)

    async def test_sixty_mp_refuses_concurrent_second_at_768mib(self):
        budget = DecodeByteBudget(max_bytes=768 * 1024 * 1024)
        weight = estimate_decode_bytes(raw=True, width=9504, height=6336)
        self.assertGreaterEqual(weight, 500 * 1024 * 1024)

        held = await budget.acquire(weight)
        blocked = asyncio.create_task(budget.acquire(weight))
        await asyncio.sleep(0.05)
        self.assertFalse(blocked.done())
        await budget.release(held)
        second = await asyncio.wait_for(blocked, timeout=1.0)
        await budget.release(second)
        self.assertEqual(budget.used_bytes, 0)

    async def test_try_acquire_never_blocks_a_holder(self):
        """A caller holding in-flight work refills without waiting.

        The bulk pump deadlocked here: it blocked in acquire while its own
        completion loop — the only place releases happen — was unreachable.
        """
        budget = DecodeByteBudget(max_bytes=768 * 1024 * 1024)
        first = budget.try_acquire(500 * 1024 * 1024)
        self.assertIsNotNone(first)
        # Does not fit alongside the first: refuse instantly, never wait.
        self.assertIsNone(budget.try_acquire(500 * 1024 * 1024))
        self.assertTrue(budget.release_nowait(first, epoch=budget.epoch))
        # Empty ledger admits even an oversized frame, capped to the budget.
        capped = budget.try_acquire(2 * 1024 * 1024 * 1024)
        self.assertEqual(capped, budget.max_bytes)
        self.assertTrue(budget.release_nowait(capped, epoch=budget.epoch))
        self.assertEqual(budget.used_bytes, 0)

    async def test_cancel_mid_batch_releases_budget_fully(self):
        """Cancelled batch finally must return budget to empty (no leak)."""
        from core import memory_pressure
        from thumbnails.decode_budget import bulk_decode_budget
        from thumbnails import pregen_worker

        # An earlier TestClient(app) startup arms a 120s startup-calm that makes
        # run_pregen_bulk_batch skip decodes entirely; clear it so the batch runs.
        memory_pressure.reset_for_tests()
        self.addCleanup(memory_pressure.reset_for_tests)

        # Isolate process-wide budget for this test.
        old_used = bulk_decode_budget._used
        old_epoch = bulk_decode_budget._epoch
        bulk_decode_budget._used = 0
        self.addCleanup(lambda: setattr(bulk_decode_budget, "_used", old_used))
        self.addCleanup(lambda: setattr(bulk_decode_budget, "_epoch", old_epoch))

        hang = asyncio.Event()
        progress_beats: list[float] = []

        def generate_sync(*_args, **_kwargs):
            # Block the executor thread until the batch task is cancelled.
            import time as _time
            deadline = _time.time() + 2.0
            while not hang.is_set() and _time.time() < deadline:
                _time.sleep(0.01)
            return {
                "source_reads": 1,
                "thumbnails_written": 1,
                "originals_written": 0,
                "source_bytes": 1024,
            }

        def note_progress():
            progress_beats.append(time.time())

        async def run_and_cancel():
            batch = asyncio.create_task(
                pregen_worker.run_pregen_bulk_batch(
                    generate_batch=2,
                    default_generate_batch=2,
                    scan_batch=2,
                    thumb_tiers=("sm",),
                    full_tier="full",
                    disk_allocations={"sm": 64 * 1024 * 1024, "full": 0},
                    is_prefetching=lambda: True,
                    is_manual_paused=lambda: False,
                    should_pause_for_priority=lambda: False,
                    flush_write_queue=lambda: True,
                    cache_metadata_backoff_active=lambda: False,
                    bulk_tier_budgets=lambda: {"sm": 64 * 1024 * 1024},
                    bulk_tier_room=lambda _b: {"sm": 64 * 1024 * 1024},
                    full_tier_room=lambda _b: 0,
                    pregen_priority_candidate_batch=lambda *_a, **_k: asyncio.sleep(
                        0, result=([], None)
                    ),
                    pregen_bulk_candidate_batch=lambda _n: asyncio.sleep(
                        0,
                        result=[
                            {
                                "id": 1,
                                "filepath": "/tmp/a.dng",
                                "content_hash": "x",
                                "metadata_version": 99,
                                "metadata_scanned_at": 1.0,
                                "width": 1000,
                                "height": 800,
                            },
                            {
                                "id": 2,
                                "filepath": "/tmp/b.dng",
                                "content_hash": "y",
                                "metadata_version": 99,
                                "metadata_scanned_at": 1.0,
                                "width": 1000,
                                "height": 800,
                            },
                        ],
                    ),
                    reset_pregen_bulk_cursor=lambda: None,
                    set_priority_scope=lambda _l: None,
                    bulk_candidate_signatures=lambda row, tier_room, tier_budgets: (
                        {"sm": "sig"},
                        10 * 1024 * 1024,
                    ),
                    full_candidate_signature=lambda *_a, **_k: None,
                    prefetch_executor=None,
                    generate_thumbnail_set_sync=generate_sync,
                    record_pregen_result=lambda result: int(result.get("thumbnails_written") or 0),
                    activity_burst_items=8,
                    prefetch_workers=2,
                    note_progress=note_progress,
                )
            )
            # Wait until budget is held (decode started).
            for _ in range(100):
                if bulk_decode_budget.used_bytes > 0:
                    break
                await asyncio.sleep(0.01)
            self.assertGreater(bulk_decode_budget.used_bytes, 0)
            self.assertGreater(len(progress_beats), 0)
            batch.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await batch
            hang.set()
            # Allow wake_waiters_soon notify task to settle.
            await asyncio.sleep(0.05)
            self.assertEqual(
                bulk_decode_budget.used_bytes,
                0,
                "cancelled batch must release all decode budget",
            )

        await run_and_cancel()

    async def test_stale_release_after_reset_does_not_steal_new_holds(self):
        budget = DecodeByteBudget(max_bytes=100 * 1024 * 1024)
        epoch = budget.epoch
        w1 = await budget.acquire(40 * 1024 * 1024)
        leaked = await budget.reset_and_notify()
        self.assertEqual(leaked, 40 * 1024 * 1024)
        self.assertEqual(budget.used_bytes, 0)
        w2 = await budget.acquire(40 * 1024 * 1024)
        self.assertEqual(budget.used_bytes, 40 * 1024 * 1024)
        # Late release from cancelled generation must be a no-op.
        self.assertFalse(budget.release_nowait(w1, epoch=epoch))
        self.assertEqual(budget.used_bytes, 40 * 1024 * 1024)
        await budget.release(w2, epoch=budget.epoch)
        self.assertEqual(budget.used_bytes, 0)


if __name__ == "__main__":
    unittest.main()
