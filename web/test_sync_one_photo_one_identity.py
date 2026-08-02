"""A hub photo names one local row, never every copy of it.

Libraries hold the same photo twice all the time — a re-import, an export
written back beside its original, a Takeout archive. The upload bookkeeping
stamped the hub's id onto every row sharing a content hash in a single
statement, which asks the unique index for the impossible and took the whole
batch down with it. Measured on the owner's laptop: 10,689 content hashes span
more than one row, and the resulting "UNIQUE constraint failed:
images.hub_image_id" is what stopped sync from finishing.
"""

import asyncio
import os
import sqlite3
import tempfile
import unittest

from features.sync.sync_worker import _give_hub_identity_to_one_row

SCHEMA = """
CREATE TABLE images (
    id INTEGER PRIMARY KEY,
    content_hash TEXT,
    hub_image_id INTEGER DEFAULT NULL,
    hub_remote INTEGER DEFAULT 0
);
CREATE UNIQUE INDEX idx_images_hub_image_id
ON images(hub_image_id) WHERE hub_image_id IS NOT NULL;
"""


class _Cursor:
    """The two async calls the helper makes, over a plain sqlite3 cursor."""

    def __init__(self, cursor):
        self._cursor = cursor

    async def fetchone(self):
        return self._cursor.fetchone()


class _Conn:
    def __init__(self, connection):
        self._connection = connection
        self._connection.row_factory = sqlite3.Row

    async def execute(self, sql, params=()):
        return _Cursor(self._connection.execute(sql, params))


class OneIdentityPerPhotoTests(unittest.TestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        self.raw = sqlite3.connect(self.path)
        self.raw.executescript(SCHEMA)
        self.conn = _Conn(self.raw)

    def tearDown(self):
        self.raw.close()
        os.unlink(self.path)

    def _add(self, image_id: int, content_hash: str, hub_image_id=None):
        self.raw.execute(
            "INSERT INTO images (id, content_hash, hub_image_id) VALUES (?, ?, ?)",
            (image_id, content_hash, hub_image_id),
        )

    def _identities(self):
        return {
            row[0]: row[1]
            for row in self.raw.execute("SELECT id, hub_image_id FROM images ORDER BY id")
        }

    def _stamp(self, content_hash: str, hub_image_id: int):
        asyncio.run(_give_hub_identity_to_one_row(self.conn, content_hash, hub_image_id))

    def test_three_copies_of_one_photo_do_not_all_claim_the_hub_id(self):
        """The failure: this used to raise and abort the whole upload batch."""

        for image_id in (1, 2, 3):
            self._add(image_id, "samehash")
        self._stamp("samehash", 900)
        self.assertEqual(self._identities(), {1: 900, 2: None, 3: None})

    def test_a_single_copy_is_claimed_as_before(self):
        self._add(1, "onlyone")
        self._stamp("onlyone", 901)
        self.assertEqual(self._identities(), {1: 901})

    def test_a_row_that_already_holds_the_identity_keeps_it(self):
        self._add(1, "samehash", 902)
        self._add(2, "samehash")
        self._stamp("samehash", 902)
        self.assertEqual(self._identities(), {1: 902, 2: None})

    def test_a_copy_already_claimed_by_another_hub_photo_is_left_alone(self):
        self._add(1, "samehash", 800)
        self._add(2, "samehash")
        self._stamp("samehash", 903)
        self.assertEqual(
            self._identities(), {1: 800, 2: 903}, "the unclaimed copy takes it"
        )

    def test_an_unknown_hash_changes_nothing(self):
        self._add(1, "somehash")
        self._stamp("missing", 904)
        self.assertEqual(self._identities(), {1: None})

    def test_stamping_twice_is_not_an_error(self):
        self._add(1, "samehash")
        self._add(2, "samehash")
        self._stamp("samehash", 905)
        self._stamp("samehash", 905)
        self.assertEqual(self._identities(), {1: 905, 2: None})


if __name__ == "__main__":
    unittest.main()
