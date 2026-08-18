"""Nothing keeps a catalog open after this process is done with it.

Windows will not delete a file another handle still has open, so a leaked
connection shows up as a random teardown failure in a different test each run —
the most expensive kind of red, because it teaches everyone to ignore red.

The leak was real and specific: interactive routes cache a long-lived
read-only reader per catalog path, on purpose, and nothing dropped it. The rule
is that `close_shared_readers` releases *every* store, so a new one added later
cannot quietly reintroduce this.
"""

import asyncio
import contextlib
import os
import sqlite3
import threading
import tempfile
import unittest
from unittest import mock

from data import connection as data_connection


def _wal_catalog(directory: str) -> str:
    path = os.path.join(directory, "catalog.db")
    conn = sqlite3.connect(path)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE images (id INTEGER PRIMARY KEY)")
        conn.commit()
    finally:
        conn.close()
    return path


class ReleasingACatalogTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.mkdtemp()
        self.path = _wal_catalog(self.directory)

    async def asyncTearDown(self):
        await data_connection.close_shared_readers()

    async def test_a_cached_reader_holds_the_file_until_it_is_released(self):
        """The leak, stated as behaviour: this is why deletes used to fail."""

        data_connection.inline_reader(self.path)
        self.assertIn(self.path, data_connection._inline_readers)

        await data_connection.close_shared_readers()
        self.assertNotIn(self.path, data_connection._inline_readers)
        os.unlink(self.path)  # would raise PermissionError on Windows if held

    async def test_releasing_one_catalog_leaves_the_others_alone(self):
        """Production uses this for a library switch, not only teardown."""

        other_directory = tempfile.mkdtemp()
        other = _wal_catalog(other_directory)
        data_connection.inline_reader(self.path)
        data_connection.inline_reader(other)

        await data_connection.release_database(self.path)

        self.assertNotIn(self.path, data_connection._inline_readers)
        self.assertIn(other, data_connection._inline_readers)

    async def test_every_store_of_open_handles_is_released(self):
        """A fourth cache added later must be emptied here too."""

        data_connection.inline_reader(self.path)
        conn = await data_connection.open_async(self.path)
        await data_connection.close_async(conn, db_path=self.path)

        await data_connection.close_shared_readers()

        self.assertEqual(data_connection._inline_readers, {})
        self.assertEqual(
            {path: handles for path, handles in data_connection._idle_connections.items() if handles},
            {},
        )


if __name__ == "__main__":
    unittest.main()


class AbandonedWorkTests(unittest.IsolatedAsyncioTestCase):
    """A request that is given up on must not keep the catalog open.

    An aiosqlite connection is a live worker thread holding the file from the
    moment `connect` returns — before the caller has it, and before any
    `finally` exists to close it. Four PRAGMAs stood in that window. A request
    abandoned there left a connection nobody held and nobody could release:
    invisible on a server until the threads add up, and on Windows a library
    file that could not be deleted. It cost this suite 2-6 random failures a
    run for months.
    """

    def setUp(self):
        self.directory = tempfile.mkdtemp()
        self.path = _wal_catalog(self.directory)

    async def asyncTearDown(self):
        await data_connection.close_shared_readers()

    def _open_connections(self) -> int:
        return sum(1 for thread in threading.enumerate() if "aiosqlite" in thread.name.lower())

    async def test_cancelling_an_open_leaves_nothing_behind(self):
        before = self._open_connections()
        for _ in range(6):
            task = asyncio.create_task(data_connection.open_async(self.path))
            await asyncio.sleep(0)  # let it reach the first await, then give up
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

        for _ in range(40):
            if self._open_connections() <= before:
                break
            await asyncio.sleep(0.05)
        self.assertLessEqual(
            self._open_connections(),
            before,
            "a cancelled open left a worker thread holding the catalog",
        )

    async def test_the_file_can_still_be_deleted(self):
        """The symptom, stated as itself: Windows refuses while a handle is open.

        Pause at a known PRAGMA so cancellation lands after SQLite is open but
        before the caller owns the connection. Timing this with a sleep could
        instead cancel an already-completed task and leak the discarded return
        value created by the test itself.
        """

        reached_pragma = asyncio.Event()
        original_execute = data_connection.aiosqlite.Connection.execute

        async def pause_during_setup(connection, sql, parameters=None):
            if str(sql).startswith("PRAGMA busy_timeout"):
                reached_pragma.set()
                await asyncio.Event().wait()
            return await original_execute(connection, sql, parameters)

        with mock.patch.object(
            data_connection.aiosqlite.Connection,
            "execute",
            pause_during_setup,
        ):
            task = asyncio.create_task(data_connection.open_async(self.path))
            await reached_pragma.wait()
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

        await data_connection.close_shared_readers()
        os.unlink(self.path)

    async def test_a_cancelled_reader_still_hands_its_connection_back(self):
        """`finally` runs, but its awaits are cancelled too — hence the shield."""

        async def read_and_be_cancelled():
            conn = await data_connection.open_async(self.path)
            try:
                await asyncio.sleep(5)
            finally:
                await data_connection.close_async(conn, db_path=self.path)

        before = self._open_connections()
        task = asyncio.create_task(read_and_be_cancelled())
        await asyncio.sleep(0.1)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

        for _ in range(40):
            if self._open_connections() <= before:
                break
            await asyncio.sleep(0.05)
        self.assertLessEqual(self._open_connections(), before)
