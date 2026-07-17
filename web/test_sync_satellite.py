"""Satellite round-trip coverage against an in-memory FIELD_SPEC hub stub."""

from __future__ import annotations

import json
import os

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from test_support import BackendTestCase
from features.library import keywords
from features.sync.sync_worker import SyncWorker
from features.sync import satellite_routes
import app as app_module


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
            with open(os.path.join(source["path"], f"field-{index}.raw"), "wb") as file:
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

    async def test_failed_cycle_reports_recovering_and_keeps_owed_count(self):
        # 2026-07-16 Holland incident: a mid-sync timeout must never read as
        # "done" — status has to say recovering and keep the owed numbers.
        from features.sync import satellite

        source = await self._source("field")
        for index in range(2):
            await self._image(source["id"], f"owe-{index}.raw")
            with open(os.path.join(source["path"], f"owe-{index}.raw"), "wb") as file:
                file.write(f"owed original {index}".encode())
        await satellite.record_local_images(__import__("db").DB_PATH)

        async def request(method, url, *, body=None, headers=None):
            raise TimeoutError("timed out")

        worker = SyncWorker(db_path=__import__("db").DB_PATH, hub="http://hub", request=request)
        with self.assertRaises(Exception):
            await worker.sync_once()
        worker._error(TimeoutError("timed out"))
        worker._note_failure(TimeoutError("timed out"))
        await worker._reconcile_status_after_failure()
        status = worker.status()
        self.assertEqual(status["state"], "recovering")
        self.assertEqual(status["queue_depth"], 2)

    async def test_upload_progress_counts_down_live_not_only_at_cycle_end(self):
        source = await self._source("field")
        for index in range(2):
            await self._image(source["id"], f"live-{index}.raw")
            with open(os.path.join(source["path"], f"live-{index}.raw"), "wb") as file:
                file.write(f"live original {index}".encode())

        seen: list[str] = []

        async def request(method, url, *, body=None, headers=None):
            if method == "POST" and "/api/sync/upload/" in url and not url.endswith("/status"):
                content_hash = url.rsplit("/", 1)[1]
                if content_hash not in seen:
                    seen.append(content_hash)
                if len(seen) == 2:
                    raise TimeoutError("timed out")
            response = self.hub.request(method, url.removeprefix("http://hub"), content=body, headers=headers)
            return response.status_code, dict(response.headers), response.content

        worker = SyncWorker(db_path=__import__("db").DB_PATH, hub="http://hub", request=request)
        with self.assertRaises(TimeoutError):
            await worker.sync_once()
        # One photo finalized before the failure: the queue must already show 1.
        self.assertEqual(worker.status()["queue_depth"], 1)

    async def test_pause_and_unknown_snapshot_states_tell_the_truth(self):
        async def request(method, url, *, body=None, headers=None):
            raise TimeoutError("timed out")

        worker = SyncWorker(db_path=__import__("db").DB_PATH, hub="http://hub", request=request)
        worker.pause()
        self.assertEqual(worker.status()["state"], "paused")
        worker.resume()
        self.assertEqual(worker.status()["state"], "idle")

        # If even the durable snapshot fails after a bad cycle, unknown must
        # read as recovering — never as done.
        from features.sync import satellite as satellite_module
        original = satellite_module.pending_upload_snapshot

        async def broken(db_path):
            raise RuntimeError("db unavailable")

        satellite_module.pending_upload_snapshot = broken
        try:
            await worker._reconcile_status_after_failure()
        finally:
            satellite_module.pending_upload_snapshot = original
        self.assertEqual(worker.status()["state"], "recovering")

    async def test_timeout_failures_backoff_instead_of_hot_loop(self):
        async def request(method, url, *, body=None, headers=None):
            raise TimeoutError("satellite sync failed: timed out")

        worker = SyncWorker(db_path=__import__("db").DB_PATH, hub="http://hub", request=request)
        self.assertEqual(worker._next_idle_seconds, 15.0)
        worker._note_failure(TimeoutError("timed out"))
        self.assertEqual(worker.status()["backoff_seconds"], 30.0)
        worker._note_failure(TimeoutError("timed out"))
        self.assertEqual(worker.status()["backoff_seconds"], 60.0)
        worker._clear_backoff()
        self.assertEqual(worker.status()["backoff_seconds"], 0)

    async def test_sync_chip_now_pause_resume_mutate_the_worker_state(self):
        class Worker:
            def __init__(self):
                self.paused = False
                self.now_calls = 0
            def pause(self): self.paused = True
            def resume(self): self.paused = False
            def sync_now(self): self.now_calls += 1
            def status(self): return {"paused": self.paused, "queue_depth": 0}

        worker = Worker()
        old_get_worker = satellite_routes.get_worker
        old_satellite_mode = satellite_routes.satellite.is_satellite_mode
        satellite_routes.get_worker = lambda: worker
        satellite_routes.satellite.is_satellite_mode = lambda: True
        try:
            def drive():
                with TestClient(app_module.app) as client:
                    now = client.post("/api/sync/now")
                    paused = client.post("/api/sync/pause")
                    resumed = client.post("/api/sync/resume")
                    return now, paused, resumed
            now, paused, resumed = await __import__("asyncio").to_thread(drive)
        finally:
            satellite_routes.get_worker = old_get_worker
            satellite_routes.satellite.is_satellite_mode = old_satellite_mode
        self.assertEqual(now.status_code, 200)
        self.assertEqual(worker.now_calls, 1)
        self.assertTrue(paused.json()["paused"])
        self.assertFalse(resumed.json()["paused"])
