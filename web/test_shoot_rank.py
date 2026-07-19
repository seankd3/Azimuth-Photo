"""Per-shoot Best-of rank payload — local excellence without rescaling elo_stars."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from features.sync import elo_stars, shoot_rank


CATALOG_DDL = """
PRAGMA foreign_keys=ON;
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
    file_ext TEXT
);
"""


class ShootRankPureTests(unittest.TestCase):
    def test_parent_folder_is_shoot_key(self):
        self.assertEqual(
            shoot_rank.shoot_key_from_filepath("/lib/RAWS/2024/2024-12-03 - Starbase/a.dng"),
            "/lib/RAWS/2024/2024-12-03 - Starbase",
        )
        self.assertEqual(
            shoot_rank.shoot_title_from_key("/lib/RAWS/2024/2024-12-03 - Starbase"),
            "2024-12-03 - Starbase",
        )
        self.assertEqual(
            shoot_rank.shoot_key_from_filepath(r"C:\Photos\2024-07-01\b.dng"),
            "C:/Photos/2024-07-01",
        )

    def test_rank_size_and_best_of(self):
        # 10 ranked photos → top 20% = 2 Best-of
        rows = [
            {"id": i, "filepath": f"/shoot/a/{i}.dng", "elo": 2000 - i, "content_hash": f"{i:032x}"}
            for i in range(1, 11)
        ]
        ranked = shoot_rank.annotate_shoot_ranks(rows)
        by_path = {r["filepath"]: r for r in ranked}
        self.assertEqual(by_path["/shoot/a/1.dng"]["rank_in_shoot"], 1)
        self.assertEqual(by_path["/shoot/a/1.dng"]["shoot_size"], 10)
        self.assertTrue(by_path["/shoot/a/1.dng"]["is_best_of_shoot"])
        self.assertTrue(by_path["/shoot/a/2.dng"]["is_best_of_shoot"])
        self.assertFalse(by_path["/shoot/a/3.dng"]["is_best_of_shoot"])
        self.assertEqual(by_path["/shoot/a/10.dng"]["rank_in_shoot"], 10)

        collections = shoot_rank.best_of_collections_from_ranks(ranked)
        self.assertEqual(len(collections), 1)
        self.assertEqual(collections[0]["filepaths"], ["/shoot/a/1.dng", "/shoot/a/2.dng"])

    def test_small_shoot_exclusion(self):
        rows = [
            {"id": i, "filepath": f"/tiny/{i}.dng", "elo": 1500 - i}
            for i in range(1, shoot_rank.BEST_OF_SHOOT_MIN_SIZE)  # one under minimum
        ]
        ranked = shoot_rank.annotate_shoot_ranks(rows)
        self.assertTrue(ranked)
        self.assertTrue(all(not r["is_best_of_shoot"] for r in ranked))
        self.assertEqual(shoot_rank.best_of_collections_from_ranks(ranked), [])

    def test_best_of_cutoff_matches_desktop_fraction(self):
        self.assertEqual(shoot_rank.best_of_cutoff(10), 2)
        self.assertEqual(shoot_rank.best_of_cutoff(5), 1)
        self.assertEqual(shoot_rank.best_of_cutoff(4), 0)
        self.assertEqual(shoot_rank.best_of_cutoff(1), 0)


class ShootRankDbTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tempdir.name) / "catalog.db")
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.executescript(CATALOG_DDL)
            # Shoot A: 10 ranked photos
            for i in range(1, 11):
                conn.execute(
                    "INSERT INTO images(id, filename, filepath, content_hash, elo, comparisons, file_ext) "
                    "VALUES (?, ?, ?, ?, ?, 5, 'dng')",
                    (i, f"a{i}.dng", f"/lib/2024-12-03/{i}.dng", f"{i:032x}", 2000 - i),
                )
            # Shoot B: too small (4 ranked)
            for i in range(11, 15):
                conn.execute(
                    "INSERT INTO images(id, filename, filepath, content_hash, elo, comparisons, file_ext) "
                    "VALUES (?, ?, ?, ?, ?, 5, 'dng')",
                    (i, f"b{i}.dng", f"/lib/tiny/{i}.dng", f"{i:032x}", 1800 - i),
                )
            # Unranked in a large shoot — ignored
            conn.execute(
                "INSERT INTO images(id, filename, filepath, content_hash, elo, comparisons, file_ext) "
                "VALUES (99, 'z.dng', '/lib/2024-12-03/z.dng', ?, 9999, 0, 'dng')",
                ("f" * 32,),
            )
            conn.commit()
        elo_stars.invalidate_elo_stars_cache()

    async def asyncTearDown(self):
        self.tempdir.cleanup()

    async def test_payload_rank_size_best_and_exclusion(self):
        payload = await shoot_rank.shoot_rank_payload(self.db_path)
        photos = payload["photos"]
        by_path = {p["filepath"]: p for p in photos}
        self.assertEqual(by_path["/lib/2024-12-03/1.dng"]["rank_in_shoot"], 1)
        self.assertEqual(by_path["/lib/2024-12-03/1.dng"]["shoot_size"], 10)
        self.assertTrue(by_path["/lib/2024-12-03/1.dng"]["is_best_of_shoot"])
        self.assertTrue(by_path["/lib/2024-12-03/2.dng"]["is_best_of_shoot"])
        self.assertFalse(by_path["/lib/2024-12-03/3.dng"]["is_best_of_shoot"])
        # Small shoot present in ranks but never Best-of
        self.assertIn("/lib/tiny/11.dng", by_path)
        self.assertEqual(by_path["/lib/tiny/11.dng"]["shoot_size"], 4)
        self.assertFalse(by_path["/lib/tiny/11.dng"]["is_best_of_shoot"])
        # Unranked omitted
        self.assertNotIn("/lib/2024-12-03/z.dng", by_path)
        # Only the large shoot gets a collection
        self.assertEqual(len(payload["best_of_shoots"]), 1)
        self.assertEqual(payload["best_of_shoots"][0]["shoot_title"], "2024-12-03")
        self.assertEqual(
            payload["best_of_shoots"][0]["filepaths"],
            ["/lib/2024-12-03/1.dng", "/lib/2024-12-03/2.dng"],
        )


if __name__ == "__main__":
    unittest.main()
