"""An ordinary quit must not look like a crash to the next launch.

The desktop shell stops its engine by killing the process, so the shutdown
handler never ran and the clean-close marker was never written. Every launch
then treated a normal window close as a crash and paid a full integrity read of
the catalog first: 9.1 seconds on the owner's 2.1GB library, every single time.
"""

import asyncio
import os
import sqlite3
import tempfile
import unittest

from features.system import backups, quit_routes


class _Client:
    def __init__(self, host: str):
        self.host = host


class _Request:
    def __init__(self, host: str = "127.0.0.1"):
        self.client = _Client(host)


class PrepareQuitTests(unittest.TestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        conn = sqlite3.connect(self.path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, payload TEXT)")
        conn.executemany("INSERT INTO t(payload) VALUES (?)", [("x" * 400,) for _ in range(4000)])
        conn.commit()
        conn.close()
        quit_routes.configure(db_path_provider=lambda: self.path)
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        for suffix in ("-wal", "-shm", ""):
            target = self.path + suffix
            if os.path.exists(target):
                try:
                    os.unlink(target)
                except PermissionError:
                    pass
        marker = backups._clean_shutdown_sentinel(self.path)
        if os.path.exists(marker):
            os.unlink(marker)

    def _prepare(self, host: str = "127.0.0.1"):
        return asyncio.run(quit_routes.prepare_quit(_Request(host)))

    def test_a_quit_leaves_the_marker_the_next_launch_looks_for(self):
        self.assertFalse(backups.consume_clean_shutdown(self.path))
        self._prepare()
        self.assertTrue(
            backups.consume_clean_shutdown(self.path),
            "without this the next launch reads the whole catalog before serving",
        )

    def test_the_write_log_is_folded_back_in(self):
        result = self._prepare()
        self.assertTrue(result["write_log_folded"])
        wal = self.path + "-wal"
        self.assertLess(os.path.getsize(wal) if os.path.exists(wal) else 0, 64 * 1024)

    def test_another_machine_cannot_close_the_library(self):
        response = self._prepare(host="192.168.1.50")
        self.assertEqual(response.status_code, 403)
        self.assertFalse(backups.consume_clean_shutdown(self.path))

    def test_an_unconfigured_server_says_so_rather_than_pretending(self):
        quit_routes.configure(db_path_provider=None)
        quit_routes._db_path_provider = None
        response = self._prepare()
        self.assertEqual(response.status_code, 503)

    def test_quitting_twice_is_not_an_error(self):
        self._prepare()
        self._prepare()
        self.assertTrue(backups.consume_clean_shutdown(self.path))


if __name__ == "__main__":
    unittest.main()
