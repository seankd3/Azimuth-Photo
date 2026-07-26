"""CRUD + Lightroom import coverage for Develop presets (§15)."""

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
os.environ.setdefault("AZIMUTH_SMOKE_MODE", "1")
os.environ.setdefault("AZIMUTH_SKIP_LR_PRESET_IMPORT", "1")

import app as app_module  # noqa: E402
import db  # noqa: E402
from features.develop import preset_routes, presets as presets_mod  # noqa: E402
from core import wiring  # noqa: E402


SAMPLE_XMP = b"""<?xml version="1.0" encoding="UTF-8"?>
<x:xmpmeta xmlns:x="adobe:ns:meta/">
 <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
  <rdf:Description xmlns:crs="http://ns.adobe.com/camera-raw-settings/1.0/"
   crs:Exposure2012="+0.85"
   crs:Contrast2012="+15"
   crs:Vibrance="+20"
   crs:WhiteBalance="As Shot"/>
 </rdf:RDF>
</x:xmpmeta>
"""


def _ensure_router_mounted() -> None:
    wiring.configure_develop_routes()
    if not getattr(app_module, "_presets_router_mounted", False):
        preset_routes.configure(db_path=lambda: db.DB_PATH)
        app_module.app.include_router(preset_routes.router)
        app_module._presets_router_mounted = True
    else:
        preset_routes.configure(db_path=lambda: db.DB_PATH)


class DevelopPresetsTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.old_db_path = db.DB_PATH
        db.DB_PATH = os.path.join(self.tempdir.name, "presets.db")
        preset_routes._lr_import_attempted = False
        asyncio.run(db.init_db())
        _ensure_router_mounted()
        self.client = TestClient(app_module.app)
        self.lr_root = Path(self.tempdir.name) / "Lightroom" / "Presets" / "Film"
        self.lr_root.mkdir(parents=True)
        (self.lr_root / "Agfa 100.xmp").write_bytes(SAMPLE_XMP)

        async def seed_image():
            source = await db.add_or_restore_source(os.path.join(self.tempdir.name, "raws"))
            path = Path(self.tempdir.name) / "raws" / "sample.dng"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"dng")
            conn = await db.get_db()
            try:
                cursor = await conn.execute(
                    "INSERT INTO images (source_id, filename, filepath, status) VALUES (?, ?, ?, 'kept')",
                    (source["id"], path.name, str(path)),
                )
                await conn.commit()
                return cursor.lastrowid
            finally:
                await conn.close()

        self.image_id = asyncio.run(seed_image())

    def tearDown(self):
        self.client.close()
        db.DB_PATH = self.old_db_path
        self.tempdir.cleanup()

    def test_crud_round_trip(self):
        created = self.client.post(
            "/api/develop/presets",
            json={
                "name": "Punchy",
                "folder": "User",
                "settings": {"Exposure2012": 0.5, "Contrast2012": 20, "FutureKey": True},
            },
        )
        self.assertEqual(created.status_code, 200, created.text)
        preset = created.json()["preset"]
        self.assertEqual(preset["name"], "Punchy")
        self.assertEqual(preset["folder"], "User")
        self.assertEqual(preset["settings"]["Exposure2012"], 0.5)
        self.assertTrue(preset["settings"]["FutureKey"])
        preset_id = preset["id"]

        listed = self.client.get("/api/develop/presets")
        self.assertEqual(listed.status_code, 200)
        names = [item["name"] for item in listed.json()["presets"]]
        self.assertIn("Punchy", names)

        renamed = self.client.patch(
            f"/api/develop/presets/{preset_id}",
            json={"name": "Punchy v2", "folder": "Favorites"},
        )
        self.assertEqual(renamed.status_code, 200)
        self.assertEqual(renamed.json()["preset"]["name"], "Punchy v2")
        self.assertEqual(renamed.json()["preset"]["folder"], "Favorites")

        got = self.client.get(f"/api/develop/presets/{preset_id}")
        self.assertEqual(got.status_code, 200)
        self.assertEqual(got.json()["preset"]["name"], "Punchy v2")

        deleted = self.client.delete(f"/api/develop/presets/{preset_id}")
        self.assertEqual(deleted.status_code, 200)
        self.assertTrue(deleted.json()["deleted"])
        missing = self.client.get(f"/api/develop/presets/{preset_id}")
        self.assertEqual(missing.status_code, 404)

    def test_apply_merges_settings_and_writes_history_label(self):
        # Seed prior develop settings so merge keeps untouched keys.
        async def seed_settings():
            conn = await db.get_db()
            try:
                await conn.execute(
                    "INSERT INTO develop_settings (image_id, settings, origin, updated_at) VALUES (?, ?, 'user', ?)",
                    (
                        self.image_id,
                        json.dumps({"CropLeft": 0.1, "Exposure2012": -1.0}),
                        "2026-01-01T00:00:00+00:00",
                    ),
                )
                await conn.commit()
            finally:
                await conn.close()

        asyncio.run(seed_settings())
        created = self.client.post(
            "/api/develop/presets",
            json={"name": "Warm", "folder": "User", "settings": {"Exposure2012": 1.25, "Temperature": 6200}},
        )
        preset_id = created.json()["preset"]["id"]
        applied = self.client.post(
            f"/api/develop/presets/{preset_id}/apply",
            json={"image_id": self.image_id},
        )
        self.assertEqual(applied.status_code, 200, applied.text)
        body = applied.json()
        self.assertEqual(body["label"], "Preset: Warm")
        self.assertEqual(body["settings"]["Exposure2012"], 1.25)
        self.assertEqual(body["settings"]["Temperature"], 6200)
        self.assertEqual(body["settings"]["CropLeft"], 0.1)

        async def history():
            conn = await db.get_db()
            try:
                cursor = await conn.execute(
                    "SELECT label, settings FROM develop_history WHERE image_id = ? ORDER BY id DESC LIMIT 1",
                    (self.image_id,),
                )
                return dict(await cursor.fetchone())
            finally:
                await conn.close()

        row = asyncio.run(history())
        self.assertEqual(row["label"], "Preset: Warm")
        self.assertEqual(json.loads(row["settings"])["Temperature"], 6200)

    def test_lightroom_xmp_import_into_lightroom_folder(self):
        async def run_import():
            conn = await db.get_db()
            try:
                await presets_mod.ensure_develop_presets(conn)
                return await presets_mod.import_lightroom_presets(
                    conn, roots=(Path(self.tempdir.name) / "Lightroom" / "Presets",)
                )
            finally:
                await conn.close()

        report = asyncio.run(run_import())
        self.assertEqual(report["imported"], 1)
        self.assertEqual(report["xmp_found"], 1)
        listed = self.client.get("/api/develop/presets")
        presets = listed.json()["presets"]
        match = next(item for item in presets if item["folder"] == "Lightroom")
        self.assertIn("Agfa 100", match["name"])
        self.assertAlmostEqual(float(match["settings"]["Exposure2012"]), 0.85)
        # Second import is idempotent.
        report2 = asyncio.run(run_import())
        self.assertEqual(report2["imported"], 0)
        self.assertEqual(report2["skipped"], 1)

    def test_ensure_develop_presets_is_idempotent(self):
        async def ensure_twice():
            conn = await db.get_db()
            try:
                await presets_mod.ensure_develop_presets(conn)
                await presets_mod.ensure_develop_presets(conn)
                cursor = await conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='develop_presets'"
                )
                return await cursor.fetchone()
            finally:
                await conn.close()

        row = asyncio.run(ensure_twice())
        self.assertIsNotNone(row)


if __name__ == "__main__":
    unittest.main()
