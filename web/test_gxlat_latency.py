"""gxlat: interactive thumbs must not stall on HDD inspect or cold decode."""

from __future__ import annotations

import asyncio
import time
import unittest
from unittest import mock

from PIL import Image

import thumbnails
from features.media import routes as media_routes
from test_support import BackendTestCase, HeaderRequest


class GxlatInteractiveThumbTests(BackendTestCase):
    async def test_cached_thumb_skips_source_inspect_under_slow_hdd(self):
        source = await self._source("gxlat-cached")
        path = f"{source['path']}/warm.jpg"
        Image.new("RGB", (64, 48), color=(10, 20, 30)).save(path, quality=90)
        image_id = await self._image(source["id"], "warm.jpg")
        await thumbnails.get_thumbnail(path, "sm", image_id)

        def slow_inspect(*_args, **_kwargs):
            time.sleep(0.4)
            return "available", None

        with mock.patch.object(media_routes, "inspect_source_file", side_effect=slow_inspect) as inspect:
            started = time.perf_counter()
            response = await media_routes.serve_thumbnail(HeaderRequest(), "sm", image_id)
            elapsed = time.perf_counter() - started

        self.assertEqual(response.status_code, 200)
        self.assertLess(elapsed, 0.2, f"cached thumb took {elapsed:.3f}s — still paying HDD inspect?")
        inspect.assert_not_called()

    async def test_cold_decode_returns_pending_when_foreground_budget_exceeded(self):
        source = await self._source("gxlat-cold")
        path = f"{source['path']}/cold.jpg"
        Image.new("RGB", (64, 48), color=(80, 20, 30)).save(path, quality=90)
        image_id = await self._image(source["id"], "cold.jpg")

        async def slow_generate(*_args, **_kwargs):
            await asyncio.sleep(3.0)
            return b"slow-jpeg"

        old_timeout = media_routes._ON_DEMAND_FOREGROUND_TIMEOUT_SECONDS
        media_routes._ON_DEMAND_FOREGROUND_TIMEOUT_SECONDS = 0.15
        try:
            with mock.patch.object(thumbnails, "get_thumbnail", side_effect=slow_generate):
                started = time.perf_counter()
                response = await media_routes.serve_thumbnail(HeaderRequest(), "md", image_id)
                elapsed = time.perf_counter() - started
        finally:
            media_routes._ON_DEMAND_FOREGROUND_TIMEOUT_SECONDS = old_timeout

        self.assertEqual(response.status_code, 204)
        self.assertEqual(response.headers.get("Retry-After"), "1")
        self.assertLess(elapsed, 1.0, f"cold pending path took {elapsed:.3f}s")


if __name__ == "__main__":
    unittest.main()
