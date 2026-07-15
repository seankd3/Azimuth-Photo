"""FIELD_SPEC_V2 catalog-mirror acceptance coverage against a stub hub app."""

from __future__ import annotations

import gzip
import io
import json
import tarfile

from fastapi import FastAPI, Response
from fastapi.testclient import TestClient

import db
from data import connection
from features.sync.mirror import MirrorPuller
from features.sync.prefetch import ThumbPrefetcher
from test_support import BackendTestCase


class MirrorAcceptanceTests(BackendTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.rows = [
            {
                "hub_image_id": index,
                "content_hash": f"{index:032x}",
                "filename": f"hub-{index}.jpg",
                "filepath": f"/hub/2026/hub-{index}.jpg",
                "file_ext": ".jpg",
                "file_size": 1000 + index,
                "date_taken": f"2026-07-{(index % 28) + 1:02d}",
                "width": 2400,
                "height": 1600,
                "orientation": "landscape",
                "flag": "picked" if index == 2 else "unflagged",
                "elo": 1200 + index,
                "comparisons": index,
                "status": "kept",
                "keywords": ["Trips > Field"] if index == 1 else [],
            }
            for index in range(1, 201)
        ]
        self.client = TestClient(self._hub_app())

    async def asyncTearDown(self):
        self.client.close()
        await super().asyncTearDown()

    def _hub_app(self) -> FastAPI:
        app = FastAPI()

        @app.get("/api/sync/catalog/export")
        async def export(cursor: int = 0):
            payload = self.rows if cursor < 7 else []
            ndjson = "\n".join([*(json.dumps(row) for row in payload), json.dumps({"cursor": 7})]) + "\n"
            return Response(gzip.compress(ndjson.encode()), media_type="application/gzip")

        @app.get("/api/sync/thumbs/pack")
        async def pack(size: str, after_id: int = 0, limit: int = 500):
            stream = io.BytesIO()
            with tarfile.open(fileobj=stream, mode="w") as archive:
                for hub_id in range(max(1, after_id + 1), min(31, after_id + limit + 1)):
                    data = f"{size}-thumb-{hub_id}".encode()
                    info = tarfile.TarInfo(f"{hub_id}.jpg")
                    info.size = len(data)
                    archive.addfile(info, io.BytesIO(data))
                trailer = json.dumps({"after_id": 30, "skipped": []}).encode()
                info = tarfile.TarInfo("trailer.json")
                info.size = len(trailer)
                archive.addfile(info, io.BytesIO(trailer))
            return Response(stream.getvalue(), media_type="application/x-tar")

        @app.get("/api/thumb/{size}/{hub_image_id}")
        async def thumb(size: str, hub_image_id: int):
            return Response(f"remote-{size}-{hub_image_id}".encode(), media_type="image/jpeg")

        return app

    async def _request(self, method, url, *, body=None, headers=None):
        response = self.client.request(method, url.removeprefix("http://hub"), content=body, headers=headers)
        return response.status_code, dict(response.headers), response.content

    async def test_v2_mirror_acceptance_scenario(self):
        source = await self._source("field-import")
        field_image = await self._image(source["id"], "already-local.jpg")
        conn = await db.get_db()
        try:
            await conn.execute("UPDATE images SET content_hash = ? WHERE id = ?", (self.rows[0]["content_hash"], field_image))
            await conn.commit()
        finally:
            await conn.close()

        mirror = MirrorPuller(db_path=db.DB_PATH, hub="http://hub", request=self._request)
        first = await mirror.refresh()
        self.assertEqual(first["rows_applied"], 200)
        conn = await db.get_db()
        try:
            rows = await (await conn.execute("SELECT id, hub_image_id, hub_remote, missing_at FROM images ORDER BY hub_image_id")).fetchall()
            self.assertEqual(len(rows), 200)
            self.assertEqual(int(rows[0]["id"]), field_image)
            self.assertEqual(int(rows[0]["hub_image_id"]), 1)
            self.assertEqual(int(rows[0]["hub_remote"]), 0)
            self.assertTrue(all(row["missing_at"] is None for row in rows))
        finally:
            await conn.close()

        cached: dict[tuple[str, int], bytes] = {}
        prefetch = ThumbPrefetcher(
            db_path=db.DB_PATH,
            hub="http://hub",
            request=self._request,
            store=lambda size, image_id, _signature, data: cached.__setitem__((size, image_id), data),
            cache_root=self.tempdir.name,
            budget_bytes=8 * 1024 ** 3,
        )
        packed = await prefetch.prefetch_once(size="sm")
        self.assertEqual(packed["cached"], 30)
        self.assertEqual(len(cached), 30)

        remote_image = next(row["id"] for row in rows if row["hub_image_id"] == 2)
        # This is the same single-item hub proxy path used by the media read-through seam.
        self.assertEqual(await prefetch.fetch_single("md", int(remote_image)), b"remote-md-2")
        second = await mirror.refresh()
        self.assertEqual(second["rows_applied"], 0)

    async def test_mirror_follows_hub_filepath_moves(self):
        """Hub paths can move (mount migrations, re-filed folders); the mirror must follow."""
        mirror = MirrorPuller(db_path=db.DB_PATH, hub="http://hub", request=self._request)
        first = await mirror.refresh()
        self.assertEqual(first["rows_applied"], 200)

        moved = "/hub-moved/2026/hub-1.jpg"
        self.rows[0]["filepath"] = moved
        conn = await db.get_db()
        try:
            await conn.execute("DELETE FROM sync_mirror_state WHERE key = 'cursor'")
            await conn.commit()
        finally:
            await conn.close()

        await mirror.refresh()
        conn = await db.get_db()
        try:
            row = await (await conn.execute(
                "SELECT filepath, hub_remote FROM images WHERE hub_image_id = 1"
            )).fetchone()
            self.assertEqual(int(row["hub_remote"]), 1)
            self.assertEqual(row["filepath"], moved)
        finally:
            await conn.close()

    async def test_mirror_reports_skipped_unhashed_rows(self):
        self.rows = [
            {
                "hub_image_id": 1,
                "content_hash": "a" * 32,
                "filename": "hashed.jpg",
                "filepath": "/hub/hashed.jpg",
                "file_ext": ".jpg",
                "status": "kept",
            },
            {
                "hub_image_id": 2,
                "content_hash": "",
                "filename": "missing-hash.jpg",
                "filepath": "/hub/missing-hash.jpg",
                "file_ext": ".jpg",
                "status": "kept",
            },
            {
                "hub_image_id": 3,
                "content_hash": None,
                "filename": "null-hash.jpg",
                "filepath": "/hub/null-hash.jpg",
                "file_ext": ".jpg",
                "status": "kept",
            },
        ]
        mirror = MirrorPuller(db_path=db.DB_PATH, hub="http://hub", request=self._request)
        result = await mirror.refresh()
        self.assertEqual(result["rows_applied"], 1)
        self.assertEqual(result["skipped_unhashed"], 2)
        self.assertEqual(mirror.status()["skipped_unhashed"], 2)
        conn = await db.get_db()
        try:
            count = await (await conn.execute("SELECT COUNT(*) AS c FROM images")).fetchone()
            self.assertEqual(int(count["c"]), 1)
        finally:
            await conn.close()
