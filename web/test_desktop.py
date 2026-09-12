"""Refuters for the one-process desktop boundary."""

import os
import tempfile
import time
import unittest
from unittest.mock import patch

from PIL import Image

import desktop
import home


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
                # The owner may have a real pointer on this machine; isolation
                # means this run does not create or change it, not that it was
                # never there.
                def pointer_state():
                    try:
                        return os.stat(home.pointer()).st_mtime_ns
                    except OSError:
                        return None

                pointer_before = pointer_state()
                where = home.current()
                catalog, previews = home.paths(where)
                product = desktop.Desktop(where)
                try:
                    counts = product.counts()
                finally:
                    product.close()
                pointer_after = pointer_state()

            self.assertEqual(counts["photos"], 0)
            self.assertEqual(where, directory)
            self.assertEqual(catalog, os.path.join(directory, "catalog", "azimuth.db"))
            self.assertEqual(previews, os.path.join(directory, "previews"))
            self.assertEqual(pointer_before, pointer_after,
                             "an isolated home must never touch the owner's pointer")
            os.replace(catalog, catalog + ".closed")

    def test_a_first_run_refuses_the_library_until_a_home_is_chosen(self):
        with tempfile.TemporaryDirectory() as directory:
            environment = {
                key: value
                for key, value in os.environ.items()
                if not key.startswith("AZIMUTH_")
            }
            environment["AZIMUTH_HOME"] = directory
            with patch.dict(os.environ, environment, clear=True):
                product = desktop.Desktop(None)
                try:
                    self.assertIsNone(product.home())
                    with self.assertRaises(RuntimeError):
                        product.counts()
                    self.assertTrue(product.propose_home().endswith("Azimuth Photo"))
                    chosen = product.settle_home(directory)
                    counts = product.counts()
                    with self.assertRaises(RuntimeError):
                        product.settle_home(directory)
                finally:
                    product.close()
            self.assertEqual(chosen, directory)
            self.assertEqual(counts["photos"], 0)

    def test_real_library_crosses_the_bridge_without_http(self):
        with tempfile.TemporaryDirectory() as directory:
            photo_root = os.path.join(directory, "Photos")
            os.makedirs(photo_root)
            Image.new("RGB", (640, 480), "teal").save(
                os.path.join(photo_root, "lake.jpg"), "JPEG"
            )
            catalog, _previews = home.paths(os.path.join(directory, "Home"))
            # The following loop would sweep the folder by itself; this test
            # asks the sweep explicitly, so it keeps the loop out.
            product = desktop.Desktop(os.path.join(directory, "Home"), follow=False)
            try:
                drive = product.attach(photo_root)
                refreshed = product.refresh(drive["uuid"])
                counts = product.counts()
                page = product.photos()
                details = product.photo(page[0]["id"])
                # The worker is already running; a row gains its tile URL when
                # the file exists, and the window reads the file itself.
                deadline = time.time() + 8
                tiled = product.photos()
                while not tiled[0]["tile"] and time.time() < deadline:
                    time.sleep(0.1)
                    tiled = product.photos()
                looked = product.look([page[0]["id"]])
                picked = product.pick([page[0]["id"]])
                picked_page = product.photos()
                cleared = product.clear_pick([page[0]["id"]])
                clear_page = product.photos()
                rejected = product.reject([page[0]["id"]])
                hidden = product.photos()
                trash_count = product.trash_count()
                trash_page = product.trash_photos()
                empty_preview = product.empty_trash(trash_count, True)
                product.undo_cull(rejected["changed"])
                restored = product.photos()
            finally:
                product.close()

            tile_path = tiled[0]["tile"].removeprefix("file:///")
            tile_magic = open(tile_path, "rb").read(3)
            renamed = catalog + ".closed"
            os.replace(catalog, renamed)
            catalog_released = os.path.isfile(renamed)

        self.assertEqual(refreshed["photos_added"], 1)
        self.assertEqual(counts["photos"], 1)
        self.assertEqual(page[0]["tail"], "lake.jpg")
        self.assertEqual(page[0]["status"], "unflagged")
        self.assertEqual(picked["changed"][0]["after"], "picked")
        self.assertEqual(picked_page[0]["status"], "picked")
        self.assertEqual(cleared["changed"][0]["after"], "unflagged")
        self.assertEqual(clear_page[0]["status"], "unflagged")
        self.assertEqual((details["width"], details["height"]), (640, 480))
        self.assertTrue(tiled[0]["tile"].startswith("file:///"))
        self.assertEqual(tile_magic, b"\xff\xd8\xff")
        self.assertEqual(looked, 1)
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

        with tempfile.TemporaryDirectory():
            product = desktop.Desktop(None)
            try:
                product.bind(Window())
                chosen = product.choose_folder()
            finally:
                product.close()

        self.assertEqual(chosen, r"C:\Photos")


if __name__ == "__main__":
    unittest.main()
