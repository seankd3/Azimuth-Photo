import asyncio
import gzip
import io
import json
from pathlib import Path
import sqlite3
import tarfile
import tempfile
import unittest
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

import db
from features.library import keywords
from features.sync import device_auth, hub_routes, mirror_export


class SyncMirrorExportTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.db_path = str(self.root / "hub.db")
        self.old_db_path = db.DB_PATH
        db.DB_PATH = self.db_path
        asyncio.run(db.init_db())
        self.auth_patch = mock.patch.object(
            device_auth, "require_device_token_enabled", return_value=False
        )
        self.auth_patch.start()
        hub_routes.configure(db_path=lambda: self.db_path)
        api = FastAPI()
        api.include_router(hub_routes.router)
        self.client_context = TestClient(api)
        self.client = self.client_context.__enter__()

    def tearDown(self):
        self.client_context.__exit__(None, None, None)
        self.auth_patch.stop()
        db.DB_PATH = self.old_db_path
        self.tempdir.cleanup()

    def _image(self, filename: str, content_hash: str) -> int:
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                "INSERT OR IGNORE INTO catalog_sources(path, display_name) VALUES (?, ?)",
                (str(self.root), "Hub library"),
            )
            source_id = conn.execute(
                "SELECT id FROM catalog_sources WHERE path = ?", (str(self.root),)
            ).fetchone()[0]
            image_id = conn.execute(
                """
                INSERT INTO images(
                    source_id, filename, filepath, content_hash, file_ext, file_size, date_taken,
                    width, height, orientation, camera_make, camera_model, lens, latitude,
                    longitude, location_source, flag, elo, comparisons
                ) VALUES (?, ?, ?, ?, '.jpg', 42, '2026-07-10', 4000, 3000, 'landscape',
                    'Canon', 'R5', 'RF 50mm', 42.1, -87.8, 'exif', 'picked', 1510, 7)
                """,
                (source_id, filename, str(self.root / filename), content_hash),
            ).lastrowid
            conn.execute(
                "INSERT INTO develop_settings(image_id, settings, origin, updated_at) VALUES (?, ?, 'user', ?)",
                (image_id, '{"Exposure2012":1.25}', "2026-07-10T12:00:00Z"),
            )
            conn.execute(
                "INSERT INTO image_quality(image_id, score, sharpness, subject_sharpness, exposure_clip, motion_blur, eyes_open, scored_at) "
                "VALUES (?, 91, .8, .7, .1, .2, NULL, 'now')",
                (image_id,),
            )
            collection_id = conn.execute(
                "INSERT INTO collections(uuid, name) VALUES (?, 'Summer')",
                (f"collection-{content_hash}",),
            ).lastrowid
            conn.execute("INSERT INTO collection_images(collection_id, image_id) VALUES (?, ?)", (collection_id, image_id))
            stack_id = conn.execute(
                "INSERT INTO stacks(kind, representative_image_id, auto) VALUES ('burst', ?, 1)", (image_id,)
            ).lastrowid
            conn.execute("INSERT INTO stack_members(stack_id, image_id) VALUES (?, ?)", (stack_id, image_id))
            conn.commit()
            return int(image_id)
        finally:
            conn.close()

    def _catalog_lines(self, cursor: int = 0) -> list[dict]:
        response = self.client.get(f"/api/sync/catalog/export?cursor={cursor}")
        self.assertEqual(response.status_code, 200, response.text)
        # httpx transparently decodes Content-Encoding for TestClient responses.
        self.assertEqual(response.headers["content-encoding"], "gzip")
        body = response.content.decode()
        return [json.loads(line) for line in body.splitlines()]

    def test_row_payload_contains_every_v2_catalog_field(self):
        image_id = self._image("full.jpg", "a" * 32)
        asyncio.run(keywords.ensure_schema())
        keyword = asyncio.run(keywords.resolve_keyword_path("Travel > Coast"))
        asyncio.run(keywords.assign_keyword([image_id], int(keyword["id"])))

        payload, terminator = self._catalog_lines()
        self.assertEqual(set(mirror_export.CATALOG_FIELDS), set(payload))
        self.assertEqual(payload["hub_image_id"], image_id)
        self.assertEqual(payload["keywords"], ["Travel > Coast"])
        self.assertEqual(payload["collection_ids"], [1])
        self.assertEqual(payload["stack_kind"], "burst")
        self.assertTrue(payload["stack_is_representative"])
        self.assertEqual(payload["develop_settings"], {"Exposure2012": 1.25})
        self.assertEqual(terminator, {"cursor": payload["row_version"]})

        async def collect() -> bytes:
            return b"".join([chunk async for chunk in mirror_export.gzip_catalog_export_stream(self.db_path, 0)])

        compressed_lines = [json.loads(line) for line in gzip.decompress(asyncio.run(collect())).decode().splitlines()]
        self.assertEqual(compressed_lines[-1], terminator)

    def test_incremental_cursor_exports_only_the_changed_row(self):
        first_id = self._image("first.jpg", "b" * 32)
        second_id = self._image("second.jpg", "c" * 32)
        first_export = self._catalog_lines()
        cursor = first_export[-1]["cursor"]

        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("UPDATE images SET flag = 'rejected' WHERE id = ?", (second_id,))
            conn.commit()
        finally:
            conn.close()

        payload, terminator = self._catalog_lines(cursor)
        self.assertNotEqual(first_id, second_id)
        self.assertEqual([payload["hub_image_id"]], [second_id])
        self.assertEqual(payload["flag"], "rejected")
        self.assertGreater(terminator["cursor"], cursor)

    def test_rating_exports_with_its_own_family_winner(self):
        image_id = self._image("rated.jpg", "f" * 32)
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                "UPDATE develop_settings SET settings = ? WHERE image_id = ?",
                ('{"Exposure2012":1.25,"_lr_rating":5}', image_id),
            )
            conn.execute(
                "INSERT INTO oplog_family_state(content_hash, family, ts, origin, origin_seq) "
                "VALUES (?, 'rating', 300, 'camera-a', 7)",
                ("f" * 32,),
            )
            conn.commit()
        finally:
            conn.close()

        payload, _terminator = self._catalog_lines()

        self.assertEqual(payload["develop_settings"], {"Exposure2012": 1.25})
        self.assertEqual(payload["rating"], 5)
        self.assertEqual(
            payload["rating_winner_key"],
            {"ts": 300.0, "origin": "camera-a", "origin_seq": 7},
        )

    def test_thumb_pack_reads_only_existing_disk_index_entries_and_reports_skips(self):
        first_id = self._image("cached.jpg", "d" * 32)
        second_id = self._image("uncached.jpg", "e" * 32)
        cached = self.root / "cached.jpg"
        cached.write_bytes(b"existing-thumbnail")

        def disk_index(size: str, image_id: int):
            self.assertEqual(size, "sm")
            return ("signature", str(cached)) if image_id == first_id else None

        with mock.patch.object(mirror_export.thumbnails, "fast_disk_path_entry", side_effect=disk_index):
            response = self.client.get("/api/sync/thumbs/pack?size=sm&after_id=0&limit=500")
        self.assertEqual(response.status_code, 200, response.text)
        with tarfile.open(fileobj=io.BytesIO(response.content), mode="r:") as archive:
            self.assertEqual(archive.getnames(), [f"{first_id}.jpg", ".azimuth-trailer.json"])
            self.assertEqual(archive.extractfile(f"{first_id}.jpg").read(), b"existing-thumbnail")
            trailer = archive.extractfile(".azimuth-trailer.json").read()
        self.assertEqual(
            json.loads(trailer),
            {"skipped": [second_id], "after_id": second_id, "order": "asc"},
        )

    def test_thumb_pack_newest_walks_high_ids_first(self):
        low_id = self._image("older.jpg", "f" * 32)
        high_id = self._image("newer.jpg", "a" * 32)
        cached_low = self.root / "older.jpg"
        cached_high = self.root / "newer.jpg"
        cached_low.write_bytes(b"low-thumb")
        cached_high.write_bytes(b"high-thumb")

        def disk_index(size: str, image_id: int):
            self.assertEqual(size, "sm")
            if image_id == high_id:
                return ("signature", str(cached_high))
            if image_id == low_id:
                return ("signature", str(cached_low))
            return None

        with mock.patch.object(mirror_export.thumbnails, "fast_disk_path_entry", side_effect=disk_index):
            first = self.client.get(
                "/api/sync/thumbs/pack?size=sm&after_id=0&limit=1&order=newest"
            )
            self.assertEqual(first.status_code, 200, first.text)
            with tarfile.open(fileobj=io.BytesIO(first.content), mode="r:") as archive:
                self.assertEqual(archive.getnames()[0], f"{high_id}.jpg")
                trailer = json.loads(
                    archive.extractfile(".azimuth-trailer.json").read()
                )
            self.assertEqual(trailer["after_id"], high_id)
            self.assertEqual(trailer["order"], "newest")

            second = self.client.get(
                f"/api/sync/thumbs/pack?size=sm&after_id={high_id}&limit=1&order=newest"
            )
            self.assertEqual(second.status_code, 200, second.text)
            with tarfile.open(fileobj=io.BytesIO(second.content), mode="r:") as archive:
                self.assertEqual(archive.getnames()[0], f"{low_id}.jpg")
                trailer = json.loads(
                    archive.extractfile(".azimuth-trailer.json").read()
                )
            self.assertEqual(trailer["after_id"], low_id)
