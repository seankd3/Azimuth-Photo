import asyncio
import gzip
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

import db
from features.develop import rawproc
from features.sync import hashing, hub, hub_routes


class SyncHubTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.db_path = str(self.root / "hub.db")
        self.intake = self.root / "_intake"
        self.raws = self.root / "RAWS"
        self.cache = self.root / "base-cache"
        self.old_db_path = db.DB_PATH
        self.old_base_cache_dir = rawproc.BASE_CACHE_DIR
        db.DB_PATH = self.db_path
        rawproc.BASE_CACHE_DIR = self.cache
        asyncio.run(db.init_db())
        hub_routes.configure(
            db_path=lambda: self.db_path,
            intake_root=lambda: self.intake,
            raws_root=lambda: self.raws,
        )
        api = FastAPI()
        api.include_router(hub_routes.router)
        self.client_context = TestClient(api)
        self.client = self.client_context.__enter__()

    def tearDown(self):
        self.client_context.__exit__(None, None, None)
        db.DB_PATH = self.old_db_path
        rawproc.BASE_CACHE_DIR = self.old_base_cache_dir
        self.tempdir.cleanup()

    def image_bytes(self, name: str, color: tuple[int, int, int]) -> bytes:
        path = self.root / name
        Image.new("RGB", (4, 3), color).save(path, format="JPEG")
        payload = path.read_bytes()
        path.unlink()
        return payload

    def digest(self, payload: bytes) -> str:
        digest = hashlib.blake2b(digest_size=16)
        digest.update(payload[: hashing.HASH_PREFIX_BYTES])
        digest.update(len(payload).to_bytes(8, "little"))
        return digest.hexdigest()

    def full_digest(self, payload: bytes) -> str:
        return hashlib.blake2b(payload, digest_size=16).hexdigest()

    def declare(self, filename: str, payload: bytes, *, date_taken: str = "2024-06-07") -> str:
        content_hash = self.digest(payload)
        response = self.client.post(
            "/api/sync/manifest",
            json={"items": [{
                "content_hash": content_hash,
                "full_hash": self.full_digest(payload),
                "bytes": len(payload),
                "filename": filename,
                "date_taken": date_taken,
            }]},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {"missing": [content_hash], "known": []})
        return content_hash

    def upload(self, content_hash: str, payload: bytes, *, split: int | None = None) -> int:
        split = split or len(payload)
        first = self.client.post(
            f"/api/sync/upload/{content_hash}",
            headers={"X-Offset": "0", "X-Total-Bytes": str(len(payload))},
            content=payload[:split],
        )
        self.assertEqual(first.status_code, 200, first.text)
        if split < len(payload):
            self.assertEqual(first.json(), {"offset": split})
            status = self.client.get(f"/api/sync/upload/{content_hash}/status")
            self.assertEqual(status.json(), {"offset": split})
            second = self.client.post(
                f"/api/sync/upload/{content_hash}",
                headers={"X-Offset": str(split), "X-Total-Bytes": str(len(payload))},
                content=payload[split:],
            )
            self.assertEqual(second.status_code, 200, second.text)
            return int(second.json()["image_id"])
        return int(first.json()["image_id"])

    def test_content_hash_uses_prefix_and_little_endian_size(self):
        path = self.root / "large.bin"
        payload = b"a" * hashing.HASH_PREFIX_BYTES + b"ignored-tail"
        path.write_bytes(payload)
        expected = hashlib.blake2b(
            payload[: hashing.HASH_PREFIX_BYTES] + len(payload).to_bytes(8, "little"),
            digest_size=16,
        ).hexdigest()
        self.assertEqual(hashing.compute_content_hash(path), expected)

    def test_manifest_resumable_upload_hash_verification_and_idempotency(self):
        payload = self.image_bytes("field.jpg", (12, 34, 56))
        content_hash = self.declare("field.jpg", payload)
        image_id = self.upload(content_hash, payload, split=len(payload) // 2)
        destination = self.raws / "2024" / "2024-06-07" / "field.jpg"
        self.assertEqual(destination.read_bytes(), payload)

        manifest = self.client.post(
            "/api/sync/manifest",
            json={"items": [{"content_hash": content_hash, "bytes": len(payload), "filename": "elsewhere.jpg"}]},
        )
        self.assertEqual(
            manifest.json(),
            {"missing": [], "known": [{"content_hash": content_hash, "image_id": image_id}]},
        )
        repeat = self.client.post(
            f"/api/sync/upload/{content_hash}",
            headers={"X-Offset": "0", "X-Total-Bytes": str(len(payload))},
            content=payload,
        )
        self.assertEqual(repeat.json(), {"image_id": image_id})

    def test_upload_rejects_wrong_hash_and_resets_resume_offset(self):
        payload = self.image_bytes("bad.jpg", (1, 2, 3))
        content_hash = "0" * 32
        response = self.client.post(
            "/api/sync/manifest",
            json={"items": [{
                "content_hash": content_hash,
                "full_hash": self.full_digest(payload),
                "bytes": len(payload),
                "filename": "bad.jpg",
            }]},
        )
        self.assertEqual(response.status_code, 200)
        response = self.client.post(
            f"/api/sync/upload/{content_hash}",
            headers={"X-Offset": "0", "X-Total-Bytes": str(len(payload))},
            content=payload,
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.client.get(f"/api/sync/upload/{content_hash}/status").json(), {"offset": 0})

    def test_upload_rejects_wrong_full_hash_and_resets_resume_offset(self):
        payload = self.image_bytes("bad-full.jpg", (3, 2, 1))
        content_hash = self.digest(payload)
        response = self.client.post(
            "/api/sync/manifest",
            json={"items": [{
                "content_hash": content_hash,
                "full_hash": "f" * 32,
                "bytes": len(payload),
                "filename": "bad-full.jpg",
            }]},
        )
        self.assertEqual(response.status_code, 200)
        response = self.client.post(
            f"/api/sync/upload/{content_hash}",
            headers={"X-Offset": "0", "X-Total-Bytes": str(len(payload))},
            content=payload,
        )
        self.assertEqual(response.status_code, 422)
        self.assertIn("full-file hash", response.text)
        self.assertEqual(self.client.get(f"/api/sync/upload/{content_hash}/status").json(), {"offset": 0})

    def test_resume_truncates_bytes_beyond_the_fsynced_offset(self):
        payload = self.image_bytes("resume.jpg", (7, 8, 9))
        content_hash = self.declare("resume.jpg", payload)
        split = len(payload) // 2
        response = self.client.post(
            f"/api/sync/upload/{content_hash}",
            headers={"X-Offset": "0", "X-Total-Bytes": str(len(payload))},
            content=payload[:split],
        )
        self.assertEqual(response.json(), {"offset": split})
        with hub.upload_part_path(self.intake, content_hash).open("ab") as handle:
            handle.write(b"torn-tail")
        self.assertEqual(
            self.client.get(f"/api/sync/upload/{content_hash}/status").json(),
            {"offset": split},
        )
        self.assertEqual(hub.upload_part_path(self.intake, content_hash).stat().st_size, split)

    def test_finalize_retry_keeps_part_until_registration_succeeds(self):
        payload = self.image_bytes("retry.jpg", (14, 15, 16))
        content_hash = self.declare("retry.jpg", payload)

        async def retry_scenario():
            with mock.patch.object(
                hub,
                "_register_original",
                new=mock.AsyncMock(side_effect=RuntimeError("registration unavailable")),
            ):
                with self.assertRaisesRegex(RuntimeError, "registration unavailable"):
                    await hub.append_upload_chunk(
                        self.db_path, self.intake, self.raws, content_hash,
                        offset=0, total_bytes=len(payload), chunk=payload,
                    )
            self.assertTrue(hub.upload_part_path(self.intake, content_hash).exists())
            return await hub.append_upload_chunk(
                self.db_path, self.intake, self.raws, content_hash,
                offset=len(payload), total_bytes=len(payload), chunk=b"",
            )

        result = asyncio.run(retry_scenario())
        self.assertIn("image_id", result)
        self.assertFalse(hub.upload_part_path(self.intake, content_hash).exists())

    def test_metadata_lww_keyword_union_and_base_stream(self):
        payload = self.image_bytes("metadata.jpg", (80, 90, 100))
        content_hash = self.declare("metadata.jpg", payload)
        image_id = self.upload(content_hash, payload)
        first = {
            "content_hash": content_hash,
            "flag": "picked",
            "flag_updated_at": "2026-07-11T12:00:00Z",
            "rating": 5,
            "rating_updated_at": "2026-07-11T12:00:00Z",
            "develop_settings": {"Exposure2012": 1.25},
            "develop_updated_at": "2026-07-11T12:00:00Z",
            "keywords": ["Travel > Field", "Favorites"],
            "iptc": {"title": "Field photo", "updated_at": "2026-07-11T12:00:00Z"},
        }
        response = self.client.post("/api/sync/metadata", json={"items": [first]})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            set(response.json()["items"][0]["applied"]),
            {"flag", "rating", "develop", "iptc", "keywords"},
        )

        stale = dict(first, flag="rejected", keywords=["New additive"])
        response = self.client.post("/api/sync/metadata", json={"items": [stale]})
        result = response.json()["items"][0]
        self.assertIn({"family": "flag", "reason": "hub-newer-or-equal"}, result["skipped"])
        self.assertIn("keywords", result["applied"])

        conn = sqlite3.connect(self.db_path)
        try:
            flag = conn.execute("SELECT flag FROM images WHERE id = ?", (image_id,)).fetchone()[0]
            settings = json.loads(conn.execute(
                "SELECT settings FROM develop_settings WHERE image_id = ?", (image_id,)
            ).fetchone()[0])
            keyword_count = conn.execute(
                "SELECT COUNT(*) FROM image_keywords WHERE image_id = ?", (image_id,)
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(flag, "picked")
        self.assertEqual(settings["Exposure2012"], 1.25)
        self.assertEqual(settings["_lr_rating"], 5)
        self.assertEqual(keyword_count, 3)

        base = self.client.get(f"/api/sync/base/{content_hash}")
        self.assertEqual(base.status_code, 200, base.text)
        self.assertTrue(base.headers["content-type"].startswith("multipart/mixed"))
        self.assertIn(b"PABASE1", gzip.decompress(rawproc.base_paths(image_id).binary.read_bytes()))
        self.assertIn(b'filename="base.json"', base.content)

    def test_hash_backfill_batch_and_endpoint(self):
        path = self.root / "legacy.jpg"
        path.write_bytes(self.image_bytes("source.jpg", (4, 5, 6)))
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("INSERT INTO images(filename, filepath) VALUES (?, ?)", (path.name, str(path)))
            conn.commit()
        finally:
            conn.close()
        cursor, counts = asyncio.run(hub.hash_backfill_batch(self.db_path))
        self.assertGreater(cursor, 0)
        self.assertEqual(counts, {"hashed": 1, "missing": 0})
        response = self.client.post("/api/sync/hash-backfill")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["started"])
        deadline = time.monotonic() + 2
        while hub_routes._backfill_status["state"] not in {"complete", "error"} and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(hub_routes._backfill_status["state"], "complete")

    def test_field_spec_three_file_round_trip_is_noop_on_second_manifest(self):
        records = []
        for index, color in enumerate(((20, 30, 40), (50, 60, 70), (80, 90, 100)), start=1):
            payload = self.image_bytes(f"satellite-{index}.jpg", color)
            records.append({
                "content_hash": self.digest(payload),
                "full_hash": self.full_digest(payload),
                "bytes": len(payload),
                "filename": f"satellite-{index}.jpg",
                "date_taken": "2025-01-02",
                "payload": payload,
            })
        request_items = [{key: value for key, value in record.items() if key != "payload"} for record in records]
        response = self.client.post("/api/sync/manifest", json={"items": request_items})
        self.assertEqual(response.json()["missing"], [record["content_hash"] for record in records])
        for record in records:
            self.upload(record["content_hash"], record["payload"])
        response = self.client.post(
            "/api/sync/metadata",
            json={"items": [{
                "content_hash": record["content_hash"],
                "flag": "picked",
                "flag_updated_at": "2026-07-11T10:00:00Z",
                "develop_settings": {"Exposure2012": index / 10},
                "develop_updated_at": "2026-07-11T10:00:00Z",
                "keywords": ["Field > Synced"],
            } for index, record in enumerate(records, start=1)]},
        )
        self.assertEqual(response.status_code, 200, response.text)
        second = self.client.post("/api/sync/manifest", json={"items": request_items}).json()
        self.assertEqual(second["missing"], [])
        self.assertEqual(len(second["known"]), 3)
        conn = sqlite3.connect(self.db_path)
        try:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM images WHERE content_hash IS NOT NULL").fetchone()[0], 3)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM develop_settings").fetchone()[0], 3)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM image_keywords").fetchone()[0], 3)
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
