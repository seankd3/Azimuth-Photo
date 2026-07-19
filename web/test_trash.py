import pytest
import os
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient

from test_support import *  # noqa: F401,F403
from data.repositories import stacks as stack_repository
from features.media import routes as media_routes
from features.sync import contract
from features.sync.sync_worker import SyncWorker
from features.develop import virtual_copies
from features.trash import service as trash_service
from features.trash import remote as trash_remote


class TrashTests(BackendTestCase):
    async def test_explicit_empty_hub_purge_is_immediate_with_busy_writer(self):
        def probe():
            with TestClient(app_module.app) as client:
                writer = sqlite3.connect(db.DB_PATH, timeout=0.1)
                try:
                    writer.execute("BEGIN IMMEDIATE")
                    started = time.perf_counter()
                    response = client.post(
                        "/api/trash/empty",
                        json={"hub_image_ids": []},
                        headers={"X-PhotoArchive-Trash-Forwarded": "1"},
                    )
                    elapsed = time.perf_counter() - started
                finally:
                    writer.rollback()
                    writer.close()
            return response, elapsed

        response, elapsed = await asyncio.to_thread(probe)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"deleted_count": 0, "freed_bytes": 0, "errors": [], "skipped_offline": 0},
        )
        self.assertLess(elapsed, 2.0)

    async def test_nonempty_purge_bounds_catalog_lock_wait(self):
        source, _root = await self._source_root()
        image_id, _filepath = await self._file_image(source, "locked-delete.jpg", data=b"locked")
        await trash_service.trash_images(db.DB_PATH, [image_id])
        trash_path = (await self._image_row(image_id))["trash_path"]
        writer = sqlite3.connect(db.DB_PATH, timeout=0.1)
        try:
            writer.execute("BEGIN IMMEDIATE")
            started = time.perf_counter()
            result = await trash_service.empty_trash(db.DB_PATH, image_ids=[image_id])
            elapsed = time.perf_counter() - started
        finally:
            writer.rollback()
            writer.close()

        self.assertEqual(result["deleted_count"], 0)
        self.assertIn("deferred", result["errors"][0]["reason"])
        self.assertLess(elapsed, 2.0)
        self.assertTrue(os.path.exists(trash_path))
        self.assertTrue(await self._image_exists(image_id))

    async def test_purge_lock_failure_keeps_files_and_rows(self):
        source, _root = await self._source_root()
        image_id, filepath = await self._file_image(source, "retryable.jpg", data=b"retryable")
        await trash_service.trash_images(db.DB_PATH, [image_id])
        trash_path = (await self._image_row(image_id))["trash_path"]
        deferred = {"id": image_id, "reason": "catalog row deletion deferred: locked"}

        with patch.object(
            trash_service,
            "_delete_emptied_catalog_rows",
            new_callable=AsyncMock,
            return_value=([], [deferred]),
        ):
            result = await trash_service.empty_trash(db.DB_PATH, image_ids=[image_id])

        row = await self._image_row(image_id)
        self.assertEqual(result["deleted_count"], 0)
        self.assertEqual(result["freed_bytes"], 0)
        self.assertEqual(result["errors"], [deferred])
        self.assertTrue(os.path.exists(trash_path))
        self.assertEqual(row["status"], "trashed")
        self.assertEqual(row["trash_path"], trash_path)

        restored = await trash_service.restore_images(db.DB_PATH, [image_id])
        self.assertEqual(restored["restored"], [image_id])
        self.assertEqual(open(filepath, "rb").read(), b"retryable")

    async def _mirrored_trash(self, *, name="mirrored.jpg", hub_image_id=92):
        conn = await db.get_db()
        try:
            source = await conn.execute(
                "INSERT INTO catalog_sources(path, display_name, included, online) "
                "VALUES ('hub://', 'Hub library', 1, 1)"
            )
            image = await conn.execute(
                "INSERT INTO images "
                "(source_id, filename, filepath, content_hash, status, trashed_at, "
                "trash_path, file_ext, file_size, hub_image_id, hub_remote) "
                "VALUES (?, ?, ?, ?, 'trashed', 1000, NULL, 'jpg', 4321, ?, 1)",
                (int(source.lastrowid), name, f'/hub/{name}', "b" * 32, hub_image_id),
            )
            await conn.commit()
            return int(image.lastrowid)
        finally:
            await conn.close()

    async def _source_root(self):
        source = await self._source("catalog")
        return source, source["path"]

    async def _file_image(self, source, relpath, *, data=b"photo bytes", elo=1200.0):
        filepath = os.path.join(source["path"], relpath)
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        with open(filepath, "wb") as handle:
            handle.write(data)
        conn = await db.get_db()
        try:
            cursor = await conn.execute(
                "INSERT INTO images "
                "(source_id, filename, filepath, elo, comparisons, propagated_updates, status, file_ext, file_size) "
                "VALUES (?, ?, ?, ?, 0, 0, 'kept', ?, ?)",
                (
                    int(source["id"]),
                    os.path.basename(filepath),
                    filepath,
                    float(elo),
                    os.path.splitext(filepath)[1].lower().lstrip("."),
                    len(data),
                ),
            )
            await db._update_source_counts(conn, int(source["id"]))
            await conn.commit()
            image_id = int(cursor.lastrowid)
        finally:
            await conn.close()
        db.invalidate_stats_cache()
        return image_id, filepath

    async def _cache_entry(self, image_id):
        cache_path = os.path.join(self.tempdir.name, "cache", f"{image_id}.jpg")
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        with open(cache_path, "wb") as handle:
            handle.write(b"thumb")
        conn = await db.get_db()
        try:
            await conn.execute(
                "INSERT OR REPLACE INTO cache_entries "
                "(cache_root, size, image_id, path, source_signature, size_bytes, last_accessed, created_at) "
                "VALUES (?, 'sm', ?, ?, ?, 5, 1000, 1000)",
                (thumbnails.SSD_CACHE_DIR, int(image_id), cache_path, f"sig-{image_id}"),
            )
            await conn.commit()
        finally:
            await conn.close()

    async def _image_exists(self, image_id):
        conn = await db.get_db()
        try:
            cursor = await conn.execute("SELECT 1 FROM images WHERE id = ?", (image_id,))
            return await cursor.fetchone() is not None
        finally:
            await conn.close()

    async def test_trash_moves_file_and_sets_columns(self):
        source, _root = await self._source_root()
        image_id, filepath = await self._file_image(source, "shoot/one.jpg", data=b"one")

        result = await trash_service.trash_images(db.DB_PATH, [image_id])
        row = await self._image_row(image_id)

        self.assertEqual(result["trashed"], [image_id])
        self.assertFalse(os.path.exists(filepath))
        self.assertEqual(row["status"], "trashed")
        self.assertIsNotNone(row["trashed_at"])
        self.assertTrue(row["trash_path"].endswith(os.path.join(".trash", "shoot", "one.jpg")))
        self.assertEqual(open(row["trash_path"], "rb").read(), b"one")

    async def test_trash_move_failure_reverts_committed_row(self):
        source, _root = await self._source_root()
        image_id, filepath = await self._file_image(source, "move-fails.jpg", data=b"keep me")
        original_move = trash_service._move_to_trash
        observed_states = []

        def fail_after_db_commit(_filepath, _dest, _token):
            conn = sqlite3.connect(db.DB_PATH)
            try:
                observed_states.append(
                    conn.execute("SELECT status, trash_path FROM images WHERE id = ?", (image_id,)).fetchone()
                )
            finally:
                conn.close()
            return 0, "injected move failure"

        trash_service._move_to_trash = fail_after_db_commit
        try:
            result = await trash_service.trash_images(db.DB_PATH, [image_id])
        finally:
            trash_service._move_to_trash = original_move
        row = await self._image_row(image_id)

        self.assertEqual(result["trashed"], [])
        self.assertEqual(result["errors"], [{"id": image_id, "reason": "injected move failure"}])
        self.assertEqual(observed_states[0][0], "trashed")
        self.assertIsNotNone(observed_states[0][1])
        self.assertTrue(os.path.exists(filepath))
        self.assertEqual(row["status"], "kept")
        self.assertIsNone(row["trashed_at"])
        self.assertIsNone(row["trash_path"])

    async def test_trashed_rows_vanish_from_rankings_and_counts(self):
        source, _root = await self._source_root()
        trashed_id, _ = await self._file_image(source, "trash-me.jpg", data=b"x", elo=1500)
        kept_id, _ = await self._file_image(source, "keep-me.jpg", data=b"y", elo=1400)
        await self._cache_entry(trashed_id)
        await self._cache_entry(kept_id)

        def probe():
            client = TestClient(app_module.app)
            try:
                trash_response = client.post("/api/images/trash", json={"ids": [trashed_id]})
                rankings = client.get("/api/rankings", params={"limit": 10})
                counts = client.get("/api/counts")
                return trash_response, rankings, counts
            finally:
                client.close()

        trash_response, rankings, counts = await asyncio.to_thread(probe)

        self.assertEqual(trash_response.status_code, 200)
        self.assertEqual(trash_response.json()["trashed"], [trashed_id])
        self.assertEqual([image["id"] for image in rankings.json()["images"]], [kept_id])
        self.assertEqual(rankings.json()["total_images"], 1)
        self.assertEqual(counts.json()["total"], 1)

    async def test_restore_round_trip_is_byte_identical(self):
        source, _root = await self._source_root()
        original = b"\x00raw-ish-bytes\xff"
        image_id, filepath = await self._file_image(source, "roundtrip/raw.dng", data=original)

        trashed = await trash_service.trash_images(db.DB_PATH, [image_id])
        self.assertEqual(trashed["trashed"], [image_id])
        restored = await trash_service.restore_images(db.DB_PATH, [image_id])

        row = await self._image_row(image_id)
        self.assertEqual(restored["restored"], [image_id])
        self.assertEqual(restored["errors"], [])
        self.assertEqual(open(filepath, "rb").read(), original)
        self.assertEqual(row["status"], "kept")
        self.assertIsNone(row["trashed_at"])
        self.assertIsNone(row["trash_path"])

    async def test_restore_collision_errors_without_overwrite(self):
        source, _root = await self._source_root()
        image_id, filepath = await self._file_image(source, "collision.jpg", data=b"original")
        await trash_service.trash_images(db.DB_PATH, [image_id])
        with open(filepath, "wb") as handle:
            handle.write(b"new file")

        restored = await trash_service.restore_images(db.DB_PATH, [image_id])
        row = await self._image_row(image_id)

        self.assertEqual(restored["restored"], [])
        self.assertEqual(restored["errors"], [{"id": image_id, "reason": "original path already exists"}])
        self.assertEqual(open(filepath, "rb").read(), b"new file")
        self.assertEqual(row["status"], "trashed")

    async def test_restore_missing_file_returns_error_and_keeps_trashed_row(self):
        source, _root = await self._source_root()
        image_id, filepath = await self._file_image(source, "missing.jpg", data=b"missing")
        await trash_service.trash_images(db.DB_PATH, [image_id])
        trash_path = (await self._image_row(image_id))["trash_path"]
        os.remove(trash_path)

        def probe():
            with TestClient(app_module.app) as client:
                return client.post("/api/images/restore", json={"ids": [image_id]})

        response = await asyncio.to_thread(probe)
        row = await self._image_row(image_id)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["restored"], [])
        self.assertEqual(response.json()["errors"], [{"id": image_id, "reason": "trash file is missing"}])
        self.assertEqual(response.json()["warnings"], [])
        self.assertEqual(row["status"], "trashed")
        self.assertEqual(row["trash_path"], trash_path)
        self.assertFalse(os.path.exists(filepath))

    async def test_empty_trash_deletes_files_rows_and_reports_freed_bytes(self):
        source, _root = await self._source_root()
        first_id, _ = await self._file_image(source, "first.jpg", data=b"12345")
        second_id, _ = await self._file_image(source, "nested/second.jpg", data=b"abcdef")
        trashed = await trash_service.trash_images(db.DB_PATH, [first_id, second_id])
        self.assertEqual(set(trashed["trashed"]), {first_id, second_id})
        trash_paths = [
            (await self._image_row(first_id))["trash_path"],
            (await self._image_row(second_id))["trash_path"],
        ]

        emptied = await trash_service.empty_trash(db.DB_PATH)

        self.assertEqual(emptied["deleted_count"], 2)
        self.assertEqual(emptied["freed_bytes"], 11)
        self.assertEqual(emptied["errors"], [])
        for path in trash_paths:
            self.assertFalse(os.path.exists(path))
        self.assertFalse(await self._image_exists(first_id))
        self.assertFalse(await self._image_exists(second_id))

    async def test_empty_trash_keeps_bytes_when_catalog_delete_fails(self):
        source, _root = await self._source_root()
        image_id, _ = await self._file_image(source, "delete-fails.jpg", data=b"irreplaceable")
        await trash_service.trash_images(db.DB_PATH, [image_id])
        trash_path = (await self._image_row(image_id))["trash_path"]

        async def fail_catalog_delete(_db_path, image_ids):
            self.assertEqual(image_ids, [image_id])
            self.assertTrue(os.path.exists(trash_path))
            return [], [{"id": image_id, "reason": "injected catalog failure"}]

        with patch.object(
            trash_service,
            "_delete_emptied_catalog_rows",
            side_effect=fail_catalog_delete,
        ):
            result = await trash_service.empty_trash(db.DB_PATH, image_ids=[image_id])

        self.assertEqual(result["deleted_count"], 0)
        self.assertEqual(result["freed_bytes"], 0)
        self.assertEqual(result["errors"], [{"id": image_id, "reason": "injected catalog failure"}])
        self.assertTrue(os.path.exists(trash_path))
        self.assertTrue(await self._image_exists(image_id))

    async def test_empty_trash_surfaces_unlink_failure_after_catalog_delete(self):
        source, _root = await self._source_root()
        image_id, _ = await self._file_image(source, "unlink-fails.jpg", data=b"leftover")
        await trash_service.trash_images(db.DB_PATH, [image_id])
        trash_path = (await self._image_row(image_id))["trash_path"]

        with patch.object(
            trash_service,
            "__to_thread_remove_trash_file",
            new_callable=AsyncMock,
            return_value=(0, "injected unlink failure"),
        ):
            result = await trash_service.empty_trash(db.DB_PATH, image_ids=[image_id])

        self.assertEqual(result["deleted_count"], 1)
        self.assertEqual(result["freed_bytes"], 0)
        self.assertEqual(result["errors"], [{"id": image_id, "reason": "injected unlink failure"}])
        self.assertTrue(os.path.exists(trash_path))
        self.assertFalse(await self._image_exists(image_id))

    async def test_empty_trash_happy_path_prunes_removed_file_tree(self):
        source, root = await self._source_root()
        image_id, _filepath = await self._file_image(source, "trip/day/photo.jpg", data=b"seven77")
        await trash_service.trash_images(db.DB_PATH, [image_id])
        trash_path = (await self._image_row(image_id))["trash_path"]
        nested_trash_dir = os.path.dirname(trash_path)

        result = await trash_service.empty_trash(db.DB_PATH, image_ids=[image_id])

        self.assertEqual(result["deleted_count"], 1)
        self.assertEqual(result["freed_bytes"], 7)
        self.assertEqual(result["errors"], [])
        self.assertFalse(await self._image_exists(image_id))
        self.assertFalse(os.path.lexists(trash_path))
        self.assertFalse(os.path.exists(nested_trash_dir))
        self.assertFalse(os.path.exists(os.path.join(root, ".trash")))

    async def test_empty_trash_does_not_follow_or_remove_symlink(self):
        source, root = await self._source_root()
        image_id, _filepath = await self._file_image(source, "linked.jpg", data=b"linked")
        await trash_service.trash_images(db.DB_PATH, [image_id])
        trash_path = (await self._image_row(image_id))["trash_path"]
        target_path = os.path.join(root, ".trash", "target.jpg")
        with open(target_path, "wb") as handle:
            handle.write(b"must remain")
        os.remove(trash_path)
        os.symlink(target_path, trash_path)

        result = await trash_service.empty_trash(db.DB_PATH, image_ids=[image_id])

        self.assertEqual(result["deleted_count"], 0)
        self.assertEqual(result["errors"], [{"id": image_id, "reason": "trash path is a symlink"}])
        self.assertTrue(os.path.islink(trash_path))
        self.assertEqual(open(target_path, "rb").read(), b"must remain")
        self.assertEqual((await self._image_row(image_id))["status"], "trashed")

    async def test_empty_trash_deletes_offline_catalog_only_hub_mirror_rows(self):
        conn = await db.get_db()
        try:
            source = await conn.execute(
                "INSERT INTO catalog_sources(path, display_name, included, online) "
                "VALUES ('hub://', 'Hub library', 1, 0)"
            )
            image = await conn.execute(
                "INSERT INTO images "
                "(source_id, filename, filepath, content_hash, status, trashed_at, "
                "trash_path, file_ext, file_size, hub_image_id, hub_remote) "
                "VALUES (?, 'remote.jpg', '/hub/remote.jpg', ?, 'trashed', 1000, "
                "NULL, 'jpg', 1234, 91, 1)",
                (int(source.lastrowid), "a" * 32),
            )
            image_id = int(image.lastrowid)
            await conn.commit()
        finally:
            await conn.close()

        result = await trash_service.empty_trash(db.DB_PATH)

        self.assertEqual(result["deleted_count"], 1)
        self.assertEqual(result["skipped_offline"], 0)
        self.assertEqual(result["freed_bytes"], 0)
        self.assertFalse(await self._image_exists(image_id))

    async def test_satellite_empty_trash_never_awaits_the_hub_from_the_request_path(self):
        conn = await db.get_db()
        try:
            source = await conn.execute(
                "INSERT INTO catalog_sources(path, display_name, included, online) "
                "VALUES ('hub://', 'Hub library', 1, 1)"
            )
            image = await conn.execute(
                "INSERT INTO images "
                "(source_id, filename, filepath, content_hash, status, trashed_at, "
                "trash_path, file_ext, file_size, hub_image_id, hub_remote) "
                "VALUES (?, 'mirrored.jpg', '/hub/mirrored.jpg', ?, 'trashed', 1000, "
                "NULL, 'jpg', 4321, 92, 1)",
                (int(source.lastrowid), "b" * 32),
            )
            image_id = int(image.lastrowid)
            await conn.commit()
        finally:
            await conn.close()

        observed_calls = []

        async def fake_empty_hub(hub_url, hub_image_ids):
            observed_calls.append((hub_url, hub_image_ids))
            return {"deleted_count": 1, "freed_bytes": 4321, "errors": [], "skipped_offline": 0}

        env = {"PHOTOARCHIVE_MODE": "satellite", "PHOTOARCHIVE_HUB_URL": "http://probe-hub"}
        with patch.dict(os.environ, env, clear=False), patch(
            "features.trash.remote.empty_hub_trash", side_effect=fake_empty_hub
        ):
            def probe():
                with TestClient(app_module.app) as client:
                    return client.post("/api/trash/empty")

            response = await asyncio.to_thread(probe)

        self.assertEqual(response.status_code, 200)
        # Law 1: no foreground hub call — the sync worker owns the hub leg.
        self.assertEqual(observed_calls, [])
        self.assertEqual(response.json()["hub_pending"], 1)
        self.assertTrue(await self._image_exists(image_id))
        conn = await db.get_db()
        try:
            row = await (await conn.execute(
                "SELECT trash_pending_hub FROM images WHERE id = ?", (image_id,)
            )).fetchone()
        finally:
            await conn.close()
        self.assertEqual(int(row["trash_pending_hub"]), 1)

    async def test_satellite_empty_trash_keeps_unreachable_mirrors_pending_but_purges_local(self):
        source, _root = await self._source_root()
        local_id, _ = await self._file_image(source, "local.jpg", data=b"local")
        await trash_service.trash_images(db.DB_PATH, [local_id])
        mirror_id = await self._mirrored_trash(hub_image_id=501)

        env = {"PHOTOARCHIVE_MODE": "satellite", "PHOTOARCHIVE_HUB_URL": "http://127.0.0.1:1"}
        with patch.dict(os.environ, env, clear=False):
            response = await asyncio.to_thread(lambda: TestClient(app_module.app).post("/api/trash/empty"))

        payload = response.json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["deleted_count"], 1)
        self.assertEqual(payload["hub_pending"], 1)
        self.assertFalse(await self._image_exists(local_id))
        pending = await self._image_row(mirror_id)
        self.assertEqual(pending["status"], "trashed")
        self.assertEqual(pending["trash_pending_hub"], 1)

    async def test_legacy_hub_version_failure_never_forwards_empty_post(self):
        calls = []
        owner = calls

        class LegacyHubHandler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                owner.append(("GET", self.path))
                self.send_response(404)
                self.end_headers()

            def do_POST(self):  # noqa: N802
                owner.append(("POST", self.path))
                self.send_response(404)
                self.end_headers()

            def log_message(self, _format, *_args):
                return

        mirror_id = await self._mirrored_trash(hub_image_id=91)
        server = ThreadingHTTPServer(("127.0.0.1", 0), LegacyHubHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        hub_url = f"http://127.0.0.1:{server.server_port}"
        contract.reset_hub_contract_cache()
        try:
            env = {"PHOTOARCHIVE_MODE": "satellite", "PHOTOARCHIVE_HUB_URL": hub_url}
            with patch.dict(os.environ, env, clear=False):
                response = await asyncio.to_thread(
                    lambda: TestClient(app_module.app).post("/api/trash/empty")
                )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=1)
            contract.reset_hub_contract_cache()

        payload = response.json()
        self.assertEqual(response.status_code, 200)
        # Law 1: the request path generates no hub traffic at all anymore —
        # the worker owns capability probing and the scoped-empty POST.
        self.assertEqual([c for c in calls if c[0] == "POST"], [])
        self.assertEqual(payload["hub_pending"], 1)
        self.assertEqual(payload["hub_pending"], 1)
        self.assertNotIn(("POST", "/api/trash/empty"), calls)
        pending = await self._image_row(mirror_id)
        self.assertEqual(pending["trash_pending_hub"], 1)

    async def test_pending_hub_trash_retry_deletes_recovered_mirror(self):
        mirror_id = await self._mirrored_trash(hub_image_id=777)
        await trash_service.mark_hub_trash_pending(db.DB_PATH, [mirror_id])
        worker = SyncWorker(db_path=db.DB_PATH, hub="http://stub-hub")

        async def recovered_hub(_hub, image_ids):
            self.assertEqual(image_ids, [777])
            return {"deleted_count": 1, "freed_bytes": 4321, "errors": [], "skipped_offline": 0}

        with patch(
            "features.sync.sync_worker.contract.hub_supports", new_callable=AsyncMock, return_value=True
        ), patch("features.sync.sync_worker.trash_remote.empty_hub_trash", side_effect=recovered_hub):
            await worker._retry_pending_hub_trash()

        self.assertFalse(await self._image_exists(mirror_id))
        self.assertEqual(worker.status()["pending_hub_trash"], 0)

    async def test_pending_hub_trash_cache_invalidates_after_emptying(self):
        mirror_id = await self._mirrored_trash(hub_image_id=779)
        await trash_service.mark_hub_trash_pending(db.DB_PATH, [mirror_id])
        self.assertEqual((await trash_service.pending_hub_trash_refs(db.DB_PATH))["count"], 1)

        await trash_service.empty_trash(db.DB_PATH, image_ids=[mirror_id])

        self.assertEqual((await trash_service.pending_hub_trash_refs(db.DB_PATH))["count"], 0)

    async def test_pending_hub_trash_retry_skips_old_hub(self):
        mirror_id = await self._mirrored_trash(hub_image_id=778)
        await trash_service.mark_hub_trash_pending(db.DB_PATH, [mirror_id])
        worker = SyncWorker(db_path=db.DB_PATH, hub="http://old-hub")

        with patch(
            "features.sync.sync_worker.contract.hub_supports", new_callable=AsyncMock, return_value=False
        ), patch("features.sync.sync_worker.trash_remote.empty_hub_trash", new_callable=AsyncMock) as forward:
            await worker._retry_pending_hub_trash()

        forward.assert_not_awaited()
        self.assertTrue(await self._image_exists(mirror_id))
        self.assertEqual(worker.status()["pending_hub_trash"], 1)

    async def test_trashed_mirror_thumbnail_reads_through_from_hub(self):
        mirror_id = await self._mirrored_trash(hub_image_id=888)

        async def hub_thumb(_method, url, **_kwargs):
            self.assertTrue(url.endswith("/api/thumb/sm/888"))
            return 200, {"Content-Type": "image/jpeg"}, b"hub-thumb"

        with patch.dict(os.environ, {"PHOTOARCHIVE_HUB_URL": "http://stub-hub"}, clear=False), patch(
            "features.media.routes._urllib_request", side_effect=hub_thumb
        ):
            response = await media_routes.serve_thumbnail(HeaderRequest(), "sm", mirror_id)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.body, b"hub-thumb")

    async def test_satellite_scoped_empty_queues_hub_without_capability(self):
        conn = await db.get_db()
        try:
            source = await conn.execute(
                "INSERT INTO catalog_sources(path, display_name, included, online) "
                "VALUES ('hub://', 'Hub library', 1, 1)"
            )
            image = await conn.execute(
                "INSERT INTO images "
                "(source_id, filename, filepath, content_hash, status, trashed_at, "
                "trash_path, file_ext, file_size, hub_image_id, hub_remote) "
                "VALUES (?, 'mirrored.jpg', '/hub/mirrored.jpg', ?, 'trashed', 1000, "
                "NULL, 'jpg', 4321, 92, 1)",
                (int(source.lastrowid), "b" * 32),
            )
            image_id = int(image.lastrowid)
            await conn.commit()
        finally:
            await conn.close()

        env = {"PHOTOARCHIVE_MODE": "satellite", "PHOTOARCHIVE_HUB_URL": "http://old-hub"}
        with patch.dict(os.environ, env, clear=False), patch(
            "features.trash.remote.empty_hub_trash"
        ) as forward:
            def probe():
                with TestClient(app_module.app) as client:
                    return client.post("/api/trash/empty")

            response = await asyncio.to_thread(probe)

        self.assertEqual(response.status_code, 200)
        forward.assert_not_called()
        self.assertEqual(response.json()["hub_pending"], 1)
        forward.assert_not_called()
        self.assertTrue(await self._image_exists(image_id))

    async def test_retention_purges_only_expired_online_trash_and_never_originals(self):
        source, root = await self._source_root()
        expired_id, _ = await self._file_image(source, "expired.jpg", data=b"expired")
        recent_id, recent_original = await self._file_image(source, "recent.jpg", data=b"recent")
        unsafe_id, _ = await self._file_image(source, "unsafe.jpg", data=b"unsafe")
        outside_original = os.path.join(root, "outside.jpg")
        with open(outside_original, "wb") as handle:
            handle.write(b"must remain")
        await trash_service.trash_images(db.DB_PATH, [expired_id, recent_id, unsafe_id])
        expired = await self._image_row(expired_id)
        recent = await self._image_row(recent_id)
        conn = await db.get_db()
        try:
            await conn.execute("UPDATE images SET trashed_at = ? WHERE id = ?", (100.0, expired_id))
            await conn.execute("UPDATE images SET trashed_at = ?, trash_path = ? WHERE id = ?", (100.0, outside_original, unsafe_id))
            await conn.commit()
        finally:
            await conn.close()

        result = await trash_service.purge_expired_trash(db.DB_PATH, retention_seconds=60, now=200.0)

        self.assertEqual(result["deleted_count"], 1)
        self.assertEqual(result["errors"], [{"id": unsafe_id, "reason": "trash path is outside source trash"}])
        self.assertFalse(os.path.exists(expired["trash_path"]))
        self.assertFalse(await self._image_exists(expired_id))
        self.assertTrue(os.path.exists(recent["trash_path"]))
        self.assertFalse(os.path.exists(recent_original))
        self.assertTrue(os.path.exists(outside_original))
        self.assertTrue(await self._image_exists(recent_id))
        self.assertTrue(await self._image_exists(unsafe_id))

    async def test_retention_skips_offline_source_without_error(self):
        source, _root = await self._source_root()
        image_id, _ = await self._file_image(source, "offline.jpg", data=b"offline")
        await trash_service.trash_images(db.DB_PATH, [image_id])
        trashed = await self._image_row(image_id)
        conn = await db.get_db()
        try:
            await conn.execute("UPDATE images SET trashed_at = ? WHERE id = ?", (100.0, image_id))
            await conn.execute("UPDATE catalog_sources SET online = 0 WHERE id = ?", (source["id"],))
            await conn.commit()
        finally:
            await conn.close()

        result = await trash_service.purge_expired_trash(db.DB_PATH, retention_seconds=60, now=200.0)

        self.assertEqual(result["deleted_count"], 0)
        self.assertEqual(result["skipped_offline"], 1)
        self.assertEqual(result["errors"], [])
        self.assertTrue(os.path.exists(trashed["trash_path"]))
        self.assertTrue(await self._image_exists(image_id))

    async def test_trashing_stack_representative_promotes_next_best_member(self):
        source, _root = await self._source_root()
        representative, _ = await self._file_image(source, "rep.jpg", data=b"rep", elo=1500)
        next_best, _ = await self._file_image(source, "next.tif", data=b"next", elo=1300)
        low, _ = await self._file_image(source, "low.jpg", data=b"low", elo=1200)
        await stack_repository.create_stack(
            db.DB_PATH,
            kind="manual",
            representative_image_id=representative,
            member_rows=[
                {"image_id": representative},
                {"image_id": next_best},
                {"image_id": low},
            ],
            auto=False,
        )

        await trash_service.trash_images(db.DB_PATH, [representative])
        stack = await stack_repository.stack_for_image(db.DB_PATH, next_best)

        self.assertIsNotNone(stack)
        self.assertEqual(stack["representative"]["id"], next_best)
        self.assertEqual(stack["member_count"], 2)
        self.assertNotIn(representative, [member["id"] for member in stack["members"]])

    async def test_trashing_collection_cover_hides_member_and_reassigns_cover(self):
        source, _root = await self._source_root()
        cover, _ = await self._file_image(source, "cover.jpg", data=b"cover")
        remaining, _ = await self._file_image(source, "remaining.jpg", data=b"remaining")
        collection = await db.create_collection(
            name="Trash-safe collection",
            image_ids=[cover, remaining],
        )

        await trash_service.trash_images(db.DB_PATH, [cover])
        detail = await db.get_collection(collection["id"])

        self.assertEqual(detail["image_count"], 1)
        self.assertEqual(detail["cover_image_id"], remaining)
        self.assertEqual([image["id"] for image in detail["images"]], [remaining])

    async def test_restoring_only_collection_member_restores_cover(self):
        source, _root = await self._source_root()
        image_id, _ = await self._file_image(source, "only.jpg", data=b"only")
        collection = await db.create_collection(name="Only", image_ids=[image_id])
        await trash_service.trash_images(db.DB_PATH, [image_id])

        await trash_service.restore_images(db.DB_PATH, [image_id])
        detail = await db.get_collection(collection["id"])

        self.assertEqual(detail["image_count"], 1)
        self.assertEqual(detail["cover_image_id"], image_id)

    async def test_missing_file_trash_still_marks_row(self):
        source, _root = await self._source_root()
        image_id, filepath = await self._file_image(source, "missing.jpg", data=b"gone")
        os.remove(filepath)

        result = await trash_service.trash_images(db.DB_PATH, [image_id])
        row = await self._image_row(image_id)

        self.assertEqual(result["trashed"], [image_id])
        self.assertEqual(result["errors"], [])
        self.assertEqual(row["status"], "trashed")
        self.assertIsNotNone(row["trashed_at"])
        self.assertIsNone(row["trash_path"])


class VirtualCopyTrashTests(BackendTestCase):
    async def _source_root(self):
        source = await self._source("catalog")
        return source, source["path"]

    async def _file_image(self, source, relpath, *, data=b"photo bytes", elo=1200.0):
        filepath = os.path.join(source["path"], relpath)
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        with open(filepath, "wb") as handle:
            handle.write(data)
        conn = await db.get_db()
        try:
            cursor = await conn.execute(
                "INSERT INTO images "
                "(source_id, filename, filepath, elo, comparisons, propagated_updates, status, file_ext, file_size) "
                "VALUES (?, ?, ?, ?, 0, 0, 'kept', ?, ?)",
                (
                    int(source["id"]),
                    os.path.basename(filepath),
                    filepath,
                    float(elo),
                    os.path.splitext(filepath)[1].lower().lstrip("."),
                    len(data),
                ),
            )
            await db._update_source_counts(conn, int(source["id"]))
            await conn.commit()
            image_id = int(cursor.lastrowid)
        finally:
            await conn.close()
        db.invalidate_stats_cache()
        return image_id, filepath

    async def _master_with_copy(self):
        source, _root = await self._source_root()
        image_id, filepath = await self._file_image(source, "vc-master.jpg", data=b"vc bytes")
        conn = await db.get_db()
        try:
            copy = await virtual_copies.create_virtual_copy(conn, image_id)
            await conn.commit()
        finally:
            await conn.close()
        return image_id, int(copy["id"]), filepath

    async def test_trashing_master_takes_virtual_copies_catalog_only(self):
        master_id, copy_id, filepath = await self._master_with_copy()
        result = await trash_service.trash_images(db.DB_PATH, [master_id])
        self.assertIn(master_id, result["trashed"])
        self.assertIn(copy_id, result["trashed"])
        conn = await db.get_db()
        rows = await (await conn.execute(
            "SELECT id, status, trash_path FROM images WHERE id IN (?, ?)",
            (master_id, copy_id),
        )).fetchall()
        by_id = {int(row["id"]): dict(row) for row in rows}
        self.assertEqual(by_id[master_id]["status"], "trashed")
        self.assertEqual(by_id[copy_id]["status"], "trashed")
        self.assertTrue(by_id[master_id]["trash_path"])
        self.assertIsNone(by_id[copy_id]["trash_path"])
        self.assertFalse(os.path.exists(filepath))

    @pytest.mark.contract
    async def test_master_trash_failure_reverts_entire_virtual_copy_family(self):
        master_id, copy_id, filepath = await self._master_with_copy()
        observed_statuses = []

        def fail_master_after_family_commit(_filepath, dest, _token):
            if dest is None:
                return 0, ""
            conn = sqlite3.connect(db.DB_PATH)
            try:
                observed_statuses.append(dict(conn.execute(
                    "SELECT id, status FROM images WHERE id IN (?, ?)",
                    (master_id, copy_id),
                ).fetchall()))
            finally:
                conn.close()
            return 0, "injected trash failure"

        with patch.object(trash_service, "_move_to_trash", fail_master_after_family_commit):
            result = await trash_service.trash_images(db.DB_PATH, [master_id])

        self.assertEqual(observed_statuses, [{master_id: "trashed", copy_id: "trashed"}])
        self.assertEqual(result["trashed"], [])
        self.assertEqual(result["errors"], [{"id": master_id, "reason": "injected trash failure"}])
        master = await self._image_row(master_id)
        copy = await self._image_row(copy_id)
        self.assertEqual(master["status"], "kept")
        self.assertEqual(copy["status"], "kept")
        self.assertTrue(os.path.exists(filepath))

    @pytest.mark.contract
    async def test_master_prepare_failure_leaves_virtual_copy_family_untouched(self):
        # If the master can't be prepared for trash (missing source path), its
        # auto-expanded virtual copies must NOT be committed to trash alone —
        # that would split the family and leave a purgeable VC over live bytes.
        master_id, copy_id, filepath = await self._master_with_copy()
        conn = await db.get_db()
        await conn.execute("UPDATE images SET filepath = '' WHERE id = ?", (master_id,))
        await conn.commit()

        result = await trash_service.trash_images(db.DB_PATH, [master_id])

        self.assertEqual(result["trashed"], [])
        master = await self._image_row(master_id)
        copy = await self._image_row(copy_id)
        self.assertEqual(master["status"], "kept")
        self.assertEqual(copy["status"], "kept")
        self.assertTrue(os.path.exists(filepath))

    async def test_trashing_a_virtual_copy_keeps_the_master_file(self):
        master_id, copy_id, filepath = await self._master_with_copy()
        result = await trash_service.trash_images(db.DB_PATH, [copy_id])
        self.assertEqual(result["errors"], [])
        self.assertIn(copy_id, result["trashed"])
        self.assertNotIn(master_id, result["trashed"])
        self.assertTrue(os.path.exists(filepath))
        conn = await db.get_db()
        row = await (await conn.execute(
            "SELECT status FROM images WHERE id = ?", (master_id,)
        )).fetchone()
        self.assertEqual(row["status"], "kept")

    async def test_restoring_a_virtual_copy_restores_its_master_and_file(self):
        master_id, copy_id, filepath = await self._master_with_copy()
        await trash_service.trash_images(db.DB_PATH, [master_id])
        self.assertFalse(os.path.exists(filepath))
        result = await trash_service.restore_images(db.DB_PATH, [copy_id])
        self.assertIn(copy_id, result["restored"])
        self.assertIn(master_id, result["restored"])
        self.assertEqual(result["warnings"], [])
        self.assertTrue(os.path.exists(filepath))

    async def test_restoring_master_restores_its_virtual_copies(self):
        master_id, copy_id, filepath = await self._master_with_copy()
        await trash_service.trash_images(db.DB_PATH, [master_id])

        result = await trash_service.restore_images(db.DB_PATH, [master_id])

        self.assertEqual(set(result["restored"]), {master_id, copy_id})
        self.assertEqual(result["errors"], [])
        self.assertTrue(os.path.exists(filepath))
        conn = await db.get_db()
        try:
            rows = await (await conn.execute(
                "SELECT id, status FROM images WHERE id IN (?, ?)",
                (master_id, copy_id),
            )).fetchall()
        finally:
            await conn.close()
        self.assertEqual({int(row["id"]): row["status"] for row in rows}, {
            master_id: "kept",
            copy_id: "kept",
        })

    @pytest.mark.contract
    async def test_master_restore_failure_reverts_entire_virtual_copy_family(self):
        master_id, copy_id, _filepath = await self._master_with_copy()
        await trash_service.trash_images(db.DB_PATH, [master_id])
        trashed_master = await self._image_row(master_id)
        observed_statuses = []

        def fail_after_family_commit(_trash_path, _filepath):
            conn = sqlite3.connect(db.DB_PATH)
            try:
                observed_statuses.append(dict(conn.execute(
                    "SELECT id, status FROM images WHERE id IN (?, ?)",
                    (master_id, copy_id),
                ).fetchall()))
            finally:
                conn.close()
            return None, "injected restore failure"

        with patch.object(trash_service, "_restore_from_trash", fail_after_family_commit):
            result = await trash_service.restore_images(db.DB_PATH, [master_id])

        self.assertEqual(observed_statuses, [{master_id: "kept", copy_id: "kept"}])
        self.assertEqual(result["restored"], [])
        self.assertEqual(result["errors"], [{"id": master_id, "reason": "injected restore failure"}])
        master = await self._image_row(master_id)
        copy = await self._image_row(copy_id)
        self.assertEqual(master["status"], "trashed")
        self.assertEqual(copy["status"], "trashed")
        self.assertEqual(master["trash_path"], trashed_master["trash_path"])
        self.assertIsNone(copy["trash_path"])

    async def test_empty_trash_purges_family_without_double_delete(self):
        master_id, copy_id, filepath = await self._master_with_copy()
        await trash_service.trash_images(db.DB_PATH, [master_id])
        result = await trash_service.empty_trash(db.DB_PATH, image_ids=[master_id, copy_id])
        self.assertEqual(result["errors"], [])
        conn = await db.get_db()
        count = await (await conn.execute(
            "SELECT COUNT(*) AS n FROM images WHERE id IN (?, ?)",
            (master_id, copy_id),
        )).fetchone()
        self.assertEqual(int(count["n"]), 0)
        self.assertFalse(os.path.exists(filepath))

    async def test_trash_listing_counts_edited_copies_for_the_empty_warning(self):
        master_id, copy_id, _filepath = await self._master_with_copy()
        conn = await db.get_db()
        try:
            await conn.execute(
                "INSERT INTO develop_history (image_id, settings, label, created_at) "
                "VALUES (?, '{}', 'Virtual Copy', 'now')",
                (copy_id,),
            )
            await conn.commit()
        finally:
            await conn.close()

        await trash_service.trash_images(db.DB_PATH, [master_id])
        listing = await trash_service.list_trash(db.DB_PATH)
        self.assertEqual(listing["edited_copy_count"], 0)

        conn = await db.get_db()
        try:
            await conn.execute(
                "INSERT INTO develop_history (image_id, settings, label, created_at) "
                "VALUES (?, '{\"Exposure2012\":1.0}', 'Exposure', 'now')",
                (copy_id,),
            )
            await conn.commit()
        finally:
            await conn.close()
        listing = await trash_service.list_trash(db.DB_PATH)
        self.assertEqual(listing["edited_copy_count"], 1)

