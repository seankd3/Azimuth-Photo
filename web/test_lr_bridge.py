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

    async def test_resolve_filepath_canonicalizes_windows_case_and_separators(self):
        path = self._catalog()
        with closing(sqlite3.connect(path)) as conn:
            conn.execute(
                "UPDATE images SET filepath = ? WHERE id = 1",
                (r"D:\Photos\IMG_1.dng",),
            )
            conn.commit()
        identity = await lr_bridge.resolve_filepath(path, r"d:/photos/img_1.dng")
        self.assertIsNotNone(identity)
        self.assertEqual(identity["image_id"], 1)
        self.assertEqual(identity["content_hash"], HASH_A)

    async def test_inbound_entries_expose_filepath_and_inbound_family(self):
        """Plugin ledger_remember_confirmed needs filepath + inbound family + value."""

        path = self._catalog()
        mixed = await lr_bridge.apply_inbound_deltas(
            path,
            [
                {"filepath": "/photos/IMG_1.dng", "family": "lr_rating", "value": 4, "observed_at": 5.0},
                {"filepath": "/missing.dng", "family": "flag", "value": "picked"},
            ],
        )
        self.assertEqual(mixed["pending_count"], 1)
        self.assertEqual(len(mixed["entries"]), 1)
        entry = mixed["entries"][0]
        self.assertEqual(entry["filepath"], "/photos/IMG_1.dng")
        self.assertEqual(entry["family"], "lr_rating")
        self.assertEqual(entry["value"], 4)
        self.assertNotIn("/missing.dng", {item.get("filepath") for item in mixed["entries"]})

    async def test_concurrent_inbound_posts_get_distinct_seqs_and_both_apply(self):
        """origin_seq must be allocated inside the insert txn — no silent collision."""

        import asyncio

        path = self._catalog()
        first, second = await asyncio.gather(
            lr_bridge.apply_inbound_deltas(
                path,
                [{"filepath": "/photos/IMG_1.dng", "family": "flag", "value": "picked", "observed_at": 10.0}],
            ),
            lr_bridge.apply_inbound_deltas(
                path,
                [{"filepath": "/photos/IMG_2.dng", "family": "flag", "value": "rejected", "observed_at": 11.0}],
            ),
        )
        self.assertEqual(first["applied"]["inserted"], 1)
        self.assertEqual(second["applied"]["inserted"], 1)
        seqs = {first["entries"][0]["origin_seq"], second["entries"][0]["origin_seq"]}
        self.assertEqual(len(seqs), 2)
        with closing(sqlite3.connect(path)) as conn:
            flags = dict(conn.execute("SELECT id, flag FROM images WHERE id IN (1, 2)").fetchall())
            rows = conn.execute(
                "SELECT origin_seq, content_hash, family FROM oplog WHERE origin = 'lr' ORDER BY origin_seq"
            ).fetchall()
        self.assertEqual(flags[1], "picked")
        self.assertEqual(flags[2], "rejected")
        self.assertEqual(len(rows), 2)
        self.assertEqual({row[0] for row in rows}, seqs)
        self.assertEqual({row[1] for row in rows}, {HASH_A, HASH_B})

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

    async def test_elo_stars_demotion_emits_clear_to_zero(self):
        """Previously projected photo dropping below threshold must emit stars=0."""

        path = self._catalog()
        elo_stars.invalidate_elo_stars_cache()
        with closing(sqlite3.connect(path)) as conn:
            for index in range(4, 14):
                content_hash = f"{index:032x}"
                conn.execute(
                    "INSERT INTO images(id, filename, filepath, content_hash, elo, comparisons, file_ext) "
                    "VALUES (?, ?, ?, ?, ?, 5, 'dng')",
                    (index, f"x{index}.dng", f"/photos/x{index}.dng", content_hash, 1000 - index),
                )
            conn.commit()
        elo_stars.invalidate_elo_stars_cache()
        first = await elo_stars.outbound_elo_star_deltas(path, limit=500)
        by_path = {item["filepath"]: item["value"] for item in first}
        self.assertEqual(by_path.get("/photos/IMG_1.dng"), 5)
        # Demote HASH_A below the projecting band via comparisons=0.
        with closing(sqlite3.connect(path)) as conn:
            conn.execute(
                "UPDATE images SET comparisons = 0, elo = 0 WHERE content_hash = ?",
                (HASH_A,),
            )
            conn.commit()
        elo_stars.invalidate_elo_stars_cache()
        second = await elo_stars.outbound_elo_star_deltas(path, limit=500)
        clears = [item for item in second if item["filepath"] == "/photos/IMG_1.dng"]
        self.assertEqual(len(clears), 1)
        self.assertEqual(clears[0]["value"], 0)
        self.assertEqual(clears[0]["family"], "elo_stars")
        # Second poll must not re-emit the same clear.
        third = await elo_stars.outbound_elo_star_deltas(path, limit=500)
        self.assertFalse(any(item["filepath"] == "/photos/IMG_1.dng" for item in third))

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

    async def test_export_ambiguous_fallback_stays_unmatched(self):
        """Blind raw_ids[0] must not link when stem does not confidently match."""

        path = self._catalog()
        capture = "2026-01-01T12:00:00"
        camera = "Canon EOS R5"
        with closing(sqlite3.connect(path)) as conn:
            conn.execute(
                "UPDATE images SET date_taken = ?, camera_model = ? WHERE id IN (1, 2)",
                (capture, camera),
            )
            conn.execute(
                "INSERT INTO images(id, filename, filepath, content_hash, file_ext, date_taken, camera_model) "
                "VALUES (20, 'EXPORT_OTHER.jpg', '/photos/EXPORT_OTHER.jpg', ?, 'jpg', ?, ?)",
                ("f" * 32, capture, camera),
            )
            conn.commit()
        matched = export_relation.match_export_fallback(path, 20)
        self.assertIsNone(matched)
        ensured = await export_relation.ensure_export_link(path, 20)
        self.assertFalse(ensured["linked"])
        self.assertEqual(ensured["reason"], "unmatched")
        with closing(sqlite3.connect(path)) as conn:
            stacks = conn.execute("SELECT COUNT(*) FROM stacks").fetchone()[0]
            members = conn.execute("SELECT COUNT(*) FROM stack_members").fetchone()[0]
        self.assertEqual(stacks, 0)
        self.assertEqual(members, 0)

    async def test_link_export_refuses_non_raw_source(self):
        path = self._catalog()
        with closing(sqlite3.connect(path)) as conn:
            conn.execute(
                "INSERT INTO images(id, filename, filepath, content_hash, file_ext) "
                "VALUES (21, 'a.jpg', '/photos/a.jpg', ?, 'jpg')",
                ("1" * 32,),
            )
            conn.execute(
                "INSERT INTO images(id, filename, filepath, content_hash, file_ext) "
                "VALUES (22, 'b.jpg', '/photos/b.jpg', ?, 'jpg')",
                ("2" * 32,),
            )
            conn.commit()
        linked = await export_relation.link_export(path, source_image_id=21, export_image_id=22)
        self.assertFalse(linked["linked"])
        self.assertEqual(linked["reason"], "source_not_raw")


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
