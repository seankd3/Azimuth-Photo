"""Regression: 04:00 cross-process snapshot collision + orphaned tmp sweep.

Two instances snapshotting in the same second used to share one tmp path;
the loser died with ``sqlite3.OperationalError: database is locked`` (prod
outage class: no nightly backup ever landed). Tmp names are now per-process,
and tmp artifacts stranded by a SIGKILLed run get swept.
"""
from __future__ import annotations

import os
import sqlite3
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path

from features.system import backups


def _make_catalog(path: Path) -> Path:
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE images (id INTEGER PRIMARY KEY, filepath TEXT)")
    conn.execute("INSERT INTO images (filepath) VALUES ('a.jpg')")
    conn.commit()
    conn.close()
    return path


class TmpCollisionTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        home = Path(self._tmp.name)
        self.db = _make_catalog(home / "azimuth.db")
        self.root = home / "backups"
        self.root.mkdir()
        self._old_env = os.environ.get("AZIMUTH_BACKUP_DIR")
        os.environ["AZIMUTH_BACKUP_DIR"] = str(self.root)

    def tearDown(self):
        if self._old_env is None:
            os.environ.pop("AZIMUTH_BACKUP_DIR", None)
        else:
            os.environ["AZIMUTH_BACKUP_DIR"] = self._old_env
        self._tmp.cleanup()

    def test_snapshot_survives_foreign_same_second_tmp(self):
        """A locked old-style tmp from another process must not break us."""
        when = datetime.now().astimezone().replace(microsecond=0)
        name = backups._timestamp_name(when)
        foreign_tmp = self.root / f".{name}.tmp.db"
        holder = sqlite3.connect(foreign_tmp)
        holder.execute("CREATE TABLE t (x)")
        holder.execute("BEGIN EXCLUSIVE")
        try:
            result = backups.create_snapshot(str(self.db), when=when)
            self.assertTrue(result["ok"])
            self.assertTrue((self.root / result["name"]).exists())
        finally:
            holder.close()

    def test_orphaned_tmp_swept_fresh_tmp_kept(self):
        stale = self.root / ".azimuth-20260101-040000.999.tmp.db"
        stale.write_bytes(b"x" * 64)
        old = time.time() - backups.ORPHANED_TMP_MAX_AGE_SECONDS - 60
        os.utime(stale, (old, old))
        fresh = self.root / ".azimuth-20260719-040000.998.tmp.db"
        fresh.write_bytes(b"y" * 64)

        result = backups.create_snapshot(str(self.db))
        self.assertTrue(result["ok"])
        self.assertFalse(stale.exists(), "stale orphan must be swept")
        self.assertTrue(fresh.exists(), "fresh tmp (a live foreign run) must survive")

    def test_tmp_names_are_per_process(self):
        result = backups.create_snapshot(str(self.db))
        self.assertTrue(result["ok"])
        leftovers = list(self.root.glob(".*.tmp.*"))
        self.assertEqual(leftovers, [], "own tmp files must be cleaned up")


if __name__ == "__main__":
    unittest.main()
