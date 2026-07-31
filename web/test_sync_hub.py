import pytest
import asyncio
from datetime import datetime
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
from features.library import keywords
from features.sync import device_auth, hashing, hub, hub_routes


class DefaultRootTests(unittest.TestCase):
    """default_intake_root/default_raws_root must follow the resolved runtime
    layout instead of leaking uploads into /mnt/expansion on isolated or
    standalone installs (regression for AZIMUTH_HOME scratch instances)."""

    CLEARED = ("AZIMUTH_SYNC_INTAKE_DIR", "AZIMUTH_SYNC_RAWS_DIR", "AZIMUTH_SMOKE_MODE")

    def _env(self, **extra):
        env = {key: value for key, value in os.environ.items() if key not in self.CLEARED}
        env.update(extra)
        return mock.patch.dict(os.environ, env, clear=True)

    def test_azimuth_home_scopes_intake_and_raws(self):
        with tempfile.TemporaryDirectory() as home:
            with self._env(AZIMUTH_HOME=home):
                intake = hub.default_intake_root()
                raws = hub.default_raws_root()
        self.assertEqual(intake, Path(home) / "data" / "photos" / "_intake")
        self.assertEqual(raws, Path(home) / "data" / "photos" / "Raws")

    def test_default_raws_root_keeps_unrenamed_legacy_tree(self):
        with tempfile.TemporaryDirectory() as home:
            legacy = Path(home) / "data" / "photos" / "RAWS"
            legacy.mkdir(parents=True)
            with self._env(AZIMUTH_HOME=home):
                raws = hub.default_raws_root()
        self.assertEqual(raws, legacy)

    def test_env_overrides_beat_layout(self):
        with tempfile.TemporaryDirectory() as home:
            with self._env(
                AZIMUTH_HOME=home,
                AZIMUTH_SYNC_INTAKE_DIR=f"{home}/custom-intake",
                AZIMUTH_SYNC_RAWS_DIR=f"{home}/custom-raws",
            ):
                self.assertEqual(hub.default_intake_root(), Path(home) / "custom-intake")
                self.assertEqual(hub.default_raws_root(), Path(home) / "custom-raws")


class SyncHubTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.db_path = str(self.root / "hub.db")
        self.intake = self.root / "_intake"
        self.raws = self.root / "Raws"
        self.cache = self.root / "base-cache"
        self.old_db_path = db.DB_PATH
        self.old_base_cache_dir = rawproc.BASE_CACHE_DIR
        db.DB_PATH = self.db_path
        rawproc.BASE_CACHE_DIR = self.cache
        asyncio.run(db.init_db())
        self.auth_patch = mock.patch.object(
            device_auth, "require_device_token_enabled", return_value=False
        )
        self.auth_patch.start()
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
        self.auth_patch.stop()
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
        original = self.client.get(f"/api/sync/original/{image_id}")
        self.assertEqual(original.status_code, 200, original.text)
        self.assertEqual(original.content, payload)

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

    @pytest.mark.contract
    def test_manifest_known_requires_live_original(self):
        """Trashed, missing, or mirror rows must not count as backed up —
        Free-up-space on the phone deletes local copies of "known" hashes."""
        payload = self.image_bytes("liveness.jpg", (9, 8, 7))
        content_hash = self.declare("liveness.jpg", payload)
        self.upload(content_hash, payload)

        def manifest_known():
            response = self.client.post(
                "/api/sync/manifest",
                json={"items": [{"content_hash": content_hash, "bytes": len(payload), "filename": "liveness.jpg"}]},
            )
            self.assertEqual(response.status_code, 200, response.text)
            return {row["content_hash"] for row in response.json()["known"]}

        def set_row(sql: str):
            conn = sqlite3.connect(self.db_path)
            conn.execute(sql, (content_hash,))
            conn.commit()
            conn.close()

        self.assertEqual(manifest_known(), {content_hash})

        set_row("UPDATE images SET status = 'trashed' WHERE content_hash = ?")
        self.assertEqual(manifest_known(), set(), "trashed row must not count as backed up")

        set_row("UPDATE images SET status = 'kept', missing_at = 123.0 WHERE content_hash = ?")
        self.assertEqual(manifest_known(), set(), "missing file must not count as backed up")

        set_row("UPDATE images SET missing_at = NULL, hub_remote = 1 WHERE content_hash = ?")
        self.assertEqual(manifest_known(), set(), "satellite mirror row must not count as backed up")

        set_row("UPDATE images SET hub_remote = 0 WHERE content_hash = ?")
        self.assertEqual(manifest_known(), {content_hash})

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

    def test_metadata_rating_preserves_develop_edit_interleaved_with_upsert(self):
        payload = self.image_bytes("rating-race.jpg", (40, 50, 60))
        content_hash = self.declare("rating-race.jpg", payload)
        image_id = self.upload(content_hash, payload)
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                "INSERT INTO develop_settings(image_id, settings, origin, updated_at) VALUES (?, ?, 'user', 'before')",
                (image_id, json.dumps({"Exposure2012": 0.0})),
            )
            conn.execute(
                "CREATE TRIGGER interleave_develop_before_rating BEFORE INSERT ON develop_settings "
                f"WHEN NEW.image_id = {image_id} BEGIN "
                "UPDATE develop_settings SET settings = json_set(settings, '$.Exposure2012', 2.25) "
                "WHERE image_id = NEW.image_id; END"
            )
            conn.commit()
        finally:
            conn.close()

        response = self.client.post("/api/sync/metadata", json={"items": [{
            "content_hash": content_hash,
            "rating": 5,
            "rating_updated_at": "2026-07-16T01:00:00Z",
        }]})

        self.assertEqual(response.status_code, 200, response.text)
        conn = sqlite3.connect(self.db_path)
        try:
            settings = json.loads(conn.execute(
                "SELECT settings FROM develop_settings WHERE image_id = ?", (image_id,)
            ).fetchone()[0])
        finally:
            conn.close()
        self.assertEqual(settings, {"Exposure2012": 2.25, "_lr_rating": 5})

    def test_rating_clock_does_not_block_older_develop_family(self):
        payload = self.image_bytes("rating-develop-order.jpg", (25, 35, 45))
        content_hash = self.declare("rating-develop-order.jpg", payload)
        image_id = self.upload(content_hash, payload)
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                "INSERT INTO develop_settings(image_id, settings, origin, updated_at) VALUES (?, ?, 'sync', ?)",
                (image_id, json.dumps({"Exposure2012": 0.25}), "2026-07-16T00:00:00Z"),
            )
            conn.commit()
        finally:
            conn.close()

        rating_at = "2026-07-16T03:00:00Z"
        rating = self.client.post("/api/sync/metadata", json={"items": [{
            "content_hash": content_hash,
            "rating": 4,
            "rating_updated_at": rating_at,
        }]})
        self.assertEqual(rating.status_code, 200, rating.text)

        conn = sqlite3.connect(self.db_path)
        try:
            updated_at = conn.execute(
                "SELECT updated_at FROM develop_settings WHERE image_id = ?", (image_id,)
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(updated_at, "2026-07-16T00:00:00Z")

        older = self.client.post("/api/sync/metadata", json={"items": [{
            "content_hash": content_hash,
            "develop_settings": {"Exposure2012": 1.0},
            "develop_updated_at": "2026-07-16T02:00:00Z",
        }]})
        self.assertEqual(older.status_code, 200, older.text)
        self.assertIn("develop", older.json()["items"][0]["applied"])

        conn = sqlite3.connect(self.db_path)
        try:
            settings = json.loads(conn.execute(
                "SELECT settings FROM develop_settings WHERE image_id = ?", (image_id,)
            ).fetchone()[0])
            family_clocks = dict(conn.execute(
                "SELECT family, ts FROM oplog_family_state WHERE content_hash = ?",
                (content_hash,),
            ))
        finally:
            conn.close()
        self.assertEqual(settings, {"Exposure2012": 1.0, "_lr_rating": 4})
        self.assertEqual(family_clocks["rating"], datetime.fromisoformat(rating_at.replace("Z", "+00:00")).timestamp())
        self.assertEqual(family_clocks["develop"], datetime.fromisoformat("2026-07-16T02:00:00+00:00").timestamp())

        newer = self.client.post("/api/sync/metadata", json={"items": [{
            "content_hash": content_hash,
            "develop_settings": {"Exposure2012": 1.5},
            "develop_updated_at": "2026-07-16T04:00:00Z",
        }]})
        self.assertEqual(newer.status_code, 200, newer.text)
        conn = sqlite3.connect(self.db_path)
        try:
            settings = json.loads(conn.execute(
                "SELECT settings FROM develop_settings WHERE image_id = ?", (image_id,)
            ).fetchone()[0])
        finally:
            conn.close()
        self.assertEqual(settings, {"Exposure2012": 1.5, "_lr_rating": 4})

    def test_older_sync_does_not_rewind_imported_develop_without_family_clock(self):
        payload = self.image_bytes("lrcat-develop-order.jpg", (35, 45, 55))
        content_hash = self.declare("lrcat-develop-order.jpg", payload)
        image_id = self.upload(content_hash, payload)
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                "INSERT INTO develop_settings(image_id, settings, origin, updated_at) "
                "VALUES (?, ?, 'lrcat', ?)",
                (
                    image_id,
                    json.dumps({"Exposure2012": 2.0}),
                    "2026-07-16T05:00:00Z",
                ),
            )
            conn.commit()
        finally:
            conn.close()

        response = self.client.post("/api/sync/metadata", json={"items": [{
            "content_hash": content_hash,
            "develop_settings": {"Exposure2012": -2.0},
            "develop_updated_at": "2026-07-16T04:00:00Z",
        }]})

        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()["items"][0]
        self.assertIn(
            {"family": "develop", "reason": "hub-newer-or-equal"},
            result["skipped"],
        )
        conn = sqlite3.connect(self.db_path)
        try:
            row = conn.execute(
                "SELECT settings, origin, updated_at FROM develop_settings WHERE image_id = ?",
                (image_id,),
            ).fetchone()
            family_clock = conn.execute(
                "SELECT ts FROM oplog_family_state "
                "WHERE content_hash = ? AND family = 'develop'",
                (content_hash,),
            ).fetchone()
        finally:
            conn.close()
        self.assertEqual(json.loads(row[0]), {"Exposure2012": 2.0})
        self.assertEqual(row[1:], ("lrcat", "2026-07-16T05:00:00Z"))
        self.assertIsNone(family_clock)

    def test_older_sync_does_not_rewind_imported_iptc_without_family_clock(self):
        payload = self.image_bytes("lrcat-iptc-order.jpg", (45, 55, 65))
        content_hash = self.declare("lrcat-iptc-order.jpg", payload)
        image_id = self.upload(content_hash, payload)
        asyncio.run(keywords.ensure_schema())
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                "INSERT INTO iptc_fields(image_id, title, caption, copyright, creator, updated_at) "
                "VALUES (?, 'Imported title', '', '', '', '2026-07-16T05:00:00Z')",
                (image_id,),
            )
            conn.commit()
        finally:
            conn.close()

        response = self.client.post("/api/sync/metadata", json={"items": [{
            "content_hash": content_hash,
            "iptc": {"title": "Older title", "updated_at": "2026-07-16T04:00:00Z"},
        }]})

        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn(
            {"family": "iptc", "reason": "hub-newer-or-equal"},
            response.json()["items"][0]["skipped"],
        )
        conn = sqlite3.connect(self.db_path)
        try:
            row = conn.execute(
                "SELECT title, updated_at FROM iptc_fields WHERE image_id = ?",
                (image_id,),
            ).fetchone()
        finally:
            conn.close()
        self.assertEqual(row, ("Imported title", "2026-07-16T05:00:00Z"))

    @pytest.mark.contract
    def test_first_synced_rating_seeds_a_neutral_develop_clock(self):
        payload = self.image_bytes("first-rating.jpg", (55, 65, 75))
        content_hash = self.declare("first-rating.jpg", payload)
        image_id = self.upload(content_hash, payload)

        response = self.client.post("/api/sync/metadata", json={"items": [{
            "content_hash": content_hash,
            "rating": 5,
            "rating_updated_at": "2026-07-16T03:00:00Z",
        }]})

        self.assertEqual(response.status_code, 200, response.text)
        conn = sqlite3.connect(self.db_path)
        try:
            row = conn.execute(
                "SELECT settings, updated_at FROM develop_settings WHERE image_id = ?",
                (image_id,),
            ).fetchone()
        finally:
            conn.close()
        self.assertEqual(json.loads(row[0]), {"_lr_rating": 5})
        self.assertEqual(row[1], "")

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

    def test_upload_routes_into_named_folder(self):
        payload = self.image_bytes("personal.jpg", (11, 22, 33))
        content_hash = self.digest(payload)
        response = self.client.post(
            "/api/sync/manifest",
            json={"items": [{
                "content_hash": content_hash,
                "full_hash": self.full_digest(payload),
                "bytes": len(payload),
                "filename": "personal.jpg",
                "date_taken": "2024-06-07",
                "folder": "Personal Photos",
            }]},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.upload(content_hash, payload)
        # The legacy "Personal Photos" hint routes to Snapshots, a sibling of Raws.
        destination = self.root / "Snapshots" / "2024" / "2024-06-07" / "personal.jpg"
        self.assertEqual(destination.read_bytes(), payload)
        self.assertFalse((self.root / "Personal Photos").exists())
        self.assertFalse((self.raws / "Snapshots" / "2024" / "2024-06-07" / "personal.jpg").exists())
        self.assertFalse((self.raws / "2024" / "2024-06-07" / "personal.jpg").exists())

    def test_manifest_rejects_invalid_folder_paths(self):
        payload = self.image_bytes("evil.jpg", (9, 8, 7))
        content_hash = self.digest(payload)
        base = {
            "content_hash": content_hash,
            "full_hash": self.full_digest(payload),
            "bytes": len(payload),
            "filename": "evil.jpg",
            "date_taken": "2024-06-07",
        }
        for folder in ("../evil", "a/b"):
            response = self.client.post(
                "/api/sync/manifest",
                json={"items": [{**base, "folder": folder}]},
            )
            self.assertEqual(response.status_code, 400, response.text)

    def test_legacy_manifest_without_folder_uses_date_tree(self):
        payload = self.image_bytes("legacy.jpg", (44, 55, 66))
        content_hash = self.declare("legacy.jpg", payload)
        self.upload(content_hash, payload)
        destination = self.raws / "2024" / "2024-06-07" / "legacy.jpg"
        self.assertEqual(destination.read_bytes(), payload)

    def test_finalize_rejects_corrupt_bytes_then_accepts_good_retry(self):
        """P0-A: declared hashes must match received bytes before any placement."""

        good = b"GOOD-BYTES-" + (b"A" * 4096)
        corrupt = b"BAD!-BYTES-" + (b"B" * 4096)
        self.assertEqual(len(good), len(corrupt))
        content_hash = self.digest(good)
        response = self.client.post(
            "/api/sync/manifest",
            json={"items": [{
                "content_hash": content_hash,
                "full_hash": self.full_digest(good),
                "bytes": len(good),
                "filename": "000018240006.tif",
                "date_taken": "2026-07-16",
            }]},
        )
        self.assertEqual(response.status_code, 200, response.text)

        failed = self.client.post(
            f"/api/sync/upload/{content_hash}",
            headers={"X-Offset": "0", "X-Total-Bytes": str(len(corrupt))},
            content=corrupt,
        )
        self.assertEqual(failed.status_code, 422, failed.text)
        self.assertFalse(hub.upload_part_path(self.intake, content_hash).exists())
        placed = list(self.root.rglob("000018240006.tif"))
        self.assertEqual(placed, [])
        conn = sqlite3.connect(self.db_path)
        try:
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM images WHERE content_hash = ?", (content_hash,)
                ).fetchone()[0],
                0,
            )
        finally:
            conn.close()

        image_id = self.upload(content_hash, good)
        destination = self.raws / "Film Scans" / "2026" / "2026-07-16" / "000018240006.tif"
        self.assertTrue(destination.is_file())
        self.assertEqual(destination.read_bytes(), good)
        self.assertGreater(image_id, 0)

    def test_placement_is_persisted_and_collision_safe_across_retries(self):
        """P0-B/S4: first placement sticks; identical is idempotent; different gets a suffix."""

        payload = b"ROLL-ORIGINAL-" + (b"C" * 2048)
        content_hash = self.digest(payload)
        first = self.client.post(
            "/api/sync/manifest",
            json={"items": [{
                "content_hash": content_hash,
                "full_hash": self.full_digest(payload),
                "bytes": len(payload),
                "filename": "roll.jpg",
                "date_taken": "2026-07-16",
                "folder": "Film Scans",
            }]},
        )
        self.assertEqual(first.status_code, 200, first.text)
        conn = sqlite3.connect(self.db_path)
        try:
            locked = conn.execute(
                "SELECT placed_relpath FROM sync_manifest_items WHERE content_hash = ?",
                (content_hash,),
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(locked, "Raws/Film Scans/2026/2026-07-16/roll.jpg")

        # A later manifest with a different date must not move the locked path.
        second = self.client.post(
            "/api/sync/manifest",
            json={"items": [{
                "content_hash": content_hash,
                "full_hash": self.full_digest(payload),
                "bytes": len(payload),
                "filename": "roll.jpg",
                "date_taken": "2026-01-01",
                "folder": "Film Scans",
            }]},
        )
        self.assertEqual(second.status_code, 200, second.text)
        conn = sqlite3.connect(self.db_path)
        try:
            still = conn.execute(
                "SELECT placed_relpath FROM sync_manifest_items WHERE content_hash = ?",
                (content_hash,),
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(still, locked)

        destination = self.root / Path(locked)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(payload)
        image_id = self.upload(content_hash, payload)
        self.assertTrue(destination.is_file())
        self.assertEqual(destination.read_bytes(), payload)
        self.assertFalse((self.raws / "Film Scans" / "2026" / "2026-01-01" / "roll.jpg").exists())
        self.assertFalse((self.raws / "Film Scans" / "2026" / "2026-07-16" / "roll-2.jpg").exists())

        # Different bytes already at the locked path → per-identity suffix, no overwrite.
        other = b"ROLL-COLLISION-" + (b"D" * 2047)
        self.assertEqual(len(other), len(payload))
        other_hash = self.digest(other)
        declare_other = self.client.post(
            "/api/sync/manifest",
            json={"items": [{
                "content_hash": other_hash,
                "full_hash": self.full_digest(other),
                "bytes": len(other),
                "filename": "collision.jpg",
                "date_taken": "2026-07-16",
                "folder": "Film Scans",
            }]},
        )
        self.assertEqual(declare_other.status_code, 200, declare_other.text)
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                "UPDATE sync_manifest_items SET placed_relpath = ? WHERE content_hash = ?",
                (locked, other_hash),
            )
            conn.commit()
        finally:
            conn.close()
        other_id = self.upload(other_hash, other)
        self.assertEqual(destination.read_bytes(), payload)
        tagged = destination.parent / f"roll-{other_hash[:8]}.jpg"
        self.assertTrue(tagged.is_file())
        self.assertEqual(tagged.read_bytes(), other)
        conn = sqlite3.connect(self.db_path)
        try:
            other_path = conn.execute(
                "SELECT placed_relpath FROM sync_manifest_items WHERE content_hash = ?",
                (other_hash,),
            ).fetchone()[0]
            rows = conn.execute(
                "SELECT id, content_hash FROM images WHERE filepath = ?",
                (str(destination),),
            ).fetchall()
            tagged_rows = conn.execute(
                "SELECT id, content_hash FROM images WHERE filepath = ?",
                (str(tagged),),
            ).fetchall()
        finally:
            conn.close()
        self.assertEqual(other_path, f"Raws/Film Scans/2026/2026-07-16/roll-{other_hash[:8]}.jpg")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], image_id)
        self.assertEqual(rows[0][1], content_hash)
        self.assertEqual(len(tagged_rows), 1)
        self.assertEqual(tagged_rows[0][0], other_id)
        self.assertEqual(tagged_rows[0][1], other_hash)

    def test_concurrent_finalize_same_filename_keeps_both_bytes(self):
        """S4: two identities, one preferred path — both survive at distinct persisted paths."""

        first = b"CONCURRENT-A-" + (b"A" * 4096)
        second = b"CONCURRENT-B-" + (b"B" * 4096)
        self.assertEqual(len(first), len(second))
        first_hash = self.digest(first)
        second_hash = self.digest(second)
        for content_hash, payload, name in (
            (first_hash, first, "shared.jpg"),
            (second_hash, second, "shared.jpg"),
        ):
            response = self.client.post(
                "/api/sync/manifest",
                json={"items": [{
                    "content_hash": content_hash,
                    "full_hash": self.full_digest(payload),
                    "bytes": len(payload),
                    "filename": name,
                    "date_taken": "2026-07-16",
                    "folder": "Film Scans",
                }]},
            )
            self.assertEqual(response.status_code, 200, response.text)

        async def _finalize_both():
            return await asyncio.gather(
                hub.append_upload_chunk(
                    self.db_path,
                    self.intake,
                    self.raws,
                    first_hash,
                    offset=0,
                    total_bytes=len(first),
                    chunk=first,
                ),
                hub.append_upload_chunk(
                    self.db_path,
                    self.intake,
                    self.raws,
                    second_hash,
                    offset=0,
                    total_bytes=len(second),
                    chunk=second,
                ),
            )

        results = asyncio.run(_finalize_both())
        image_ids = {int(row["image_id"]) for row in results}
        self.assertEqual(len(image_ids), 2)

        preferred = self.raws / "Film Scans" / "2026" / "2026-07-16" / "shared.jpg"
        conn = sqlite3.connect(self.db_path)
        try:
            rows = conn.execute(
                "SELECT content_hash, placed_relpath, filepath FROM images "
                "JOIN sync_manifest_items USING (content_hash) "
                "WHERE content_hash IN (?, ?) ORDER BY content_hash",
                (first_hash, second_hash),
            ).fetchall()
        finally:
            conn.close()
        self.assertEqual(len(rows), 2)
        paths = {row[1] for row in rows}
        self.assertEqual(len(paths), 2)
        on_disk = []
        for _content_hash, placed, filepath in rows:
            path = Path(filepath)
            self.assertTrue(path.is_file())
            self.assertEqual(path, self.root / Path(placed))
            on_disk.append(path.read_bytes())
        self.assertEqual(sorted(on_disk), sorted([first, second]))
        self.assertTrue(preferred.is_file())
        self.assertIn(preferred.read_bytes(), (first, second))

    @pytest.mark.contract
    def test_manifest_known_requires_original_bytes_on_disk(self):
        """P1-C: a catalog row without byte proof must report as needed, not known."""

        payload = self.image_bytes("ghost.jpg", (70, 80, 90))
        content_hash = self.declare("ghost.jpg", payload)
        image_id = self.upload(content_hash, payload)
        destination = self.raws / "2024" / "2024-06-07" / "ghost.jpg"
        self.assertTrue(destination.is_file())

        destination.unlink()
        missing = self.client.post(
            "/api/sync/manifest",
            json={"items": [{
                "content_hash": content_hash,
                "full_hash": self.full_digest(payload),
                "bytes": len(payload),
                "filename": "ghost.jpg",
                "date_taken": "2024-06-07",
            }]},
        )
        self.assertEqual(missing.status_code, 200, missing.text)
        self.assertEqual(missing.json(), {"missing": [content_hash], "known": []})

        # Size mismatch is also not proof.
        destination.write_bytes(payload + b"x")
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                "UPDATE images SET missing_at = NULL, file_size = ? WHERE id = ?",
                (len(payload), image_id),
            )
            conn.commit()
        finally:
            conn.close()
        mismatched = self.client.post(
            "/api/sync/manifest",
            json={"items": [{
                "content_hash": content_hash,
                "full_hash": self.full_digest(payload),
                "bytes": len(payload),
                "filename": "ghost.jpg",
                "date_taken": "2024-06-07",
            }]},
        )
        self.assertEqual(mismatched.json(), {"missing": [content_hash], "known": []})

        destination.write_bytes(payload)
        restored = self.client.post(
            "/api/sync/manifest",
            json={"items": [{
                "content_hash": content_hash,
                "full_hash": self.full_digest(payload),
                "bytes": len(payload),
                "filename": "ghost.jpg",
                "date_taken": "2024-06-07",
            }]},
        )
        self.assertEqual(
            restored.json(),
            {"missing": [], "known": [{"content_hash": content_hash, "image_id": image_id}]},
        )

    def test_upload_short_circuit_bypasses_stale_byte_proof_cache(self):
        """S1: single-hash upload known-check must fresh-stat, not trust the TTL cache."""

        payload = self.image_bytes("stale-cache.jpg", (11, 22, 33))
        content_hash = self.declare("stale-cache.jpg", payload)
        image_id = self.upload(content_hash, payload)
        destination = self.raws / "2024" / "2024-06-07" / "stale-cache.jpg"
        self.assertTrue(destination.is_file())

        # Warm the manifest bulk cache with a positive proof.
        warm = self.client.post(
            "/api/sync/manifest",
            json={"items": [{
                "content_hash": content_hash,
                "full_hash": self.full_digest(payload),
                "bytes": len(payload),
                "filename": "stale-cache.jpg",
                "date_taken": "2024-06-07",
            }]},
        )
        self.assertEqual(
            warm.json(),
            {"missing": [], "known": [{"content_hash": content_hash, "image_id": image_id}]},
        )
        self.assertIn(
            (str(destination), len(payload)),
            hub._stat_proof_cache,
        )

        destination.unlink()
        # Upload short-circuit must re-stat and refuse "known" despite the warm cache.
        known = asyncio.run(hub._known_image_id(self.db_path, content_hash))
        self.assertIsNone(known)
        # Manifest bulk path may still report known from cache until TTL — that is
        # intentional; upload is the path that must not skip restoring bytes.

    def test_null_file_size_requires_fresh_nonempty_byte_proof(self):
        """S3: NULL file_size only proves a fresh non-empty file and is never cached."""

        payload = self.image_bytes("null-size.jpg", (44, 55, 66))
        content_hash = self.declare("null-size.jpg", payload)
        image_id = self.upload(content_hash, payload)
        destination = self.raws / "2024" / "2024-06-07" / "null-size.jpg"

        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("UPDATE images SET file_size = NULL WHERE id = ?", (image_id,))
            conn.commit()
        finally:
            conn.close()

        hub._stat_proof_cache.clear()
        self.assertTrue(
            hub._byte_proof_ok(str(destination), str(self.root), None),
        )
        # Null-size proofs must not enter the cache.
        self.assertEqual(hub._stat_proof_cache, {})
        cached = hub._byte_proof_ok_cached(str(destination), str(self.root), None)
        self.assertTrue(cached)
        self.assertEqual(hub._stat_proof_cache, {})

        destination.write_bytes(b"")
        self.assertFalse(hub._byte_proof_ok(str(destination), str(self.root), None))
        self.assertIsNone(asyncio.run(hub._known_image_id(self.db_path, content_hash)))
        missing = self.client.post(
            "/api/sync/manifest",
            json={"items": [{
                "content_hash": content_hash,
                "full_hash": self.full_digest(payload),
                "bytes": len(payload),
                "filename": "null-size.jpg",
                "date_taken": "2024-06-07",
            }]},
        )
        self.assertEqual(missing.json(), {"missing": [content_hash], "known": []})

    def test_manifest_freezes_bytes_and_full_hash_mid_upload(self):
        """S6: re-manifest during an in-flight .part must not rewrite upload-critical fields."""

        payload = b"FREEZE-ORIGINAL-" + (b"E" * 2048)
        content_hash = self.digest(payload)
        full_hash = self.full_digest(payload)
        first = self.client.post(
            "/api/sync/manifest",
            json={"items": [{
                "content_hash": content_hash,
                "full_hash": full_hash,
                "bytes": len(payload),
                "filename": "freeze.jpg",
                "date_taken": "2026-07-16",
            }]},
        )
        self.assertEqual(first.status_code, 200, first.text)

        split = len(payload) // 2
        partial = self.client.post(
            f"/api/sync/upload/{content_hash}",
            headers={"X-Offset": "0", "X-Total-Bytes": str(len(payload))},
            content=payload[:split],
        )
        self.assertEqual(partial.status_code, 200, partial.text)
        self.assertEqual(partial.json(), {"offset": split})
        self.assertTrue(hub.upload_part_path(self.intake, content_hash).exists())

        hostile = self.client.post(
            "/api/sync/manifest",
            json={"items": [{
                "content_hash": content_hash,
                "full_hash": "f" * 32,
                "bytes": len(payload) + 99,
                "filename": "freeze-renamed.jpg",
                "date_taken": "2026-01-01",
            }]},
        )
        self.assertEqual(hostile.status_code, 200, hostile.text)
        conn = sqlite3.connect(self.db_path)
        try:
            row = conn.execute(
                "SELECT full_hash, bytes, filename FROM sync_manifest_items WHERE content_hash = ?",
                (content_hash,),
            ).fetchone()
        finally:
            conn.close()
        self.assertEqual(row[0], full_hash)
        self.assertEqual(row[1], len(payload))
        self.assertEqual(row[2], "freeze-renamed.jpg")

        finish = self.client.post(
            f"/api/sync/upload/{content_hash}",
            headers={"X-Offset": str(split), "X-Total-Bytes": str(len(payload))},
            content=payload[split:],
        )
        self.assertEqual(finish.status_code, 200, finish.text)
        image_id = int(finish.json()["image_id"])
        destination = self.raws / "2026" / "2026-07-16" / "freeze.jpg"
        # placed_relpath was locked on first sighting (freeze.jpg), not the rename.
        self.assertTrue(destination.is_file())
        self.assertEqual(destination.read_bytes(), payload)
        self.assertGreater(image_id, 0)


if __name__ == "__main__":
    unittest.main()
