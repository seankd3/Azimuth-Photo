"""Proofs for fire-and-forget exception logging and off-loop catalog browse."""

from __future__ import annotations

import asyncio
import inspect
import os
import unittest
from unittest import mock

from core import background as background_runtime
from data.repositories import catalog as catalog_repository
from data.repositories import filter_options as filter_options_repository
from features.catalog import routes as catalog_routes


class BackgroundRefreshHygieneTests(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self):
        filter_options_repository.clear_filter_options_cache()

    async def test_filter_options_swr_refresh_logs_and_clears_inflight_flag(self):
        filter_options_repository._filter_options_cache["data"] = {
            "years": [],
            "file_types": [],
            "undated": 0,
            "cameras": [],
            "lenses": [],
            "people": [],
        }
        filter_options_repository._filter_options_cache["expires"] = 0
        filter_options_repository._filter_options_refreshing = False

        async def boom(*_args, **_kwargs):
            raise RuntimeError("refresh exploded")

        with mock.patch.object(
            filter_options_repository,
            "load_filter_options_uncached",
            side_effect=boom,
        ):
            with self.assertLogs(filter_options_repository.log, level="ERROR") as captured:
                result = await filter_options_repository.filter_options_cached(
                    "/tmp/unused.db",
                    get_catalog_image_counts=mock.AsyncMock(),
                    get_active_source_id_set=mock.AsyncMock(),
                )
                for _ in range(50):
                    if not filter_options_repository._filter_options_refreshing:
                        break
                    await asyncio.sleep(0)

        self.assertEqual(result["years"], [])
        self.assertFalse(filter_options_repository._filter_options_refreshing)
        self.assertTrue(
            any("filter options background refresh failed" in line for line in captured.output)
        )

    async def test_tracked_background_task_retrieves_exception(self):
        """Done-callback must retrieve the exception (no 'never retrieved' leak)."""
        tracker = background_runtime.BackgroundTaskTracker()
        seen = []

        async def fail_worker():
            raise RuntimeError("worker exploded")

        def spy_done(task: asyncio.Task) -> None:
            tracker.tasks.discard(task)
            if task.cancelled():
                return
            exc = task.exception()
            seen.append(exc)

        tracker._task_done = spy_done  # type: ignore[method-assign]
        task = tracker.track(fail_worker())
        await asyncio.gather(task, return_exceptions=True)
        await asyncio.sleep(0)

        self.assertEqual(len(seen), 1)
        self.assertIsInstance(seen[0], RuntimeError)
        self.assertEqual(str(seen[0]), "worker exploded")


class CatalogBrowseHygieneTests(unittest.TestCase):
    def test_api_catalog_browse_uses_to_thread_browse_dir(self):
        source = inspect.getsource(catalog_routes.api_catalog_browse)
        self.assertIn("asyncio.to_thread(_browse_dir", source)
        self.assertNotIn("os.scandir", source)
        self.assertNotIn("os.path.exists", source)

    def test_browse_dir_helper_is_sync_and_callable(self):
        self.assertFalse(inspect.iscoroutinefunction(catalog_routes._browse_dir))
        home = os.path.expanduser("~")
        result = catalog_routes._browse_dir(home)
        self.assertIn("roots", result)
        self.assertIn("entries", result)
        self.assertIn("exists", result)
        self.assertTrue(result["exists"])
        self.assertTrue(result["is_dir"])
        self.assertEqual(result["path"], catalog_repository.normalize_source_path(home))
