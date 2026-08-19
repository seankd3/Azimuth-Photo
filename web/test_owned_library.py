"""Refuters for the V2 library's owned execution lanes."""

import asyncio
import os
import tempfile
import threading
import unittest
from unittest.mock import patch

from PIL import Image

import boot


class OwnedLibraryTests(unittest.IsolatedAsyncioTestCase):
    async def test_browsing_does_not_wait_behind_a_drive_sweep(self):
        with tempfile.TemporaryDirectory() as directory:
            photo_root = os.path.join(directory, "Photos")
            os.makedirs(photo_root)
            Image.new("RGB", (32, 24), "navy").save(
                os.path.join(photo_root, "lake.jpg"), "JPEG"
            )
            owned = boot.OwnedLibrary(
                os.path.join(directory, "catalog.db"),
                os.path.join(directory, "tiles"),
            )
            drive = await owned.run(lambda product: product.attach(photo_root))
            started = threading.Event()
            release = threading.Event()
            real_sweep = boot.copies.sweep

            def held_sweep(conn, drive_uuid, under=""):
                started.set()
                release.wait(timeout=2)
                return real_sweep(conn, drive_uuid, under=under)

            try:
                with patch.object(boot.copies, "sweep", held_sweep):
                    sweeping = asyncio.create_task(owned.refresh(drive["uuid"]))
                    await asyncio.to_thread(started.wait, 2)
                    page = await asyncio.wait_for(
                        owned.run(lambda product: product.browse()), timeout=0.5
                    )
                    release.set()
                    result = await sweeping
            finally:
                release.set()
                await owned.close()

        self.assertEqual(page, [])
        self.assertEqual(result["photos_added"], 1)

    async def test_cancelled_close_still_releases_the_catalog(self):
        with tempfile.TemporaryDirectory() as directory:
            catalog = os.path.join(directory, "catalog.db")
            owned = boot.OwnedLibrary(catalog, os.path.join(directory, "tiles"))
            started = threading.Event()
            release = threading.Event()

            def hold(_product):
                started.set()
                release.wait(timeout=2)

            admitted = asyncio.create_task(owned.run(hold))
            await asyncio.to_thread(started.wait, 2)
            closing = asyncio.create_task(owned.close())
            await asyncio.sleep(0)
            closing.cancel()
            release.set()
            await admitted
            with self.assertRaises(asyncio.CancelledError):
                await closing
            await owned.close()

            renamed = catalog + ".closed"
            os.replace(catalog, renamed)
            self.assertTrue(os.path.isfile(renamed))

    async def test_close_drains_admitted_work_and_refuses_new_work(self):
        with tempfile.TemporaryDirectory() as directory:
            owned = boot.OwnedLibrary(
                os.path.join(directory, "catalog.db"),
                os.path.join(directory, "tiles"),
            )
            started = threading.Event()
            release = threading.Event()

            def hold(_product):
                started.set()
                release.wait(timeout=2)
                return "finished"

            admitted = asyncio.create_task(owned.run(hold))
            await asyncio.to_thread(started.wait, 2)
            closing = asyncio.create_task(owned.close())
            await asyncio.sleep(0)
            with self.assertRaises(RuntimeError):
                await owned.run(lambda product: product.browse())
            release.set()

            self.assertEqual(await admitted, "finished")
            await closing

    async def test_real_browse_stays_on_one_owned_thread(self):
        with tempfile.TemporaryDirectory() as directory:
            photo_root = os.path.join(directory, "Photos")
            source = os.path.join(photo_root, "Trips", "lake.jpg")
            os.makedirs(os.path.dirname(source))
            Image.new("RGB", (640, 480), "navy").save(source, "JPEG")
            owned = boot.OwnedLibrary(
                os.path.join(directory, "catalog.db"),
                os.path.join(directory, "tiles"),
            )
            try:
                caller = threading.get_ident()
                first = await owned.run(lambda product: threading.get_ident())
                drive = await owned.run(lambda product: product.attach(photo_root))
                await owned.refresh(drive["uuid"])
                page = await owned.run(lambda product: product.browse())
                second = await owned.run(lambda product: threading.get_ident())
            finally:
                await owned.close()

        self.assertNotEqual(first, caller)
        self.assertEqual(first, second)
        self.assertEqual(page[0]["tail"], "Trips/lake.jpg")
        with self.assertRaises(RuntimeError):
            await owned.run(lambda product: product.browse())


if __name__ == "__main__":
    unittest.main()
