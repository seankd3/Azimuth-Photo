from test_support import *  # noqa: F401,F403

from data import connection as data_connection
from data import schema as data_schema
from features.collections import graph as collection_graph
from features.publish import nodes as published_nodes
from features.publish.builder import website_tree_manifest


class PublishedNodeTests(BackendTestCase):
    async def _resolve_smart(self, _query):
        return []

    async def _node_image_ids(self, node_id):
        images = await published_nodes.node_images(db.DB_PATH, node_id)
        return [int(image["id"]) for image in images or []]

    async def test_collection_graph_link_cycle_rejection(self):
        first = await db.create_collection(name="First")
        second = await db.create_collection(name="Second")
        third = await db.create_collection(name="Third")

        await collection_graph.add_link(db.DB_PATH, first["id"], second["id"], 0)
        await collection_graph.add_link(db.DB_PATH, second["id"], third["id"], 0)

        with self.assertRaises(collection_graph.CollectionGraphConflict):
            await collection_graph.add_link(db.DB_PATH, third["id"], first["id"], 0)

        tree = await collection_graph.workspace_tree(db.DB_PATH)
        self.assertEqual(
            [(link["parent_id"], link["child_id"]) for link in tree["links"]],
            [(first["id"], second["id"]), (second["id"], third["id"])],
        )

    async def test_collection_share_owner_migration_is_idempotent_and_preserves_rows(self):
        migration_path = os.path.join(self.tempdir.name, "legacy-share.db")
        conn = await data_connection.open_async(migration_path)
        try:
            await conn.executescript(
                """
                CREATE TABLE collections (id INTEGER PRIMARY KEY);
                CREATE TABLE published_nodes (id INTEGER PRIMARY KEY);
                CREATE TABLE images (id INTEGER PRIMARY KEY);
                CREATE TABLE collection_shares (
                    id INTEGER PRIMARY KEY,
                    collection_id INTEGER NOT NULL REFERENCES collections(id),
                    token TEXT NOT NULL UNIQUE,
                    created_at REAL NOT NULL,
                    expires_at REAL,
                    revoked_at REAL,
                    password_hash TEXT,
                    view_count INTEGER NOT NULL DEFAULT 0,
                    first_viewed_at REAL,
                    last_viewed_at REAL
                );
                CREATE TABLE share_images (
                    share_id INTEGER NOT NULL REFERENCES collection_shares(id) ON DELETE CASCADE,
                    image_id INTEGER NOT NULL REFERENCES images(id),
                    PRIMARY KEY (share_id, image_id)
                );
                CREATE TABLE share_favorites (
                    id INTEGER PRIMARY KEY,
                    share_id INTEGER NOT NULL REFERENCES collection_shares(id),
                    image_id INTEGER NOT NULL
                );
                INSERT INTO collections(id) VALUES (1);
                INSERT INTO images(id) VALUES (9);
                INSERT INTO collection_shares(id, collection_id, token, created_at) VALUES (4, 1, 'kept-token', 10);
                INSERT INTO share_images(share_id, image_id) VALUES (4, 9);
                INSERT INTO share_favorites(id, share_id, image_id) VALUES (7, 4, 9);
                """
            )
            await data_schema.migrate_collection_shares_for_published_nodes(conn)
            await data_schema.migrate_collection_shares_for_published_nodes(conn)
            cursor = await conn.execute("SELECT * FROM collection_shares")
            share = dict(await cursor.fetchone())
            cursor = await conn.execute("SELECT COUNT(*) AS count FROM share_images")
            image_count = int((await cursor.fetchone())["count"])
            cursor = await conn.execute("SELECT COUNT(*) AS count FROM share_favorites")
            favorite_count = int((await cursor.fetchone())["count"])
        finally:
            await data_connection.close_async(conn, db_path=migration_path)

        self.assertEqual(share["token"], "kept-token")
        self.assertEqual(share["collection_id"], 1)
        self.assertIsNone(share["published_node_id"])
        self.assertEqual(image_count, 1)
        self.assertEqual(favorite_count, 1)

    async def test_recursive_membership_is_own_first_ordered_and_deduplicated(self):
        source = await self._source()
        first = await self._image(source["id"], "first.jpg")
        shared = await self._image(source["id"], "shared.jpg")
        second = await self._image(source["id"], "second.jpg")
        third = await self._image(source["id"], "third.jpg")
        root = await db.create_collection(name="Root", image_ids=[first, shared])
        child = await db.create_collection(name="Child", image_ids=[shared, second])
        sibling = await db.create_collection(name="Sibling", image_ids=[third])
        await collection_graph.add_link(db.DB_PATH, root["id"], sibling["id"], 20)
        await collection_graph.add_link(db.DB_PATH, root["id"], child["id"], 10)

        image_ids = await collection_graph.recursive_image_ids(
            db.DB_PATH,
            root["id"],
            recursive=True,
            resolve_smart_image_ids=self._resolve_smart,
        )

        self.assertEqual(image_ids, [first, shared, second, third])

    async def test_snapshot_is_independent_and_diff_reports_only_current_changes(self):
        source = await self._source()
        first = await self._image(source["id"], "first.jpg")
        second = await self._image(source["id"], "second.jpg")
        third = await self._image(source["id"], "third.jpg")
        root = await db.create_collection(name="Root", image_ids=[first])
        child = await db.create_collection(name="Child", image_ids=[second])
        await collection_graph.add_link(db.DB_PATH, root["id"], child["id"], 0)

        node = await published_nodes.create_snapshot_tree(
            db.DB_PATH,
            area="website",
            parent_id=None,
            source_collection_id=root["id"],
            slug="root",
            title=None,
            resolve_smart_image_ids=self._resolve_smart,
        )
        destination_tree = await published_nodes.published_tree(db.DB_PATH, "website")
        child_node = next(item for item in destination_tree["nodes"] if item["parent_id"] == node["id"])

        await db.add_collection_images(root["id"], [third])
        await db.remove_collection_images(root["id"], [first])

        self.assertEqual(await self._node_image_ids(node["id"]), [first])
        self.assertEqual(await self._node_image_ids(child_node["id"]), [second])
        diff = await published_nodes.node_diff(
            db.DB_PATH,
            node["id"],
            resolve_smart_image_ids=self._resolve_smart,
        )
        self.assertEqual(diff["added"], [third])
        self.assertEqual(diff["removed"], [first])
        self.assertFalse(diff["source_deleted"])

    async def test_explicit_update_applies_only_accepted_changes_and_attaches_children(self):
        source = await self._source()
        first = await self._image(source["id"], "first.jpg")
        second = await self._image(source["id"], "second.jpg")
        child_image = await self._image(source["id"], "child.jpg")
        root = await db.create_collection(name="Root", image_ids=[first])
        node = await published_nodes.create_snapshot_tree(
            db.DB_PATH,
            area="website",
            parent_id=None,
            source_collection_id=root["id"],
            slug=None,
            title=None,
            resolve_smart_image_ids=self._resolve_smart,
        )

        await db.add_collection_images(root["id"], [second])
        await db.remove_collection_images(root["id"], [first])
        child = await db.create_collection(name="New child", image_ids=[child_image])
        await collection_graph.add_link(db.DB_PATH, root["id"], child["id"], 7)

        diff = await published_nodes.node_diff(
            db.DB_PATH,
            node["id"],
            resolve_smart_image_ids=self._resolve_smart,
        )
        self.assertEqual(diff["attachable_children"], [child["id"]])
        await published_nodes.update_node(
            db.DB_PATH,
            node["id"],
            add_image_ids=[second],
            remove_image_ids=[],
            attach_child_collection_ids=[child["id"]],
            resolve_smart_image_ids=self._resolve_smart,
        )

        self.assertEqual(await self._node_image_ids(node["id"]), [first, second])
        destination_tree = await published_nodes.published_tree(db.DB_PATH, "website")
        attached = next(item for item in destination_tree["nodes"] if item["parent_id"] == node["id"])
        self.assertEqual(attached["source_collection_id"], child["id"])
        self.assertEqual(await self._node_image_ids(attached["id"]), [child_image])
        remaining = await published_nodes.node_diff(
            db.DB_PATH,
            node["id"],
            resolve_smart_image_ids=self._resolve_smart,
        )
        self.assertEqual(remaining["added"], [])
        self.assertEqual(remaining["removed"], [first])
        self.assertEqual(remaining["attachable_children"], [])

    async def test_deleted_source_keeps_snapshot_and_returns_gone_diff(self):
        source = await self._source()
        image_id = await self._image(source["id"], "kept.jpg")
        collection = await db.create_collection(name="Temporary source", image_ids=[image_id])
        node = await published_nodes.create_snapshot_tree(
            db.DB_PATH,
            area="website",
            parent_id=None,
            source_collection_id=collection["id"],
            slug=None,
            title=None,
            resolve_smart_image_ids=self._resolve_smart,
        )

        await db.delete_collection(collection["id"])
        diff = await published_nodes.node_diff(
            db.DB_PATH,
            node["id"],
            resolve_smart_image_ids=self._resolve_smart,
        )

        self.assertTrue(diff["source_deleted"])
        self.assertEqual(diff["added"], [])
        self.assertEqual(diff["removed"], [])
        self.assertEqual(await self._node_image_ids(node["id"]), [image_id])

    async def test_private_node_share_serves_destination_snapshot(self):
        source = await self._source()
        first = await self._image(source["id"], "first.jpg")
        later = await self._image(source["id"], "later.jpg")
        collection = await db.create_collection(name="Private delivery", image_ids=[first])
        node = await published_nodes.create_snapshot_tree(
            db.DB_PATH,
            area="private",
            parent_id=None,
            source_collection_id=collection["id"],
            slug=None,
            title=None,
            resolve_smart_image_ids=self._resolve_smart,
        )
        share = await db.create_published_node_share(node["id"])

        await db.add_collection_images(collection["id"], [later])
        resolved = await db.resolve_share_token(share["token"])

        self.assertEqual(resolved["published_node_id"], node["id"])
        self.assertEqual([image["id"] for image in resolved["images"]], [first])
        self.assertTrue(await db.share_token_allows_image(share["token"], first))
        self.assertFalse(await db.share_token_allows_image(share["token"], later))

    async def test_website_manifest_walks_nested_nodes_with_photo_fields(self):
        source = await self._source()
        first = await self._image(source["id"], "first.jpg")
        second = await self._image(source["id"], "second.jpg")
        root = await db.create_collection(name="Portfolio", image_ids=[first])
        child = await db.create_collection(name="Travel", image_ids=[second])
        await collection_graph.add_link(db.DB_PATH, root["id"], child["id"], 0)
        node = await published_nodes.create_snapshot_tree(
            db.DB_PATH,
            area="website",
            parent_id=None,
            source_collection_id=root["id"],
            slug="portfolio",
            title=None,
            resolve_smart_image_ids=self._resolve_smart,
        )
        tree = await published_nodes.published_tree(db.DB_PATH, "website")
        images_by_node = {
            item["id"]: await published_nodes.node_images(db.DB_PATH, item["id"])
            for item in tree["nodes"]
        }

        manifest = website_tree_manifest(tree, images_by_node)

        self.assertEqual(manifest["slug"], "portfolio")
        self.assertEqual(manifest["photos"][0]["id"], first)
        self.assertEqual(
            manifest["photos"][0]["urls"]["thumb"],
            f"/g/portfolio/thumb/sm/{first}.jpg",
        )
        self.assertEqual(manifest["children"][0]["slug"], "travel")
        self.assertEqual(manifest["children"][0]["photos"][0]["id"], second)
        self.assertEqual(manifest["children"][0]["photos"][0]["caption"], "")
        self.assertEqual(node["source_collection_id"], root["id"])
