"""HTTP behavior gates for catalog-source mutations."""

import asyncio
import os

from fastapi.testclient import TestClient

import db
from test_support import BackendTestCase


class CatalogSourceRouteTests(BackendTestCase):
    async def _request(self, method, path, **kwargs):
        def send():
            with TestClient(__import__("app").app) as client:
                return client.request(method, path, **kwargs)
        return await asyncio.to_thread(send)

    async def test_add_source_then_keep_remove_preserves_rows_and_originals(self):
        folder = os.path.join(self.tempdir.name, "camera")
        os.makedirs(folder)
        original = os.path.join(folder, "keep.jpg")
        with open(original, "wb") as handle:
            handle.write(b"original bytes")

        added = await self._request("POST", "/api/catalog/sources", json={"path": folder, "scan": False})
        self.assertEqual(added.status_code, 200, added.text)
        source_id = added.json()["source"]["id"]
        image_id = await self._image(source_id, "keep.jpg")

        removed = await self._request("POST", f"/api/catalog/sources/{source_id}/remove", json={"mode": "keep"})
        self.assertEqual(removed.status_code, 200, removed.text)
        self.assertTrue(removed.json()["kept_data"])
        self.assertEqual((await self._image_row(image_id))["source_id"], source_id)
        self.assertTrue(os.path.exists(original))

    async def test_purge_remove_deletes_catalog_rows_but_never_original_files(self):
        source = await self._source("remove")
        original = os.path.join(source["path"], "original.jpg")
        with open(original, "wb") as handle:
            handle.write(b"do not delete")
        image_id = await self._image(source["id"], "original.jpg")

        removed = await self._request("POST", f"/api/catalog/sources/{source['id']}/remove", json={"mode": "purge"})
        self.assertEqual(removed.status_code, 200, removed.text)
        self.assertFalse(removed.json()["kept_data"])
        conn = await db.get_db()
        try:
            row = await (await conn.execute("SELECT id FROM images WHERE id = ?", (image_id,))).fetchone()
        finally:
            await conn.close()
        self.assertIsNone(row)
        self.assertTrue(os.path.exists(original))
