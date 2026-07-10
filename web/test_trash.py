import os
import sqlite3
from fastapi.testclient import TestClient

from test_support import *  # noqa: F401,F403
from data.repositories import stacks as stack_repository
from features.trash import service as trash_service


class TrashTests(BackendTestCase):
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
