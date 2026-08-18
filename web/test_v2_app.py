"""Fresh-boot proof for the isolated V2 local engine."""

import os
import tempfile
import unittest

from fastapi.testclient import TestClient
from PIL import Image

import boot
import v2_app


class V2AppTests(unittest.TestCase):
    def test_default_catalog_is_not_the_inherited_catalog(self):
        catalog, tiles = v2_app.default_paths()

        self.assertEqual(os.path.basename(catalog), "azimuth-v2.db")
        self.assertEqual(os.path.basename(tiles), "v2-tiles")

    def test_fresh_engine_mounts_real_routes_and_releases_on_quit(self):
        with tempfile.TemporaryDirectory() as directory:
            catalog = os.path.join(directory, "azimuth-v2.db")
            tile_root = os.path.join(directory, "tiles")
            photo_root = os.path.join(directory, "Photos")
            os.makedirs(photo_root)
            Image.new("RGB", (640, 480), "navy").save(
                os.path.join(photo_root, "lake.jpg"), "JPEG"
            )
            with boot.Library(catalog, tile_root) as product:
                drive = product.attach(photo_root)
                product.refresh(drive["uuid"])

            app = v2_app.create_app(catalog_path=catalog, tile_root=tile_root)
            with TestClient(app) as client:
                health = client.get("/")
                desktop = client.get("/d")
                stylesheet = client.get("/static/index.css")
                shell = client.get("/static/index.js")
                manifest = client.get("/api/routes")
                photos = client.get("/api/photos")
                prepared = client.post("/api/system/prepare-quit")
                closed_health = client.get("/api/system/health")

            renamed = catalog + ".closed"
            os.replace(catalog, renamed)

        self.assertEqual(health.json(), {"product": "azimuth-v2", "ready": True})
        self.assertEqual(desktop.status_code, 200)
        self.assertEqual(stylesheet.headers["content-type"], "text/css; charset=utf-8")
        self.assertIn("javascript", shell.headers["content-type"])
        self.assertIn("/api/photos", manifest.json())
        self.assertIn("/api/drives", manifest.json())
        self.assertIn("/api/library/counts", manifest.json())
        self.assertIn("/api/photos/{photo_id}/tile", manifest.json())
        self.assertEqual(photos.json()[0]["tail"], "lake.jpg")
        self.assertEqual(prepared.json(), {"ok": True})
        self.assertEqual(closed_health.status_code, 503)


if __name__ == "__main__":
    unittest.main()
