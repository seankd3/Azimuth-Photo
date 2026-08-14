import gc
import io
import inspect
import json
import os
import sqlite3
import sys
import tempfile
from types import SimpleNamespace
import time
import asyncio
import unittest

import numpy as np
from fastapi import BackgroundTasks
from fastapi.responses import FileResponse
from starlette.requests import Request
from starlette.responses import Response

sys.path.insert(0, os.path.dirname(__file__))
import app as app_module  # noqa: E402
import ai_models  # noqa: E402
import db
from data import connection as data_connection  # noqa: E402
import elo_propagation  # noqa: E402
import scanner  # noqa: E402
import settings  # noqa: E402
import thumbnails  # noqa: E402
from core import app_factory, bulk_scheduler  # noqa: E402
from core import background as background_runtime  # noqa: E402
from core import on_the_loop  # noqa: E402
from core import cache_events  # noqa: E402
from core import memory_pressure  # noqa: E402
from core import query_constraints  # noqa: E402
from core import work_coordination  # noqa: E402
from core.static_assets import StaticAssetContext  # noqa: E402
from data.repositories import filter_options as filter_options_repository  # noqa: E402
from data.repositories import cache_entries as cache_entry_repository  # noqa: E402
from data.repositories import catalog as catalog_repository  # noqa: E402
from data.repositories import images as image_repository  # noqa: E402
from data.repositories import metadata_search, rankings, ratings, stats as stats_repository  # noqa: E402
from features.ai import routes as ai_routes  # noqa: E402
from features.cache import routes as cache_routes  # noqa: E402
from features.cache import status as cache_status_service  # noqa: E402
from features.catalog import routes as catalog_routes  # noqa: E402
from features.compare import routes as compare_routes  # noqa: E402
from features.compare import service as compare_service  # noqa: E402
from features.library import routes as library_routes  # noqa: E402
from features.library import service as library_service  # noqa: E402
from features.media import routes as media_routes  # noqa: E402
from features.media import warm as media_warm  # noqa: E402
from features.people import routes as people_routes  # noqa: E402
from features.search import routes as search_routes  # noqa: E402
from features.search import service as search_service  # noqa: E402
from features.settings import routes as settings_routes  # noqa: E402
from features.settings import status as settings_status  # noqa: E402
from thumbnails import cache_entries as thumbnail_cache_entries  # noqa: E402


class JsonRequest:
    def __init__(self, payload):
        self.payload = payload

    async def json(self):
        return self.payload


class BadJsonRequest:
    async def json(self):
        raise ValueError("bad json")


class HeaderRequest:
    def __init__(self, headers=None):
        self.headers = headers or {}


class TempCatalogTestCase(unittest.IsolatedAsyncioTestCase):
    """A test that owns real SQLite files on disk.

    Windows will not delete a file any handle still has open, and the code under
    test opens connections of its own — an inline reader, a pooled one — that
    outlive the call. Tests that unlink their temporary catalog directly fail
    on that, randomly, in a different test each run, which teaches everyone
    reading the suite that red means nothing. Ask the process to let the
    database go first, then delete, and give a closing worker thread a moment
    if it has not finished.
    """

    def _setupAsyncioRunner(self):
        # Same reason as BackendTestCase: debug mode makes every future capture
        # a stack trace, and this suite does not rely on the warnings.
        self._asyncioRunner = asyncio.Runner(debug=False)

    def temp_catalog(self, ddl: str = "") -> str:
        """A throwaway catalog file that is really gone when the test ends."""

        handle, path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        if ddl:
            conn = sqlite3.connect(path)
            try:
                conn.executescript(ddl)
                conn.commit()
            finally:
                conn.close()
        self.addAsyncCleanup(self._let_the_catalog_go, path)
        return path

    async def _let_the_catalog_go(self, path: str) -> None:
        await data_connection.release_database(path)
        for attempt in range(20):
            try:
                for suffix in ("-wal", "-shm", ""):
                    target = path + suffix
                    if os.path.exists(target):
                        os.unlink(target)
                return
            except PermissionError:
                if attempt == 19:
                    raise
                await asyncio.sleep(0.05)


class BackendTestCase(unittest.IsolatedAsyncioTestCase):
    def _setupAsyncioRunner(self):
        # IsolatedAsyncioTestCase runs the loop in debug mode, which makes every
        # future capture a stack trace. Measured on this suite: a test that does
        # nothing at all cost 2.09s, almost all of it linecache.checkcache
        # stat-ing source files for those traces. Debug mode catches unawaited
        # coroutines and slow callbacks, neither of which this suite relies on,
        # and a suite too slow to run is worth less than the warnings.
        self._asyncioRunner = asyncio.Runner(debug=False)

    async def asyncSetUp(self):
        # The app records its loop at startup; a test is the app here, and work
        # that starts on a thread must come back to this loop, not build one.
        on_the_loop.remember_the_loop()
        self.tempdir = tempfile.TemporaryDirectory()
        # Every aiosqlite connection keeps a worker thread holding the DB file
        # open; on Windows one leaked handle blocks tempdir cleanup forever.
        # Track opens so teardown can close what a test (or a background code
        # path) forgot.
        self._tracked_conns = set()
        self._orig_open_async = data_connection.open_async

        async def _tracked_open(db_path, **kwargs):
            conn = await self._orig_open_async(db_path, **kwargs)
            self._tracked_conns.add(conn)
            return conn

        data_connection.open_async = _tracked_open
        self.old_db_path = db.DB_PATH
        self.old_schedule_propagation = compare_routes._schedule_propagation
        self.old_get_matrix = elo_propagation.embed_cache.get_matrix
        self.old_get_index = elo_propagation.embed_cache.get_index
        self.old_get_vector = elo_propagation.embed_cache.get_vector
        self.old_prefetch_images = thumbnails.prefetch_images
        self.old_schedule_full_image_cache = thumbnails.schedule_full_image_cache
        self.old_has_cached_fast = thumbnails.has_cached_fast
        self.old_fast_disk_path_entry = thumbnails.fast_disk_path_entry
        self.old_fast_disk_read_entry = thumbnails.fast_disk_read_entry
        self.old_thumbnail_persistent_conn = thumbnail_cache_entries._persistent_conn
        self.old_settings_path = settings.SETTINGS_PATH
        self.old_settings_state = settings._settings

        db.DB_PATH = os.path.join(self.tempdir.name, "azimuth-test.db")
        thumbnail_cache_entries._persistent_conn = None
        settings.SETTINGS_PATH = os.path.join(self.tempdir.name, "settings.local.json")
        settings._settings = None
        bulk_scheduler.reset_for_tests(
            desired_path=os.path.join(self.tempdir.name, "bulk_desired.json")
        )
        self._reset_shared_runtime_state()
        cache_events.invalidate_stats_cache()
        cache_events.invalidate_cached_image_ids_cache()
        cache_events.invalidate_past_matchups_cache()
        db.clear_filter_options_cache()
        await db.init_db()
        compare_service._pairing_cache.update({"data": None, "valid": False})
        compare_service._matchups_cache.update({"data": None, "valid": False})
        compare_service._visible_matchups_cache.clear()
        compare_service._visible_pairing_candidates_cache.clear()
        compare_service._interaction_response_cache.clear()
        cache_events.invalidate_rankings_cache()
        catalog_routes.clear_folders_cache()
        cache_status_service.invalidate_cache_status_cache()
        settings_status.invalidate_settings_response_cache()

        def close_scheduled(coro):
            coro.close()

        async def noop_prefetch(*_args, **_kwargs):
            return 0

        compare_routes._schedule_propagation = close_scheduled
        thumbnails.prefetch_images = noop_prefetch

    async def asyncTearDown(self):
        await self._close_thumbnail_cache_after_background_tasks()
        compare_routes._schedule_propagation = self.old_schedule_propagation
        elo_propagation.embed_cache.get_matrix = self.old_get_matrix
        elo_propagation.embed_cache.get_index = self.old_get_index
        elo_propagation.embed_cache.get_vector = self.old_get_vector
        thumbnails.prefetch_images = self.old_prefetch_images
        thumbnails.schedule_full_image_cache = self.old_schedule_full_image_cache
        thumbnails.has_cached_fast = self.old_has_cached_fast
        thumbnails.fast_disk_path_entry = self.old_fast_disk_path_entry
        thumbnails.fast_disk_read_entry = self.old_fast_disk_read_entry
        self._reset_shared_runtime_state()
        thumbnail_cache_entries._persistent_conn = self.old_thumbnail_persistent_conn
        settings.SETTINGS_PATH = self.old_settings_path
        settings._settings = self.old_settings_state
        bulk_scheduler.reset_for_tests()
        db.DB_PATH = self.old_db_path
        cache_events.invalidate_stats_cache()
        cache_events.invalidate_cached_image_ids_cache()
        cache_events.invalidate_past_matchups_cache()
        db.clear_filter_options_cache()
        compare_service._visible_matchups_cache.clear()
        compare_service._visible_pairing_candidates_cache.clear()
        compare_service._interaction_response_cache.clear()
        cache_events.invalidate_rankings_cache()
        catalog_routes.clear_folders_cache()
        cache_status_service.invalidate_cache_status_cache()
        settings_status.invalidate_settings_response_cache()
        await self._cleanup_tempdir()

    async def _close_thumbnail_cache_after_background_tasks(self):
        """Keep the per-test thumbnail database alive until async work drains."""
        await self._drain_test_tasks(
            tuple(media_warm._background_tasks),
            "media-warm background",
        )

        # A media-warm wrapper can finish after handing thumbnail probes to
        # asyncio/to_thread or an executor. Those child tasks are not retained
        # in media_warm._background_tasks, but still share this test's database.
        current = asyncio.current_task()
        handed_off_tasks = tuple(
            task
            for task in asyncio.all_tasks()
            if task is not current and not task.done()
        )
        await self._drain_test_tasks(handed_off_tasks, "handed-off background")

        if thumbnail_cache_entries._persistent_conn is not None:
            thumbnail_cache_entries._persistent_conn.close()

    async def _drain_test_tasks(self, tasks, label):
        if tasks:
            _, pending = await asyncio.wait(tasks, timeout=8)
            if pending:
                raise AssertionError(
                    f"{len(pending)} {label} task(s) did not finish "
                    "before thumbnail database teardown"
                )
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _cleanup_tempdir(self):
        """Let this process go of every database, then remove the per-test root.

        Windows will not delete a file another handle still has open, and this
        suite used to lose 2-6 tests a run to exactly that — a different test
        each time, which is the most expensive kind of red because it teaches
        everyone to ignore red.

        The cause was in `open_async`, not here: a connection is a live worker
        thread the moment aiosqlite returns it, and four awaits stood between
        that moment and the caller receiving it. A request abandoned in that
        window left a connection nobody held and nobody could close. The retry
        loop that used to stand here was waiting for a garbage collection that
        was never going to help.
        """

        data_connection.open_async = self._orig_open_async
        for conn in list(self._tracked_conns):
            try:
                await conn.close()
            except Exception:
                pass
        self._tracked_conns.clear()
        await data_connection.close_shared_readers()
        thumbnail_cache_entries.close_persistent_conn()
        self.tempdir.cleanup()

    def _reset_shared_runtime_state(self):
        # Any TestClient(app) startup arms memory_pressure's 120s startup-calm,
        # which pauses all bulk work (pregen, captions, decode batches) for
        # every later test in the process; clear it on both setup and teardown.
        memory_pressure.reset_for_tests()
        thumbnails._clear_memory_cache()
        # Each test gets a fresh DB; a disk-path index built against an earlier
        # test's DB would hide this test's cache rows from fast_disk_has.
        from thumbnails import cache_entries as _tce
        _tce._clear_disk_index()
        thumbnails._thumbnail_retry_after.clear()
        thumbnails._inflight.clear()
        media_warm._thumbnail_prefetch_inflight.clear()
        media_warm._thumbnail_memory_warm_inflight.clear()
        compare_service._visible_pairing_candidates_refreshing.clear()
        library_service._rankings_response_cache.clear()
        query_constraints._text_search_resolution_cache.clear()

    async def _source(self, name="catalog", *, online=True):
        path = os.path.join(self.tempdir.name, name)
        os.makedirs(path, exist_ok=True)
        source = await db.add_or_restore_source(path)
        if not online:
            conn = await db.get_db()
            try:
                await conn.execute("UPDATE catalog_sources SET online = 0 WHERE id = ?", (source["id"],))
                await conn.commit()
            finally:
                await conn.close()
            cache_events.invalidate_stats_cache()
        return source

    async def _image(
        self,
        source_id,
        filename,
        *,
        elo=1200.0,
        comparisons=0,
        propagated_updates=0,
        missing_at=None,
    ):
        conn = await db.get_db()
        try:
            source = await (
                await conn.execute("SELECT path FROM catalog_sources WHERE id = ?", (source_id,))
            ).fetchone()
            # Callers pass '/'-relative names; real writers always join natively,
            # so the fixture must too or Windows rows get mixed separators.
            filepath = os.path.join(source["path"], *str(filename).split("/"))
            cursor = await conn.execute(
                "INSERT INTO images "
                "(source_id, filename, filepath, elo, comparisons, propagated_updates, status, missing_at) "
                "VALUES (?, ?, ?, ?, ?, ?, 'kept', ?)",
                (source_id, filename, filepath, elo, comparisons, propagated_updates, missing_at),
            )
            await catalog_repository.update_source_counts_on_conn(conn, source_id)
            await conn.commit()
            image_id = cursor.lastrowid
        finally:
            await conn.close()
        cache_events.invalidate_stats_cache()
        return image_id

    async def _image_row(self, image_id):
        conn = await db.get_db()
        try:
            cursor = await conn.execute("SELECT * FROM images WHERE id = ?", (image_id,))
            return dict(await cursor.fetchone())
        finally:
            await conn.close()

    async def _cache_entry(self, image_id, size="sm"):
        conn = await db.get_db()
        try:
            now = 12345.0 + int(image_id)
            await conn.execute(
                "INSERT OR REPLACE INTO cache_entries "
                "(cache_root, size, image_id, path, source_signature, size_bytes, last_accessed, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    thumbnails.SSD_CACHE_DIR,
                    size,
                    image_id,
                    os.path.join(self.tempdir.name, f"{size}-{image_id}.jpg"),
                    f"sig-{size}-{image_id}",
                    123,
                    now,
                    now,
                ),
            )
            await conn.commit()
        finally:
            await conn.close()
        # Honor the row=>file invariant the app enforces: a cache_entries row
        # must point at a real file (admit-time gates stat it).
        stub = os.path.join(self.tempdir.name, f"{size}-{image_id}.jpg")
        if not os.path.exists(stub):
            with open(stub, "wb") as fh:
                fh.write(b"stub")

    async def _stub_text_search(self, image_ids, scores, query="landscapes"):
        # The query encoder died with the derivation fleet (2026-08-14);
        # semantic search serves cached query vectors. The stub caches a unit
        # vector for the query at the model's real dimension and pins a
        # matrix whose first column carries each photo's score — the same
        # similarities the encoder used to produce.
        config = settings.active_embedding_config()
        dim = int(config["dimension"])
        matrix = np.zeros((len(scores), dim), dtype=np.float32)
        matrix[:, 0] = scores

        async def fake_get_matrix(_model_key=None):
            return image_ids, matrix

        elo_propagation.embed_cache.get_matrix = fake_get_matrix
        from data.repositories import embeddings as embedding_repository

        vec = np.zeros(dim, dtype=np.float32)
        vec[0] = 1.0
        await embedding_repository.store_search_query_embedding(
            db.DB_PATH, config=config, query=query, blob=vec.tobytes(),
        )
