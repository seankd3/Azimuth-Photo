"""Foreground media isolation and manual sync route regressions."""

from __future__ import annotations

import asyncio
import json
import threading
import time
import unittest
from types import SimpleNamespace
from unittest import mock

from features.sync import executor, satellite_routes


class ForegroundExecutorTests(unittest.IsolatedAsyncioTestCase):
    async def test_foreground_hub_work_does_not_queue_behind_background_pool(self):
        started = [threading.Event() for _ in range(executor._SYNC_WORKERS)]
        release = threading.Event()

        def occupy_background(index: int) -> None:
            started[index].set()
            release.wait(timeout=2)

        tasks = [
            asyncio.create_task(executor.run_sync_work(occupy_background, index))
            for index in range(executor._SYNC_WORKERS)
        ]
        try:
            for event in started:
                self.assertTrue(await asyncio.to_thread(event.wait, 1))
            began = time.perf_counter()
            result = await asyncio.wait_for(
                executor.run_foreground_sync_work(lambda: "foreground"),
                timeout=1,
            )
            elapsed = time.perf_counter() - began
        finally:
            release.set()
            await asyncio.gather(*tasks)
            executor._semaphore = None
            executor._foreground_semaphore = None

        self.assertEqual(result, "foreground")
        self.assertLess(elapsed, 1.0)


class ManualSyncRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_mirror_refresh_and_prefetch_return_202_before_work_finishes(self):
        mirror_started = asyncio.Event()
        prefetch_started = asyncio.Event()
        release = asyncio.Event()

        async def refresh():
            mirror_started.set()
            await release.wait()

        async def prefetch_once(*, size):
            self.assertEqual(size, "sm")
            prefetch_started.set()
            await release.wait()

        worker = SimpleNamespace(
            mirror=SimpleNamespace(refresh=refresh, _status={}),
            prefetch=SimpleNamespace(prefetch_once=prefetch_once, _status={}),
        )
        with mock.patch.object(satellite_routes, "get_worker", return_value=worker):
            began = time.perf_counter()
            mirror_response = await satellite_routes.sync_mirror_refresh()
            prefetch_response = await satellite_routes.sync_prefetch()
            elapsed = time.perf_counter() - began
            await asyncio.wait_for(mirror_started.wait(), timeout=1)
            await asyncio.wait_for(prefetch_started.wait(), timeout=1)

        release.set()
        await asyncio.gather(*tuple(satellite_routes._manual_sync_tasks))

        self.assertEqual(mirror_response.status_code, 202)
        self.assertEqual(prefetch_response.status_code, 202)
        self.assertEqual(json.loads(mirror_response.body)["status"], "pending")
        self.assertEqual(json.loads(prefetch_response.body)["status"], "pending")
        self.assertLess(elapsed, 1.0)


if __name__ == "__main__":
    unittest.main()
