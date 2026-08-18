"""Refuters for the one-process desktop boundary."""

import os
import base64
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

import desktop


class DesktopTests(unittest.TestCase):
    def test_fresh_home_opens_the_isolated_v2_catalog(self):
        with tempfile.TemporaryDirectory() as directory:
            environment = {
                key: value
                for key, value in os.environ.items()
                if not key.startswith("AZIMUTH_")
            }
            environment["AZIMUTH_HOME"] = directory
            with patch.dict(os.environ, environment, clear=True):
                catalog, tile_root = desktop.default_paths()
                product = desktop.Desktop(catalog, tile_root)
                try:
                    counts = product.counts()
                finally:
                    product.close()

            self.assertEqual(counts["photos"], 0)
            self.assertEqual(
                catalog,
                os.path.join(directory, "data", "catalog", "azimuth-v2.db"),
            )
            self.assertEqual(tile_root, os.path.join(directory, "cache", "v2-tiles"))
            os.replace(catalog, catalog + ".closed")

    def test_real_library_crosses_the_bridge_without_http(self):
        with tempfile.TemporaryDirectory() as directory:
            photo_root = os.path.join(directory, "Photos")
            os.makedirs(photo_root)
            Image.new("RGB", (640, 480), "teal").save(
                os.path.join(photo_root, "lake.jpg"), "JPEG"
            )
            catalog = os.path.join(directory, "catalog.db")
            product = desktop.Desktop(catalog, os.path.join(directory, "tiles"))
            try:
                drive = product.attach(photo_root)
                refreshed = product.refresh(drive["uuid"])
                counts = product.counts()
                page = product.photos()
                details = product.photo(page[0]["id"])
                tile_uri = product.tile(page[0]["id"])
                trashed = product.trash([page[0]["id"]])
                hidden = product.photos()
                trash_count = product.trash_count()
                trash_page = product.trash_photos()
                empty_preview = product.empty_trash(trash_count, True)
                product.undo_trash(trashed["changed"])
                restored = product.photos()
            finally:
                product.close()

            prefix, encoded = tile_uri.split(",", 1)
            renamed = catalog + ".closed"
            os.replace(catalog, renamed)
            catalog_released = os.path.isfile(renamed)

        self.assertEqual(refreshed["photos_added"], 1)
        self.assertEqual(counts["photos"], 1)
        self.assertEqual(page[0]["tail"], "lake.jpg")
        self.assertEqual((details["width"], details["height"]), (640, 480))
        self.assertEqual(prefix, "data:image/jpeg;base64")
        self.assertTrue(base64.b64decode(encoded).startswith(b"\xff\xd8\xff"))
        self.assertEqual(hidden, [])
        self.assertEqual(trash_count, 1)
        self.assertEqual(trash_page[0]["id"], page[0]["id"])
        self.assertEqual(empty_preview["files"], 1)
        self.assertEqual(restored[0]["id"], page[0]["id"])
        self.assertTrue(catalog_released)

    def test_folder_choice_is_a_native_window_verb(self):
        class Window:
            @staticmethod
            def create_file_dialog(_kind):
                return (r"C:\Photos",)

        with tempfile.TemporaryDirectory() as directory:
            product = desktop.Desktop(
                os.path.join(directory, "catalog.db"),
                os.path.join(directory, "tiles"),
            )
            try:
                product.bind(Window())
                chosen = product.choose_folder()
            finally:
                product.close()

        self.assertEqual(chosen, r"C:\Photos")


if __name__ == "__main__":
    unittest.main()
