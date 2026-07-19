"""LR bridge satellite deltas — origin lr via shared oplog apply path."""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing

from fastapi import FastAPI
from fastapi.testclient import TestClient

import db
from features.sync import elo_stars, export_relation, lr_bridge, lr_routes, oplog


HASH_A = "a" * 32
HASH_B = "b" * 32
HASH_C = "c" * 32


CATALOG_DDL = """
PRAGMA foreign_keys=ON;
CREATE TABLE catalog_sources (
    id INTEGER PRIMARY KEY,
    path TEXT,
    display_name TEXT,
    online INTEGER DEFAULT 1
);
CREATE TABLE images (
    id INTEGER PRIMARY KEY,
    filename TEXT,
    filepath TEXT,
    content_hash TEXT UNIQUE,
    flag TEXT NOT NULL DEFAULT 'unflagged',
    elo REAL DEFAULT 1200,
    comparisons INTEGER DEFAULT 0,
    status TEXT DEFAULT 'kept',
    missing_at REAL,
    file_ext TEXT,
    date_taken TEXT,
    camera_model TEXT,
    source_id INTEGER,
    vc_of INTEGER
);
CREATE TABLE develop_settings (
    image_id INTEGER PRIMARY KEY REFERENCES images(id) ON DELETE CASCADE,
    settings TEXT NOT NULL DEFAULT '{}',
    origin TEXT NOT NULL DEFAULT 'user',
    updated_at TEXT NOT NULL DEFAULT ''
);
CREATE TABLE stacks (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,
    representative_image_id INTEGER,
    auto INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL DEFAULT 0
);
CREATE TABLE stack_members (
    stack_id INTEGER NOT NULL REFERENCES stacks(id) ON DELETE CASCADE,
    image_id INTEGER NOT NULL REFERENCES images(id) ON DELETE CASCADE,
    score REAL NOT NULL DEFAULT 0,
    added_at REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (stack_id, image_id)
);
"""


class LrBridgeTests(unittest.IsolatedAsyncioTestCase):
    def _catalog(self) -> str:
        handle, path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        self.addCleanup(lambda: os.path.exists(path) and os.unlink(path))
        with closing(sqlite3.connect(path)) as conn:
            conn.executescript(CATALOG_DDL)
            conn.execute(
                "INSERT INTO images(id, filename, filepath, content_hash, flag, elo, comparisons, file_ext) "
                "VALUES (1, 'IMG_1.dng', '/photos/IMG_1.dng', ?, 'unflagged', 1600, 10, 'dng')",
                (HASH_A,),
            )
            conn.execute(
                "INSERT INTO images(id, filename, filepath, content_hash, flag, elo, comparisons, file_ext) "
                "VALUES (2, 'IMG_2.dng', '/photos/IMG_2.dng', ?, 'unflagged', 1400, 5, 'dng')",
                (HASH_B,),
            )
            conn.execute(
                "INSERT INTO images(id, filename, filepath, content_hash, flag, elo, comparisons, file_ext) "
                "VALUES (3, 'IMG_3.dng', '/photos/IMG_3.dng', ?, 'unflagged', 1300, 0, 'dng')",
                (HASH_C,),
            )
            conn.commit()
        return path

    async def test_inbound_flag_uses_origin_lr_and_shared_apply(self):
        path = self._catalog()
        result = await lr_bridge.apply_inbound_deltas(
            path,
            [{"filepath": "/photos/IMG_1.dng", "family": "flag", "value": "picked", "observed_at": 100.0}],
        )
        self.assertEqual(result["pending_count"], 0)
        self.assertEqual(result["entries"][0]["origin"], "lr")

        with closing(sqlite3.connect(path)) as conn:
            flag = conn.execute("SELECT flag FROM images WHERE id = 1").fetchone()[0]
            clock = conn.execute(
                "SELECT origin, ts FROM oplog_family_state WHERE content_hash = ? AND family = 'flag'",
                (HASH_A,),
            ).fetchone()
        self.assertEqual(flag, "picked")
        self.assertEqual(clock[0], "lr")
        self.assertEqual(clock[1], 100.0)

    async def test_inbound_lr_rating_does_not_advance_develop_clock(self):
        path = self._catalog()
        with closing(sqlite3.connect(path)) as conn:
            conn.execute(
                "INSERT INTO develop_settings(image_id, settings, origin, updated_at) VALUES (1, ?, 'user', 'before')",
                (json.dumps({"Exposure2012": 0.5}),),
            )
            conn.commit()

        await lr_bridge.apply_inbound_deltas(
            path,
            [{"filepath": "/photos/IMG_1.dng", "family": "lr_rating", "value": 4, "observed_at": 200.0}],
        )

        with closing(sqlite3.connect(path)) as conn:
            row = conn.execute(
                "SELECT settings, updated_at FROM develop_settings WHERE image_id = 1"
            ).fetchone()
            rating_clock = conn.execute(
                "SELECT family, ts FROM oplog_family_state WHERE content_hash = ?",
                (HASH_A,),
            ).fetchall()
        self.assertEqual(json.loads(row[0]), {"Exposure2012": 0.5, "_lr_rating": 4})
        self.assertEqual(row[1], "before")
        self.assertEqual(dict(rating_clock), {"rating": 200.0})

    async def test_unmatched_filepath_queues_as_pending(self):
        path = self._catalog()
        result = await lr_bridge.apply_inbound_deltas(
            path,
            [{"filepath": "/missing/nope.dng", "family": "flag", "value": "picked"}],
        )
        self.assertEqual(result["pending_count"], 1)
        self.assertEqual(result["applied"]["received"], 0)

    async def test_outbound_flags_exclude_lr_origin(self):
        path = self._catalog()
        await oplog.apply_entries(
            path,
            [{
                "origin": "phone",
                "origin_seq": 1,
                "content_hash": HASH_A,
                "family": "flag",
                "payload": {"value": "rejected"},
                "ts": 50.0,
            }],
            applied_from="test",
            receive_time=60.0,
        )
        await lr_bridge.apply_inbound_deltas(
            path,
            [{"filepath": "/photos/IMG_2.dng", "family": "flag", "value": "picked", "observed_at": 80.0}],
        )
        outbound = await lr_bridge.outbound_flag_deltas(path, since=0.0)
        paths = {item["filepath"] for item in outbound}
        self.assertIn("/photos/IMG_1.dng", paths)
        self.assertNotIn("/photos/IMG_2.dng", paths)

    async def test_elo_stars_gates_comparisons_and_percentiles(self):
        path = self._catalog()
        elo_stars.invalidate_elo_stars_cache()
        # Seed a wider distribution so percentiles are meaningful.
        with closing(sqlite3.connect(path)) as conn:
            for index in range(4, 54):
                content_hash = f"{index:032x}"
                conn.execute(
                    "INSERT INTO images(id, filename, filepath, content_hash, elo, comparisons, file_ext) "
                    "VALUES (?, ?, ?, ?, ?, 5, 'dng')",
                    (index, f"x{index}.dng", f"/photos/x{index}.dng", content_hash, 1200 - index),
                )
            conn.commit()
        elo_stars.invalidate_elo_stars_cache()
        by_hash = await elo_stars.elo_stars_for_hashes(path)
        self.assertEqual(by_hash.get(HASH_A), 5)  # top of the pack
        self.assertNotIn(HASH_C, by_hash)  # comparisons=0 never projects
        # Predicted-only (0 comparisons) must stay out even with high Elo.
        with closing(sqlite3.connect(path)) as conn:
            conn.execute("UPDATE images SET elo = 9999, comparisons = 0 WHERE content_hash = ?", (HASH_C,))
            conn.commit()
        elo_stars.invalidate_elo_stars_cache()
        by_hash = await elo_stars.elo_stars_for_hashes(path)
        self.assertNotIn(HASH_C, by_hash)

    def test_project_stars_thresholds(self):
        thresholds = (0.02, 0.10, 0.30)
        self.assertEqual(elo_stars.project_stars(0, 100, thresholds), 5)
        self.assertEqual(elo_stars.project_stars(1, 100, thresholds), 5)
        self.assertEqual(elo_stars.project_stars(2, 100, thresholds), 4)
        self.assertEqual(elo_stars.project_stars(9, 100, thresholds), 4)
        self.assertEqual(elo_stars.project_stars(10, 100, thresholds), 3)
        self.assertEqual(elo_stars.project_stars(29, 100, thresholds), 3)
        self.assertEqual(elo_stars.project_stars(30, 100, thresholds), 0)
        self.assertEqual(elo_stars.project_stars(0, 1, thresholds), 5)

    async def test_export_of_reuses_version_stack(self):
        path = self._catalog()
        with closing(sqlite3.connect(path)) as conn:
            conn.execute(
                "INSERT INTO images(id, filename, filepath, content_hash, file_ext, elo, comparisons) "
                "VALUES (10, 'IMG_1.jpg', '/photos/IMG_1.jpg', ?, 'jpg', 1200, 0)",
                ("d" * 32,),
            )
            conn.commit()
        linked = await export_relation.link_export(
            path, source_image_id=1, export_image_id=10
        )
        self.assertTrue(linked["linked"])
        self.assertEqual(linked["relation"], "export_of")
        raw_payload = await export_relation.export_of_for_image(path, 1)
        edit_payload = await export_relation.export_of_for_image(path, 10)
        self.assertIsNotNone(raw_payload)
        self.assertIsNotNone(edit_payload)
        self.assertEqual(raw_payload["source_image_id"], 1)
        self.assertEqual(edit_payload["export_image_id"], 10)
        self.assertEqual(raw_payload["stack_id"], edit_payload["stack_id"])

    async def test_export_fallback_stem_match(self):
        path = self._catalog()
        with closing(sqlite3.connect(path)) as conn:
            conn.execute(
                "INSERT INTO images(id, filename, filepath, content_hash, file_ext) "
                "VALUES (11, 'IMG_1.tif', '/photos/IMG_1.tif', ?, 'tif')",
                ("e" * 32,),
            )
            conn.commit()
        matched = export_relation.match_export_fallback(path, 11)
        self.assertIsNotNone(matched)
        self.assertEqual(matched["source_image_id"], 1)
        self.assertEqual(matched["match"], "stem")


class LrBridgeRouteTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tempdir.name, "lr.db")
        self.old_db = db.DB_PATH
        db.DB_PATH = self.db_path
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.executescript(CATALOG_DDL)
            conn.execute(
                "INSERT INTO images(id, filename, filepath, content_hash, flag, elo, comparisons, file_ext) "
                "VALUES (1, 'a.dng', '/a.dng', ?, 'unflagged', 1500, 8, 'dng')",
                (HASH_A,),
            )
            conn.execute(
                "INSERT INTO images(id, filename, filepath, content_hash, file_ext) "
                "VALUES (2, 'a.jpg', '/a.jpg', ?, 'jpg')",
                (HASH_B,),
            )
            conn.commit()
        app = FastAPI()
        app.include_router(lr_routes.router)
        self.client = TestClient(app)

    async def asyncTearDown(self):
        db.DB_PATH = self.old_db
        self.tempdir.cleanup()

    async def test_post_and_get_deltas(self):
        await oplog.apply_entries(
            self.db_path,
            [{
                "origin": "azimuth",
                "origin_seq": 1,
                "content_hash": HASH_A,
                "family": "flag",
                "payload": {"value": "picked"},
                "ts": 10.0,
            }],
            applied_from="test",
            receive_time=20.0,
        )
        posted = self.client.post(
            "/api/lr/deltas",
            json={"items": [{"filepath": "/a.dng", "family": "lr_rating", "value": 3, "observed_at": 30}]},
        )
        self.assertEqual(posted.status_code, 200)
        self.assertEqual(posted.json()["entries"][0]["origin"], "lr")

        outbound = self.client.get("/api/lr/deltas", params={"since": 0})
        self.assertEqual(outbound.status_code, 200)
        body = outbound.json()
        families = {item["family"] for item in body["items"]}
        self.assertIn("flag", families)
        self.assertIn("elo_stars", families)

    async def test_export_relation_api(self):
        linked = self.client.post(
            "/api/lr/exports",
            json={"source_image_id": 1, "export_image_id": 2},
        )
        self.assertEqual(linked.status_code, 200)
        self.assertTrue(linked.json()["linked"])
        for image_id in (1, 2):
            payload = self.client.get(f"/api/lr/relation/{image_id}")
            self.assertEqual(payload.status_code, 200)
            self.assertEqual(payload.json()["export_of"]["relation"], "export_of")


if __name__ == "__main__":
    unittest.main()
