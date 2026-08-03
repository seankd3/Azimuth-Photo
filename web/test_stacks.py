import asyncio
import math
import os
import tempfile
import unittest
import unittest.mock

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
from features.stacks import identical
from features.stacks import routes as stack_routes


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

    async def _bulk_cached_images(self, source, count):
        rows = []
        now = 1000.0
        root = thumbnails.SSD_CACHE_DIR
        for index in range(count):
            image_id = index + 1
            filename = f"bulk-{index:04d}.jpg"
            filepath = os.path.join(source["path"], filename)
            rows.append((
                int(source["id"]),
                filename,
                filepath,
                2000.0 - index,
                "kept",
                "jpg",
                100,
                100,
                100,
                root,
                "sm",
                os.path.join(self.tempdir.name, f"{image_id}.jpg"),
                f"sig-{image_id}",
                now,
            ))
        conn = await db.get_db()
        try:
            ids = []
            for row in rows:
                cursor = await conn.execute(
                    "INSERT INTO images "
                    "(source_id, filename, filepath, elo, comparisons, propagated_updates, "
                    "status, flag, file_ext, file_size, width, height) "
                    "VALUES (?, ?, ?, ?, 0, 0, ?, 'unflagged', ?, ?, ?, ?)",
                    row[:9],
                )
                image_id = int(cursor.lastrowid)
                ids.append(image_id)
                await conn.execute(
                    "INSERT OR REPLACE INTO cache_entries "
                    "(cache_root, size, image_id, path, source_signature, size_bytes, last_accessed, created_at) "
                    "VALUES (?, ?, ?, ?, ?, 100, ?, ?)",
                    (row[9], row[10], image_id, row[11], row[12], row[13], row[13]),
                )
            await db._update_source_counts(conn, int(source["id"]))
            await conn.commit()
            return ids
        finally:
            await conn.close()

    def _stub_embeddings(self, image_ids, matrix):
        async def fake_get_matrix(_model_key=None):
            return image_ids, matrix

        embed_cache.get_matrix = fake_get_matrix

    async def test_variant_builder_keeps_sequential_export_numbers(self):
        source = await self._source("catalog")
        image_ids = [
            await self._image(source, f"SKD-Starbase-2024-12-03-{index}.jpg", folder="shoot")
            for index in range(1, 6)
        ]

        groups = builders.build_variant_groups(db.DB_PATH)

        sequential_ids = set(image_ids)
        self.assertFalse(any(sequential_ids.issubset(set(group[0])) for group in groups))

    async def test_variant_builder_groups_edit_family(self):
        source = await self._source("catalog")
        first = await self._image(
            source,
            "20201119-IMG_4860-Edit.jpg",
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

    async def test_variant_builder_strips_marker_counter_chain(self):
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

    async def test_variant_builder_groups_same_stem_extension_siblings(self):
        source = await self._source("catalog")
        jpg = await self._image(source, "IMG_4860.jpg", folder="shoot", file_ext="jpg")
        png = await self._image(source, "IMG_4860.png", folder="shoot", file_ext="png")

        groups = builders.build_variant_groups(db.DB_PATH)

        member_sets = [set(group[0]) for group in groups]
        self.assertIn({jpg, png}, member_sets)

    async def test_variant_rebuild_skips_and_reports_oversize_candidates(self):
        source = await self._source("catalog")
        for index in range(13):
            await self._image(source, f"IMG_1000-Edit-{index + 1}.jpg", folder="shoot")

        result = builders.rebuild_stacks(db.DB_PATH, kinds=["variant"])

        variant_result = result["results"]["variant"]
        self.assertEqual(variant_result["candidate_groups"], 0)
        self.assertEqual(variant_result["created"], 0)
        self.assertEqual(variant_result["stack_count"], 0)
        self.assertEqual(variant_result["skipped_oversize_candidate_groups"], 1)

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

    async def test_identical_candidates_prefer_earliest_file_modified_copy(self):
        source = await self._source("catalog")
        newer = await self._image(source, "newer.jpg", file_size=4)
        older = await self._image(source, "older.jpg", file_size=4)
        conn = await db.get_db()
        try:
            await conn.executemany(
                "UPDATE images SET content_hash = ?, file_modified_at = ? WHERE id = ?",
                [("candidate", 200.0, newer), ("candidate", 100.0, older)],
            )
            await conn.commit()
        finally:
            await conn.close()

        groups, summary = identical.candidate_groups_sync(db.DB_PATH)

        self.assertEqual(summary["total_groups"], 1)
        self.assertEqual(summary["potential_removable_count"], 1)
        self.assertEqual(groups[0]["representative"]["id"], older)

    async def test_identical_verification_requires_matching_full_files(self):
        source = await self._source("catalog")
        first_path = os.path.join(source["path"], "first.jpg")
        second_path = os.path.join(source["path"], "second.jpg")
        with open(first_path, "wb") as handle:
            handle.write(b"first")
        with open(second_path, "wb") as handle:
            handle.write(b"other")
        first = await self._image(source, "first.jpg", filepath=first_path, file_size=5)
        second = await self._image(source, "second.jpg", filepath=second_path, file_size=5)
        conn = await db.get_db()
        try:
            await conn.execute(
                "UPDATE images SET content_hash = 'fast-candidate' WHERE id IN (?, ?)",
                (first, second),
            )
            await conn.commit()
        finally:
            await conn.close()

        token = identical.queue_verification()
        self.assertIsNotNone(token)
        identical.run_verification(db.DB_PATH, token)
        status = identical.verification_status()

        self.assertEqual(status["state"], "complete")
        self.assertEqual(status["ready_groups"], 0)
        self.assertEqual(status["exception_groups"], 1)

    async def test_identical_cleanup_plan_rejects_files_changed_after_verification(self):
        source = await self._source("catalog")
        first_path = os.path.join(source["path"], "first.jpg")
        second_path = os.path.join(source["path"], "second.jpg")
        for path in (first_path, second_path):
            with open(path, "wb") as handle:
                handle.write(b"same bytes")
        first = await self._image(source, "first.jpg", filepath=first_path, file_size=10)
        second = await self._image(source, "second.jpg", filepath=second_path, file_size=10)
        conn = await db.get_db()
        try:
            await conn.execute(
                "UPDATE images SET content_hash = 'fast-candidate' WHERE id IN (?, ?)",
                (first, second),
            )
            await conn.commit()
        finally:
            await conn.close()

        token = identical.queue_verification()
        self.assertIsNotNone(token)
        identical.run_verification(db.DB_PATH, token)
        self.assertEqual(identical.verification_status()["ready_groups"], 1)
        with open(second_path, "ab") as handle:
            handle.write(b"changed")

        image_ids, skipped = identical.validated_cleanup_ids(token)

        self.assertEqual(image_ids, [])
        self.assertEqual(len(skipped), 1)

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

    async def test_versions_filter_combines_variants_and_raw_edit_lineage(self):
        source = await self._source("catalog")
        image_ids = [await self._image(source, f"photo-{index}.jpg") for index in range(4)]
        await stack_repository.create_stack(
            db.DB_PATH,
            kind="variant",
            representative_image_id=image_ids[0],
            member_rows=[{"image_id": image_ids[0]}, {"image_id": image_ids[1]}],
        )
        await stack_repository.create_stack(
            db.DB_PATH,
            kind="version",
            representative_image_id=image_ids[2],
            member_rows=[{"image_id": image_ids[2]}, {"image_id": image_ids[3]}],
        )

        result = await stack_repository.list_stacks(db.DB_PATH, kind="versions")

        self.assertEqual(result["total"], 2)
        self.assertEqual({stack["kind"] for stack in result["stacks"]}, {"variant", "version"})

    async def test_rebuild_reports_final_counts_after_priority_steals(self):
        primary = await self._source("catalog")
        exported = await self._source("Exported Edits")
        first = await self._image(
            primary,
            "first.jpg",
            date_taken="2024-01-01 10:00:00",
            camera_model="X100",
        )
        second = await self._image(
            primary,
            "second.jpg",
            date_taken="2024-01-01 10:00:30",
            camera_model="X100",
        )
        third = await self._image(
            exported,
            "first.jpg",
            date_taken="2024-01-02 10:00:00",
            camera_model="GFX",
        )
        angle = math.acos(0.93)
        matrix = np.array(
            [
                [1.0, 0.0],
                [math.cos(angle), math.sin(angle)],
                [1.0, 0.0],
            ],
            dtype=np.float32,
        )
        self._stub_embeddings([first, second, third], matrix)

        result = await asyncio.to_thread(builders.rebuild_stacks, db.DB_PATH, ["burst", "crosssource"])

        self.assertEqual(result["results"]["burst"]["created"], 0)
        self.assertEqual(result["results"]["burst"]["inserted"], 1)
        self.assertEqual(result["results"]["burst"]["stack_count"], 0)
        self.assertEqual(result["results"]["crosssource"]["created"], 1)
        self.assertEqual(result["results"]["crosssource"]["stack_count"], 1)

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

    async def test_stack_member_folder_uses_parent_under_source_root(self):
        source = await self._source("catalog")
        cover = await self._image(source, "IMG_1000.jpg", folder="Facebook Photos")
        member = await self._image(source, "IMG_1000 copy.jpg", folder="Facebook Photos")

        stack = await stack_repository.create_stack(
            db.DB_PATH,
            kind="manual",
            representative_image_id=cover,
            member_rows=[{"image_id": cover}, {"image_id": member}],
            auto=False,
        )

        detail = await stack_repository.get_stack(db.DB_PATH, stack["id"])
        self.assertEqual({image["folder"] for image in detail["members"]}, {"Facebook Photos"})

    async def test_full_library_collapsed_rankings_do_not_materialize_id_filter(self):
        # Twelve rows, not 1,200. The property — a collapsed full-library query
        # never materialises an id filter — does not depend on catalogue size,
        # and 1,200 sat below both the 5,000 and 12,000 thresholds anyway, so
        # the large number exercised neither of them.
        source = await self._source("catalog")
        image_ids = await self._bulk_cached_images(source, 12)
        representative, first_hidden, second_hidden = image_ids[:3]
        await stack_repository.create_stack(
            db.DB_PATH,
            kind="manual",
            representative_image_id=representative,
            member_rows=[
                {"image_id": representative},
                {"image_id": first_hidden},
                {"image_id": second_hidden},
            ],
            auto=False,
        )
        cache_events.invalidate_stats_cache()
        library_service._rankings_response_cache.clear()

        expanded = await library_routes.api_rankings(limit=100, stacks="expanded")
        collapsed = await library_routes.api_rankings(limit=100, stacks="collapsed")
        collapsed_ids = {image["id"] for image in collapsed["images"]}
        counts = await library_routes.api_counts(stacks="collapsed")
        date_groups = await library_routes.api_date_groups(stacks="collapsed")
        histogram = await library_routes.api_date_histogram(stacks="collapsed")

        self.assertEqual(expanded["total_kept"], 12)
        self.assertEqual(collapsed["total_kept"], 10)
        self.assertEqual(len(collapsed["images"]), 10)
        self.assertIn(representative, collapsed_ids)
        self.assertNotIn(first_hidden, collapsed_ids)
        self.assertNotIn(second_hidden, collapsed_ids)
        self.assertEqual(counts["total"], collapsed["total_kept"])
        self.assertEqual(sum(group["count"] for group in date_groups["groups"]), collapsed["total_kept"])
        self.assertEqual(histogram["total"], collapsed["total_kept"])

    async def test_collapsed_rankings_apply_search_id_filter_and_stack_exclusion(self):
        source = await self._source("catalog")
        representative = await self._image(source, "rep.jpg", elo=1500)
        hidden = await self._image(source, "hidden.jpg", elo=1400)
        outside_search = await self._image(source, "outside.jpg", elo=1300)
        for image_id in (representative, hidden, outside_search):
            await self._cache_entry(image_id)
        await stack_repository.create_stack(
            db.DB_PATH,
            kind="manual",
            representative_image_id=representative,
            member_rows=[{"image_id": representative}, {"image_id": hidden}],
            auto=False,
        )
        cache_events.invalidate_stats_cache()
        library_service._rankings_response_cache.clear()

        old_resolver = library_service._resolve_library_constraints

        async def search_subset(_q, *, people="", deep=False):
            return {
                "id_filter": {representative, hidden},
                "scores": {},
                "search_mode": "metadata",
                "text_query": "",
                "active": True,
                "ai_unavailable": False,
                "fallback_reason": "",
                "search_sources": [],
                "people_ids": [],
                "people_active": False,
            }

        library_service._resolve_library_constraints = search_subset
        try:
            collapsed = await library_routes.api_rankings(limit=10, q="stack-search", stacks="collapsed")
        finally:
            library_service._resolve_library_constraints = old_resolver

        self.assertEqual([image["id"] for image in collapsed["images"]], [representative])
        self.assertEqual(collapsed["total_kept"], 1)

    async def test_rebuild_failure_is_logged_and_status_hides_internal_path(self):
        secret = "/home/photographer/private/catalog.db"
        with unittest.mock.patch.object(
            builders,
            "rebuild_stacks",
            side_effect=RuntimeError(f"failed opening {secret}"),
        ), unittest.mock.patch.object(stack_routes.log, "exception") as error_log:
            await stack_routes._run_rebuild_task(["burst"])

        self.assertEqual(stack_routes._rebuild_status["state"], "error")
        self.assertNotIn(secret, stack_routes._rebuild_status["error"])
        self.assertIn("try again", stack_routes._rebuild_status["error"])
        error_log.assert_called_once()


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
                unstacked = client.post(f"/api/stacks/{payload['id']}/unstack")
                self.assertEqual(unstacked.status_code, 200, unstacked.text)
                self.assertEqual(asyncio.run(stack_repository.get_stack(db.DB_PATH, payload["id"])), None)
                async def member_count():
                    conn = await db.get_db()
                    try:
                        return (await (await conn.execute(
                            "SELECT COUNT(*) AS count FROM stack_members WHERE image_id IN (?, ?)", (first, second)
                        )).fetchone())["count"]
                    finally:
                        await conn.close()
                self.assertEqual(asyncio.run(member_count()), 0)
            finally:
                db.DB_PATH = old_db_path
                cache_events.invalidate_stats_cache()

    def test_manual_stack_route_rejects_unknown_image_ids(self):
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
                        await conn.commit()
                        return int(cursor.lastrowid)
                    finally:
                        await conn.close()

                first = asyncio.run(insert("a.jpg"))
                with TestClient(app_module.app) as client:
                    response = client.post(
                        "/api/stacks",
                        json={"image_ids": [first, 999999], "representative_id": first},
                    )
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json()["image_ids"], [999999])
            finally:
                db.DB_PATH = old_db_path
                cache_events.invalidate_stats_cache()
