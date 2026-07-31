"""Stored star projection — images.stars follows Elo, and filters read it."""

from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from contextlib import closing

from data.repositories import rankings as ranking_repository
from features.sync import elo_stars


CATALOG_DDL = """
CREATE TABLE images (
    id INTEGER PRIMARY KEY,
    filename TEXT,
    filepath TEXT,
    content_hash TEXT UNIQUE,
    flag TEXT NOT NULL DEFAULT 'unflagged',
    elo REAL DEFAULT 1200,
    comparisons INTEGER DEFAULT 0,
    propagated_updates INTEGER DEFAULT 0,
    status TEXT DEFAULT 'kept',
    missing_at REAL,
    vc_of INTEGER,
    file_ext TEXT
);
"""


class StoredEloStarsTests(unittest.IsolatedAsyncioTestCase):
    def _catalog(self, count: int = 50) -> str:
        """Mini catalog WITHOUT a stars column — refresh must add it."""
        handle, path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        self.addCleanup(lambda: os.path.exists(path) and os.unlink(path))
        with closing(sqlite3.connect(path)) as conn:
            conn.executescript(CATALOG_DDL)
            for index in range(1, count + 1):
                conn.execute(
                    "INSERT INTO images(id, filename, filepath, content_hash, elo, comparisons, file_ext) "
                    "VALUES (?, ?, ?, ?, ?, 5, 'dng')",
                    (index, f"x{index}.dng", f"/photos/x{index}.dng", f"{index:032x}", 2000 - index),
                )
            conn.commit()
        elo_stars.invalidate_elo_stars_cache()
        return path

    def _stars(self, path: str) -> dict[int, int]:
        with closing(sqlite3.connect(path)) as conn:
            return dict(conn.execute("SELECT id, stars FROM images ORDER BY id").fetchall())

    async def test_refresh_adds_column_and_persists_percentile_bands(self):
        path = self._catalog(50)
        changed = await elo_stars.refresh_stored_stars(path)
        stars = self._stars(path)
        # 50 eligible: rank 0 → 5★ (top 2%), ranks 1-4 → 4★ (top 10%),
        # ranks 5-14 → 3★ (top 30%), the rest → 0.
        self.assertEqual(stars[1], 5)
        self.assertEqual([stars[i] for i in range(2, 6)], [4] * 4)
        self.assertEqual([stars[i] for i in range(6, 16)], [3] * 10)
        self.assertEqual([stars[i] for i in range(16, 51)], [0] * 35)
        self.assertEqual(changed, 15)
        # Diff-write: a second refresh with no Elo movement writes nothing.
        self.assertEqual(await elo_stars.refresh_stored_stars(path), 0)

    async def test_projection_updates_when_elo_moves(self):
        path = self._catalog(50)
        await elo_stars.refresh_stored_stars(path)
        self.assertEqual(self._stars(path)[30], 0)
        # Image 30 wins its way to the top of the pack.
        with closing(sqlite3.connect(path)) as conn:
            conn.execute("UPDATE images SET elo = 9999, comparisons = 9 WHERE id = 30")
            conn.commit()
        await elo_stars.refresh_stored_stars(path)
        stars = self._stars(path)
        self.assertEqual(stars[30], 5)
        # The old leader slid down into the 4★ band (rank 1 of 50).
        self.assertEqual(stars[1], 4)

    async def test_demotion_clears_stored_stars_to_zero(self):
        path = self._catalog(50)
        await elo_stars.refresh_stored_stars(path)
        self.assertEqual(self._stars(path)[1], 5)
        # Undo takes the leader's comparisons below the projection gate.
        with closing(sqlite3.connect(path)) as conn:
            conn.execute("UPDATE images SET comparisons = 0 WHERE id = 1")
            conn.commit()
        await elo_stars.refresh_stored_stars(path)
        self.assertEqual(self._stars(path)[1], 0)

    async def test_min_stars_filter_reads_the_stored_value(self):
        path = self._catalog(50)
        await elo_stars.refresh_stored_stars(path)
        # A high raw Elo without a stored star must not pass the filter.
        with closing(sqlite3.connect(path)) as conn:
            conn.execute("UPDATE images SET elo = 8888, stars = 0 WHERE id = 40")
            conn.commit()
        conditions, params = ranking_repository.ranking_filter_parts(
            min_stars=4, include_source=False
        )
        with closing(sqlite3.connect(path)) as conn:
            rows = conn.execute(
                f"SELECT i.id FROM images i WHERE {' AND '.join(conditions)} ORDER BY i.id",
                params,
            ).fetchall()
        self.assertEqual([row[0] for row in rows], [1, 2, 3, 4, 5])

    async def test_stars_sort_orders_by_stored_projection(self):
        path = self._catalog(20)
        await elo_stars.refresh_stored_stars(path)
        order = ranking_repository.RANKING_SORTS["stars"]
        with closing(sqlite3.connect(path)) as conn:
            rows = conn.execute(
                f"SELECT i.id, COALESCE(i.stars, 0) FROM images i ORDER BY {order} LIMIT 6"
            ).fetchall()
        star_values = [row[1] for row in rows]
        self.assertEqual(star_values, sorted(star_values, reverse=True))
        self.assertEqual(rows[0][0], 1)


if __name__ == "__main__":
    unittest.main()
