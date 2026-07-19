"""Develop settings sync (§24) — copy chosen groups across a selection."""

from __future__ import annotations

import pytest

import asyncio
import json
import os
import sys
import tempfile
import unittest

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(__file__))
import app as app_module  # noqa: E402
import db  # noqa: E402
from features.develop import rawproc, routes as develop_routes  # noqa: E402


class DevelopSyncUnitTests(unittest.TestCase):
    def test_extract_sync_slice_masks_wholesale_and_named_groups(self):
        settings = {
            "Temperature": 6200,
            "Tint": -12,
            "WhiteBalance": "Custom",
            "Exposure2012": 0.8,
            "Contrast2012": 10,
            "Texture": 20,
            "ToneCurvePV2012": ["0, 0", "255, 255"],
            "HueAdjustmentRed": 11,
            "ColorGradeShadowHue": 40,
            "Sharpness": 70,
            "GrainAmount": 15,
            "MaskGroupBasedCorrections": [{"CorrectionID": "m1", "CorrectionActive": True}],
            "CropLeft": 0.1,
            "FutureCrsKey": "keep-me",
        }
        slice_ = develop_routes.extract_sync_slice(settings, ["wb", "masks", "grade"])
        self.assertEqual(slice_["Temperature"], 6200)
        self.assertEqual(slice_["MaskGroupBasedCorrections"], settings["MaskGroupBasedCorrections"])
        self.assertEqual(slice_["ColorGradeShadowHue"], 40)
        self.assertNotIn("Exposure2012", slice_)
        self.assertNotIn("CropLeft", slice_)
        self.assertNotIn("FutureCrsKey", slice_)

    def test_detail_sync_copies_rendered_nr_detail_keys(self):
        settings = {
            "LuminanceSmoothing": 64,
            "LuminanceDetail": 73,
            "LuminanceContrast": 28,
            "LuminanceNoiseReductionDetail": 9,
        }

        slice_ = develop_routes.extract_sync_slice(settings, ["detail"])

        self.assertEqual(slice_["LuminanceDetail"], 73)
        self.assertEqual(slice_["LuminanceContrast"], 28)
        self.assertNotIn("LuminanceNoiseReductionDetail", slice_)


@pytest.mark.serial
class DevelopSyncHttpTests(unittest.TestCase):
    """HTTP develop-sync paths share the global app — keep them off the xdist wave."""

    def setUp(self):
        worker = os.environ.get("PYTEST_XDIST_WORKER", "gw0")
        self.tempdir = tempfile.TemporaryDirectory(prefix=f"pa-develop-sync-{worker}-")
        self.old_db_path = db.DB_PATH
        self.old_cache_dir = rawproc.BASE_CACHE_DIR
        self.old_cache_root = rawproc.BASE_CACHE_ROOT
        self.old_smoke = os.environ.get("PHOTOARCHIVE_SMOKE_MODE")
        os.environ["PHOTOARCHIVE_SMOKE_MODE"] = "1"
        db.DB_PATH = os.path.join(self.tempdir.name, "develop-sync.db")
        rawproc.BASE_CACHE_ROOT = __import__("pathlib").Path(self.tempdir.name) / "develop-cache"
        rawproc.BASE_CACHE_DIR = rawproc.BASE_CACHE_ROOT / "base" / "v2"
        rawproc.BASE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        rawproc._recent_decodes.clear()
        asyncio.run(db.init_db())
        source = asyncio.run(db.add_or_restore_source(os.path.join(self.tempdir.name, "raws")))
        self.source_id = source["id"]
        self.raw_a = self._image("source.dng")
        self.raw_b = self._image("target-a.dng")
        self.raw_c = self._image("target-b.dng")
        self.client = TestClient(app_module.app)

    def tearDown(self):
        try:
            self.client.close()
        except Exception:
            pass
        db.DB_PATH = self.old_db_path
        rawproc.BASE_CACHE_DIR = self.old_cache_dir
        rawproc.BASE_CACHE_ROOT = self.old_cache_root
        rawproc._recent_decodes.clear()
        if self.old_smoke is None:
            os.environ.pop("PHOTOARCHIVE_SMOKE_MODE", None)
        else:
            os.environ["PHOTOARCHIVE_SMOKE_MODE"] = self.old_smoke
        try:
            self.tempdir.cleanup()
        except Exception:
            pass

    def _image(self, name):
        path = __import__("pathlib").Path(self.tempdir.name) / "raws" / name
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

    def test_sync_writes_settings_and_history_label(self):
        put = self.client.put(
            f"/api/develop/{self.raw_a}",
            json={
                "settings": {
                    "Temperature": 7100,
                    "Tint": 18,
                    "WhiteBalance": "Custom",
                    "Exposure2012": 1.25,
                    "MaskGroupBasedCorrections": [{"CorrectionID": "brush-1"}],
                    "CropAngle": 2.5,
                },
                "label": "Source look",
            },
        )
        self.assertEqual(put.status_code, 200)
        # Target keeps its own crop / unknown keys while receiving synced groups.
        seed = self.client.put(
            f"/api/develop/{self.raw_b}",
            json={"settings": {"Exposure2012": -0.2, "CropAngle": -1.0, "LocalOnly": True}, "label": "Target seed"},
        )
        self.assertEqual(seed.status_code, 200)

        sync = self.client.post(
            "/api/develop/sync",
            json={
                "source_id": self.raw_a,
                "target_ids": [self.raw_b, self.raw_c],
                "groups": ["wb", "tone", "masks"],
            },
        )
        self.assertEqual(sync.status_code, 200, sync.text)
        payload = sync.json()
        self.assertEqual(payload["source_id"], self.raw_a)
        self.assertEqual({row["image_id"] for row in payload["synced"]}, {self.raw_b, self.raw_c})
        self.assertIn("Temperature", payload["keys"])
        self.assertIn("MaskGroupBasedCorrections", payload["keys"])

        async def read_target(image_id):
            conn = await db.get_db()
            try:
                settings_row = await (
                    await conn.execute(
                        "SELECT settings FROM develop_settings WHERE image_id = ?",
                        (image_id,),
                    )
                ).fetchone()
                history = await (
                    await conn.execute(
                        "SELECT label, settings FROM develop_history WHERE image_id = ? ORDER BY id DESC LIMIT 1",
                        (image_id,),
                    )
                ).fetchall()
                return json.loads(settings_row["settings"]), [dict(row) for row in history]
            finally:
                await conn.close()

        target_b, history_b = asyncio.run(read_target(self.raw_b))
        self.assertEqual(target_b["Temperature"], 7100)
        self.assertEqual(target_b["Exposure2012"], 1.25)
        self.assertEqual(target_b["MaskGroupBasedCorrections"], [{"CorrectionID": "brush-1"}])
        self.assertEqual(target_b["CropAngle"], -1.0)
        self.assertTrue(target_b["LocalOnly"])
        self.assertEqual(history_b[0]["label"], f"Sync from #{self.raw_a}")

        target_c, history_c = asyncio.run(read_target(self.raw_c))
        self.assertEqual(target_c["Tint"], 18)
        self.assertEqual(history_c[0]["label"], f"Sync from #{self.raw_a}")

    def test_sync_rejects_empty_groups_and_missing_targets(self):
        bad_groups = self.client.post(
            "/api/develop/sync",
            json={"source_id": self.raw_a, "target_ids": [self.raw_b], "groups": []},
        )
        self.assertEqual(bad_groups.status_code, 400)
        no_targets = self.client.post(
            "/api/develop/sync",
            json={"source_id": self.raw_a, "target_ids": [self.raw_a], "groups": ["wb"]},
        )
        self.assertEqual(no_targets.status_code, 400)


if __name__ == "__main__":
    unittest.main()
