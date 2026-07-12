import asyncio
import io
import os
import sqlite3
import sys
import tempfile
import unittest
import zipfile
from unittest import mock

import numpy as np
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(__file__))
import app as app_module  # noqa: E402
import db  # noqa: E402
import embed_cache  # noqa: E402
import embedding_worker  # noqa: E402
import settings  # noqa: E402
import thumbnails  # noqa: E402
from features.export import routes as export_routes  # noqa: E402
from features.compare import service as compare_service  # noqa: E402
from thumbnails import cache_entries as thumbnail_cache_entries  # noqa: E402


CARD_KEYS = {
    "id",
    "filename",
    "elo",
    "comparisons",
    "propagated_updates",
    "status",
    "flag",
    "aspect_ratio",
    "date_taken",
    "date_source",
    "camera_make",
    "camera_model",
    "lens",
    "file_ext",
    "file_size",
    "file_modified_at",
    "width",
    "height",
    "latitude",
    "longitude",
    "created_at",
    "thumb_url",
}
EXPORT_FIELD_NAMES = [
    "rank",
    "filename",
    "filepath",
    "elo",
    "comparisons",
    "propagated_updates",
    "status",
    "flag",
    "date_taken",
    "camera_make",
    "camera_model",
    "lens",
    "file_ext",
    "file_size",
    "file_modified_at",
    "width",
    "height",
    "latitude",
    "longitude",
]


class ApiShapeTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.old_db_path = db.DB_PATH
        self.old_settings_path = settings.SETTINGS_PATH
        self.old_settings_state = settings._settings
        self.old_cache_dir = thumbnails.SSD_CACHE_DIR
        self.old_prefetch_images = thumbnails.prefetch_images
        self.old_encode_text = embedding_worker.encode_text
        self.old_get_matrix = embed_cache.get_matrix
        self.old_get_index = embed_cache.get_index
        self.old_get_vector = embed_cache.get_vector
        self.old_ensure_model_loaded_for_search = embedding_worker.ensure_model_loaded_for_search
        self.old_thumbnail_persistent_conn = thumbnail_cache_entries._persistent_conn
        self.old_smoke_mode = os.environ.get("PHOTOARCHIVE_SMOKE_MODE")

        os.environ["PHOTOARCHIVE_SMOKE_MODE"] = "1"
        db.DB_PATH = os.path.join(self.tempdir.name, "api-shapes.db")
        thumbnail_cache_entries._persistent_conn = None
        settings.SETTINGS_PATH = os.path.join(self.tempdir.name, "settings.local.json")
        settings._settings = None
        thumbnails.SSD_CACHE_DIR = os.path.join(self.tempdir.name, "cache")
        thumbnails._clear_disk_index()
        db.invalidate_stats_cache()
        db.invalidate_cached_image_ids_cache()
        db.clear_filter_options_cache()
        asyncio.run(db.init_db())
        settings.save_settings({})
        compare_service._pairing_cache.update({"data": None, "valid": False})
        compare_service._matchups_cache.update({"data": None, "valid": False})

        async def noop_prefetch(*_args, **_kwargs):
            return 0

        async def no_model_load():
            return False

        thumbnails.prefetch_images = noop_prefetch
        embedding_worker.ensure_model_loaded_for_search = no_model_load
        self.source_id = self._create_source()
        self.ids = self._create_images()
        self.client = TestClient(app_module.app)

    def tearDown(self):
        self.client.close()
        thumbnails.prefetch_images = self.old_prefetch_images
        embedding_worker.encode_text = self.old_encode_text
        embedding_worker.ensure_model_loaded_for_search = self.old_ensure_model_loaded_for_search
        embed_cache.get_matrix = self.old_get_matrix
        embed_cache.get_index = self.old_get_index
        embed_cache.get_vector = self.old_get_vector
        if thumbnail_cache_entries._persistent_conn is not None:
            thumbnail_cache_entries._persistent_conn.close()
        thumbnail_cache_entries._persistent_conn = self.old_thumbnail_persistent_conn
        thumbnails.SSD_CACHE_DIR = self.old_cache_dir
        settings.SETTINGS_PATH = self.old_settings_path
        settings._settings = self.old_settings_state
        if self.old_smoke_mode is None:
            os.environ.pop("PHOTOARCHIVE_SMOKE_MODE", None)
        else:
            os.environ["PHOTOARCHIVE_SMOKE_MODE"] = self.old_smoke_mode
        thumbnails._clear_disk_index()
        db.DB_PATH = self.old_db_path
        db.invalidate_stats_cache()
        db.invalidate_cached_image_ids_cache()
        db.clear_filter_options_cache()
        compare_service._pairing_cache.update({"data": None, "valid": False})
        compare_service._matchups_cache.update({"data": None, "valid": False})
        self.tempdir.cleanup()

    def _create_source(self):
        source_path = os.path.join(self.tempdir.name, "catalog")
        os.makedirs(source_path, exist_ok=True)
        source = asyncio.run(db.add_or_restore_source(source_path))
        return int(source["id"])

    def _create_images(self):
        rows = [
            (
                self.source_id,
                "sunset-alpha.jpg",
                os.path.join(self.tempdir.name, "catalog", "sunset-alpha.jpg"),
                1500.0,
                5,
                0,
                "kept",
                "picked",
                "landscape",
                1.6,
                "2024-05-10 12:00:00",
                "Fuji",
                "X-T5",
                "35mm",
                ".jpg",
                1000,
                1700000000.0,
                3200,
                2000,
                41.1,
                -87.1,
            ),
            (
                self.source_id,
                "portrait-beta.jpg",
                os.path.join(self.tempdir.name, "catalog", "portrait-beta.jpg"),
                1320.0,
                1,
                0,
                "kept",
                "unflagged",
                "portrait",
                0.75,
                "2024-06-02 08:00:00",
                "Canon",
                "R5",
                "50mm",
                ".jpg",
                2000,
                1700500000.0,
                2400,
                3200,
                41.2,
                -87.2,
            ),
            (
                self.source_id,
                "sunset-gamma.jpg",
                os.path.join(self.tempdir.name, "catalog", "sunset-gamma.jpg"),
                1210.0,
                0,
                1,
                "kept",
                "rejected",
                "landscape",
                1.5,
                None,
                "Fuji",
                "X-T5",
                "35mm",
                ".jpg",
                3000,
                1700600000.0,
                3000,
                2000,
                None,
                None,
            ),
            (
                self.source_id,
                "hidden-delta.jpg",
                os.path.join(self.tempdir.name, "catalog", "hidden-delta.jpg"),
                1250.0,
                0,
                0,
                "kept",
                "unflagged",
                "landscape",
                1.4,
                "2024-04-01 08:00:00",
                "Sony",
                "A7",
                "24mm",
                ".jpg",
                4000,
                1700700000.0,
                2800,
                2000,
                None,
                None,
            ),
        ]
        conn = sqlite3.connect(db.DB_PATH)
        try:
            ids = []
            for row in rows:
                cursor = conn.execute(
                    "INSERT INTO images "
                    "(source_id, filename, filepath, elo, comparisons, propagated_updates, "
                    "status, flag, orientation, aspect_ratio, date_taken, camera_make, camera_model, "
                    "lens, file_ext, file_size, file_modified_at, width, height, latitude, longitude) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    row,
                )
                ids.append(int(cursor.lastrowid))
            now = 12345.0
            for image_id in ids[:3]:
                for size in ("sm", "md"):
                    conn.execute(
                        "INSERT INTO cache_entries "
                        "(cache_root, size, image_id, path, source_signature, size_bytes, last_accessed, created_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            thumbnails.SSD_CACHE_DIR,
                            size,
                            image_id,
                            os.path.join(self.tempdir.name, f"{size}-{image_id}.jpg"),
                            f"sig-{size}-{image_id}",
                            123,
                            now,
                            now,
                        ),
                    )
            conn.execute(
                "UPDATE catalog_sources SET image_count = ("
                "  SELECT COUNT(*) FROM images WHERE images.source_id = catalog_sources.id"
                "), active_image_count = CASE WHEN included = 1 THEN ("
                "  SELECT COUNT(*) FROM images "
                "  WHERE images.source_id = catalog_sources.id AND images.missing_at IS NULL"
                ") ELSE 0 END WHERE id = ?",
                (self.source_id,),
            )
            conn.commit()
        finally:
            conn.close()
        db.invalidate_stats_cache()
        db.invalidate_cached_image_ids_cache()
        return ids

    def assertCardShape(self, card, thumb_size="sm", *, contextual=()):
        self.assertTrue(CARD_KEYS.issubset(card.keys()))
        self.assertEqual(card["thumb_url"], f"/api/thumb/{thumb_size}/{card['id']}")
        for key in ("similarity", "date_group"):
            if key in contextual:
                self.assertIn(key, card)
            else:
                self.assertNotIn(key, card)

    def test_rankings_cards_are_normalized(self):
        response = self.client.get("/api/rankings?limit=10&sort=elo")
        self.assertEqual(response.status_code, 200)
        data = response.json()

        self.assertEqual(data["visible_images"], 3)
        self.assertEqual(data["total_images"], 4)
        self.assertEqual(data["hidden_pending_thumbnails"], 1)
        for card in data["images"]:
            self.assertCardShape(card)

        with mock.patch.dict(
            os.environ,
            {"PHOTOARCHIVE_MODE": "satellite", "PHOTOARCHIVE_HUB_URL": "http://stub-hub"},
        ):
            from features.library import service as library_service
            library_service._rankings_response_cache.clear()
            satellite = self.client.get("/api/rankings?limit=10&sort=elo").json()

        self.assertEqual(satellite["visible_images"], 4)
        self.assertEqual(satellite["total_images"], 4)
        self.assertEqual(satellite["hidden_pending_thumbnails"], 0)
        self.assertEqual(len(satellite["images"]), 4)

    def test_date_group_rankings_include_contextual_group_only(self):
        response = self.client.get("/api/rankings?limit=10&sort=date_taken")
        self.assertEqual(response.status_code, 200)
        cards = response.json()["images"]

        self.assertTrue(cards)
        for card in cards:
            self.assertCardShape(card, contextual=("date_group",))
        self.assertIn("2024-06", [card["date_group"] for card in cards])
        self.assertIn("", [card["date_group"] for card in cards])

    def test_search_cards_include_similarity_context(self):
        embedding_worker.encode_text = lambda _query: None

        response = self.client.get("/api/search?q=sunset&limit=10")
        self.assertEqual(response.status_code, 200)
        data = response.json()

        self.assertEqual(data["search_mode"], "metadata")
        self.assertEqual(data["visible_images"], 2)
        self.assertEqual(data["total_images"], 2)
        for card in data["images"]:
            self.assertCardShape(card, contextual=("similarity",))
            self.assertIsNone(card["similarity"])

    def test_similar_cards_include_similarity_context(self):
        image_ids = self.ids
        matrix = np.array(
            [
                [1.0, 0.0],
                [0.8, 0.2],
                [0.7, 0.3],
                [0.95, 0.05],
            ],
            dtype=np.float32,
        )

        async def fake_get_matrix():
            return image_ids, matrix

        def fake_get_index():
            return {image_id: index for index, image_id in enumerate(image_ids)}

        def fake_get_vector(image_id):
            return matrix[fake_get_index()[image_id]]

        embed_cache.get_matrix = fake_get_matrix
        embed_cache.get_index = fake_get_index
        embed_cache.get_vector = fake_get_vector

        response = self.client.get(f"/api/similar/{image_ids[0]}?limit=10")
        self.assertEqual(response.status_code, 200)
        data = response.json()

        self.assertEqual(data["visible_images"], 2)
        self.assertEqual(data["total_images"], 3)
        for card in data["images"]:
            self.assertCardShape(card, contextual=("similarity",))
            self.assertIsInstance(card["similarity"], float)

    def test_mosaic_cards_are_normalized(self):
        response = self.client.get("/api/mosaic/next?n=3&strategy=top")
        self.assertEqual(response.status_code, 200)
        data = response.json()

        self.assertEqual(data["visible_images"], 3)
        self.assertEqual(data["total_images"], 4)
        for card in data["images"]:
            self.assertCardShape(card)

    def test_library_date_groups_preserve_visible_group_shape(self):
        response = self.client.get("/api/date-groups")
        self.assertEqual(response.status_code, 200)
        groups = response.json()["groups"]

        self.assertEqual(
            groups,
            [
                {"date": "2024-06", "label": "June 2024", "count": 1},
                {"date": "2024-05", "label": "May 2024", "count": 1},
                {"date": "", "label": "No Date", "count": 1},
            ],
        )

        with mock.patch.dict(
            os.environ,
            {"PHOTOARCHIVE_MODE": "satellite", "PHOTOARCHIVE_HUB_URL": "http://stub-hub"},
        ):
            satellite_groups = self.client.get("/api/date-groups").json()["groups"]
        self.assertEqual(
            satellite_groups,
            [
                {"date": "2024-06", "label": "June 2024", "count": 1},
                {"date": "2024-05", "label": "May 2024", "count": 1},
                {"date": "2024-04", "label": "April 2024", "count": 1},
                {"date": "", "label": "No Date", "count": 1},
            ],
        )

    def test_library_map_markers_preserve_counts_and_thumb_urls(self):
        response = self.client.get("/api/map/markers")
        self.assertEqual(response.status_code, 200)
        data = response.json()

        self.assertEqual(data["total_count"], 4)
        self.assertEqual(data["visible_count"], 3)
        self.assertEqual(data["gps_count"], 2)
        self.assertEqual(data["gps_total_count"], 2)
        self.assertEqual(data["hidden_pending_thumbnails"], 0)
        self.assertEqual(
            [marker["thumb_url"] for marker in data["markers"]],
            [f"/api/thumb/sm/{self.ids[0]}", f"/api/thumb/sm/{self.ids[1]}"],
        )

        conn = sqlite3.connect(db.DB_PATH)
        try:
            conn.execute(
                "UPDATE images SET latitude = 42.0, longitude = -88.0 WHERE id = ?",
                (self.ids[3],),
            )
            conn.commit()
        finally:
            conn.close()
        db.invalidate_stats_cache()
        with mock.patch.dict(
            os.environ,
            {"PHOTOARCHIVE_MODE": "satellite", "PHOTOARCHIVE_HUB_URL": "http://stub-hub"},
        ):
            satellite = self.client.get("/api/map/markers").json()

        self.assertEqual(satellite["visible_count"], 4)
        self.assertEqual(satellite["gps_count"], 3)
        self.assertEqual(satellite["gps_total_count"], 3)
        self.assertEqual(satellite["hidden_pending_thumbnails"], 0)
        markers = {marker["id"]: marker for marker in satellite["markers"]}
        self.assertEqual(markers[self.ids[0]]["thumb_url"], f"/api/thumb/sm/{self.ids[0]}")
        self.assertNotIn("thumb_url", markers[self.ids[3]])

    def test_library_filter_options_and_stats_shapes(self):
        filters_response = self.client.get("/api/filter-options")
        self.assertEqual(filters_response.status_code, 200)
        filters = filters_response.json()

        self.assertIn("file_types", filters)
        self.assertIn("cameras", filters)
        self.assertIn("lenses", filters)

        stats_response = self.client.get("/api/stats")
        self.assertEqual(stats_response.status_code, 200)
        stats = stats_response.json()

        self.assertEqual(stats["total_catalog_images"], 4)
        self.assertEqual(stats["active_images"], 4)

    def test_compare_cards_are_normalized(self):
        response = self.client.get("/api/compare/next?n=2&mode=swiss")
        self.assertEqual(response.status_code, 200)
        data = response.json()

        self.assertEqual(data["visible_images"], 3)
        self.assertEqual(data["total_images"], 4)
        self.assertTrue(data["pairs"])
        for pair in data["pairs"]:
            self.assertCardShape(pair["left"], thumb_size="md")
            self.assertCardShape(pair["right"], thumb_size="md")

    def test_settings_catalog_includes_counts(self):
        response = self.client.get("/api/settings")
        self.assertEqual(response.status_code, 200)
        data = response.json()

        self.assertEqual(len(data["catalog"]["sources"]), 1)
        self.assertEqual(data["catalog"]["stats"]["total_catalog_images"], 4)
        self.assertEqual(data["catalog"]["stats"]["active_images"], 4)
        self.assertEqual(data["catalog"]["stats"]["total_images"], 4)
        resources = data["cache_stats"]["system_resources"]
        self.assertIn("free_bytes", resources["disk"])
        self.assertIn("available_bytes", resources["memory"])

    def test_catalog_summary_endpoint_includes_counts(self):
        response = self.client.get("/api/catalog")
        self.assertEqual(response.status_code, 200)
        data = response.json()

        self.assertEqual(len(data["sources"]), 1)
        self.assertEqual(data["stats"]["total_catalog_images"], 4)
        self.assertEqual(data["stats"]["active_images"], 4)

    def test_catalog_browse_preserves_directory_shape(self):
        nested = os.path.join(self.tempdir.name, "catalog", "nested")
        os.makedirs(nested, exist_ok=True)

        response = self.client.get("/api/catalog/browse", params={"path": os.path.dirname(nested)})
        self.assertEqual(response.status_code, 200)
        data = response.json()

        self.assertEqual(data["path"], os.path.dirname(nested))
        self.assertTrue(data["exists"])
        self.assertTrue(data["is_dir"])
        self.assertIn("roots", data)
        self.assertEqual(data["error"], "")
        self.assertIn(
            {"name": "nested", "path": nested, "readable": True},
            data["entries"],
        )

    def test_scan_and_folders_endpoint_shapes(self):
        scan_response = self.client.get("/api/scan/status")
        self.assertEqual(scan_response.status_code, 200)
        self.assertIn("scanning", scan_response.json())

        folder_response = self.client.get("/api/scan/folder")
        self.assertEqual(folder_response.status_code, 200)
        self.assertIn("folder", folder_response.json())

        folders_response = self.client.get("/api/folders?max_depth=1")
        self.assertEqual(folders_response.status_code, 200)
        self.assertIsInstance(folders_response.json()["folders"], list)
        folder_tree_response = self.client.get("/api/folders/tree")
        self.assertEqual(folder_tree_response.status_code, 200)
        self.assertIsInstance(folder_tree_response.json()["sources"], list)

    def test_cache_and_ai_status_endpoint_shapes(self):
        cache_response = self.client.get("/api/cache/status?ahead=0")
        self.assertEqual(cache_response.status_code, 200)
        cache_data = cache_response.json()
        self.assertIn("memory", cache_data)
        self.assertIn("disk", cache_data)
        self.assertIn("pregen", cache_data)

        pregen_response = self.client.get("/api/cache/pregen/status")
        self.assertEqual(pregen_response.status_code, 200)
        self.assertIsInstance(pregen_response.json(), dict)

        ai_response = self.client.get("/api/ai/status")
        self.assertEqual(ai_response.status_code, 200)
        ai_data = ai_response.json()
        self.assertIn("worker_state", ai_data)
        self.assertIn("model_id", ai_data)
        self.assertIn("embedding_index", ai_data)

    def test_media_status_endpoint_shapes(self):
        single_response = self.client.get(f"/api/image/{self.ids[0]}/media-status")
        self.assertEqual(single_response.status_code, 200)
        single = single_response.json()

        self.assertEqual(single["id"], self.ids[0])
        self.assertIn("sm", single["tiers"])
        self.assertIn("full", single["tiers"])
        self.assertEqual(single["tiers"]["sm"]["url"], f"/api/thumb/sm/{self.ids[0]}")
        self.assertEqual(single["tiers"]["full"]["cached_url"], f"/api/full/{self.ids[0]}?cached=1")

        batch_response = self.client.post(
            "/api/images/media-status",
            json={"ids": [self.ids[0], str(self.ids[0]), "bad", self.ids[1]]},
        )
        self.assertEqual(batch_response.status_code, 200)
        statuses = batch_response.json()["statuses"]

        self.assertEqual([status["id"] for status in statuses], [self.ids[0], self.ids[1]])

    def test_export_json_preserves_field_order_and_requested_ids(self):
        requested_ids = [self.ids[1], self.ids[0]]
        response = self.client.get(f"/api/export?ids={requested_ids[0]},{requested_ids[1]}")
        self.assertEqual(response.status_code, 200)
        data = response.json()

        self.assertEqual([row["filename"] for row in data], ["portrait-beta.jpg", "sunset-alpha.jpg"])
        self.assertEqual([row["rank"] for row in data], [1, 2])
        self.assertEqual(list(data[0].keys()), EXPORT_FIELD_NAMES)
        self.assertEqual(data[0]["flag"], "unflagged")

    def test_export_csv_preserves_header_order(self):
        response = self.client.get(f"/api/export?format=csv&ids={self.ids[0]},{self.ids[1]}")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.headers["content-type"].startswith("text/csv"))
        self.assertEqual(
            response.headers["content-disposition"],
            "attachment; filename=rankings.csv",
        )
        lines = response.text.splitlines()

        self.assertEqual(lines[0], ",".join(EXPORT_FIELD_NAMES))
        self.assertIn("sunset-alpha.jpg", lines[1])

    def test_export_zip_streams_original_files_for_requested_ids(self):
        first_path = os.path.join(self.tempdir.name, "catalog", "sunset-alpha.jpg")
        second_path = os.path.join(self.tempdir.name, "catalog", "portrait-beta.jpg")
        with open(first_path, "wb") as fh:
            fh.write(b"first image")
        with open(second_path, "wb") as fh:
            fh.write(b"second image")

        response = self.client.get(f"/api/export?format=zip&ids={self.ids[0]},{self.ids[1]}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.headers["content-disposition"],
            'attachment; filename="photoarchive-export-2.zip"',
        )
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            names = sorted(archive.namelist())
            self.assertEqual(
                names,
                [
                    f"{self.ids[0]}-sunset-alpha.jpg",
                    f"{self.ids[1]}-portrait-beta.jpg",
                ],
            )
            self.assertEqual(archive.read(f"{self.ids[0]}-sunset-alpha.jpg"), b"first image")

    def test_export_zip_streams_cached_md_tier_and_rejects_sm_size(self):
        md_path = os.path.join(self.tempdir.name, f"md-{self.ids[0]}.jpg")
        with open(md_path, "wb") as fh:
            fh.write(b"cached md image")
        thumbnails._clear_disk_index()

        response = self.client.get(f"/api/export?format=zip&size=md&ids={self.ids[0]}")
        sm_response = self.client.get(f"/api/export?format=zip&size=sm&ids={self.ids[0]}")

        self.assertEqual(response.status_code, 200)
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            names = archive.namelist()
            self.assertEqual(names, [f"{self.ids[0]}-sunset-alpha.jpg"])
            self.assertEqual(archive.read(f"{self.ids[0]}-sunset-alpha.jpg"), b"cached md image")
        self.assertEqual(sm_response.status_code, 400)
        self.assertIn("size must be original, lg, or md", sm_response.json()["detail"])

    def test_export_zip_manifest_names_missing_original_id(self):
        first_path = os.path.join(self.tempdir.name, "catalog", "sunset-alpha.jpg")
        with open(first_path, "wb") as fh:
            fh.write(b"first image")

        response = self.client.get(f"/api/export?format=zip&ids={self.ids[0]},{self.ids[1]}")

        self.assertEqual(response.status_code, 200)
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            names = sorted(archive.namelist())
            self.assertEqual(names, [f"{self.ids[0]}-sunset-alpha.jpg", "manifest.txt"])
            self.assertEqual(archive.read(f"{self.ids[0]}-sunset-alpha.jpg"), b"first image")
            manifest = archive.read("manifest.txt").decode("utf-8")
            self.assertIn(f"{self.ids[1]}: source file unavailable", manifest)

    def test_export_zip_skips_symlinks_and_paths_outside_library(self):
        first_path = os.path.join(self.tempdir.name, "catalog", "sunset-alpha.jpg")
        symlink_path = os.path.join(self.tempdir.name, "catalog", "portrait-beta.jpg")
        outside_path = os.path.join(self.tempdir.name, "outside.jpg")
        with open(first_path, "wb") as fh:
            fh.write(b"first image")
        with open(outside_path, "wb") as fh:
            fh.write(b"outside image")
        os.symlink(outside_path, symlink_path)
        conn = sqlite3.connect(db.DB_PATH)
        try:
            conn.execute("UPDATE images SET filepath = ? WHERE id = ?", (outside_path, self.ids[2]))
            conn.commit()
        finally:
            conn.close()

        response = self.client.get(
            f"/api/export?format=zip&ids={self.ids[0]},{self.ids[1]},{self.ids[2]}"
        )

        self.assertEqual(response.status_code, 200)
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            names = sorted(archive.namelist())
            self.assertEqual(names, [f"{self.ids[0]}-sunset-alpha.jpg", "manifest.txt"])
            manifest = archive.read("manifest.txt").decode("utf-8")
            self.assertIn(f"{self.ids[1]}: source path is a symlink", manifest)
            self.assertIn(f"{self.ids[2]}: outside library", manifest)

    def test_export_zip_stops_when_original_byte_cap_is_reached(self):
        first_path = os.path.join(self.tempdir.name, "catalog", "sunset-alpha.jpg")
        second_path = os.path.join(self.tempdir.name, "catalog", "portrait-beta.jpg")
        with open(first_path, "wb") as fh:
            fh.write(b"1234")
        with open(second_path, "wb") as fh:
            fh.write(b"5678")
        old_cap = export_routes.ZIP_EXPORT_ORIGINAL_MAX_BYTES
        export_routes.ZIP_EXPORT_ORIGINAL_MAX_BYTES = 5
        try:
            response = self.client.get(f"/api/export?format=zip&ids={self.ids[0]},{self.ids[1]}")
        finally:
            export_routes.ZIP_EXPORT_ORIGINAL_MAX_BYTES = old_cap

        self.assertEqual(response.status_code, 200)
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            names = sorted(archive.namelist())
            self.assertEqual(names, [f"{self.ids[0]}-sunset-alpha.jpg", "manifest.txt"])
            manifest = archive.read("manifest.txt").decode("utf-8")
            self.assertIn(f"{self.ids[1]}: original export size limit reached", manifest)
            self.assertIn("cutoff: original export limited to 5 bytes", manifest)

    def test_export_zip_returns_507_when_temp_disk_space_is_too_low(self):
        first_path = os.path.join(self.tempdir.name, "catalog", "sunset-alpha.jpg")
        with open(first_path, "wb") as fh:
            fh.write(b"first image")

        class LowDisk:
            free = 1

        old_disk_usage = export_routes.shutil.disk_usage
        export_routes.shutil.disk_usage = lambda _path: LowDisk()
        try:
            response = self.client.get(f"/api/export?format=zip&ids={self.ids[0]}")
        finally:
            export_routes.shutil.disk_usage = old_disk_usage

        self.assertEqual(response.status_code, 507)
        self.assertIn("temporary disk space", response.json()["detail"])

    def test_export_zip_rejects_requests_over_cap(self):
        ids = ",".join(str(image_id) for image_id in range(1, 2002))

        response = self.client.get(f"/api/export?format=zip&ids={ids}")

        self.assertEqual(response.status_code, 400)
        self.assertIn("2000", response.json()["detail"])


if __name__ == "__main__":
    unittest.main()
