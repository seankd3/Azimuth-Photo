"""Client gallery snapshots and export-preset round trips."""

from __future__ import annotations

import asyncio
import io
import json
import sqlite3
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from fastapi.responses import Response
from fastapi.testclient import TestClient
from fastapi import FastAPI

from test_support import *  # noqa: F401,F403
from features.develop import export_presets
from features.publishing import galleries
from features.publishing import routes as gallery_routes


def _mount_owned_routers() -> list:
    routes_before = list(app_module.app.router.routes)
    if not getattr(app_module, "_gallery_lane_routers_mounted", False):
        app_module.app.include_router(gallery_routes.router)
        app_module.app.include_router(export_presets.router)
        app_module._gallery_lane_routers_mounted = True
    return [route for route in app_module.app.router.routes if route not in routes_before]


def _unmount_owned_routers(routes: list) -> None:
    if routes:
        app_module.app.router.routes[:] = [route for route in app_module.app.router.routes if route not in routes]
    app_module._gallery_lane_routers_mounted = False


class GalleryTests(BackendTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self._mounted_routes = _mount_owned_routers()
        gallery_routes.configure(db_path=lambda: db.DB_PATH, thumbnail_response=self._thumbnail_response)
        export_presets.configure(db_path=lambda: db.DB_PATH)
        gallery_routes._unlock_failures.clear()

    async def asyncTearDown(self):
        _unmount_owned_routers(self._mounted_routes)
        await super().asyncTearDown()

    async def _thumbnail_response(self, _request, size, image_id, cached=False):
        return Response(content=f"{size}-{image_id}".encode("ascii"), media_type="image/jpeg")

    async def _collection(self):
        source = await self._source("gallery")
        first = await self._image(source["id"], "first.jpg")
        second = await self._image(source["id"], "second.jpg")
        collection = await db.create_collection(name="Client selects", image_ids=[first, second])
        return collection, first, second

    async def test_snapshot_options_and_active_membership(self):
        collection, first, second = await self._collection()
        gallery = await galleries.create_gallery(
            db.DB_PATH,
            collection_id=collection["id"],
            title="Wedding selects",
            image_ids=[first, first, second, 99999],
            options={"layout": "masonry", "theme": "warm", "cover_image_id": second, "download_size": "md"},
        )
        self.assertEqual([row["id"] for row in gallery["images"]], [first, second])
        self.assertEqual(gallery["layout"], "masonry")
        self.assertEqual(gallery["theme"], "warm")
        self.assertEqual(gallery["cover_image_id"], second)
        self.assertEqual(gallery["download_size"], "md")
        await db.set_image_status(first, "trashed")
        resolved = await galleries.resolve_token(db.DB_PATH, gallery["token"])
        self.assertEqual([row["id"] for row in resolved["images"]], [second])
        self.assertFalse(await galleries.gallery_allows_image(db.DB_PATH, gallery["token"], first))

    async def test_public_client_page_password_download_size_and_zip_toggle(self):
        collection, first, _second = await self._collection()

        def probe():
            with TestClient(app_module.app) as client:
                created = client.post(
                    f"/api/user-collections/{collection['id']}/galleries",
                    json={"title": "Password gallery", "layout": "slideshow", "password": "open-sesame", "download_size": "md", "allow_download_all": False},
                )
                gallery = created.json()["gallery"]
                locked = client.get(f"/s/gallery/{gallery['token']}")
                wrong = client.post(f"/s/gallery/{gallery['token']}/unlock", data={"password": "wrong"}, follow_redirects=False)
                unlocked = client.post(f"/s/gallery/{gallery['token']}/unlock", data={"password": "open-sesame"}, follow_redirects=True)
                thumb = client.get(f"/s/gallery/{gallery['token']}/thumb/sm/{first}")
                good_download = client.get(f"/s/gallery/{gallery['token']}/download/md/{first}")
                blocked_download = client.get(f"/s/gallery/{gallery['token']}/download/lg/{first}")
                zip_blocked = client.get(f"/s/gallery/{gallery['token']}/download-all")
                return created, locked, wrong, unlocked, thumb, good_download, blocked_download, zip_blocked

        created, locked, wrong, unlocked, thumb, good_download, blocked_download, zip_blocked = await asyncio.to_thread(probe)
        self.assertEqual(created.status_code, 200, created.text)
        self.assertIn("/s/gallery/", created.json()["gallery"]["url"])
        self.assertEqual(locked.status_code, 200)
        self.assertIn("password protected", locked.text)
        self.assertEqual(wrong.status_code, 303)
        self.assertEqual(unlocked.status_code, 200)
        self.assertIn("Password gallery", unlocked.text)
        self.assertIn('data-layout="slideshow"', unlocked.text)
        self.assertNotIn('id="download-all"', unlocked.text)
        self.assertEqual(thumb.status_code, 200)
        self.assertEqual(good_download.status_code, 200)
        self.assertEqual(good_download.content, f"md-{first}".encode("ascii"))
        self.assertEqual(blocked_download.status_code, 404)
        self.assertEqual(zip_blocked.status_code, 404)

    async def test_gallery_patch_renames_owner_payload_and_public_page(self):
        collection, _first, _second = await self._collection()

        def probe():
            with TestClient(app_module.app) as client:
                created = client.post(
                    f"/api/user-collections/{collection['id']}/galleries",
                    json={"title": "Old gallery title"},
                )
                gallery = created.json()["gallery"]
                renamed = client.patch(
                    f"/api/user-collections/{collection['id']}/galleries/{gallery['id']}",
                    json={"title": "Summer favorites"},
                )
                public = client.get(f"/s/gallery/{gallery['token']}")
                return renamed, public

        renamed, public = await asyncio.to_thread(probe)

        self.assertEqual(renamed.status_code, 200, renamed.text)
        self.assertEqual(renamed.json()["gallery"]["title"], "Summer favorites")
        self.assertIn("<h1>Summer favorites</h1>", public.text)
        self.assertNotIn("Old gallery title", public.text)

    async def test_hub_mirror_original_and_zip_are_streamed_or_manifested(self):
        collection, local_id, remote_id = await self._collection()
        local = await self._image_row(local_id)
        Path(local["filepath"]).parent.mkdir(parents=True, exist_ok=True)
        Path(local["filepath"]).write_bytes(b"local original")
        with sqlite3.connect(db.DB_PATH) as conn:
            conn.execute(
                "UPDATE images SET hub_remote = 1, hub_image_id = 90210 WHERE id = ?",
                (remote_id,),
            )
        gallery = await galleries.create_gallery(
            db.DB_PATH,
            collection_id=collection["id"],
            title="Mirror delivery",
            image_ids=[local_id, remote_id],
            options={"download_size": "original"},
        )

        def available_probe():
            with mock.patch.object(
                gallery_routes.readthrough,
                "open_hub_original",
                side_effect=lambda _image_id: io.BytesIO(b"hub original"),
            ):
                with TestClient(app_module.app) as client:
                    original = client.get(
                        f"/s/gallery/{gallery['token']}/download/original/{remote_id}"
                    )
                    archive = client.get(f"/s/gallery/{gallery['token']}/download-all")
                    return original, archive

        original, archive = await asyncio.to_thread(available_probe)
        self.assertEqual(original.status_code, 200, original.text)
        self.assertEqual(original.content, b"hub original")
        self.assertEqual(archive.headers["x-azimuth-skipped-count"], "0")
        with zipfile.ZipFile(io.BytesIO(archive.content)) as payload:
            self.assertEqual(payload.read("first.jpg"), b"local original")
            self.assertEqual(payload.read("second.jpg"), b"hub original")

        def unavailable_probe():
            with mock.patch.object(gallery_routes.readthrough, "open_hub_original", return_value=None):
                with TestClient(app_module.app) as client:
                    return client.get(f"/s/gallery/{gallery['token']}/download-all")

        short_archive = await asyncio.to_thread(unavailable_probe)
        self.assertEqual(short_archive.status_code, 200, short_archive.text)
        self.assertEqual(short_archive.headers["x-azimuth-skipped-count"], "1")
        with zipfile.ZipFile(io.BytesIO(short_archive.content)) as payload:
            self.assertEqual(payload.read("first.jpg"), b"local original")
            manifest = json.loads(payload.read("azimuth-download-manifest.json"))
        self.assertEqual(manifest["included_count"], 1)
        self.assertEqual(manifest["skipped_count"], 1)
        self.assertEqual(manifest["skipped"][0]["image_id"], remote_id)

    async def test_export_preset_round_trip_and_print_recipe(self):
        print_options = export_presets.print_ready_options(color_space="adobe_rgb", border_px=48)
        self.assertEqual(print_options["format"], "tiff16")
        self.assertEqual(print_options["dpi"], 300)

        def probe():
            export_app = FastAPI()
            export_app.include_router(export_presets.router)
            with TestClient(export_app) as client:
                saved = client.post("/api/develop/export-presets", json={"name": "Fine art print", "options": print_options})
                listed = client.get("/api/develop/export-presets")
                return saved, listed

        saved, listed = await asyncio.to_thread(probe)
        self.assertEqual(saved.status_code, 200, saved.text)
        preset = saved.json()["preset"]
        self.assertEqual(preset["options"]["format"], "tiff16")
        self.assertEqual(preset["options"]["color_space"], "adobe_rgb")
        self.assertEqual(preset["options"]["border_px"], 48)
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.json()["presets"][0]["name"], "Fine art print")

    async def test_production_router_resolves_export_presets_before_image_route(self):
        def probe():
            with TestClient(app_module.app) as client:
                return client.get("/api/develop/export-presets")

        response = await asyncio.to_thread(probe)

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {"presets": []})


if __name__ == "__main__":
    unittest.main()
