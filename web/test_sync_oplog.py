import json
import os
import random
import sqlite3
import tempfile
import time
import unittest

from features.sync import oplog


HASH_A = "a" * 32
HASH_B = "b" * 32


CATALOG_DDL = """
PRAGMA foreign_keys=ON;
CREATE TABLE images (
    id INTEGER PRIMARY KEY,
    content_hash TEXT UNIQUE,
    flag TEXT NOT NULL DEFAULT 'unflagged'
);
CREATE TABLE develop_settings (
    image_id INTEGER PRIMARY KEY REFERENCES images(id) ON DELETE CASCADE,
    settings TEXT NOT NULL DEFAULT '{}',
    origin TEXT NOT NULL DEFAULT 'user',
    updated_at TEXT NOT NULL
);
INSERT INTO images(id, content_hash) VALUES (1, 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa');
INSERT INTO images(id, content_hash) VALUES (2, 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb');
"""


class OplogConvergenceTests(unittest.IsolatedAsyncioTestCase):
    def _catalog(self) -> str:
        handle, path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        self.addCleanup(lambda: os.path.exists(path) and os.unlink(path))
        conn = sqlite3.connect(path)
        try:
            conn.executescript(CATALOG_DDL)
            conn.commit()
        finally:
            conn.close()
        return path

    @staticmethod
    def _count(path: str) -> int:
        with sqlite3.connect(path) as conn:
            return int(conn.execute("SELECT COUNT(*) FROM oplog").fetchone()[0])

    @staticmethod
    def _flags(path: str) -> dict[str, str]:
        with sqlite3.connect(path) as conn:
            return dict(conn.execute("SELECT content_hash, flag FROM images"))

    @staticmethod
    def _snapshot(path: str) -> dict:
        with sqlite3.connect(path) as conn:
            conn.row_factory = sqlite3.Row
            flags = [tuple(row) for row in conn.execute("SELECT content_hash, flag FROM images ORDER BY id")]
            iptc = [tuple(row) for row in conn.execute(
                "SELECT image_id, title, caption, copyright, creator FROM iptc_fields ORDER BY image_id"
            )]
            keywords = [str(row[0]) for row in conn.execute(
                "WITH RECURSIVE tree(id, path) AS ("
                "SELECT id, name FROM keywords WHERE parent_id IS NULL UNION ALL "
                "SELECT child.id, tree.path || ' > ' || child.name "
                "FROM keywords child JOIN tree ON child.parent_id = tree.id) "
                "SELECT tree.path FROM image_keywords JOIN tree ON tree.id = image_keywords.keyword_id "
                "ORDER BY tree.path COLLATE NOCASE"
            )]
            return {"flags": flags, "iptc": iptc, "keywords": keywords}

    async def test_apply_is_order_and_replay_independent_for_three_families(self):
        entries = [
            {"origin": "alpha", "origin_seq": 1, "content_hash": HASH_A, "family": "flag", "payload": {"value": "picked"}, "ts": 100.0},
            {"origin": "beta", "origin_seq": 1, "content_hash": HASH_A, "family": "flag", "payload": {"value": "rejected"}, "ts": 300.0},
            {"origin": "alpha", "origin_seq": 2, "content_hash": HASH_A, "family": "iptc", "payload": {"title": "Earlier", "caption": "", "creator": "A"}, "ts": 200.0},
            {"origin": "beta", "origin_seq": 2, "content_hash": HASH_A, "family": "iptc", "payload": {"title": "Winner", "caption": "Final", "creator": "B"}, "ts": 400.0},
            {"origin": "alpha", "origin_seq": 3, "content_hash": HASH_A, "family": "keywords", "payload": {"paths": ["Travel > Field"]}, "ts": 150.0},
            {"origin": "beta", "origin_seq": 3, "content_hash": HASH_A, "family": "keywords", "payload": {"paths": ["Favorites", "Travel > Field"]}, "ts": 250.0},
        ]
        expected = None
        rng = random.Random(20260711)
        for _ in range(12):
            path = self._catalog()
            shuffled = entries * 2
            rng.shuffle(shuffled)
            await oplog.apply_entries(path, shuffled, applied_from="property-test", receive_time=500.0)
            snapshot = self._snapshot(path)
            expected = expected or snapshot
            self.assertEqual(snapshot, expected)
            self.assertEqual(self._count(path), len(entries))

    async def test_two_catalog_exchange_replay_and_triple_exchange_do_not_echo(self):
        hub = self._catalog()
        satellite = self._catalog()
        hub_id = await oplog.device_id(hub)
        satellite_id = await oplog.device_id(satellite)

        await oplog.append_flags(hub, [1], "picked")
        hub_entries = await oplog.origin_entries(hub, hub_id)
        await oplog.apply_entries(satellite, hub_entries, applied_from=hub_id)
        self.assertEqual(self._flags(satellite)[HASH_A], "picked")

        await oplog.append_flags(satellite, [2], "rejected")
        satellite_entries = await oplog.origin_entries(satellite, satellite_id)
        first_push = await oplog.apply_entries(hub, satellite_entries, applied_from=satellite_id)
        replay_push = await oplog.apply_entries(hub, satellite_entries, applied_from=satellite_id)
        self.assertEqual(first_push["inserted"], 1)
        self.assertEqual(replay_push["inserted"], 0)
        self.assertEqual(self._flags(hub)[HASH_B], "rejected")

        async def hub_request(method, path, payload):
            self.assertEqual(method, "POST")
            if path.endswith("/push"):
                return await oplog.apply_entries(
                    hub, payload["entries"], applied_from=payload["device_id"]
                )
            self.assertTrue(path.endswith("/pull"))
            return await oplog.pull_entries(
                hub, device=payload["device_id"], cursors=payload["cursors"]
            )

        await oplog.exchange_with_hub(satellite, hub_request)
        self.assertEqual(self._flags(satellite), self._flags(hub))

        stable_counts = (self._count(hub), self._count(satellite))
        for _ in range(3):
            await oplog.exchange_with_hub(satellite, hub_request)
        self.assertEqual((self._count(hub), self._count(satellite)), stable_counts)

    async def test_future_entry_is_clamped_once_at_receive(self):
        path = self._catalog()
        received_at = time.time()
        entry = {
            "origin": "bad-clock",
            "origin_seq": 1,
            "content_hash": HASH_A,
            "family": "flag",
            "payload": {"value": "picked"},
            "ts": received_at + oplog.MAX_CLOCK_SKEW_SECONDS + 60,
        }
        with self.assertLogs(oplog.log, level="WARNING"):
            await oplog.apply_entries(path, [entry], applied_from="satellite", receive_time=received_at)
        with sqlite3.connect(path) as conn:
            stored = conn.execute("SELECT ts, payload FROM oplog").fetchone()
        self.assertEqual(stored[0], received_at)
        self.assertEqual(json.loads(stored[1]), {"value": "picked"})


if __name__ == "__main__":
    unittest.main()
