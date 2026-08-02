"""Opening the app must not wait on search-index maintenance.

The metadata index is kept in step by triggers. Boot compared its row count
with the table's and rebuilt the whole thing whenever they differed by any
amount — which sounds cautious and is not. Measured on a 155,000-photo library:
the index was one row ahead, and that one row cost 12.3 seconds of every single
launch before the grid could show anything.
"""

import asyncio
import sqlite3
import unittest

from data import schema as data_schema

SCHEMA = """
CREATE TABLE images (id INTEGER PRIMARY KEY, filename TEXT, filepath TEXT);
CREATE TABLE images_metadata_fts_docsize (id INTEGER PRIMARY KEY);
"""


class _Cursor:
    def __init__(self, cursor):
        self._cursor = cursor

    async def fetchone(self):
        row = self._cursor.fetchone()
        return {"count": row[0]} if row else None


class _Conn:
    """Just enough async surface for the two counts and the rebuild."""

    def __init__(self, connection):
        self._connection = connection
        self.rebuilds = 0

    async def execute(self, sql, params=()):
        if "VALUES('rebuild')" in sql:
            self.rebuilds += 1
            return _Cursor(self._connection.execute("SELECT 1"))
        return _Cursor(self._connection.execute(sql, params))

    async def commit(self):
        self._connection.commit()


class BootDoesNotRebuildTests(unittest.TestCase):
    def _catalog(self, images: int, indexed: int) -> _Conn:
        raw = sqlite3.connect(":memory:")
        raw.executescript(SCHEMA)
        raw.executemany("INSERT INTO images(id) VALUES (?)", [(i,) for i in range(1, images + 1)])
        raw.executemany(
            "INSERT INTO images_metadata_fts_docsize(id) VALUES (?)",
            [(i,) for i in range(1, indexed + 1)],
        )
        raw.commit()
        return _Conn(raw)

    def test_one_row_of_drift_does_not_rebuild_the_world(self):
        """The laptop's exact shape: 155k photos, index one ahead."""

        conn = self._catalog(images=154_941, indexed=154_942)
        asyncio.run(data_schema.ensure_metadata_fts(conn))
        self.assertEqual(conn.rebuilds, 0)

    def test_a_missing_index_is_still_built(self):
        conn = self._catalog(images=1000, indexed=0)
        asyncio.run(data_schema.ensure_metadata_fts(conn))
        self.assertEqual(conn.rebuilds, 1)

    def test_an_empty_library_builds_nothing(self):
        conn = self._catalog(images=0, indexed=0)
        asyncio.run(data_schema.ensure_metadata_fts(conn))
        self.assertEqual(conn.rebuilds, 0)

    def test_an_index_in_step_builds_nothing(self):
        conn = self._catalog(images=5000, indexed=5000)
        asyncio.run(data_schema.ensure_metadata_fts(conn))
        self.assertEqual(conn.rebuilds, 0)


class DriftIsStillNoticedTests(unittest.TestCase):
    """Boot stops rebuilding, so something else has to know repair is owed."""

    def _catalog(self, images: int, indexed: int) -> _Conn:
        return BootDoesNotRebuildTests._catalog(BootDoesNotRebuildTests(), images, indexed)

    def test_drift_is_reported(self):
        self.assertEqual(asyncio.run(data_schema.metadata_fts_drift(self._catalog(154_941, 154_942))), 1)

    def test_an_index_in_step_reports_nothing_to_do(self):
        self.assertEqual(asyncio.run(data_schema.metadata_fts_drift(self._catalog(5000, 5000))), 0)

    def test_an_empty_index_is_left_to_the_boot_builder(self):
        self.assertEqual(asyncio.run(data_schema.metadata_fts_drift(self._catalog(5000, 0))), 0)

    def test_the_repair_actually_rebuilds(self):
        conn = self._catalog(154_941, 154_942)
        asyncio.run(data_schema.rebuild_metadata_fts(conn))
        self.assertEqual(conn.rebuilds, 1)


if __name__ == "__main__":
    unittest.main()
