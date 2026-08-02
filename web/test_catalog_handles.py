"""Nothing keeps a catalog open after this process is done with it.

Windows will not delete a file another handle still has open, so a leaked
connection shows up as a random teardown failure in a different test each run —
the most expensive kind of red, because it teaches everyone to ignore red.

The leak was real and specific: interactive routes cache a long-lived
read-only reader per catalog path, on purpose, and nothing dropped it. The rule
is that `close_shared_readers` releases *every* store, so a new one added later
cannot quietly reintroduce this.
"""

import os
import sqlite3
import tempfile
import unittest

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
