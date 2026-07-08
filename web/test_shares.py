import asyncio
import time

from fastapi.responses import Response
from fastapi.testclient import TestClient

from test_support import *  # noqa: F401,F403
from features.share import auth as share_auth
from features.share import routes as share_routes


class ShareTests(BackendTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        templates = app_module.app.state.photoarchive_shell.templates
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
            set_favorite=lambda share_id, image_id, on, client_name=None: db.set_share_favorite(
                share_id,
                image_id,
                on,
                client_name=client_name,
            ),
            list_favorites=lambda share_id: db.list_share_favorites(share_id),
            favorites_for_collection=lambda collection_id: db.favorites_for_collection(collection_id),
            thumbnail_response=self._thumbnail_response,
        )

    async def _thumbnail_response(self, _request, _size, image_id, cached=False):
        return Response(content=f"thumb-{image_id}".encode("ascii"), media_type="image/jpeg")

    async def _collection_with_images(self):
        source = await self._source()
        first = await self._image(source["id"], "first.jpg")
        second = await self._image(source["id"], "second.jpg")
        third = await self._image(source["id"], "third.jpg")
        collection = await db.create_collection(name="Shared set", image_ids=[first, second])
        return collection, first, second, third

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

    async def test_resolve_token_rejects_expired_share(self):
        collection, *_ = await self._collection_with_images()
        share = await db.create_or_rotate_share(collection["id"], expires_at=time.time() - 60)

        resolved = await db.resolve_share_token(share["token"])
        active = await db.get_collection_share(collection["id"])

        self.assertIsNone(resolved)
        self.assertEqual(active["token"], share["token"])

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
        self.assertEqual(ok.headers.get("referrer-policy"), "no-referrer")
        self.assertEqual(missing.status_code, 404)
        self.assertIn("Share unavailable", missing.text)
        self.assertEqual(missing.headers.get("referrer-policy"), "no-referrer")

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
                not_member = client.post(
                    f"/s/{share['token']}/favorite",
                    json={"image_id": third, "on": True},
                )
                owner = client.get(f"/api/user-collections/{collection['id']}/share/favorites")
                return initial, first_on, second_on, not_member, owner
            finally:
                client.close()

        initial, first_on, second_on, not_member, owner = await asyncio.to_thread(probe)

        self.assertEqual(initial.status_code, 200)
        self.assertEqual(initial.json(), {"favorites": []})
        self.assertEqual(first_on.status_code, 200)
        self.assertEqual(first_on.json()["favorites"], [first])
        self.assertEqual(second_on.status_code, 200)
        self.assertEqual(second_on.json()["favorites"], [first, second])
        self.assertEqual(not_member.status_code, 404)
        self.assertEqual(not_member.headers.get("referrer-policy"), "no-referrer")
        self.assertEqual(owner.status_code, 200)
        self.assertEqual(owner.json()["count"], 2)
        self.assertEqual([row["image_id"] for row in owner.json()["favorites"]], [first, second])

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

    async def test_delete_collection_removes_shares(self):
        collection, *_ = await self._collection_with_images()
        share = await db.create_or_rotate_share(collection["id"])

        deleted = await db.delete_collection(collection["id"])
        resolved = await db.resolve_share_token(share["token"])

        self.assertTrue(deleted)
        self.assertIsNone(resolved)


if __name__ == "__main__":
    unittest.main()
