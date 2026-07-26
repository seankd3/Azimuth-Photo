import asyncio
import glob
import io
import tempfile
import time
import zipfile
from pathlib import Path

from fastapi.responses import Response
from fastapi.testclient import TestClient

from test_support import *  # noqa: F401,F403
from data import connection as data_connection
from data import schema as data_schema
from features.collections import routes as collection_routes
from features.share import auth as share_auth
from features.share import routes as share_routes


class ShareTests(BackendTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        templates = app_module.app.state.azimuth_shell.templates
        share_routes.configure(
            templates=templates,
            create_or_rotate_share=lambda collection_id, **kwargs: db.create_or_rotate_share(
                collection_id,
                **kwargs,
            ),
            get_share=lambda collection_id: db.get_collection_share(collection_id),
            revoke_share=lambda collection_id: db.revoke_collection_share(collection_id),
            set_share_password=lambda collection_id, password_hash: db.set_collection_share_password(
                collection_id,
                password_hash,
            ),
            record_share_view=lambda token: db.record_share_view(token),
            resolve_token=lambda token: db.resolve_share_token(token),
            token_allows_image=lambda token, image_id: db.share_token_allows_image(token, image_id),
            set_favorite=lambda share_id, image_id, on, client_name=None, visitor_id="legacy": db.set_share_favorite(
                share_id,
                image_id,
                on,
                client_name=client_name,
                visitor_id=visitor_id,
            ),
            mark_finished=lambda share_id: db.mark_share_finished(share_id),
            list_favorites=lambda share_id, visitor_id=None: db.list_share_favorites(
                share_id,
                visitor_id=visitor_id,
            ),
            favorites_for_collection=lambda collection_id: db.favorites_for_collection(collection_id),
            favorite_visitors_for_collection=lambda collection_id: db.favorite_visitors_for_collection(collection_id),
            thumbnail_response=self._thumbnail_response,
        )
        collection_routes.configure(
            create_collection=lambda **kwargs: db.create_collection(**kwargs),
            list_collections=lambda: db.list_collections(),
            get_collection=lambda collection_id, **kwargs: db.get_collection(collection_id, **kwargs),
            rename_collection=lambda collection_id, **kwargs: db.rename_collection(collection_id, **kwargs),
            delete_collection=lambda collection_id: db.delete_collection(collection_id),
            add_collection_images=lambda collection_id, image_ids: db.add_collection_images(collection_id, image_ids),
            remove_collection_images=lambda collection_id, image_ids: db.remove_collection_images(
                collection_id,
                image_ids,
            ),
            get_suggestions=lambda: [],
        )
        share_routes._unlock_failures.clear()

    async def _thumbnail_response(self, _request, _size, image_id, cached=False):
        return Response(content=f"thumb-{image_id}".encode("ascii"), media_type="image/jpeg")

    async def _collection_with_images(self):
        source = await self._source()
        first = await self._image(source["id"], "first.jpg")
        second = await self._image(source["id"], "second.jpg")
        third = await self._image(source["id"], "third.jpg")
        collection = await db.create_collection(name="Shared set", image_ids=[first, second])
        return collection, first, second, third

    async def _collection_with_duplicate_filenames(self):
        first_source = await self._source("share-first")
        second_source = await self._source("share-second")
        first = await self._image(first_source["id"], "same-name.jpg")
        second = await self._image(second_source["id"], "same-name.jpg")
        collection = await db.create_collection(name="Duplicate names", image_ids=[first, second])
        return collection, first, second

    async def test_share_create_is_idempotent_and_rotate_replaces_token(self):
        collection, *_ = await self._collection_with_images()

        first = await db.create_or_rotate_share(collection["id"])
        second = await db.create_or_rotate_share(collection["id"])
        rotated = await db.create_or_rotate_share(collection["id"], rotate=True)
        active = await db.get_collection_share(collection["id"])

        self.assertEqual(first["token"], second["token"])
        self.assertNotEqual(first["token"], rotated["token"])
        self.assertEqual(active["token"], rotated["token"])

    async def test_share_create_returns_none_for_missing_collection(self):
        self.assertIsNone(await db.create_or_rotate_share(999999))

    async def test_existing_share_hides_trashed_snapshot_member(self):
        collection, first, second, _third = await self._collection_with_images()
        share = await db.create_or_rotate_share(collection["id"])
        await db.set_image_status(first, "trashed")

        resolved = await db.resolve_share_token(share["token"])

        self.assertEqual([image["id"] for image in resolved["images"]], [second])
        self.assertEqual(resolved["image_count"], 1)
        self.assertFalse(await db.share_token_allows_image(share["token"], first))
        self.assertTrue(await db.share_token_allows_image(share["token"], second))

    async def test_tokened_share_media_never_enters_shared_caches(self):
        collection, first, _second, _third = await self._collection_with_images()
        share = await db.create_or_rotate_share(collection["id"])

        def probe():
            with TestClient(app_module.app) as client:
                thumb = client.get(f"/s/{share['token']}/thumb/sm/{first}")
                image = client.get(f"/s/{share['token']}/img/{first}")
                return thumb, image

        thumb, image = await asyncio.to_thread(probe)

        self.assertEqual(thumb.status_code, 200)
        self.assertEqual(image.status_code, 200)
        self.assertEqual(thumb.headers.get("cache-control"), "private, no-store")
        self.assertEqual(image.headers.get("cache-control"), "private, no-store")

    async def test_unicode_filename_roundtrips_through_private_share(self):
        source = await self._source("share-unicode")
        image_id = await self._image(source["id"], "été 📸.jpg")
        collection = await db.create_collection(name="Famille 🎉", image_ids=[image_id])
        share = await db.create_or_rotate_share(collection["id"])

        resolved = await db.resolve_share_token(share["token"])

        self.assertEqual(resolved["name"], "Famille 🎉")
        self.assertEqual(resolved["images"][0]["filename"], "été 📸.jpg")

    async def test_password_hash_roundtrip(self):
        stored = share_auth.hash_password("correct horse")

        self.assertTrue(share_auth.verify_password("correct horse", stored))
        self.assertFalse(share_auth.verify_password("wrong horse", stored))
        self.assertTrue(stored.startswith("scrypt$"))

    async def test_share_password_can_update_without_rotating(self):
        collection, *_ = await self._collection_with_images()
        share = await db.create_or_rotate_share(collection["id"])
        password_hash = share_auth.hash_password("gallery")

        updated = await db.set_collection_share_password(collection["id"], password_hash)
        cleared = await db.set_collection_share_password(collection["id"], None)

        self.assertEqual(updated["token"], share["token"])
        self.assertEqual(updated["password_hash"], password_hash)
        self.assertEqual(cleared["token"], share["token"])
        self.assertIsNone(cleared["password_hash"])

    async def test_share_revoke_disables_resolution(self):
        collection, *_ = await self._collection_with_images()
        share = await db.create_or_rotate_share(collection["id"])

        revoked = await db.revoke_collection_share(collection["id"])
        active = await db.get_collection_share(collection["id"])
        resolved = await db.resolve_share_token(share["token"])

        self.assertTrue(revoked)
        self.assertIsNone(active)
        self.assertIsNone(resolved)

    async def test_share_revoke_http_hides_public_token_and_owner_payload(self):
        collection, *_ = await self._collection_with_images()
        share = await db.create_or_rotate_share(collection["id"])

        def revoke_and_probe():
            with TestClient(app_module.app) as client:
                revoked = client.post(f"/api/user-collections/{collection['id']}/share/revoke")
                public = client.get(f"/s/{share['token']}")
                owner = client.get(f"/api/user-collections/{collection['id']}/share")
                return revoked, public, owner

        revoked, public, owner = await asyncio.to_thread(revoke_and_probe)
        self.assertEqual(revoked.status_code, 200, revoked.text)
        self.assertEqual(public.status_code, 404)
        self.assertIsNone(owner.json()["share"])

    async def test_resolve_token_rejects_expired_share(self):
        collection, *_ = await self._collection_with_images()
        share = await db.create_or_rotate_share(collection["id"], expires_at=time.time() - 60)

        resolved = await db.resolve_share_token(share["token"])
        active = await db.get_collection_share(collection["id"])

        self.assertIsNone(resolved)
        self.assertEqual(active["token"], share["token"])
        self.assertTrue(active["expired"])

        def probe():
            with TestClient(app_module.app) as client:
                owner = client.get(f"/api/user-collections/{collection['id']}/share")
                shared = client.get("/api/shares")
                return owner, shared

        owner, shared = await asyncio.to_thread(probe)
        self.assertTrue(owner.json()["share"]["expired"])
        item = next(row for row in shared.json()["items"] if row["collection_id"] == collection["id"])
        self.assertTrue(item["private_link"]["expired"])

        with (Path(__file__).parent / "static/js/desktop/panel.js").open(encoding="utf-8") as handle:
            self.assertIn("Expired - rotate to renew", handle.read())

    async def test_token_allows_only_member_images(self):
        collection, first, _second, third = await self._collection_with_images()
        share = await db.create_or_rotate_share(collection["id"])

        self.assertTrue(await db.share_token_allows_image(share["token"], first))
        self.assertFalse(await db.share_token_allows_image(share["token"], third))

    async def test_share_favorite_set_unset_and_list(self):
        collection, first, second, _third = await self._collection_with_images()
        share = await db.create_or_rotate_share(collection["id"])

        self.assertTrue(await db.set_share_favorite(share["id"], first, True, client_name="Client"))
        self.assertTrue(await db.set_share_favorite(share["id"], second, True))
        self.assertTrue(await db.set_share_favorite(share["id"], first, False))
        favorites = await db.list_share_favorites(share["id"])

        self.assertEqual([row["image_id"] for row in favorites], [second])
        self.assertIsNotNone(favorites[0]["created_at"])

    async def test_share_favorite_requires_collection_member(self):
        collection, _first, _second, third = await self._collection_with_images()
        share = await db.create_or_rotate_share(collection["id"])

        self.assertFalse(await db.set_share_favorite(share["id"], third, True))
        self.assertEqual(await db.list_share_favorites(share["id"]), [])

    async def test_share_rotate_carries_favorites_forward(self):
        collection, first, second, _third = await self._collection_with_images()
        share = await db.create_or_rotate_share(collection["id"])
        await db.set_share_favorite(share["id"], first, True)
        await db.set_share_favorite(share["id"], second, True)

        rotated = await db.create_or_rotate_share(collection["id"], rotate=True)
        favorites = await db.favorites_for_collection(collection["id"])

        self.assertNotEqual(share["id"], rotated["id"])
        self.assertEqual([row["image_id"] for row in favorites], [first, second])

    async def test_public_gallery_returns_200_and_404(self):
        settings.save_settings({
            "share_brand_name": "Northstar Studio",
            "publish_site_base_url": "https://photos.example.test",
        })
        collection, *_ = await self._collection_with_images()
        share = await db.create_or_rotate_share(collection["id"])

        def probe():
            client = TestClient(app_module.app)
            try:
                ok = client.get(f"/s/{share['token']}")
                missing = client.get("/s/not-a-token")
                return ok, missing
            finally:
                client.close()

        ok, missing = await asyncio.to_thread(probe)

        self.assertEqual(ok.status_code, 200)
        self.assertIn("Shared set", ok.text)
        self.assertIn("Northstar Studio", ok.text)
        self.assertIn("https://photos.example.test", ok.text)
        self.assertIn("Download all", ok.text)
        self.assertIn(f'href="/s/{share["token"]}/download-all"', ok.text)
        self.assertIn("Download photo", ok.text)
        self.assertIn("Photo 1 of 2", ok.text)
        self.assertIn("your photographer sees these", ok.text)
        self.assertIn('property="og:image"', ok.text)
        self.assertEqual(ok.headers.get("referrer-policy"), "no-referrer")
        self.assertEqual(missing.status_code, 404)
        self.assertIn("Share unavailable", missing.text)
        self.assertEqual(missing.headers.get("referrer-policy"), "no-referrer")

    async def test_public_share_download_all_returns_zip_with_unique_filenames(self):
        collection, first, second = await self._collection_with_duplicate_filenames()
        share = await db.create_or_rotate_share(collection["id"])

        def probe():
            with TestClient(app_module.app) as client:
                return client.get(f"/s/{share['token']}/download-all")

        pattern = f"{tempfile.gettempdir()}/azimuth-share-*.zip"
        before = set(glob.glob(pattern))
        response = await asyncio.to_thread(probe)

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers.get("referrer-policy"), "no-referrer")
        self.assertEqual(response.headers.get("x-azimuth-skipped-count"), "0")
        # Streaming generator's finally must delete the temp zip once consumed.
        self.assertEqual(set(glob.glob(pattern)) - before, set())
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            self.assertEqual(archive.namelist(), ["same-name.jpg", "same-name-2.jpg"])
            self.assertEqual(archive.read("same-name.jpg"), f"thumb-{first}".encode("ascii"))
            self.assertEqual(archive.read("same-name-2.jpg"), f"thumb-{second}".encode("ascii"))

    async def test_protected_share_download_all_requires_unlock_cookie(self):
        collection, first, second = await self._collection_with_duplicate_filenames()
        share = await db.create_or_rotate_share(
            collection["id"],
            password_hash=share_auth.hash_password("open-sesame"),
        )

        def probe():
            with TestClient(app_module.app) as client:
                locked = client.get(f"/s/{share['token']}/download-all")
                unlock = client.post(
                    f"/s/{share['token']}/unlock",
                    data={"password": "open-sesame"},
                    follow_redirects=False,
                )
                unlocked = client.get(f"/s/{share['token']}/download-all")
                return locked, unlock, unlocked

        locked, unlock, unlocked = await asyncio.to_thread(probe)

        self.assertEqual(locked.status_code, 404)
        self.assertEqual(locked.json(), {"error": "Not found"})
        self.assertEqual(locked.headers.get("referrer-policy"), "no-referrer")
        self.assertEqual(unlock.status_code, 303)
        self.assertEqual(unlocked.status_code, 200, unlocked.text)
        with zipfile.ZipFile(io.BytesIO(unlocked.content)) as archive:
            self.assertEqual(archive.namelist(), ["same-name.jpg", "same-name-2.jpg"])
            self.assertEqual(
                {archive.read(name) for name in archive.namelist()},
                {f"thumb-{first}".encode("ascii"), f"thumb-{second}".encode("ascii")},
            )

    async def test_public_favorite_routes_and_owner_count(self):
        collection, first, second, third = await self._collection_with_images()
        share = await db.create_or_rotate_share(collection["id"])

        def probe():
            client = TestClient(app_module.app)
            try:
                initial = client.get(f"/s/{share['token']}/favorites")
                first_on = client.post(
                    f"/s/{share['token']}/favorite",
                    json={"image_id": first, "on": True, "name": "Client"},
                )
                second_on = client.post(
                    f"/s/{share['token']}/favorite",
                    json={"image_id": second, "on": True},
                )
                done = client.post(f"/s/{share['token']}/favorite", json={"done": True})
                not_member = client.post(
                    f"/s/{share['token']}/favorite",
                    json={"image_id": third, "on": True},
                )
                owner = client.get(f"/api/user-collections/{collection['id']}/share/favorites")
                return initial, first_on, second_on, done, not_member, owner
            finally:
                client.close()

        initial, first_on, second_on, done, not_member, owner = await asyncio.to_thread(probe)

        self.assertEqual(initial.status_code, 200)
        self.assertEqual(initial.json(), {"favorites": [], "done": False})
        self.assertEqual(first_on.status_code, 200)
        self.assertEqual(first_on.json()["favorites"], [first])
        self.assertEqual(second_on.status_code, 200)
        self.assertEqual(second_on.json()["favorites"], [first, second])
        self.assertTrue(done.json()["done"])
        self.assertEqual(not_member.status_code, 404)
        self.assertEqual(not_member.headers.get("referrer-policy"), "no-referrer")
        self.assertEqual(owner.status_code, 200)
        self.assertEqual(owner.json()["count"], 2)
        self.assertIsNotNone(owner.json()["client_finished_at"])
        self.assertEqual([row["image_id"] for row in owner.json()["favorites"]], [first, second])

    async def test_share_favorites_are_isolated_per_visitor_and_owner_sees_all_sets(self):
        collection, first, second, _third = await self._collection_with_images()
        share = await db.create_or_rotate_share(collection["id"])

        def probe():
            visitor_a = TestClient(app_module.app)
            visitor_b = TestClient(app_module.app)
            try:
                a_pick = visitor_a.post(
                    f"/s/{share['token']}/favorite",
                    json={"image_id": first, "on": True},
                )
                b_first_pick = visitor_b.post(
                    f"/s/{share['token']}/favorite",
                    json={"image_id": first, "on": True},
                )
                b_second_pick = visitor_b.post(
                    f"/s/{share['token']}/favorite",
                    json={"image_id": second, "on": True},
                )
                b_remove_a_pick = visitor_b.post(
                    f"/s/{share['token']}/favorite",
                    json={"image_id": first, "on": False},
                )
                a_favorites = visitor_a.get(f"/s/{share['token']}/favorites")
                b_favorites = visitor_b.get(f"/s/{share['token']}/favorites")
                owner = visitor_a.get(f"/api/user-collections/{collection['id']}/share/favorites")
                return a_pick, b_first_pick, b_second_pick, b_remove_a_pick, a_favorites, b_favorites, owner
            finally:
                visitor_a.close()
                visitor_b.close()

        (
            a_pick,
            b_first_pick,
            b_second_pick,
            b_remove_a_pick,
            a_favorites,
            b_favorites,
            owner,
        ) = await asyncio.to_thread(probe)

        self.assertIn("pa_sv=", a_pick.headers.get("set-cookie", ""))
        self.assertIn(f"Path=/s/{share['token']}", a_pick.headers.get("set-cookie", ""))
        self.assertEqual(a_pick.json()["favorites"], [first])
        self.assertEqual(b_first_pick.json()["favorites"], [first])
        self.assertEqual(b_second_pick.json()["favorites"], [first, second])
        self.assertEqual(b_remove_a_pick.json()["favorites"], [second])
        self.assertEqual(a_favorites.json()["favorites"], [first])
        self.assertEqual(b_favorites.json()["favorites"], [second])
        self.assertEqual([row["image_id"] for row in owner.json()["favorites"]], [first, second])
        self.assertEqual(owner.json()["count"], 2)
        self.assertEqual(sorted(group["count"] for group in owner.json()["visitors"]), [1, 1])
        self.assertEqual(
            sorted(group["image_ids"] for group in owner.json()["visitors"]),
            [[first], [second]],
        )

    async def test_owner_keeps_legacy_share_favorites_visible(self):
        collection, first, _second, _third = await self._collection_with_images()
        share = await db.create_or_rotate_share(collection["id"])
        await db.set_share_favorite(share["id"], first, True)

        def probe():
            with TestClient(app_module.app) as client:
                return client.get(f"/api/user-collections/{collection['id']}/share/favorites")

        owner = await asyncio.to_thread(probe)

        self.assertEqual([row["image_id"] for row in owner.json()["favorites"]], [first])
        self.assertEqual(owner.json()["visitors"], [{"visitor": "legacy", "count": 1, "image_ids": [first]}])

    async def test_share_favorites_migration_assigns_legacy_visitor(self):
        migration_path = str(Path(self.tempdir.name) / "legacy-favorites.db")
        conn = await data_connection.open_async(migration_path)
        try:
            await conn.executescript(
                """
                CREATE TABLE collection_shares (id INTEGER PRIMARY KEY);
                CREATE TABLE share_favorites (
                    id INTEGER PRIMARY KEY,
                    share_id INTEGER NOT NULL REFERENCES collection_shares(id) ON DELETE CASCADE,
                    image_id INTEGER NOT NULL,
                    client_name TEXT NULL,
                    created_at REAL NOT NULL,
                    UNIQUE(share_id, image_id)
                );
                INSERT INTO collection_shares(id) VALUES (4);
                INSERT INTO share_favorites(id, share_id, image_id, client_name, created_at)
                VALUES (7, 4, 9, 'Client', 11);
                """
            )
            await data_schema.migrate_share_favorites_per_visitor(conn)
            await data_schema.migrate_share_favorites_per_visitor(conn)
            cursor = await conn.execute(
                "SELECT visitor_id, image_id FROM share_favorites WHERE id = 7"
            )
            row = dict(await cursor.fetchone())
        finally:
            await data_connection.close_async(conn, db_path=migration_path)

        self.assertEqual(row, {"visitor_id": "legacy", "image_id": 9})
        self.assertTrue(Path(f"{migration_path}.pre-share-favorites-visitors.bak").exists())

    async def test_shared_surfaces_aggregate_private_and_website_state(self):
        settings.save_settings({"publish_site_base_url": "https://example.test"})
        collection, first, _second, _third = await self._collection_with_images()
        share = await db.create_or_rotate_share(
            collection["id"],
            password_hash=share_auth.hash_password("gallery"),
        )
        await db.set_share_favorite(share["id"], first, True, client_name="Client")
        await db.upsert_collection_publish(
            collection_id=collection["id"],
            slug="shared-set",
            title="Shared Set",
            image_count=2,
            bundle_bytes=123,
            last_commit=None,
            hook_exit_code=7,
            hook_output="deploy failed",
            hook_ran_at=123.0,
        )

        def probe():
            client = TestClient(app_module.app)
            try:
                return client.get("/api/shares")
            finally:
                client.close()

        response = await asyncio.to_thread(probe)
        item = response.json()["items"][0]

        self.assertEqual(response.status_code, 200)
        self.assertEqual(item["collection_id"], collection["id"])
        self.assertEqual(item["private_link"]["token"], share["token"])
        self.assertTrue(item["private_link"]["protected"])
        self.assertEqual(item["private_link"]["pick_count"], 1)
        self.assertEqual(item["website"]["url"], "https://example.test/g/shared-set/")
        self.assertFalse(item["website"]["hook_status"]["ok"])
        self.assertEqual(item["website"]["hook_status"]["output"], "deploy failed")

    async def test_public_favorite_route_rejects_locked_share_without_cookie(self):
        collection, first, *_ = await self._collection_with_images()
        share = await db.create_or_rotate_share(
            collection["id"],
            password_hash=share_auth.hash_password("open-sesame"),
        )

        def probe():
            client = TestClient(app_module.app)
            try:
                return client.post(
                    f"/s/{share['token']}/favorite",
                    json={"image_id": first, "on": True},
                )
            finally:
                client.close()

        response = await asyncio.to_thread(probe)

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.headers.get("referrer-policy"), "no-referrer")
        self.assertEqual(await db.list_share_favorites(share["id"]), [])

    async def test_share_rotate_preserves_password_over_http(self):
        collection, *_ = await self._collection_with_images()

        def probe():
            client = TestClient(app_module.app)
            try:
                created = client.post(
                    f"/api/user-collections/{collection['id']}/share",
                    json={"password": "gallery"},
                )
                old_token = created.json()["share"]["token"]
                rotated = client.post(
                    f"/api/user-collections/{collection['id']}/share",
                    json={"rotate": True},
                )
                new_token = rotated.json()["share"]["token"]
                old_gallery = client.get(f"/s/{old_token}")
                new_gallery = client.get(f"/s/{new_token}")
                return created, rotated, old_token, new_token, old_gallery, new_gallery
            finally:
                client.close()

        created, rotated, old_token, new_token, old_gallery, new_gallery = await asyncio.to_thread(probe)
        active = await db.get_collection_share(collection["id"])

        self.assertEqual(created.status_code, 200)
        self.assertTrue(created.json()["share"]["protected"])
        self.assertEqual(rotated.status_code, 200)
        self.assertTrue(rotated.json()["share"]["protected"])
        self.assertNotEqual(old_token, new_token)
        self.assertEqual(active["token"], new_token)
        self.assertIsNotNone(active["password_hash"])
        self.assertEqual(old_gallery.status_code, 404)
        self.assertEqual(new_gallery.status_code, 200)
        self.assertIn("Unlock", new_gallery.text)
        self.assertNotIn("gallery-data", new_gallery.text)

    async def test_share_clear_password_without_rotation_over_http(self):
        collection, *_ = await self._collection_with_images()

        def probe():
            client = TestClient(app_module.app)
            try:
                created = client.post(
                    f"/api/user-collections/{collection['id']}/share",
                    json={"password": "gallery"},
                )
                token = created.json()["share"]["token"]
                cleared = client.post(
                    f"/api/user-collections/{collection['id']}/share",
                    json={"clear_password": True},
                )
                gallery = client.get(f"/s/{token}")
                return created, cleared, gallery
            finally:
                client.close()

        created, cleared, gallery = await asyncio.to_thread(probe)
        active = await db.get_collection_share(collection["id"])

        self.assertEqual(created.status_code, 200)
        self.assertTrue(created.json()["share"]["protected"])
        self.assertEqual(cleared.status_code, 200)
        self.assertFalse(cleared.json()["share"]["protected"])
        self.assertEqual(cleared.json()["share"]["token"], created.json()["share"]["token"])
        self.assertEqual(active["token"], created.json()["share"]["token"])
        self.assertIsNone(active["password_hash"])
        self.assertEqual(gallery.status_code, 200)
        self.assertIn("gallery-data", gallery.text)
        self.assertIn("Shared set", gallery.text)

    async def test_protected_share_unlocks_media_and_counts_one_view_per_cookie(self):
        collection, first, *_ = await self._collection_with_images()
        password_hash = share_auth.hash_password("open-sesame")
        share = await db.create_or_rotate_share(collection["id"], password_hash=password_hash)

        def probe():
            client = TestClient(app_module.app)
            try:
                locked = client.get(f"/s/{share['token']}")
                blocked_thumb = client.get(f"/s/{share['token']}/thumb/sm/{first}")
                wrong = client.post(
                    f"/s/{share['token']}/unlock",
                    data={"password": "nope"},
                    follow_redirects=False,
                )
                right = client.post(
                    f"/s/{share['token']}/unlock",
                    data={"password": "open-sesame"},
                    follow_redirects=False,
                )
                gallery = client.get(f"/s/{share['token']}")
                again = client.get(f"/s/{share['token']}")
                thumb = client.get(f"/s/{share['token']}/thumb/sm/{first}")
                return locked, blocked_thumb, wrong, right, gallery, again, thumb
            finally:
                client.close()

        locked, blocked_thumb, wrong, right, gallery, again, thumb = await asyncio.to_thread(probe)
        active = await db.get_collection_share(collection["id"])

        self.assertEqual(locked.status_code, 200)
        self.assertIn("Unlock", locked.text)
        self.assertNotIn("gallery-data", locked.text)
        self.assertNotIn(f"/s/{share['token']}/thumb", locked.text)
        self.assertEqual(blocked_thumb.status_code, 404)
        self.assertEqual(wrong.status_code, 303)
        self.assertTrue(wrong.headers.get("location", "").endswith("?e=1"))
        self.assertEqual(right.status_code, 303)
        self.assertIn("pa_s=", right.headers.get("set-cookie", ""))
        self.assertEqual(gallery.status_code, 200)
        self.assertIn("gallery-data", gallery.text)
        self.assertIn("first.jpg", gallery.text)
        self.assertEqual(again.status_code, 200)
        self.assertEqual(thumb.status_code, 200)
        self.assertEqual(thumb.content, b"thumb-%d" % first)
        self.assertEqual(active["view_count"], 1)
        self.assertIsNotNone(active["first_viewed_at"])
        self.assertIsNotNone(active["last_viewed_at"])

    async def test_share_favorites_require_current_unlock_cookie(self):
        collection, first, second, *_ = await self._collection_with_images()
        share = await db.create_or_rotate_share(
            collection["id"],
            password_hash=share_auth.hash_password("old-password"),
        )

        def probe():
            client = TestClient(app_module.app)
            try:
                unlock_old = client.post(
                    f"/s/{share['token']}/unlock",
                    data={"password": "old-password"},
                    follow_redirects=False,
                )
                first_favorite = client.post(
                    f"/s/{share['token']}/favorite",
                    json={"image_id": first, "on": True},
                )
                changed = client.post(
                    f"/api/user-collections/{collection['id']}/share",
                    json={"password": "new-password"},
                )
                old_cookie_read = client.get(f"/s/{share['token']}/favorites")
                old_cookie_write = client.post(
                    f"/s/{share['token']}/favorite",
                    json={"image_id": second, "on": True},
                )
                unlock_new = client.post(
                    f"/s/{share['token']}/unlock",
                    data={"password": "new-password"},
                    follow_redirects=False,
                )
                new_cookie_read = client.get(f"/s/{share['token']}/favorites")
                new_cookie_write = client.post(
                    f"/s/{share['token']}/favorite",
                    json={"image_id": second, "on": True},
                )
                return (
                    unlock_old,
                    first_favorite,
                    changed,
                    old_cookie_read,
                    old_cookie_write,
                    unlock_new,
                    new_cookie_read,
                    new_cookie_write,
                )
            finally:
                client.close()

        (
            unlock_old,
            first_favorite,
            changed,
            old_cookie_read,
            old_cookie_write,
            unlock_new,
            new_cookie_read,
            new_cookie_write,
        ) = await asyncio.to_thread(probe)

        self.assertEqual(unlock_old.status_code, 303)
        self.assertEqual(first_favorite.status_code, 200)
        self.assertEqual(first_favorite.json()["favorites"], [first])
        self.assertEqual(changed.status_code, 200)
        self.assertTrue(changed.json()["share"]["protected"])
        self.assertEqual(old_cookie_read.status_code, 404)
        self.assertEqual(old_cookie_write.status_code, 404)
        self.assertEqual(unlock_new.status_code, 303)
        self.assertEqual(new_cookie_read.status_code, 200)
        self.assertEqual(new_cookie_read.json()["favorites"], [first])
        self.assertEqual(new_cookie_write.status_code, 200)
        self.assertEqual(new_cookie_write.json()["favorites"], [first, second])

    async def test_share_view_count_is_per_distinct_session(self):
        collection, *_ = await self._collection_with_images()
        share = await db.create_or_rotate_share(collection["id"])

        def probe():
            first_client = TestClient(app_module.app)
            second_client = TestClient(app_module.app)
            try:
                first_load = first_client.get(f"/s/{share['token']}")
                first_again = first_client.get(f"/s/{share['token']}")
                second_load = second_client.get(f"/s/{share['token']}")
                return first_load, first_again, second_load
            finally:
                first_client.close()
                second_client.close()

        first_load, first_again, second_load = await asyncio.to_thread(probe)
        active = await db.get_collection_share(collection["id"])

        self.assertEqual(first_load.status_code, 200)
        self.assertEqual(first_again.status_code, 200)
        self.assertEqual(second_load.status_code, 200)
        self.assertEqual(active["view_count"], 2)
        self.assertIsNotNone(active["first_viewed_at"])
        self.assertIsNotNone(active["last_viewed_at"])

    async def test_share_unlock_rejects_oversized_password_before_hashing(self):
        collection, *_ = await self._collection_with_images()
        password_hash = share_auth.hash_password("open-sesame")
        share = await db.create_or_rotate_share(collection["id"], password_hash=password_hash)

        def probe():
            client = TestClient(app_module.app)
            try:
                return client.post(
                    f"/s/{share['token']}/unlock",
                    data={"password": "x" * (share_routes.MAX_UNLOCK_PASSWORD_LENGTH + 1)},
                    follow_redirects=False,
                )
            finally:
                client.close()

        response = await asyncio.to_thread(probe)

        self.assertEqual(response.status_code, 413)
        self.assertIn("text/html", response.headers.get("content-type", ""))
        self.assertIn("That password is too long.", response.text)
        self.assertNotIn("gallery-data", response.text)

    async def test_share_unlock_throttles_repeated_failures_by_token(self):
        collection, *_ = await self._collection_with_images()
        password_hash = share_auth.hash_password("open-sesame")
        share = await db.create_or_rotate_share(collection["id"], password_hash=password_hash)

        async def no_sleep(_seconds):
            return None

        old_sleep = share_routes.asyncio.sleep
        share_routes.asyncio.sleep = no_sleep
        try:
            def probe():
                client = TestClient(app_module.app)
                try:
                    failures = [
                        client.post(
                            f"/s/{share['token']}/unlock",
                            data={"password": "nope"},
                            follow_redirects=False,
                        )
                        for _ in range(share_routes.UNLOCK_FAILURE_LIMIT)
                    ]
                    throttled = client.post(
                        f"/s/{share['token']}/unlock",
                        data={"password": "nope"},
                        follow_redirects=False,
                    )
                    share_routes._unlock_failures[share["token"]]["first_at"] -= (
                        share_routes.UNLOCK_FAILURE_WINDOW_SECONDS + 1
                    )
                    recovered = client.post(
                        f"/s/{share['token']}/unlock",
                        data={"password": "open-sesame"},
                        follow_redirects=False,
                    )
                    return failures, throttled, recovered
                finally:
                    client.close()

            failures, throttled, recovered = await asyncio.to_thread(probe)
        finally:
            share_routes.asyncio.sleep = old_sleep

        self.assertEqual([response.status_code for response in failures], [303] * share_routes.UNLOCK_FAILURE_LIMIT)
        self.assertEqual(throttled.status_code, 429)
        self.assertIn("text/html", throttled.headers.get("content-type", ""))
        self.assertIn("Too many tries", throttled.text)
        self.assertNotIn("gallery-data", throttled.text)
        self.assertGreater(int(throttled.headers.get("retry-after", "0")), 0)
        self.assertEqual(recovered.status_code, 303)
        self.assertNotIn(share["token"], share_routes._unlock_failures)

    async def test_public_thumb_rejects_non_member_image(self):
        collection, _first, _second, third = await self._collection_with_images()
        share = await db.create_or_rotate_share(collection["id"])

        def probe():
            client = TestClient(app_module.app)
            try:
                return client.get(f"/s/{share['token']}/thumb/sm/{third}")
            finally:
                client.close()

        response = await asyncio.to_thread(probe)

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.headers.get("referrer-policy"), "no-referrer")

    async def test_public_image_rejects_non_member_without_calling_provider(self):
        collection, _first, _second, third = await self._collection_with_images()
        share = await db.create_or_rotate_share(collection["id"])
        calls = []
        old_thumbnail_response = share_routes._thumbnail_response

        async def fail_if_called(*args, **kwargs):
            calls.append((args, kwargs))
            return Response(content=b"unexpected", media_type="image/jpeg")

        share_routes._thumbnail_response = fail_if_called
        try:
            def probe():
                client = TestClient(app_module.app)
                try:
                    return client.get(f"/s/{share['token']}/img/{third}")
                finally:
                    client.close()

            response = await asyncio.to_thread(probe)
        finally:
            share_routes._thumbnail_response = old_thumbnail_response

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.headers.get("referrer-policy"), "no-referrer")
        self.assertEqual(calls, [])

    async def test_http_collection_delete_removes_public_share_and_favorites(self):
        collection, first, *_ = await self._collection_with_images()
        share = await db.create_or_rotate_share(collection["id"])

        def probe():
            client = TestClient(app_module.app)
            try:
                favorite = client.post(
                    f"/s/{share['token']}/favorite",
                    json={"image_id": first, "on": True},
                )
                deleted = client.post(f"/api/user-collections/{collection['id']}/delete")
                gallery = client.get(f"/s/{share['token']}")
                return favorite, deleted, gallery
            finally:
                client.close()

        favorite, deleted, gallery = await asyncio.to_thread(probe)
        resolved = await db.resolve_share_token(share["token"])

        self.assertEqual(favorite.status_code, 200)
        self.assertEqual(favorite.json()["favorites"], [first])
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(deleted.json(), {"ok": True})
        self.assertEqual(gallery.status_code, 404)
        self.assertIsNone(resolved)

    async def test_delete_collection_removes_shares(self):
        collection, *_ = await self._collection_with_images()
        share = await db.create_or_rotate_share(collection["id"])

        deleted = await db.delete_collection(collection["id"])
        resolved = await db.resolve_share_token(share["token"])

        self.assertTrue(deleted)
        self.assertIsNone(resolved)


if __name__ == "__main__":
    unittest.main()
