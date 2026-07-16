"""HTTP behavior gates for settings and batch cull mutations."""

import asyncio

from fastapi.testclient import TestClient

import db
import settings
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
