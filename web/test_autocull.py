"""Regression spine for quality-based, review-first stack culling."""

from __future__ import annotations

import unittest
import asyncio

from fastapi.testclient import TestClient

import db
import app as app_module
from features.quality import autocull
from features.quality import routes as quality_routes
from test_support import BackendTestCase


class AutocullTests(BackendTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()

    async def _stack(self, image_ids: list[int], *, kind: str = "burst") -> int:
        conn = await db.get_db()
        try:
            cursor = await conn.execute(
                "INSERT INTO stacks (kind, representative_image_id, auto, created_at, updated_at) VALUES (?, ?, 1, 1, 1)",
                (kind, image_ids[0]),
            )
            stack_id = int(cursor.lastrowid)
            await conn.executemany(
                "INSERT INTO stack_members (stack_id, image_id, score, added_at) VALUES (?, ?, NULL, 1)",
                [(stack_id, image_id) for image_id in image_ids],
            )
            await conn.commit()
            return stack_id
        finally:
            await conn.close()

    async def _quality(self, image_id: int, score: float) -> None:
        conn = await db.get_db()
        try:
            await autocull.ensure_autocull_tables(conn)
            await conn.execute(
                "INSERT INTO image_quality (image_id, score, scored_at) VALUES (?, ?, 'now')",
                (image_id, score),
            )
            await conn.commit()
        finally:
            await conn.close()

    async def test_suggestions_are_non_destructive_and_rank_quality(self):
        source = await self._source()
        soft = await self._image(source["id"], "soft.jpg")
        sharp = await self._image(source["id"], "sharp.jpg")
        stack_id = await self._stack([soft, sharp])
        await self._quality(soft, 34)
        await self._quality(sharp, 87)
        await self._cache_entry(soft)
        await self._cache_entry(sharp)

        payload = await quality_routes.api_quality_autocull(quality_routes.AutocullBody(stack_ids=[stack_id]))

        self.assertEqual(payload["scene_count"], 1)
        suggestion = payload["suggestions"][0]
        self.assertEqual(suggestion["suggested_pick_id"], sharp)
        self.assertEqual([member["filename"] for member in suggestion["members"]], ["sharp.jpg", "soft.jpg"])
        self.assertTrue(suggestion["members"][0]["suggested_pick"])
        self.assertEqual((await self._image_row(soft))["flag"], "unflagged")
        self.assertEqual((await self._image_row(sharp))["flag"], "unflagged")

    async def test_suggestions_wait_until_every_member_has_a_ready_preview(self):
        source = await self._source()
        ready = await self._image(source["id"], "ready.jpg")
        pending = await self._image(source["id"], "pending.jpg")
        stack_id = await self._stack([ready, pending])
        await self._quality(ready, 72)
        await self._quality(pending, 68)
        await self._cache_entry(ready)

        waiting = await quality_routes.api_quality_autocull(
            quality_routes.AutocullBody(stack_ids=[stack_id])
        )
        self.assertEqual(waiting["suggestions"], [])

        await self._cache_entry(pending)
        available = await quality_routes.api_quality_autocull(
            quality_routes.AutocullBody(stack_ids=[stack_id])
        )
        self.assertEqual(available["scene_count"], 1)
        self.assertTrue(all(member["preview_ready"] for member in available["suggestions"][0]["members"]))
        self.assertTrue(all(member["thumb_url"] for member in available["suggestions"][0]["members"]))

    async def test_apply_sets_flags_and_records_previous_state(self):
        source = await self._source()
        first = await self._image(source["id"], "first.jpg")
        best = await self._image(source["id"], "best.jpg")
        stack_id = await self._stack([first, best], kind="variant")
        await self._quality(first, 42)
        await self._quality(best, 92)
        await self._cache_entry(first)
        await self._cache_entry(best)

        response = await quality_routes.api_quality_autocull_apply(
            quality_routes.AutocullApplyBody(stack_ids=[stack_id])
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.body)
        self.assertEqual((await self._image_row(best))["flag"], "picked")
        self.assertEqual((await self._image_row(first))["flag"], "rejected")
        conn = await db.get_db()
        try:
            cursor = await conn.execute("SELECT previous_flag, applied_flag FROM autocull_history_items ORDER BY image_id")
            rows = [tuple(row) for row in await cursor.fetchall()]
        finally:
            await conn.close()
        self.assertEqual(rows, [("unflagged", "rejected"), ("unflagged", "picked")])

    async def test_cull_brief_apply_http_sets_each_scene_member_flag(self):
        source = await self._source()
        soft = await self._image(source["id"], "soft.jpg")
        sharp = await self._image(source["id"], "sharp.jpg")
        stack_id = await self._stack([soft, sharp])
        await self._quality(soft, 20)
        await self._quality(sharp, 90)
        await self._cache_entry(soft)
        await self._cache_entry(sharp)

        def apply():
            with TestClient(app_module.app) as client:
                return client.post("/api/quality/autocull/apply", json={"stack_ids": [stack_id]})
        response = await asyncio.to_thread(apply)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual((await self._image_row(sharp))["flag"], "picked")
        self.assertEqual((await self._image_row(soft))["flag"], "rejected")

    async def test_existing_manual_flag_is_not_suggested_or_overwritten(self):
        source = await self._source()
        first = await self._image(source["id"], "first.jpg")
        second = await self._image(source["id"], "second.jpg")
        stack_id = await self._stack([first, second])
        await self._quality(first, 90)
        await self._quality(second, 30)
        await self._cache_entry(first)
        await self._cache_entry(second)
        await db.set_image_flag(first, "picked")

        payload = await quality_routes.api_quality_autocull(quality_routes.AutocullBody(stack_ids=[stack_id]))

        self.assertEqual(payload["suggestions"], [])
        self.assertEqual((await self._image_row(first))["flag"], "picked")


if __name__ == "__main__":
    unittest.main()
