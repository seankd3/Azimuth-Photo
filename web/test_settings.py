"""HTTP behavior gates for settings and batch cull mutations."""

import asyncio
import json
from unittest.mock import patch

from fastapi.testclient import TestClient

import db
import settings
from data.repositories import images as image_repository
from test_support import BackendTestCase


class SettingsRouteTests(BackendTestCase):
    async def _request(self, method, path, **kwargs):
        def send():
            with TestClient(__import__("app").app) as client:
                return client.request(method, path, **kwargs)
        return await asyncio.to_thread(send)

    async def test_reset_restores_defaults_without_clobbering_develop_rows(self):
        source = await self._source()
        image_id = await self._image(source["id"], "edited.jpg")
        conn = await db.get_db()
        try:
            await conn.execute(
                "INSERT INTO develop_settings(image_id, settings, origin, updated_at) VALUES (?, ?, 'user', 'now')",
                (image_id, '{"Exposure2012":1.25}'),
            )
            await conn.commit()
        finally:
            await conn.close()
        settings.save_settings({**settings.get_settings(), "thumb_size_sm": 777})

        reset = await self._request("POST", "/api/settings/reset")
        self.assertEqual(reset.status_code, 200, reset.text)
        self.assertEqual(reset.json()["settings"]["thumb_size_sm"], settings.DEFAULT_SETTINGS["thumb_size_sm"])
        conn = await db.get_db()
        try:
            row = await (await conn.execute("SELECT settings FROM develop_settings WHERE image_id = ?", (image_id,))).fetchone()
        finally:
            await conn.close()
        self.assertEqual(row["settings"], '{"Exposure2012":1.25}')

    async def test_batch_flag_persists_every_requested_image(self):
        source = await self._source()
        image_ids = [await self._image(source["id"], f"flag-{index}.jpg") for index in range(3)]
        changed = await self._request("POST", "/api/images/flag", json={"image_ids": image_ids, "flag": "picked"})
        self.assertEqual(changed.status_code, 200, changed.text)
        self.assertEqual(changed.json()["count"], 3)
        self.assertEqual([(await self._image_row(image_id))["flag"] for image_id in image_ids], ["picked"] * 3)

    async def test_set_rating_writes_lr_rating_and_oplog_without_clobbering_settings(self):
        source = await self._source()
        image_id = await self._image(source["id"], "starred.jpg")
        conn = await db.get_db()
        try:
            await conn.execute(
                "INSERT INTO develop_settings(image_id, settings, origin, updated_at) VALUES (?, ?, 'user', 'now')",
                (image_id, '{"Exposure2012":0.7}'),
            )
            await conn.execute(
                "UPDATE images SET content_hash = 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa' WHERE id = ?", (image_id,)
            )
            await conn.commit()
        finally:
            await conn.close()

        rated = await self._request("POST", f"/api/image/{image_id}/rating", json={"rating": 4})
        self.assertEqual(rated.status_code, 200, rated.text)
        self.assertEqual(rated.json()["rating"], 4)
        bad = await self._request("POST", f"/api/image/{image_id}/rating", json={"rating": 9})
        self.assertEqual(bad.status_code, 400)

        import json as _json
        conn = await db.get_db()
        try:
            row = await (await conn.execute(
                "SELECT settings FROM develop_settings WHERE image_id = ?", (image_id,)
            )).fetchone()
            settings = _json.loads(row["settings"])
            ops = await (await conn.execute(
                "SELECT family, payload FROM oplog WHERE family = 'rating'"
            )).fetchall()
        finally:
            await conn.close()
        self.assertEqual(settings["_lr_rating"], 4)
        self.assertEqual(settings["Exposure2012"], 0.7)
        self.assertEqual(len(ops), 1)
        self.assertEqual(_json.loads(ops[0]["payload"])["value"], 4)

    async def test_get_rating_round_trips_the_posted_star(self):
        source = await self._source()
        image_id = await self._image(source["id"], "roundtrip.jpg")
        empty = await self._request("GET", f"/api/image/{image_id}/rating")
        self.assertEqual(empty.status_code, 200, empty.text)
        self.assertEqual(empty.json()["rating"], 0)
        posted = await self._request("POST", f"/api/image/{image_id}/rating", json={"rating": 3})
        self.assertEqual(posted.status_code, 200, posted.text)
        fetched = await self._request("GET", f"/api/image/{image_id}/rating")
        self.assertEqual(fetched.json()["rating"], 3)

    async def test_set_rating_does_not_advance_the_develop_clock(self):
        source = await self._source()
        edited_id = await self._image(source["id"], "rated-edit.jpg")
        unrated_id = await self._image(source["id"], "first-rating.jpg")
        conn = await db.get_db()
        try:
            await conn.execute(
                "INSERT INTO develop_settings(image_id, settings, origin, updated_at) "
                "VALUES (?, ?, 'user', 'develop-save')",
                (edited_id, '{"Exposure2012":0.7}'),
            )
            await conn.commit()
        finally:
            await conn.close()

        await image_repository.set_image_rating(db.DB_PATH, edited_id, 4)
        await image_repository.set_image_rating(db.DB_PATH, unrated_id, 5)

        conn = await db.get_db()
        try:
            rows = await (await conn.execute(
                "SELECT image_id, updated_at FROM develop_settings WHERE image_id IN (?, ?)",
                (edited_id, unrated_id),
            )).fetchall()
        finally:
            await conn.close()
        clocks = {row["image_id"]: row["updated_at"] for row in rows}
        self.assertEqual(clocks[edited_id], "develop-save")
        self.assertEqual(clocks[unrated_id], "")

    async def test_set_rating_preserves_interleaved_develop_save(self):
        source = await self._source()
        image_id = await self._image(source["id"], "interleaved-rating.jpg")
        conn = await db.get_db()
        try:
            await conn.execute(
                "INSERT INTO develop_settings(image_id, settings, origin, updated_at) "
                "VALUES (?, ?, 'user', 'before')",
                (image_id, '{"Exposure2012":0.7}'),
            )
            await conn.commit()
        finally:
            await conn.close()

        original_open = image_repository.connection.open_async
        rating_conn = await original_open(db.DB_PATH)
        interleaved = False

        class InterleavingConnection:
            async def execute(self, sql, parameters=None):
                nonlocal interleaved
                if not interleaved and sql.lstrip().startswith("UPDATE develop_settings"):
                    interleaved = True
                    develop_conn = await original_open(db.DB_PATH)
                    try:
                        await develop_conn.execute(
                            "UPDATE develop_settings SET settings = ?, updated_at = 'develop-save' "
                            "WHERE image_id = ?",
                            ('{"Exposure2012":1.4,"Contrast2012":22}', image_id),
                        )
                        await develop_conn.commit()
                    finally:
                        await image_repository.connection.close_async(
                            develop_conn,
                            db_path=db.DB_PATH,
                        )
                if parameters is None:
                    return await rating_conn.execute(sql)
                return await rating_conn.execute(sql, parameters)

            async def commit(self):
                await rating_conn.commit()

            async def close(self):
                await rating_conn.close()

        async def open_interleaving(_db_path, **_kwargs):
            return InterleavingConnection()

        with patch.object(image_repository.connection, "open_async", open_interleaving):
            await image_repository.set_image_rating(db.DB_PATH, image_id, 5)

        conn = await db.get_db()
        try:
            row = await (await conn.execute(
                "SELECT settings FROM develop_settings WHERE image_id = ?",
                (image_id,),
            )).fetchone()
        finally:
            await conn.close()
        self.assertTrue(interleaved)
        self.assertEqual(json.loads(row["settings"]), {
            "Exposure2012": 1.4,
            "Contrast2012": 22,
            "_lr_rating": 5,
        })

    async def test_set_rating_recovers_non_object_develop_settings(self):
        source = await self._source()
        image_id = await self._image(source["id"], "non-object-settings.jpg")
        conn = await db.get_db()
        try:
            await conn.execute(
                "INSERT INTO develop_settings(image_id, settings, origin, updated_at) "
                "VALUES (?, '[]', 'user', 'before')",
                (image_id,),
            )
            await conn.commit()
        finally:
            await conn.close()

        await image_repository.set_image_rating(db.DB_PATH, image_id, 3)

        conn = await db.get_db()
        try:
            row = await (await conn.execute(
                "SELECT settings FROM develop_settings WHERE image_id = ?",
                (image_id,),
            )).fetchone()
        finally:
            await conn.close()
        self.assertEqual(json.loads(row["settings"]), {"_lr_rating": 3})

    async def test_set_rating_upserts_when_a_first_develop_row_arrives_during_the_write(self):
        source = await self._source()
        image_id = await self._image(source["id"], "racing-rating.jpg")
        original_open = image_repository.connection.open_async
        rating_conn = await original_open(db.DB_PATH)
        raced = False

        class EmptyCursor:
            rowcount = 0

        class RacingConnection:
            async def execute(self, sql, parameters=None):
                nonlocal raced
                if not raced and sql.lstrip().startswith("UPDATE develop_settings"):
                    raced = True
                    concurrent_conn = await original_open(db.DB_PATH)
                    try:
                        await concurrent_conn.execute(
                            "INSERT INTO develop_settings(image_id, settings, origin, updated_at) VALUES (?, ?, 'user', 'concurrent')",
                            (image_id, '{"Exposure2012":1.4}'),
                        )
                        await concurrent_conn.commit()
                    finally:
                        await image_repository.connection.close_async(concurrent_conn, db_path=db.DB_PATH)
                    return EmptyCursor()
                if parameters is None:
                    return await rating_conn.execute(sql)
                return await rating_conn.execute(sql, parameters)

            async def commit(self):
                await rating_conn.commit()

            async def close(self):
                await rating_conn.close()

        async def open_racing(_db_path, **_kwargs):
            return RacingConnection()

        with patch.object(image_repository.connection, "open_async", open_racing):
            await image_repository.set_image_rating(db.DB_PATH, image_id, 5)

        conn = await db.get_db()
        try:
            row = await (await conn.execute(
                "SELECT settings FROM develop_settings WHERE image_id = ?", (image_id,)
            )).fetchone()
        finally:
            await conn.close()
        self.assertTrue(raced)
        self.assertEqual(json.loads(row["settings"]), {"Exposure2012": 1.4, "_lr_rating": 5})
