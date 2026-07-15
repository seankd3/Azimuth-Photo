from test_support import *  # noqa: F401,F403

from fastapi.testclient import TestClient

from data import connection as data_connection
from data import schema as data_schema
from core import wiring
from features.collections import graph as collection_graph
from features.publish import nodes as published_nodes
from features.publish.builder import website_tree_manifest


class PublishedNodeTests(BackendTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        wiring.configure_publish_routes(
            templates=app_module.app.state.photoarchive_shell.templates,
        )

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
                    image_id INTEGER NOT NULL REFERENCES images(id) ON DELETE CASCADE,
                    position INTEGER NOT NULL DEFAULT 0,
                    added_at REAL NOT NULL DEFAULT (strftime('%s', 'now')),
                    PRIMARY KEY (share_id, image_id)
                );
                CREATE TABLE share_favorites (
                    id INTEGER PRIMARY KEY,
                    share_id INTEGER NOT NULL REFERENCES collection_shares(id),
                    image_id INTEGER NOT NULL,
                    client_name TEXT NULL,
                    created_at REAL NOT NULL,
                    UNIQUE(share_id, image_id)
                );
                INSERT INTO collections(id) VALUES (1);
                INSERT INTO images(id) VALUES (9);
                INSERT INTO collection_shares(id, collection_id, token, created_at) VALUES (4, 1, 'kept-token', 10);
                INSERT INTO share_images(share_id, image_id) VALUES (4, 9);
                INSERT INTO share_favorites(id, share_id, image_id, client_name, created_at)
                VALUES (7, 4, 9, 'Client', 11);
                """
            )
            await data_schema.migrate_collection_shares_for_published_nodes(conn)
            await data_schema.migrate_collection_shares_for_published_nodes(conn)
            await data_schema.migrate_share_owner_cascades(conn)
            await data_schema.migrate_share_owner_cascades(conn)
            cursor = await conn.execute("SELECT * FROM collection_shares")
            share = dict(await cursor.fetchone())
            cursor = await conn.execute("SELECT COUNT(*) AS count FROM share_images")
            image_count = int((await cursor.fetchone())["count"])
            cursor = await conn.execute("SELECT COUNT(*) AS count FROM share_favorites")
            favorite_count = int((await cursor.fetchone())["count"])
            cursor = await conn.execute("PRAGMA foreign_key_list(share_images)")
            image_fks = [dict(row) for row in await cursor.fetchall()]
            cursor = await conn.execute("PRAGMA foreign_key_list(share_favorites)")
            favorite_fks = [dict(row) for row in await cursor.fetchall()]
        finally:
            await data_connection.close_async(conn, db_path=migration_path)

        self.assertEqual(share["token"], "kept-token")
        self.assertEqual(share["collection_id"], 1)
        self.assertIsNone(share["published_node_id"])
        self.assertEqual(image_count, 1)
        self.assertEqual(favorite_count, 1)
        self.assertTrue(os.path.exists(f"{migration_path}.pre-v20.bak"))
        self.assertEqual(
            next(row["on_delete"] for row in image_fks if row["from"] == "share_id"),
            "CASCADE",
        )
        self.assertEqual(
            next(row["on_delete"] for row in favorite_fks if row["from"] == "share_id"),
            "CASCADE",
        )

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

    async def test_published_snapshot_hides_member_that_is_later_trashed(self):
        source = await self._source("published-trash")
        image_id = await self._image(source["id"], "trashed-after-publish.jpg")
        collection = await db.create_collection(name="Published", image_ids=[image_id])
        node = await published_nodes.create_snapshot_tree(
            db.DB_PATH,
            area="website",
            parent_id=None,
            source_collection_id=collection["id"],
            slug="published",
            title=None,
            resolve_smart_image_ids=self._resolve_smart,
        )

        await db.set_image_status(image_id, "trashed")
        refreshed = await published_nodes.get_node(db.DB_PATH, node["id"])

        self.assertEqual(await self._node_image_ids(node["id"]), [])
        self.assertEqual(refreshed["image_count"], 0)

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

    async def test_published_private_link_is_in_shared_aggregation(self):
        collection = await db.create_collection(name="Client delivery")
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

        def probe():
            with TestClient(app_module.app) as client:
                return client.get("/api/shares")

        response = await asyncio.to_thread(probe)
        self.assertEqual(response.status_code, 200, response.text)
        item = response.json()["items"][0]
        self.assertIsNone(item["collection_id"])
        self.assertEqual(item["published_node_id"], node["id"])
        self.assertEqual(item["private_link"]["token"], share["token"])

    async def test_root_share_serves_entire_subtree_once_in_document_order(self):
        source = await self._source()
        root_image = await self._image(source["id"], "root.jpg")
        shared_image = await self._image(source["id"], "shared.jpg")
        child_image = await self._image(source["id"], "child.jpg")
        root = await db.create_collection(name="Delivery", image_ids=[root_image, shared_image])
        child = await db.create_collection(name="Chapter", image_ids=[shared_image, child_image])
        await collection_graph.add_link(db.DB_PATH, root["id"], child["id"], 0)

        def create_delivery():
            with TestClient(app_module.app) as client:
                return client.post(
                    "/api/published/nodes",
                    json={"area": "private", "source_collection_id": root["id"]},
                )

        created = await asyncio.to_thread(create_delivery)
        self.assertEqual(created.status_code, 200)
        root_node = created.json()["node"]
        share = created.json()["share"]
        tree = await published_nodes.published_tree(db.DB_PATH, "private")
        child_node = next(node for node in tree["nodes"] if node["parent_id"] == root_node["id"])

        resolved = await db.resolve_share_token(share["token"])

        self.assertEqual(
            [image["id"] for image in resolved["images"]],
            [root_image, shared_image, child_image],
        )
        self.assertTrue(await db.share_token_allows_image(share["token"], child_image))
        self.assertTrue(await db.set_share_favorite(share["id"], child_image, True))
        self.assertIsNone(child_node["share_token"])

    async def test_published_share_lists_and_revokes_by_node_route(self):
        collection = await db.create_collection(name="Client delivery")
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

        listed = await db.list_active_collection_shares()
        published = next(item for item in listed if item["published_node_id"] == node["id"])
        self.assertEqual(published["collection_name"], "Client delivery")

        def revoke():
            with TestClient(app_module.app) as client:
                return client.delete(f"/api/published/nodes/{node['id']}/share")

        response = await asyncio.to_thread(revoke)
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(await db.resolve_share_token(share["token"]))

    async def test_reshare_preserves_password_and_keeps_one_active_token(self):
        collection = await db.create_collection(name="Protected delivery")
        node = await published_nodes.create_snapshot_tree(
            db.DB_PATH,
            area="private",
            parent_id=None,
            source_collection_id=collection["id"],
            slug=None,
            title=None,
            resolve_smart_image_ids=self._resolve_smart,
        )

        def share_requests():
            with TestClient(app_module.app) as client:
                protected = client.post(
                    f"/api/published/nodes/{node['id']}/share",
                    json={"password": "secret"},
                )
                unchanged = client.post(
                    f"/api/published/nodes/{node['id']}/share",
                    json={},
                )
                cleared = client.post(
                    f"/api/published/nodes/{node['id']}/share",
                    json={"password": ""},
                )
                return protected, unchanged, cleared

        protected, unchanged, cleared = await asyncio.to_thread(share_requests)
        self.assertEqual(protected.status_code, 200)
        self.assertEqual(unchanged.status_code, 200)
        self.assertEqual(protected.json()["share"]["token"], unchanged.json()["share"]["token"])
        self.assertTrue(unchanged.json()["share"]["protected"])
        self.assertFalse(cleared.json()["share"]["protected"])
        active = await db.create_published_node_share(node["id"])
        self.assertIsNone(active["password_hash"])

        conn = await data_connection.open_async(db.DB_PATH)
        try:
            cursor = await conn.execute(
                """
                SELECT COUNT(*) AS count FROM collection_shares
                WHERE published_node_id = ? AND revoked_at IS NULL
                """,
                (node["id"],),
            )
            active_count = int((await cursor.fetchone())["count"])
        finally:
            await data_connection.close_async(conn, db_path=db.DB_PATH)
        self.assertEqual(active_count, 1)

    async def test_delete_node_with_client_favorite_cascades_share_state(self):
        source = await self._source()
        image_id = await self._image(source["id"], "favorite.jpg")
        collection = await db.create_collection(name="Disposable delivery", image_ids=[image_id])
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
        self.assertTrue(await db.set_share_favorite(share["id"], image_id, True))

        self.assertTrue(await published_nodes.delete_node(db.DB_PATH, node["id"]))
        self.assertIsNone(await db.resolve_share_token(share["token"]))
        self.assertEqual(await db.list_share_favorites(share["id"]), [])

    async def test_website_area_export_route_writes_configured_tree(self):
        collection = await db.create_collection(name="Portfolio")
        await published_nodes.create_snapshot_tree(
            db.DB_PATH,
            area="website",
            parent_id=None,
            source_collection_id=collection["id"],
            slug="portfolio",
            title=None,
            resolve_smart_image_ids=self._resolve_smart,
        )
        destination = os.path.join(self.tempdir.name, "published")
        settings.save_settings({"publish_dir": destination})

        def export():
            with TestClient(app_module.app) as client:
                return client.post("/api/published/export?area=website")

        response = await asyncio.to_thread(export)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["manifest"]["slug"], "portfolio")
        self.assertTrue(os.path.exists(os.path.join(destination, "portfolio", "index.html")))
        self.assertTrue(os.path.exists(os.path.join(destination, "manifest.json")))

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
