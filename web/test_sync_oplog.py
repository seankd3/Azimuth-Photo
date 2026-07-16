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
HASH_C = "c" * 32
COLLECTION_ROOT = "11111111-1111-4111-8111-111111111111"
COLLECTION_CHILD = "22222222-2222-4222-8222-222222222222"


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
CREATE TABLE collections (
    id INTEGER PRIMARY KEY,
    uuid TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    visibility TEXT NOT NULL DEFAULT 'private',
    status TEXT NOT NULL DEFAULT 'draft',
    query TEXT DEFAULT NULL,
    cover_image_id INTEGER REFERENCES images(id),
    created_at REAL NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL DEFAULT 0
);
CREATE TABLE collection_images (
    collection_id INTEGER NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
    image_id INTEGER NOT NULL REFERENCES images(id) ON DELETE CASCADE,
    position INTEGER NOT NULL DEFAULT 0,
    added_at REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (collection_id, image_id)
);
CREATE TABLE collection_links (
    parent_id INTEGER NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
    child_id INTEGER NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
    position INTEGER NOT NULL DEFAULT 0,
    added_at REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (parent_id, child_id)
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
    def _pending_count(path: str) -> int:
        with sqlite3.connect(path) as conn:
            return int(conn.execute("SELECT COUNT(*) FROM oplog_pending").fetchone()[0])

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

    @staticmethod
    def _collection_snapshot(path: str) -> dict:
        with sqlite3.connect(path) as conn:
            collections = list(conn.execute(
                "SELECT uuid, name FROM collections ORDER BY uuid"
            ))
            links = list(conn.execute(
                "SELECT parent.uuid, child.uuid FROM collection_links links "
                "JOIN collections parent ON parent.id = links.parent_id "
                "JOIN collections child ON child.id = links.child_id "
                "ORDER BY parent.uuid, child.uuid"
            ))
            memberships = list(conn.execute(
                "SELECT collections.uuid, images.content_hash FROM collection_images "
                "JOIN collections ON collections.id = collection_images.collection_id "
                "JOIN images ON images.id = collection_images.image_id "
                "ORDER BY collections.uuid, images.content_hash"
            ))
        return {"collections": collections, "links": links, "memberships": memberships}

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

    async def test_synced_rating_preserves_develop_edit_interleaved_with_upsert(self):
        path = self._catalog()
        with sqlite3.connect(path) as conn:
            conn.execute(
                "INSERT INTO develop_settings(image_id, settings, origin, updated_at) VALUES (1, ?, 'user', 'before')",
                (json.dumps({"Exposure2012": 0.0}),),
            )
            conn.execute(
                "CREATE TRIGGER interleave_develop_before_rating BEFORE INSERT ON develop_settings "
                "WHEN NEW.image_id = 1 BEGIN "
                "UPDATE develop_settings SET settings = json_set(settings, '$.Exposure2012', 1.75) "
                "WHERE image_id = NEW.image_id; END"
            )
            conn.commit()

        await oplog.apply_entries(path, [{
            "origin": "satellite",
            "origin_seq": 1,
            "content_hash": HASH_A,
            "family": "rating",
            "payload": {"value": 4},
            "ts": 200.0,
        }], applied_from="satellite", receive_time=500.0)

        with sqlite3.connect(path) as conn:
            settings = json.loads(conn.execute(
                "SELECT settings FROM develop_settings WHERE image_id = 1"
            ).fetchone()[0])
        self.assertEqual(settings, {"Exposure2012": 1.75, "_lr_rating": 4})

    async def test_rating_clock_does_not_bump_develop_timestamp(self):
        path = self._catalog()
        with sqlite3.connect(path) as conn:
            conn.execute(
                "INSERT INTO develop_settings(image_id, settings, origin, updated_at) VALUES (1, ?, 'sync', 'before')",
                (json.dumps({"Exposure2012": 0.5}),),
            )
            conn.commit()

        await oplog.apply_entries(path, [{
            "origin": "satellite",
            "origin_seq": 1,
            "content_hash": HASH_A,
            "family": "rating",
            "payload": {"value": 4},
            "ts": 300.0,
        }], applied_from="satellite", receive_time=500.0)

        with sqlite3.connect(path) as conn:
            updated_at = conn.execute(
                "SELECT updated_at FROM develop_settings WHERE image_id = 1"
            ).fetchone()[0]
        self.assertEqual(updated_at, "before")

        await oplog.apply_entries(path, [{
            "origin": "satellite",
            "origin_seq": 2,
            "content_hash": HASH_A,
            "family": "develop",
            "payload": {
                "settings": {"Exposure2012": 1.5},
                "updated_at": oplog._iso_timestamp(200.0),
                "origin": "sync",
            },
            "ts": 200.0,
        }], applied_from="satellite", receive_time=500.0)

        with sqlite3.connect(path) as conn:
            settings = json.loads(conn.execute(
                "SELECT settings FROM develop_settings WHERE image_id = 1"
            ).fetchone()[0])
            family_clocks = dict(conn.execute(
                "SELECT family, ts FROM oplog_family_state WHERE content_hash = ?",
                (HASH_A,),
            ))
        self.assertEqual(settings, {"Exposure2012": 1.5, "_lr_rating": 4})
        self.assertEqual(family_clocks, {"develop": 200.0, "rating": 300.0})

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

    async def test_collection_ops_converge_under_randomized_interleaving(self):
        alpha = [
            {
                "origin": "alpha", "origin_seq": 1, "content_hash": oplog.COLLECTION_CONTENT_HASH,
                "family": "collection_meta",
                "payload": {"collection_uuid": COLLECTION_ROOT, "name": "Trips", "parent_uuid": None, "deleted": False},
                "ts": 100.0,
            },
            {
                "origin": "alpha", "origin_seq": 2, "content_hash": oplog.COLLECTION_CONTENT_HASH,
                "family": "collection_membership",
                "payload": {"collection_uuid": COLLECTION_CHILD, "content_hash": HASH_A, "member": True},
                "ts": 120.0,
            },
            {
                "origin": "alpha", "origin_seq": 3, "content_hash": oplog.COLLECTION_CONTENT_HASH,
                "family": "collection_membership",
                "payload": {"collection_uuid": COLLECTION_CHILD, "content_hash": HASH_B, "member": True},
                "ts": 140.0,
            },
        ]
        beta = [
            {
                "origin": "beta", "origin_seq": 1, "content_hash": oplog.COLLECTION_CONTENT_HASH,
                "family": "collection_meta",
                "payload": {"collection_uuid": COLLECTION_CHILD, "name": "Beach", "parent_uuid": COLLECTION_ROOT, "deleted": False},
                "ts": 110.0,
            },
            {
                "origin": "beta", "origin_seq": 2, "content_hash": oplog.COLLECTION_CONTENT_HASH,
                "family": "collection_meta",
                "payload": {"collection_uuid": COLLECTION_CHILD, "name": "Beach finalists", "parent_uuid": COLLECTION_ROOT, "deleted": False},
                "ts": 130.0,
            },
            {
                "origin": "beta", "origin_seq": 3, "content_hash": oplog.COLLECTION_CONTENT_HASH,
                "family": "collection_membership",
                "payload": {"collection_uuid": COLLECTION_CHILD, "content_hash": HASH_A, "member": False},
                "ts": 150.0,
            },
        ]
        node_a = self._catalog()
        node_b = self._catalog()
        await oplog.apply_entries(node_a, alpha, applied_from="alpha", receive_time=500.0)
        await oplog.apply_entries(node_b, beta, applied_from="beta", receive_time=500.0)

        rng = random.Random(20260712)
        for _ in range(12):
            shuffled = (alpha + beta) * 2
            rng.shuffle(shuffled)
            await oplog.apply_entries(node_a, shuffled, applied_from="relay", receive_time=500.0)
            rng.shuffle(shuffled)
            await oplog.apply_entries(node_b, shuffled, applied_from="relay", receive_time=500.0)

        expected = {
            "collections": [(COLLECTION_ROOT, "Trips"), (COLLECTION_CHILD, "Beach finalists")],
            "links": [(COLLECTION_ROOT, COLLECTION_CHILD)],
            "memberships": [(COLLECTION_CHILD, HASH_B)],
        }
        self.assertEqual(self._collection_snapshot(node_a), expected)
        self.assertEqual(self._collection_snapshot(node_b), expected)

    async def test_membership_before_collection_meta_applies_by_batch_end(self):
        path = self._catalog()
        entries = [
            {
                "origin": "alpha", "origin_seq": 1, "content_hash": oplog.COLLECTION_CONTENT_HASH,
                "family": "collection_membership",
                "payload": {"collection_uuid": COLLECTION_CHILD, "content_hash": HASH_A, "member": True},
                "ts": 100.0,
            },
            {
                "origin": "beta", "origin_seq": 1, "content_hash": oplog.COLLECTION_CONTENT_HASH,
                "family": "collection_meta",
                "payload": {"collection_uuid": COLLECTION_CHILD, "name": "Beach", "parent_uuid": None, "deleted": False},
                "ts": 110.0,
            },
        ]

        await oplog.apply_entries(path, entries, applied_from="hub", receive_time=500.0)

        self.assertEqual(
            self._collection_snapshot(path)["memberships"],
            [(COLLECTION_CHILD, HASH_A)],
        )
        self.assertEqual(self._pending_count(path), 0)

    async def test_unknown_collection_membership_materializes_on_next_exchange(self):
        hub = self._catalog()
        satellite = self._catalog()
        membership = {
            "origin": "camera", "origin_seq": 1, "content_hash": oplog.COLLECTION_CONTENT_HASH,
            "family": "collection_membership",
            "payload": {"collection_uuid": COLLECTION_CHILD, "content_hash": HASH_A, "member": True},
            "ts": 100.0,
        }
        collection_meta = {
            "origin": "desktop", "origin_seq": 1, "content_hash": oplog.COLLECTION_CONTENT_HASH,
            "family": "collection_meta",
            "payload": {"collection_uuid": COLLECTION_CHILD, "name": "Beach", "parent_uuid": None, "deleted": False},
            "ts": 110.0,
        }
        await oplog.apply_entries(hub, [membership], applied_from="camera", receive_time=500.0)

        async def hub_request(method, path, payload):
            self.assertEqual(method, "POST")
            if path.endswith("/push"):
                return await oplog.apply_entries(
                    hub, payload["entries"], applied_from=payload["device_id"]
                )
            return await oplog.pull_entries(
                hub, device=payload["device_id"], cursors=payload["cursors"]
            )

        await oplog.exchange_with_hub(satellite, hub_request)
        self.assertEqual(self._collection_snapshot(satellite)["memberships"], [])

        await oplog.apply_entries(hub, [collection_meta], applied_from="desktop", receive_time=500.0)
        await oplog.exchange_with_hub(satellite, hub_request)

        self.assertEqual(
            self._collection_snapshot(satellite)["memberships"],
            [(COLLECTION_CHILD, HASH_A)],
        )
        self.assertEqual(self._pending_count(satellite), 0)

    async def test_unknown_content_hash_applies_after_image_arrives(self):
        path = self._catalog()
        entry = {
            "origin": "alpha", "origin_seq": 1, "content_hash": HASH_C,
            "family": "flag", "payload": {"value": "picked"}, "ts": 100.0,
        }
        await oplog.apply_entries(path, [entry], applied_from="hub", receive_time=500.0)
        with sqlite3.connect(path) as conn:
            conn.execute("INSERT INTO images(id, content_hash) VALUES (3, ?)", (HASH_C,))
            conn.commit()

        result = await oplog.retry_pending_entries(path)

        self.assertEqual(result, {"retried": 1, "applied": 1, "still_pending": 0})
        self.assertEqual(self._flags(path)[HASH_C], "picked")
        self.assertEqual(self._pending_count(path), 0)

    async def test_permanently_unknown_hash_has_one_pending_row_across_retries(self):
        path = self._catalog()
        entry = {
            "origin": "alpha", "origin_seq": 1, "content_hash": HASH_C,
            "family": "flag", "payload": {"value": "picked"}, "ts": 100.0,
        }
        await oplog.apply_entries(path, [entry], applied_from="hub", receive_time=500.0)

        for _ in range(5):
            result = await oplog.retry_pending_entries(path)
            self.assertEqual(result, {"retried": 1, "applied": 0, "still_pending": 1})

        self.assertEqual(self._pending_count(path), 1)


if __name__ == "__main__":
    unittest.main()
