import asyncio
import time

from fastapi.responses import Response
from fastapi.testclient import TestClient

from test_support import *  # noqa: F401,F403
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
            resolve_token=lambda token: db.resolve_share_token(token),
            token_allows_image=lambda token, image_id: db.share_token_allows_image(token, image_id),
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
