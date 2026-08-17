from core.catalog_path import catalog_path, use as catalog_path_use
import unittest.mock
from test_support import *  # noqa: F401,F403
from data import schema as data_schema


class CleanInstallSchemaTests(unittest.IsolatedAsyncioTestCase):
    async def test_init_db_creates_clean_catalog_when_wal_is_enabled(self):
        original_path = catalog_path()
        with tempfile.TemporaryDirectory(dir=os.path.dirname(__file__)) as tempdir:
            clean_path = os.path.join(tempdir, "clean-catalog.db")
            catalog_path_use(clean_path)
            cache_events.invalidate_stats_cache()
            try:
                await db.init_db()
                conn = sqlite3.connect(clean_path)
                try:
                    version = conn.execute("PRAGMA user_version").fetchone()[0]
                    journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
                    tables = {
                        row[0]
                        for row in conn.execute(
                            "SELECT name FROM sqlite_master WHERE type = 'table'"
                        )
                    }
                finally:
                    conn.close()
            finally:
                catalog_path_use(original_path)
                cache_events.invalidate_stats_cache()

        self.assertEqual(version, data_schema.SCHEMA_VERSION)
        self.assertEqual(journal_mode, "wal")
        self.assertIn("images", tables)
        self.assertIn("catalog_sources", tables)


class BackendIntegrationTests(BackendTestCase):
    async def test_init_db_migrates_legacy_comparison_action_id_before_indexes(self):
        original_path = catalog_path()
        legacy_path = os.path.join(self.tempdir.name, "legacy-comparisons.db")
        conn = sqlite3.connect(legacy_path)
        try:
            conn.execute(
                "CREATE TABLE comparisons ("
                "id INTEGER PRIMARY KEY, "
                "winner_id INTEGER, "
                "loser_id INTEGER, "
                "mode TEXT, "
                "elo_before_winner REAL, "
                "elo_before_loser REAL, "
                "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP"
                ")"
            )
            conn.commit()
        finally:
            conn.close()

        catalog_path_use(legacy_path)
        cache_events.invalidate_stats_cache()
        try:
            await db.init_db()
            conn = sqlite3.connect(legacy_path)
            try:
                columns = {row[1] for row in conn.execute("PRAGMA table_info(comparisons)")}
                indexes = {row[1] for row in conn.execute("PRAGMA index_list(comparisons)")}
            finally:
                conn.close()
        finally:
            catalog_path_use(original_path)
            cache_events.invalidate_stats_cache()

        self.assertIn("action_id", columns)
        self.assertIn("idx_comparisons_action_id", indexes)

    async def test_interaction_cache_warmup_starts_quickly_after_startup(self):
        self.assertLessEqual(app_factory.INTERACTION_CACHE_WARMUP_DELAY_SECONDS, 0.05)
        self.assertGreaterEqual(compare_service._visible_pairing_candidates_cache_ttl_seconds, 5.0)

    async def test_light_startup_warmup_does_not_cold_load_diverse_mosaic(self):
        startup_source = inspect.getsource(background_runtime.run_startup)
        light_warmup = startup_source.split(
            "async def _warm_light_startup_caches():",
            1,
        )[1].split("async def _warm_priority_interaction_caches", 1)[0]

        self.assertNotIn('strategy="diverse"', light_warmup)
        self.assertIn('strategy="explore"', light_warmup)

    async def test_shutdown_cancels_tracked_background_tasks(self):
        cancelled = asyncio.Event()

        async def waits_forever():
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        shell = app_module.app.state.azimuth_shell
        task = shell.track_background_task(waits_forever())
        self.assertIn(task, shell.background_tasks)
        await asyncio.sleep(0)

        await shell.lifecycle.shutdown()

        self.assertTrue(cancelled.is_set())
        self.assertTrue(task.cancelled())
        self.assertEqual(shell.background_tasks, set())

    async def test_background_task_tracker_logs_unhandled_task_failure(self):
        tracker = background_runtime.BackgroundTaskTracker()

        async def fail_worker():
            raise RuntimeError("worker exploded")

        with unittest.mock.patch.object(background_runtime.log, "error") as error_log:
            task = tracker.track(fail_worker())
            await asyncio.gather(task, return_exceptions=True)
            await asyncio.sleep(0)

        self.assertEqual(tracker.tasks, set())
        error_log.assert_called_once()
        self.assertIn("worker=", error_log.call_args.args[0])

    async def test_startup_warmup_logs_individual_gather_failures(self):
        async def succeed():
            return "ok"

        async def fail():
            raise RuntimeError("warmup failed")

        with unittest.mock.patch.object(background_runtime.log, "error") as error_log:
            await background_runtime._gather_logged("test_warmup", succeed(), fail())

        error_log.assert_called_once()
        self.assertEqual(error_log.call_args.args[1], "test_warmup")

