"""Satellite round-trip coverage against an in-memory FIELD_SPEC hub stub."""

from __future__ import annotations

import json
import os

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from test_support import BackendTestCase
from features.library import keywords
from features.sync.sync_worker import SyncWorker


class SatelliteSyncTests(BackendTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.hub_files: dict[str, bytearray] = {}
        self.hub_metadata: list[dict] = []
        self.hub = TestClient(self._hub_app())

    async def asyncTearDown(self):
        self.hub.close()
        await super().asyncTearDown()

    def _hub_app(self) -> FastAPI:
        app = FastAPI()

        @app.post("/api/sync/manifest")
        async def manifest(request: Request):
            items = (await request.json()).get("items") or []
            known = []
            missing = []
            for item in items:
                content_hash = item["content_hash"]
                if content_hash in self.hub_files and self.hub_files[content_hash]:
                    known.append({"content_hash": content_hash, "image_id": len(known) + 1})
                else:
                    missing.append(content_hash)
            return {"missing": missing, "known": known}

        @app.get("/api/sync/upload/{content_hash}/status")
        async def upload_status(content_hash: str):
            return {"offset": len(self.hub_files.get(content_hash, b""))}

        @app.post("/api/sync/upload/{content_hash}")
        async def upload(content_hash: str, request: Request):
            offset = int(request.headers["X-Offset"])
            total = int(request.headers["X-Total-Bytes"])
            data = await request.body()
            target = self.hub_files.setdefault(content_hash, bytearray())
            assert len(target) == offset
            target.extend(data)
            assert len(target) <= total
            return {"image_id": len(self.hub_files)}

        @app.post("/api/sync/metadata")
        async def metadata(request: Request):
            items = (await request.json()).get("items") or []
            self.hub_metadata.extend(items)
            return {"applied": [{"content_hash": item["content_hash"]} for item in items], "skipped": []}

        return app

    async def test_round_trip_uploads_metadata_and_is_idempotent(self):
        source = await self._source("field")
        image_ids = []
        payloads = [b"first satellite original", b"second satellite original", b"third satellite original"]
        for index, payload in enumerate(payloads, start=1):
            image_id = await self._image(source["id"], f"field-{index}.raw")
            image_ids.append(image_id)
            with open(os.path.join(self.tempdir.name, f"{source['id']}-field-{index}.raw"), "wb") as file:
                file.write(payload)

        conn = await __import__("db").get_db()
        try:
            await conn.execute("UPDATE images SET flag = 'picked' WHERE id = ?", (image_ids[0],))
            await conn.execute(
                "INSERT INTO develop_settings(image_id, settings, origin, updated_at) VALUES (?, ?, 'user', ?)",
                (image_ids[0], json.dumps({"Exposure2012": 0.7}), "2026-07-11T12:00:00Z"),
            )
            await conn.commit()
        finally:
            await conn.close()
        keyword = await keywords.create_keyword("Field")
        await keywords.assign_keyword([image_ids[0]], keyword["id"])

        async def request(method, url, *, body=None, headers=None):
            response = self.hub.request(method, url.removeprefix("http://hub"), content=body, headers=headers)
            return response.status_code, dict(response.headers), response.content

        worker = SyncWorker(db_path=__import__("db").DB_PATH, hub="http://hub", request=request)
        await worker.sync_once()

        self.assertEqual({bytes(value) for value in self.hub_files.values()}, set(payloads))
        self.assertEqual(len(self.hub_metadata), 3)
        first = next(item for item in self.hub_metadata if item["flag"] == "picked")
        self.assertEqual(first["develop_settings"], {"Exposure2012": 0.7})
        self.assertEqual(first["keywords"], ["Field"])
        self.assertEqual(worker.status()["queue_depth"], 0)
        uploads = dict(self.hub_files)
        metadata_count = len(self.hub_metadata)

        await worker.sync_once()
        self.assertEqual(self.hub_files, uploads)
        self.assertEqual(len(self.hub_metadata), metadata_count)
