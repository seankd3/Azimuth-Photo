"""The requests that most need to be quick must not open the slowest thing.

How long to wait for a busy database used to be baked in when a connection was
opened, which quietly opted every caller asking for a short wait out of the
pool. The callers asking for a short wait are the interactive ones — the grid,
compare, trash — so those were the only requests opening a fresh connection,
and opening a fresh connection to a 2.1GB WAL catalog is not quick. A thread
dump caught 29 of them alive at once while the first page of photos waited.
"""

import asyncio
import os
import tempfile
import unittest

from data import connection as data_connection


class PoolReuseTests(unittest.TestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix="-catalog.db", dir=os.getcwd())
        os.close(handle)
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        asyncio.run(data_connection.release_database(self.path))
        for suffix in ("-wal", "-shm", ""):
            target = self.path + suffix
            if os.path.exists(target):
                try:
                    os.unlink(target)
                except PermissionError:
                    pass

    async def _open_close(self, timeout=None):
        conn = await data_connection.open_async(self.path, timeout=timeout)
        key = getattr(conn, "_azimuth_pool_key", None)
        await data_connection.close_async(conn, db_path=self.path)
        return key

    def test_a_short_wait_still_gets_a_pooled_connection(self):
        """The grid asks for 0.25s; that must not cost it a fresh connection."""

        self.assertEqual(asyncio.run(self._open_close(timeout=0.25)), self.path)

    def test_the_ordinary_caller_is_unchanged(self):
        self.assertEqual(asyncio.run(self._open_close()), self.path)

    def test_the_same_connection_comes_back(self):
        async def twice():
            first = await data_connection.open_async(self.path, timeout=0.25)
            await data_connection.close_async(first, db_path=self.path)
            second = await data_connection.open_async(self.path, timeout=0.25)
            same = first is second
            await data_connection.close_async(second, db_path=self.path)
            return same

        self.assertTrue(asyncio.run(twice()), "a warm connection should be reused")

    def test_the_requested_wait_is_applied_to_a_reused_connection(self):
        """Pooling must not hand back someone else's timeout."""

        async def check():
            first = await data_connection.open_async(self.path, timeout=5.0)
            await data_connection.close_async(first, db_path=self.path)
            second = await data_connection.open_async(self.path, timeout=0.25)
            row = await (await second.execute("PRAGMA busy_timeout")).fetchone()
            await data_connection.close_async(second, db_path=self.path)
            return int(row[0])

        self.assertEqual(asyncio.run(check()), 250)

    def test_a_throwaway_catalog_is_still_never_pooled(self):
        """Test databases must stay unpooled so they can be deleted."""

        handle, path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        try:
            async def open_it():
                conn = await data_connection.open_async(path)
                key = getattr(conn, "_azimuth_pool_key", None)
                await data_connection.close_async(conn, db_path=path)
                return key

            if data_connection.is_ephemeral_db_path(path):
                self.assertIsNone(asyncio.run(open_it()))
        finally:
            for suffix in ("-wal", "-shm", ""):
                if os.path.exists(path + suffix):
                    try:
                        os.unlink(path + suffix)
                    except PermissionError:
                        pass


if __name__ == "__main__":
    unittest.main()
