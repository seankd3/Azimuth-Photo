"""EDITFLOW clipboard requests preserve the selected or complete Develop look."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(__file__))
import app as app_module  # noqa: E402
import db  # noqa: E402
from features.develop import rawproc, routes as develop_routes  # noqa: E402


class DevelopEditFlowTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.old_db_path = db.DB_PATH
        self.old_cache_root = rawproc.BASE_CACHE_ROOT
        self.old_cache_dir = rawproc.BASE_CACHE_DIR
        db.DB_PATH = os.path.join(self.tempdir.name, "editflow.db")
        rawproc.BASE_CACHE_ROOT = Path(self.tempdir.name) / "develop-cache"
        rawproc.BASE_CACHE_DIR = rawproc.BASE_CACHE_ROOT / "base" / "v2"
        asyncio.run(db.init_db())
        source = asyncio.run(db.add_or_restore_source(os.path.join(self.tempdir.name, "raws")))
        self.source_id = source["id"]
        self.raw_a = self._image("a.dng")
        self.raw_b = self._image("b.dng")
        self.client = TestClient(app_module.app)

    def tearDown(self):
        self.client.close()
        db.DB_PATH = self.old_db_path
        rawproc.BASE_CACHE_ROOT = self.old_cache_root
        rawproc.BASE_CACHE_DIR = self.old_cache_dir
        self.tempdir.cleanup()

    def _image(self, name):
        path = Path(self.tempdir.name) / "raws" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"not decoded")

        async def insert():
            conn = await db.get_db()
            try:
                cursor = await conn.execute(
                    "INSERT INTO images (source_id, filename, filepath, status) VALUES (?, ?, ?, 'kept')",
                    (self.source_id, path.name, str(path)),
                )
                await conn.commit()
                return cursor.lastrowid
            finally:
                await conn.close()

        return asyncio.run(insert())

    def _settings(self, image_id):
        async def read():
            conn = await db.get_db()
            try:
                row = await (await conn.execute(
                    "SELECT settings FROM develop_settings WHERE image_id = ?", (image_id,)
                )).fetchone()
                history = await (await conn.execute(
                    "SELECT label FROM develop_history WHERE image_id = ? ORDER BY id DESC LIMIT 1", (image_id,)
                )).fetchone()
                return json.loads(row["settings"]), history["label"]
            finally:
                await conn.close()

        return asyncio.run(read())

    def test_clipboard_groups_are_sliced_before_the_target_is_merged(self):
        copied = {"Temperature": 6900, "Exposure2012": 1.0, "GrainAmount": 22}
        self.client.put(f"/api/develop/{self.raw_b}", json={"settings": {"Exposure2012": -1.0, "CropAngle": 3}})
        response = self.client.post("/api/develop/sync", json={
            "source_id": self.raw_a,
            "source_settings": copied,
            "target_ids": [self.raw_b],
            "groups": ["wb", "effects"],
            "label": "Pasted settings",
        })
        self.assertEqual(response.status_code, 200, response.text)
        settings, label = self._settings(self.raw_b)
        self.assertEqual(settings["Temperature"], 6900)
        self.assertEqual(settings["GrainAmount"], 22)
        self.assertEqual(settings["Exposure2012"], -1.0)
        self.assertEqual(settings["CropAngle"], 3)
        self.assertEqual(label, "Pasted settings")
        self.assertEqual(develop_routes.extract_sync_slice(copied, ["wb", "effects"]), {
            "Temperature": 6900, "GrainAmount": 22,
        })

    def test_previous_replaces_with_the_last_saved_other_images_full_settings(self):
        previous = {"Exposure2012": 1.0, "Temperature": 7000, "CustomFutureSetting": "keep"}
        self.client.put(f"/api/develop/{self.raw_b}", json={"settings": {"Exposure2012": -2, "CropAngle": 3}})
        response = self.client.post("/api/develop/sync", json={
            "source_id": self.raw_a,
            "source_settings": previous,
            "target_ids": [self.raw_b],
            "full": True,
            "label": "From previous",
        })
        self.assertEqual(response.status_code, 200, response.text)
        settings, label = self._settings(self.raw_b)
        self.assertEqual(settings, previous)
        self.assertEqual(label, "From previous")


if __name__ == "__main__":
    unittest.main()
