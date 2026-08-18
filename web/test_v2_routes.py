"""Refuters for the first V2 desktop transport seam."""

import asyncio
import json
import os
import tempfile
import threading
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

import boot
import library as library_surface
import model
from model.scope import where
from routes import library as library_routes


class ScopeTransportTests(unittest.TestCase):
    def test_an_unreadable_scope_is_400_and_never_calls_the_library(self):
        class MustNotRun:
            async def run(self, _operation):
                raise AssertionError("an invalid scope reached the library")

        app = FastAPI()
        app.state.library = MustNotRun()
        app.include_router(library_routes.router)

        with TestClient(app) as client:
            malformed = client.get("/api/photos", params={"scope": "{"})
            unknown = client.get(
                "/api/photos",
                params={"scope": json.dumps({"surprise": True})},
            )

        self.assertEqual(malformed.status_code, 400)
        self.assertEqual(unknown.status_code, 400)
        self.assertEqual(unknown.json()["detail"], "unknown scope field: surprise")

    def test_scope_values_are_typed_and_bounded(self):
        refused = (
            "[]",
            json.dumps({"folder": 7}),
            json.dumps({"stars": True}),
            json.dumps({"set": ""}),
            json.dumps({"ids": [1, "2"]}),
            json.dumps({"ids": list(range(10_001))}),
        )

        for value in refused:
            with self.subTest(value=value[:80]):
                with self.assertRaises(ValueError):
                    library_routes.parse_scope(value)

    def test_star_scope_keeps_the_library_query_on_its_index(self):
        selected = library_routes.parse_scope(json.dumps({"stars": 4}))
        clause, args = where(selected)
        conn = model.connect()
        try:
            plan = " ".join(
                str(column)
                for row in conn.execute(
                    f"EXPLAIN QUERY PLAN SELECT i.id FROM images i "
                    f"WHERE {library_surface.IN_LIBRARY} AND ({clause}) "
                    f"ORDER BY {library_surface.SORTS['stars']} LIMIT ?",
                    (*args, 200),
                )
                for column in row
            )
        finally:
            conn.close()

        self.assertIn("idx_photos_stars", plan)


class OwnedLibraryTests(unittest.IsolatedAsyncioTestCase):
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
                await owned.run(lambda product: product.attach(photo_root))
                page = await owned.run(lambda product: product.browse())
                second = await owned.run(lambda product: threading.get_ident())
            finally:
                await owned.close()

        self.assertNotEqual(first, caller)
        self.assertEqual(first, second)
        self.assertEqual(page[0]["tail"], "Trips/lake.jpg")
        with self.assertRaises(RuntimeError):
            await owned.run(lambda product: product.browse())

    async def test_route_reads_the_real_v2_library(self):
        with tempfile.TemporaryDirectory() as directory:
            photo_root = os.path.join(directory, "Photos")
            os.makedirs(os.path.join(photo_root, "Trips"))
            Image.new("RGB", (640, 480), "teal").save(
                os.path.join(photo_root, "Trips", "lake.jpg"), "JPEG"
            )
            Image.new("RGB", (640, 480), "gold").save(
                os.path.join(photo_root, "portrait.jpg"), "JPEG"
            )
            owned = boot.OwnedLibrary(
                os.path.join(directory, "catalog.db"),
                os.path.join(directory, "tiles"),
            )
            try:
                await owned.run(lambda product: product.attach(photo_root))
                app = FastAPI()
                app.state.library = owned
                app.include_router(library_routes.router)
                with TestClient(app) as client:
                    response = client.get(
                        "/api/photos",
                        params={"scope": json.dumps({"folder": "Trips"})},
                    )
                    bad_sort = client.get("/api/photos", params={"sort": "plausible"})
            finally:
                await owned.close()

        self.assertEqual(response.status_code, 200)
        self.assertEqual([photo["tail"] for photo in response.json()], ["Trips/lake.jpg"])
        self.assertEqual(bad_sort.status_code, 400)


if __name__ == "__main__":
    unittest.main()
