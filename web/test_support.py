import asyncio
import io
import inspect
import json
import os
import sqlite3
import sys
import tempfile
from types import SimpleNamespace
import time
import unittest

import numpy as np
from fastapi import BackgroundTasks
from fastapi.responses import FileResponse
from starlette.requests import Request
from starlette.responses import Response

sys.path.insert(0, os.path.dirname(__file__))
import app as app_module  # noqa: E402
import ai_models  # noqa: E402
import db  # noqa: E402
import embedding_worker  # noqa: E402
import elo_propagation  # noqa: E402
import face_worker  # noqa: E402
import scanner  # noqa: E402
import settings  # noqa: E402
import thumbnails  # noqa: E402
from core import app_factory  # noqa: E402
from core import background as background_runtime  # noqa: E402
from core import cache_events  # noqa: E402
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


class BackendTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.old_db_path = db.DB_PATH
        self.old_schedule_pairing_propagation = compare_routes._schedule_pairing_propagation
        self.old_get_matrix = elo_propagation.embed_cache.get_matrix
        self.old_get_index = elo_propagation.embed_cache.get_index
        self.old_get_vector = elo_propagation.embed_cache.get_vector
        self.old_encode_text = embedding_worker.encode_text
        self.old_ensure_model_loaded_for_search = embedding_worker.ensure_model_loaded_for_search
        self.old_start_search_model_load = embedding_worker.start_search_model_load
        self.old_embedding_manual_pause = embedding_worker.get_worker_status()["manual_pause"]
        self.old_prefetch_images = thumbnails.prefetch_images
        self.old_schedule_full_image_cache = thumbnails.schedule_full_image_cache
        self.old_has_cached_fast = thumbnails.has_cached_fast
        self.old_fast_disk_path_entry = thumbnails.fast_disk_path_entry
        self.old_fast_disk_read_entry = thumbnails.fast_disk_read_entry
        self.old_thumbnail_persistent_conn = thumbnail_cache_entries._persistent_conn
        self.old_settings_path = settings.SETTINGS_PATH
        self.old_settings_state = settings._settings

        db.DB_PATH = os.path.join(self.tempdir.name, "photoarchive-test.db")
        thumbnail_cache_entries._persistent_conn = None
        settings.SETTINGS_PATH = os.path.join(self.tempdir.name, "settings.local.json")
        settings._settings = None
        self._reset_shared_runtime_state()
        db.invalidate_stats_cache()
        db.invalidate_cached_image_ids_cache()
        db._invalidate_past_matchups_cache()
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

        async def no_model_load_for_search():
            return False

        compare_routes._schedule_pairing_propagation = close_scheduled
        thumbnails.prefetch_images = noop_prefetch
        embedding_worker.ensure_model_loaded_for_search = no_model_load_for_search
        embedding_worker.start_search_model_load = lambda: False

    async def asyncTearDown(self):
        compare_routes._schedule_pairing_propagation = self.old_schedule_pairing_propagation
        elo_propagation.embed_cache.get_matrix = self.old_get_matrix
        elo_propagation.embed_cache.get_index = self.old_get_index
        elo_propagation.embed_cache.get_vector = self.old_get_vector
        embedding_worker.encode_text = self.old_encode_text
        embedding_worker.ensure_model_loaded_for_search = self.old_ensure_model_loaded_for_search
        embedding_worker.start_search_model_load = self.old_start_search_model_load
        if self.old_embedding_manual_pause:
            embedding_worker.pause_embedding_worker()
        else:
            embedding_worker.resume_embedding_worker()
        thumbnails.prefetch_images = self.old_prefetch_images
        thumbnails.schedule_full_image_cache = self.old_schedule_full_image_cache
        thumbnails.has_cached_fast = self.old_has_cached_fast
        thumbnails.fast_disk_path_entry = self.old_fast_disk_path_entry
        thumbnails.fast_disk_read_entry = self.old_fast_disk_read_entry
        self._reset_shared_runtime_state()
        if thumbnail_cache_entries._persistent_conn is not None:
            thumbnail_cache_entries._persistent_conn.close()
        thumbnail_cache_entries._persistent_conn = self.old_thumbnail_persistent_conn
        settings.SETTINGS_PATH = self.old_settings_path
        settings._settings = self.old_settings_state
        db.DB_PATH = self.old_db_path
        db.invalidate_stats_cache()
        db.invalidate_cached_image_ids_cache()
        db._invalidate_past_matchups_cache()
        db.clear_filter_options_cache()
        compare_service._visible_matchups_cache.clear()
        compare_service._visible_pairing_candidates_cache.clear()
        compare_service._interaction_response_cache.clear()
        cache_events.invalidate_rankings_cache()
        catalog_routes.clear_folders_cache()
        cache_status_service.invalidate_cache_status_cache()
        settings_status.invalidate_settings_response_cache()
        await self._cleanup_tempdir()

    async def _cleanup_tempdir(self):
        """Windows holds file locks while background tasks finish; retry briefly.

        A bounded retry absorbs the teardown race (post-response prefetch or a
        worker still closing its connection) without masking real leaks -- a
        connection that never closes still fails after the retries.
        """
        import asyncio as _asyncio
        import gc as _gc

        for attempt in range(20):
            try:
                self.tempdir.cleanup()
                return
            except PermissionError:
                if attempt == 19:
                    raise
                # Dropped-but-uncollected sqlite3/aiosqlite handles keep the
                # file locked on Windows; a collect closes what tests forgot.
                _gc.collect()
                await _asyncio.sleep(0.4)

    def _reset_shared_runtime_state(self):
        thumbnails._clear_memory_cache()
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
            db.invalidate_stats_cache()
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
            await db._update_source_counts(conn, source_id)
            await conn.commit()
            image_id = cursor.lastrowid
        finally:
            await conn.close()
        db.invalidate_stats_cache()
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

    def _stub_text_search(self, image_ids, scores):
        matrix = np.array([[score, 0.0] for score in scores], dtype=np.float32)

        async def fake_get_matrix(_model_key=None):
            return image_ids, matrix

        def fake_encode_text(_query):
            return np.array([1.0, 0.0], dtype=np.float32)

        elo_propagation.embed_cache.get_matrix = fake_get_matrix
        embedding_worker.encode_text = fake_encode_text
