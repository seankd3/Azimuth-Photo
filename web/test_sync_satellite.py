"""Satellite round-trip coverage against an in-memory FIELD_SPEC hub stub."""

from __future__ import annotations

import json
import os
import unittest
from unittest import mock

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from test_support import BackendTestCase
from features.library import keywords
from features.sync.sync_worker import SyncWorker
from features.sync import satellite, satellite_routes
import app as app_module


class SatelliteSyncTests(BackendTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.hub_files: dict[str, bytearray] = {}
        self.hub_image_ids: dict[str, int] = {}
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
                    known.append(
                        {
                            "content_hash": content_hash,
                            "image_id": self.hub_image_ids[content_hash],
                        }
                    )
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
            self.hub_image_ids.setdefault(content_hash, len(self.hub_image_ids) + 1)
            assert len(target) == offset
            target.extend(data)
            assert len(target) <= total
            return {"image_id": self.hub_image_ids[content_hash]}

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
        conn = await __import__("db").get_db()
        try:
            receipts = [
                dict(row)
                for row in await (
                    await conn.execute(
                        "SELECT uploaded, full_hash, uploaded_at, hub_image_id "
                        "FROM sync_state ORDER BY image_id"
                    )
                ).fetchall()
            ]
        finally:
            await conn.close()
        self.assertEqual(len(receipts), 3)
        self.assertTrue(all(row["uploaded"] == 1 for row in receipts))
        self.assertTrue(all(len(row["full_hash"] or "") == 32 for row in receipts))
        self.assertTrue(all(row["uploaded_at"] is not None for row in receipts))
        self.assertTrue(all(row["hub_image_id"] is not None for row in receipts))
        uploads = dict(self.hub_files)
        metadata_count = len(self.hub_metadata)

        await worker.sync_once()
        self.assertEqual(self.hub_files, uploads)
        self.assertEqual(len(self.hub_metadata), metadata_count)

    async def test_unchanged_original_reuses_persisted_hash_receipt(self):
        source = await self._source("field")
        image_id = await self._image(source["id"], "cached.raw")
        path = os.path.join(source["path"], "cached.raw")
        with open(path, "wb") as file:
            file.write(b"hash me once, then trust the stat fingerprint")

        with mock.patch.object(
            satellite,
            "compute_hash_pair",
            wraps=satellite.compute_hash_pair,
        ) as hash_pair:
            first = await satellite.record_local_images(__import__("db").DB_PATH)
            second = await satellite.record_local_images(__import__("db").DB_PATH)

        self.assertEqual(len(first), 1)
        self.assertEqual(len(second), 1)
        self.assertEqual(hash_pair.call_count, 1)
        self.assertEqual(first[0]["content_hash"], second[0]["content_hash"])

        conn = await __import__("db").get_db()
        try:
            receipt = await (
                await conn.execute(
                    "SELECT full_hash, file_size, file_modified_ns "
                    "FROM sync_state WHERE image_id = ?",
                    (image_id,),
                )
            ).fetchone()
        finally:
            await conn.close()
        self.assertEqual(len(receipt["full_hash"]), 32)
        self.assertEqual(receipt["file_size"], os.path.getsize(path))
        self.assertGreater(receipt["file_modified_ns"], 0)

    async def test_legacy_upload_receipt_is_reconfirmed_with_a_full_hash(self):
        source = await self._source("field")
        image_id = await self._image(source["id"], "legacy.raw")
        path = os.path.join(source["path"], "legacy.raw")
        with open(path, "wb") as file:
            file.write(b"legacy upload that predates complete-file proofs")

        content_hash = satellite.content_hash_for_file(path)
        stat = os.stat(path)
        await satellite.ensure_sync_state(__import__("db").DB_PATH)
        conn = await __import__("db").get_db()
        try:
            await conn.execute(
                "UPDATE images SET content_hash = ? WHERE id = ?",
                (content_hash, image_id),
            )
            await conn.execute(
                """
                INSERT INTO sync_state(
                    content_hash, image_id, last_local_change_at, uploaded,
                    file_size, file_modified_ns
                ) VALUES (?, ?, ?, 1, ?, ?)
                """,
                (content_hash, image_id, stat.st_mtime, stat.st_size, stat.st_mtime_ns),
            )
            await conn.commit()
        finally:
            await conn.close()

        items = await satellite.record_local_images(__import__("db").DB_PATH)

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["uploaded"], 0)
        self.assertEqual(len(items[0]["full_hash"]), 32)
        conn = await __import__("db").get_db()
        try:
            receipt = await (
                await conn.execute(
                    "SELECT uploaded, full_hash FROM sync_state WHERE image_id = ?",
                    (image_id,),
                )
            ).fetchone()
        finally:
            await conn.close()
        self.assertEqual(receipt["uploaded"], 0)
        self.assertEqual(len(receipt["full_hash"]), 32)

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

    async def test_hub_health_is_sampled_without_polling_every_cycle(self):
        calls = []

        async def request(method, url, *, body=None, headers=None):
            calls.append((method, url))
            return 200, {}, json.dumps({
                "status": "warn",
                "checks": {"workers": "warn", "catalog_db": "ok"},
            }).encode()

        worker = SyncWorker(db_path=__import__("db").DB_PATH, hub="http://hub", request=request)

        await worker._refresh_hub_health()
        await worker._refresh_hub_health()

        self.assertEqual(calls, [("GET", "http://hub/api/health")])
        self.assertEqual(worker.status()["hub_system_health"], "warn")
        self.assertEqual(
            worker.status()["hub_health_checks"],
            {"workers": "warn", "catalog_db": "ok"},
        )

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
        old_satellite_mode = satellite_routes.role.works_in_someone_elses_archive
        satellite_routes.get_worker = lambda: worker
        satellite_routes.role.works_in_someone_elses_archive = lambda: True
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
            satellite_routes.role.works_in_someone_elses_archive = old_satellite_mode
        self.assertEqual(now.status_code, 200)
        self.assertEqual(worker.now_calls, 1)
        self.assertTrue(paused.json()["paused"])
        self.assertFalse(resumed.json()["paused"])


class ManifestBatchingTests(unittest.TestCase):
    """A backlog bigger than the hub's cap must still sync.

    Sending it whole returned 422 on every cycle, so a satellite with real
    work queued could never push anything at all.
    """

    def test_manifest_is_split_into_hub_sized_batches(self):
        from features.sync import sync_worker
        from features.sync.hub_routes import ManifestRequest

        cap = None
        for field_name, field in ManifestRequest.model_fields.items():
            if field_name != "items":
                continue
            for meta in field.metadata:
                cap = getattr(meta, "max_length", None) or cap
        self.assertIsNotNone(cap, "hub manifest cap should be declared")
        self.assertLessEqual(
            sync_worker.MANIFEST_BATCH,
            int(cap),
            "satellite batch must never exceed what the hub accepts",
        )

    def test_oversized_backlog_pushes_every_item(self):
        from features.sync import sync_worker

        total = sync_worker.MANIFEST_BATCH * 2 + 7
        payload = [{"content_hash": f"h{i:06d}", "bytes": 1} for i in range(total)]
        seen_batches = []

        def chunks():
            for start in range(0, len(payload), sync_worker.MANIFEST_BATCH):
                seen_batches.append(payload[start:start + sync_worker.MANIFEST_BATCH])

        chunks()
        self.assertEqual(sum(len(batch) for batch in seen_batches), total)
        self.assertTrue(
            all(len(batch) <= sync_worker.MANIFEST_BATCH for batch in seen_batches),
            "no batch may exceed the hub cap",
        )
        self.assertEqual(len(seen_batches), 3)
