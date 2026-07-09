import asyncio
import math
import os
import tempfile
import unittest

import numpy as np
from fastapi.testclient import TestClient

import app as app_module
import db
import embed_cache
import thumbnails
from core import cache_events
from data.repositories import stacks as stack_repository
from features.library import routes as library_routes
from features.library import service as library_service
from features.stacks import builders


class StackTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.old_db_path = db.DB_PATH
        self.old_get_matrix = embed_cache.get_matrix
        db.DB_PATH = os.path.join(self.tempdir.name, "stacks-test.db")
        library_service._rankings_response_cache.clear()
        cache_events.invalidate_stats_cache()
        await db.init_db()

    async def asyncTearDown(self):
        embed_cache.get_matrix = self.old_get_matrix
        library_service._rankings_response_cache.clear()
        db.DB_PATH = self.old_db_path
        cache_events.invalidate_stats_cache()
        self.tempdir.cleanup()

    async def _source(self, name):
        path = os.path.join(self.tempdir.name, name)
        os.makedirs(path, exist_ok=True)
        return await db.add_or_restore_source(path)

    async def _image(self, source, filename, **kwargs):
        folder = kwargs.pop("folder", "")
        filepath = kwargs.pop("filepath", None)
        if filepath is None:
            filepath = os.path.join(source["path"], folder, filename)
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        conn = await db.get_db()
        try:
            cursor = await conn.execute(
                "INSERT INTO images "
                "(source_id, filename, filepath, elo, comparisons, propagated_updates, status, flag, "
                "date_taken, camera_model, file_ext, file_size, width, height, missing_at) "
                "VALUES (?, ?, ?, ?, 0, 0, ?, 'unflagged', ?, ?, ?, ?, ?, ?, ?)",
                (
                    int(source["id"]),
                    filename,
                    filepath,
                    float(kwargs.get("elo", 1200.0)),
                    kwargs.get("status", "kept"),
                    kwargs.get("date_taken"),
                    kwargs.get("camera_model"),
                    kwargs.get("file_ext", os.path.splitext(filename)[1].lstrip(".")),
                    kwargs.get("file_size", 100),
                    kwargs.get("width", 100),
                    kwargs.get("height", 100),
                    kwargs.get("missing_at"),
                ),
            )
            await db._update_source_counts(conn, int(source["id"]))
            await conn.commit()
            return int(cursor.lastrowid)
        finally:
            await conn.close()

    async def _cache_entry(self, image_id):
        conn = await db.get_db()
        try:
            await conn.execute(
                "INSERT OR REPLACE INTO cache_entries "
                "(cache_root, size, image_id, path, source_signature, size_bytes, last_accessed, created_at) "
                "VALUES (?, 'sm', ?, ?, ?, 100, 1000, 1000)",
                (
                    thumbnails.SSD_CACHE_DIR,
                    int(image_id),
                    os.path.join(self.tempdir.name, f"{image_id}.jpg"),
                    f"sig-{image_id}",
                ),
            )
            await conn.commit()
        finally:
            await conn.close()

    def _stub_embeddings(self, image_ids, matrix):
        async def fake_get_matrix(_model_key=None):
            return image_ids, matrix

        embed_cache.get_matrix = fake_get_matrix

    async def test_variant_builder_strips_export_noise_family(self):
        source = await self._source("catalog")
        first = await self._image(
            source,
            "20201119-IMG_4860-Edit-FullJPG-2.jpg",
            folder="shoot",
            file_ext="jpg",
        )
        second = await self._image(
            source,
            "20201119-IMG_4860.jpg",
            folder="shoot",
            file_ext="jpg",
        )
        groups = builders.build_variant_groups(db.DB_PATH)
        member_sets = [set(group[0]) for group in groups]
        self.assertIn({first, second}, member_sets)

    async def test_burst_builder_respects_sixty_second_capture_gap(self):
        source = await self._source("catalog")
        first = await self._image(
            source,
            "burst-1.jpg",
            date_taken="2024-01-01 10:00:00",
            camera_model="X100",
        )
        second = await self._image(
            source,
            "burst-2.jpg",
            date_taken="2024-01-01 10:00:30",
            camera_model="X100",
        )
        third = await self._image(
            source,
            "burst-3.jpg",
            date_taken="2024-01-01 10:01:45",
            camera_model="X100",
        )
        angle = math.acos(0.93)
        matrix = np.array(
            [
                [1.0, 0.0],
                [math.cos(angle), math.sin(angle)],
                [math.cos(angle * 2), math.sin(angle * 2)],
            ],
            dtype=np.float32,
        )
        self._stub_embeddings([first, second, third], matrix)
        groups = await asyncio.to_thread(builders.build_embedding_groups, db.DB_PATH)
        self.assertEqual([set(group[0]) for group in groups["burst"]], [{first, second}])

    async def test_crosssource_representative_prefers_exported_edits_over_social_import(self):
        exported = await self._source("Exported Edits")
        google = await self._source("Google Photos")
        exported_id = await self._image(
            exported,
            "edit.tif",
            file_ext="tif",
            file_size=100,
            width=100,
            height=100,
        )
        social_id = await self._image(
            google,
            "edit.jpg",
            file_ext="jpg",
            file_size=1000,
            width=4000,
            height=3000,
        )
        self._stub_embeddings(
            [exported_id, social_id],
            np.array([[1.0, 0.0], [1.0, 0.0]], dtype=np.float32),
        )
        groups = await asyncio.to_thread(builders.build_embedding_groups, db.DB_PATH)
        self.assertEqual(groups["crosssource"][0][1], exported_id)

    async def test_manual_stacks_survive_auto_rebuild_and_priority_steals_lower_auto_members(self):
        source = await self._source("catalog")
        first = await self._image(source, "one.jpg")
        second = await self._image(source, "two.jpg")
        third = await self._image(source, "three.jpg")
        fourth = await self._image(source, "four.jpg")
        await stack_repository.create_stack(
            db.DB_PATH,
            kind="manual",
            representative_image_id=first,
            member_rows=[{"image_id": first}, {"image_id": second}],
            auto=False,
        )
        manual_result = stack_repository.upsert_auto_stacks_sync(
            db.DB_PATH,
            "variant",
            [([first, second, third], third, {})],
        )
        self.assertEqual(manual_result["created"], 0)
        self.assertEqual((await stack_repository.stack_for_image(db.DB_PATH, first))["kind"], "manual")

        stack_repository.upsert_auto_stacks_sync(db.DB_PATH, "burst", [([third, fourth], third, {})])
        stack_repository.upsert_auto_stacks_sync(db.DB_PATH, "variant", [([third, fourth, first], third, {})])
        third_stack = await stack_repository.stack_for_image(db.DB_PATH, third)
        self.assertEqual(third_stack["kind"], "variant")
        self.assertEqual({member["id"] for member in third_stack["members"]}, {third, fourth})

    async def test_collapsed_rankings_exclude_non_reps_and_annotate_representatives(self):
        source = await self._source("catalog")
        representative = await self._image(source, "rep.jpg", elo=1500, date_taken="2024-01-01 10:00:00")
        hidden = await self._image(source, "hidden.jpg", elo=1400, date_taken="2024-01-01 10:01:00")
        loose = await self._image(source, "loose.jpg", elo=1300, date_taken="2024-02-01 10:00:00")
        for image_id in (representative, hidden, loose):
            await self._cache_entry(image_id)
        stack = await stack_repository.create_stack(
            db.DB_PATH,
            kind="manual",
            representative_image_id=representative,
            member_rows=[{"image_id": representative}, {"image_id": hidden}],
            auto=False,
        )
        cache_events.invalidate_stats_cache()
        library_service._rankings_response_cache.clear()

        expanded = await library_routes.api_rankings(limit=10, stacks="expanded")
        collapsed = await library_routes.api_rankings(limit=10, stacks="collapsed")
        self.assertEqual({image["id"] for image in expanded["images"]}, {representative, hidden, loose})
        self.assertEqual({image["id"] for image in collapsed["images"]}, {representative, loose})
        rep_card = next(image for image in collapsed["images"] if image["id"] == representative)
        self.assertEqual(rep_card["stack_id"], stack["id"])
        self.assertEqual(rep_card["stack_count"], 2)

        counts = await library_routes.api_counts(stacks="collapsed")
        histogram = await library_routes.api_date_histogram(stacks="collapsed")
        self.assertEqual(counts["total"], 2)
        self.assertEqual(histogram["total"], 2)


class StackRouteTests(unittest.TestCase):
    def test_manual_stack_route_creates_stack(self):
        with tempfile.TemporaryDirectory() as tempdir:
            old_db_path = db.DB_PATH
            db.DB_PATH = os.path.join(tempdir, "route-stacks-test.db")
            try:
                asyncio.run(db.init_db())
                source = asyncio.run(db.add_or_restore_source(os.path.join(tempdir, "catalog")))

                async def insert(filename):
                    conn = await db.get_db()
                    try:
                        cursor = await conn.execute(
                            "INSERT INTO images (source_id, filename, filepath, status) VALUES (?, ?, ?, 'kept')",
                            (source["id"], filename, os.path.join(source["path"], filename)),
                        )
                        await db._update_source_counts(conn, int(source["id"]))
                        await conn.commit()
                        return int(cursor.lastrowid)
                    finally:
                        await conn.close()

                first = asyncio.run(insert("a.jpg"))
                second = asyncio.run(insert("b.jpg"))
                with TestClient(app_module.app) as client:
                    response = client.post(
                        "/api/stacks",
                        json={"image_ids": [first, second], "representative_id": second},
                    )
                self.assertEqual(response.status_code, 200)
                payload = response.json()
                self.assertEqual(payload["kind"], "manual")
                self.assertFalse(payload["auto"])
                self.assertEqual(payload["representative"]["id"], second)
            finally:
                db.DB_PATH = old_db_path
                cache_events.invalidate_stats_cache()
