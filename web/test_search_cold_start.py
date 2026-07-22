from __future__ import annotations

import asyncio
import unittest
from unittest import mock

from core import query_constraints


class SearchColdStartTests(unittest.IsolatedAsyncioTestCase):
    async def test_lexical_results_remain_intelligent_while_model_warms(self):
        result = {
            "id_filter": None,
            "scores": {},
            "search_mode": "",
            "search_sources": [],
            "ai_unavailable": False,
            "fallback_reason": "",
            "evidence_by_id": {},
        }

        used = await query_constraints._apply_lexical_search(
            result,
            "kindergarten signs",
            query_plan=query_constraints.plan_search("kindergarten signs"),
            metadata_ranked_image_ids=mock.AsyncMock(return_value=[(2, 1.0)]),
            caption_ranked_image_ids=mock.AsyncMock(return_value=[(1, 2.0), (2, 1.0)]),
            get_active_images_by_ids=mock.AsyncMock(return_value={1: {}, 2: {}}),
        )

        self.assertTrue(used)
        self.assertEqual(result["search_mode"], "fused")
        self.assertEqual(result["fallback_reason"], "embedding_warming")
        self.assertEqual(result["evidence_by_id"][2]["signals"], ["captions", "metadata"])

    async def test_cold_model_wait_is_bounded_without_cancelling_load(self):
        load_finished = asyncio.Event()

        async def slow_load(_worker):
            await asyncio.sleep(0.03)
            load_finished.set()
            return True

        with (
            mock.patch.object(query_constraints, "_COMMITTED_COLD_START_BUDGET_SECONDS", 0.001),
            mock.patch.object(query_constraints, "ensure_search_model_loaded", side_effect=slow_load),
        ):
            task = asyncio.create_task(query_constraints.ensure_search_model_loaded(object()))
            try:
                await asyncio.wait_for(asyncio.shield(task), 0.001)
            except asyncio.TimeoutError:
                pass
            await asyncio.wait_for(load_finished.wait(), 0.1)

        self.assertTrue(task.done())
        self.assertFalse(task.cancelled())


if __name__ == "__main__":
    unittest.main()
