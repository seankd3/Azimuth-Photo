"""Keyword taxonomy, inherited matching, and IPTC storage contracts."""

from __future__ import annotations

import sqlite3
import xml.etree.ElementTree as etree

from test_support import BackendTestCase
import db

from features.library import keyword_routes, keywords


class KeywordTests(BackendTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.source = await self._source("keywords")
        self.first = await self._image(self.source["id"], "first.dng")
        self.second = await self._image(self.source["id"], "second.dng")

    async def test_path_creation_preserves_leaf_assignments_and_resolves_ancestors(self):
        cat = await keywords.resolve_keyword_path("Animals > Cats > Maine Coon")
        dog = await keywords.resolve_keyword_path("Animals > Dogs")
        await keywords.assign_keyword([self.first], cat["id"])
        await keywords.assign_keyword([self.second], dog["id"])

        rows = await keywords.list_keywords()
        paths = {row["path"]: row for row in rows}
        self.assertEqual(paths["Animals > Cats > Maine Coon"]["direct_count"], 1)
        self.assertEqual(await keywords.keyword_image_ids(paths["Animals"]["id"]), [self.first, self.second])
        self.assertEqual(await keywords.keyword_image_ids(paths["Animals > Cats"]["id"]), [self.first])

        attached = await keywords.image_keywords(self.first)
        self.assertEqual([(row["path"], row["direct"]) for row in attached], [
            ("Animals", 0), ("Animals > Cats", 0), ("Animals > Cats > Maine Coon", 1),
        ])

    async def test_batch_assign_unassign_and_iptc_are_idempotent(self):
        keyword = await keywords.resolve_keyword_path("Travel > Iceland")
        self.assertGreaterEqual(await keywords.assign_keyword([self.first, self.second], keyword["id"]), 1)
        self.assertEqual(await keywords.keyword_image_ids(keyword["id"]), [self.first, self.second])
        self.assertEqual(await keywords.unassign_keyword([self.first], keyword["id"]), 1)
        self.assertEqual(await keywords.keyword_image_ids(keyword["id"]), [self.second])

        saved = await keywords.save_iptc(
            self.first, title="Aurora", caption="Northern lights", copyright="Sean", creator="Sean McAvoy",
        )
        self.assertEqual(saved["title"], "Aurora")
        self.assertEqual((await keywords.get_iptc(self.first))["creator"], "Sean McAvoy")

    async def test_routes_cover_resolution_assignment_and_iptc(self):
        resolved = await keyword_routes.api_resolve_keyword(keyword_routes.KeywordPath(path="People > Portraits"))
        keyword_id = resolved["keyword"]["id"]
        assigned = await keyword_routes.api_assign_keyword(
            keyword_routes.KeywordAssignment(keyword_id=keyword_id, image_ids=[self.first, self.second])
        )
        image_keywords = await keyword_routes.api_image_keywords(self.first)
        saved = await keyword_routes.api_save_iptc(
            self.first, keyword_routes.IptcFields(title="Portrait", creator="Sean")
        )

        self.assertGreaterEqual(assigned["assigned"], 1)
        self.assertEqual(image_keywords["keywords"][-1]["name"], "Portraits")
        self.assertEqual(saved["iptc"]["title"], "Portrait")

    async def test_lightroom_tree_and_xmp_metadata_helpers_keep_only_direct_terms(self):
        catalog = sqlite3.connect(":memory:")
        catalog.row_factory = sqlite3.Row
        catalog.executescript("""
            CREATE TABLE AgLibraryKeyword (id_local INTEGER PRIMARY KEY, name TEXT, parent INTEGER);
            CREATE TABLE AgLibraryKeywordImage (image INTEGER, tag INTEGER);
            INSERT INTO AgLibraryKeyword VALUES (1, 'Travel', NULL), (2, 'Iceland', 1);
            INSERT INTO AgLibraryKeywordImage VALUES (17, 2);
        """)
        library = sqlite3.connect(db.DB_PATH)
        library.row_factory = sqlite3.Row
        try:
            imported = keywords.import_lightroom_keywords(library, catalog, {17: self.first})
            library.commit()
        finally:
            library.close()
            catalog.close()

        await keywords.save_iptc(self.first, title="Waterfall", creator="Sean")
        payload = keywords.xmp_metadata_for_image(db.DB_PATH, self.first)
        self.assertEqual(imported, 1)
        self.assertEqual(payload["keywords"], ["Iceland"])
        self.assertEqual(payload["iptc"]["title"], "Waterfall")
        packet = keywords.decorate_xmp_packet(
            '<x:xmpmeta xmlns:x="adobe:ns:meta/" xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"><rdf:RDF><rdf:Description/></rdf:RDF></x:xmpmeta>',
            payload,
        )
        root = etree.fromstring(packet)
        subject = root.find('.//{http://purl.org/dc/elements/1.1/}subject/{http://www.w3.org/1999/02/22-rdf-syntax-ns#}Bag/{http://www.w3.org/1999/02/22-rdf-syntax-ns#}li')
        self.assertEqual(subject.text, "Iceland")
