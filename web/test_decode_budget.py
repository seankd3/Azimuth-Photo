"""Decode-byte budget for bulk preview warming."""

from __future__ import annotations

import asyncio
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


if __name__ == "__main__":
    unittest.main()
