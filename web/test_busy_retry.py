"""Regression coverage for SQLite busy-retry on user-facing writes."""

from __future__ import annotations

import sqlite3
import unittest
from unittest import mock

import db
from data import connection
from data.repositories import images as image_repository
from features.develop import routes as develop_routes
from features.library import keywords
from test_support import BackendTestCase


class BusyRetryHelperTests(unittest.IsolatedAsyncioTestCase):
    async def test_run_with_busy_retry_succeeds_after_transient_locks(self):
        attempts = {"n": 0}

        async def flaky():
            attempts["n"] += 1
            if attempts["n"] <= 3:
                raise sqlite3.OperationalError("database is locked")
            return "ok"

        with mock.patch.object(connection.asyncio, "sleep", new=mock.AsyncMock()) as sleeper:
            result = await connection.run_with_busy_retry(flaky)

        self.assertEqual(result, "ok")
        self.assertEqual(attempts["n"], 4)
        self.assertEqual(sleeper.await_count, 3)
        sleeper.assert_awaited_with(connection.USER_WRITE_LOCK_BACKOFF_SECONDS)

    async def test_run_with_busy_retry_raises_after_budget(self):
        async def always_locked():
            raise sqlite3.OperationalError("database is locked")

        with mock.patch.object(connection.asyncio, "sleep", new=mock.AsyncMock()):
            with self.assertRaises(sqlite3.OperationalError):
                await connection.run_with_busy_retry(always_locked, retries=2)


class UserFacingBusyRetryTests(BackendTestCase):
    async def test_flag_write_retries_transient_lock(self):
        source = await self._source("busy-flag")
        image_id = await self._image(source["id"], "a.jpg")
        attempts = {"n": 0}
        real_open = connection.open_async

        async def flaky_open(db_path, **kwargs):
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise sqlite3.OperationalError("database is locked")
            return await real_open(db_path, **kwargs)

        with (
            mock.patch.object(connection, "open_async", side_effect=flaky_open),
            mock.patch.object(connection.asyncio, "sleep", new=mock.AsyncMock()),
        ):
            await image_repository.set_image_flag(db.DB_PATH, image_id, "picked")

        self.assertEqual(attempts["n"], 2)
        conn = await db.get_db()
        try:
            row = await (await conn.execute("SELECT flag FROM images WHERE id = ?", (image_id,))).fetchone()
            self.assertEqual(row["flag"], "picked")
        finally:
            await conn.close()

    async def test_keyword_assign_retries_transient_lock(self):
        source = await self._source("busy-kw")
        image_id = await self._image(source["id"], "b.jpg")
        created = await keywords.create_keyword("Busy")
        keyword_id = int(created["id"])
        attempts = {"n": 0}
        real_get_db = db.get_db

        async def flaky_get_db():
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise sqlite3.OperationalError("database is locked")
            return await real_get_db()

        with (
            mock.patch.object(db, "get_db", side_effect=flaky_get_db),
            mock.patch.object(connection.asyncio, "sleep", new=mock.AsyncMock()),
        ):
            assigned = await keywords.assign_keyword([image_id], keyword_id)

        self.assertGreaterEqual(assigned, 0)
        self.assertEqual(attempts["n"], 2)

    async def test_develop_settings_put_retries_transient_lock(self):
        source = await self._source("busy-dev")
        image_id = await self._image(source["id"], "c.dng")
        attempts = {"n": 0}
        real_open = connection.open_async

        async def flaky_open(db_path, **kwargs):
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise sqlite3.OperationalError("database is locked")
            return await real_open(db_path, **kwargs)

        with (
            mock.patch.object(connection, "open_async", side_effect=flaky_open),
            mock.patch.object(connection.asyncio, "sleep", new=mock.AsyncMock()),
        ):
            saved = await develop_routes._upsert_settings(image_id, {"Exposure2012": 0.5}, "Busy")

        self.assertEqual(saved["settings"]["Exposure2012"], 0.5)
        self.assertGreaterEqual(attempts["n"], 2)
