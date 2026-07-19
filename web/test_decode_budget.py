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


if __name__ == "__main__":
    unittest.main()
