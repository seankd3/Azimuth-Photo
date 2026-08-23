"""Keywords, now that they are sets.

Four tests became one, because three of them covered a keyword *table* — a tree,
its ancestors and a cascade — and that table is gone. What is left is the
property that actually broke: **the XMP write-back must carry the photograph's
keywords.** During this refactor it briefly did not, because the write-back
still read `image_keywords` after keywords had moved to the log, and the failure
mode is silent — Lightroom reads back an empty `dc:subject` and the owner's
keywords are erased in the file, not in a database anyone can restore.

That is the whole bar for a test here: it holds a property that already broke.
"""

from __future__ import annotations
from core.catalog_path import catalog_path

import contextlib
import sqlite3

from test_support import BackendTestCase
import db
from features.library import keywords
from model import photos, sets


class KeywordTests(BackendTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.source = await self._source("keywords")
        self.first = await self._image(self.source["id"], "first.dng")
        self.second = await self._image(self.source["id"], "second.dng")
        # A keyword is a decision about a photograph's identity, so the fixture
        # needs one. The importer hashes on the way in; this stands in for that.
        with self._conn() as conn:
            for image_id, digest in ((self.first, "hash-first"), (self.second, "hash-second")):
                conn.execute("UPDATE images SET content_hash = ? WHERE id = ?", (digest, image_id))
            conn.commit()

    @contextlib.contextmanager
    def _conn(self):
        # Closed on the way out: Windows will not delete the temp catalog while
        # a handle is open, so a leaked connection fails teardown, not the test.
        conn = sqlite3.connect(catalog_path())
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    async def test_assigned_keywords_reach_the_xmp_write_back(self):
        image_id = self.first
        with self._conn() as conn:
            digest = photos.hashes(conn, [image_id])
            self.assertTrue(digest, "the fixture photograph needs a content hash")
            animals = sets.create(conn, keywords.clean_path("Animals > Cats"), kind=sets.LABEL)
            sets.add(conn, animals, digest)
            conn.commit()

        packet = keywords.xmp_metadata_for_image(catalog_path(), image_id)
        self.assertEqual(packet["keywords"], ["Animals > Cats"])

    async def test_a_removed_keyword_leaves_the_xmp_write_back(self):
        image_id = self.second
        with self._conn() as conn:
            digest = photos.hashes(conn, [image_id])
            keyword = sets.create(conn, "Travel", kind=sets.LABEL)
            sets.add(conn, keyword, digest)
            sets.remove(conn, keyword, digest)
            conn.commit()

        self.assertEqual(keywords.xmp_metadata_for_image(catalog_path(), image_id)["keywords"], [])

    def test_a_path_is_the_hierarchy(self):
        """No parent column, so spacing is the only thing that can differ."""

        self.assertEqual(keywords.clean_path("Animals>Cats"), "Animals > Cats")
        self.assertEqual(keywords.clean_path("Animals  >   Cats "), "Animals > Cats")
        with self.assertRaises(ValueError):
            keywords.clean_path("   ")
