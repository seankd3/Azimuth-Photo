import asyncio
import gzip
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
from fastapi.testclient import TestClient

try:
    import pytest
except ImportError:  # Keep the repository's unittest fallback runnable in minimal venvs.
    class _PytestMark:
        @staticmethod
        def skipif(condition, reason):
            return unittest.skipIf(condition, reason)

    class _PytestFallback:
        mark = _PytestMark()

    pytest = _PytestFallback()

sys.path.insert(0, os.path.dirname(__file__))
import app as app_module  # noqa: E402
import db  # noqa: E402
from features.develop import rawproc  # noqa: E402


RAW_ROOT = Path("/mnt/expansion/Photos/RAWS")


class DevelopBackendTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.old_db_path = db.DB_PATH
        self.old_cache_dir = rawproc.BASE_CACHE_DIR
        self.old_cache_root = rawproc.BASE_CACHE_ROOT
        db.DB_PATH = os.path.join(self.tempdir.name, "develop.db")
        rawproc.BASE_CACHE_ROOT = Path(self.tempdir.name) / "develop-cache"
        rawproc.BASE_CACHE_DIR = rawproc.BASE_CACHE_ROOT / "base" / "v2"
        rawproc._recent_decodes.clear()
        asyncio.run(db.init_db())
        source = asyncio.run(db.add_or_restore_source(os.path.join(self.tempdir.name, "raws")))
        self.raw_path = Path(self.tempdir.name) / "raws" / "sample.dng"
        self.raw_path.parent.mkdir(parents=True, exist_ok=True)
        self.raw_path.write_bytes(b"not decoded in settings tests")
        self.raw_id = self._image(source["id"], self.raw_path)
        self.jpg_id = self._image(source["id"], Path(self.tempdir.name) / "raws" / "sample.jpg")
        self.client = TestClient(app_module.app)

    def tearDown(self):
        self.client.close()
        db.DB_PATH = self.old_db_path
        rawproc.BASE_CACHE_DIR = self.old_cache_dir
        rawproc.BASE_CACHE_ROOT = self.old_cache_root
        rawproc._recent_decodes.clear()
        self.tempdir.cleanup()

    def _image(self, source_id, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch(exist_ok=True)
        async def insert():
            conn = await db.get_db()
            try:
                cursor = await conn.execute(
                    "INSERT INTO images (source_id, filename, filepath, status) VALUES (?, ?, ?, 'kept')",
                    (source_id, path.name, str(path)),
                )
                await conn.commit()
                return cursor.lastrowid
            finally:
                await conn.close()
        return asyncio.run(insert())

    def test_settings_round_trip_preserves_unknown_keys_and_appends_history(self):
        first = self.client.put(
            f"/api/develop/{self.raw_id}",
            json={"settings": {"Exposure2012": 1.25, "FutureCrsKey": {"keep": True}}, "label": "Exposure"},
        )
        self.assertEqual(first.status_code, 200)
        second = self.client.put(
            f"/api/develop/{self.raw_id}",
            json={"settings": {"Exposure2012": -0.5}, "label": "Exposure"},
        )
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.json()["settings"]["FutureCrsKey"], {"keep": True})

        async def read_row():
            conn = await db.get_db()
            try:
                cursor = await conn.execute("SELECT settings FROM develop_settings WHERE image_id = ?", (self.raw_id,))
                return (await cursor.fetchone())["settings"]
            finally:
                await conn.close()
        stored = json.loads(asyncio.run(read_row()))
        self.assertEqual(stored["FutureCrsKey"], {"keep": True})
        self.assertGreaterEqual(len(asyncio.run(self._history_rows())), 2)

    def test_history_read_is_capped_at_forty(self):
        for index in range(45):
            response = self.client.put(
                f"/api/develop/{self.raw_id}",
                json={"settings": {"Exposure2012": index / 10}, "label": f"Step {index}"},
            )
            self.assertEqual(response.status_code, 200)
        # Avoid a real decode here; base cache metadata is enough for the GET contract.
        self._write_cached_base()
        response = self.client.get(f"/api/develop/{self.raw_id}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()["history"]), 40)

    def test_base_endpoints_and_pregen_contract(self):
        self._write_cached_base()
        binary = self.client.get(f"/api/develop/{self.raw_id}/base.bin")
        preview = self.client.get(f"/api/develop/{self.raw_id}/base.jpg")
        pregen = self.client.post("/api/develop/pregen", json={"image_ids": []})
        self.assertEqual(binary.status_code, 200)
        self.assertEqual(binary.headers["content-encoding"], "gzip")
        # TestClient follows Content-Encoding semantics and hands callers the
        # decoded body, exactly as browser fetch does.
        parsed, width, height = rawproc.parse_base_payload(binary.content)
        self.assertEqual((width, height, parsed.dtype), (1, 1, np.dtype("<u2")))
        self.assertEqual(preview.status_code, 200)
        self.assertEqual(preview.headers["content-type"], "image/jpeg")
        self.assertEqual(pregen.status_code, 202)
        self.assertEqual(pregen.json()["queued"], [])

    def test_get_meta_self_heals_camera_profile_and_lens_data(self):
        self._write_cached_base()
        fitted = {
            "slug": "canon-eos-r5",
            "model": "Canon EOS R5",
            "tone_nodes": [index / 15 for index in range(16)],
            "tone_values": [index / 15 for index in range(16)],
            "oklab_ab_delta": np.zeros((12, 3, 2)).tolist(),
            "chroma_edges": [0.02, 0.06, 0.12, 1.0],
        }
        correction = {"distortion": {"model": "ptlens", "terms": [0.01, 0.02, 0.03]}}
        exif = {
            "Make": "Canon",
            "UniqueCameraModel": "Canon EOS R5",
            "LensModel": "RF24-105mm F4 L IS USM",
            "FocalLength": 37,
            "FNumber": 11,
        }
        with (
            mock.patch.object(rawproc, "read_exif", return_value=exif),
            mock.patch.object(rawproc, "load_camera_profile", return_value=fitted),
            mock.patch.object(rawproc, "resolve_lens_correction", return_value=correction),
        ):
            response = self.client.get(f"/api/develop/{self.raw_id}")
        self.assertEqual(response.status_code, 200)
        meta = response.json()["meta"]
        self.assertEqual(meta["camera_model"], "Canon EOS R5")
        self.assertEqual(meta["camera_profile"]["slug"], "canon-eos-r5")
        self.assertEqual(meta["lens_correction"]["distortion"]["model"], "ptlens")
        self.assertEqual(meta["color"]["camera_profile"]["slug"], "canon-eos-r5")

    async def _history_rows(self):
        conn = await db.get_db()
        try:
            cursor = await conn.execute("SELECT id FROM develop_history WHERE image_id = ?", (self.raw_id,))
            return await cursor.fetchall()
        finally:
            await conn.close()

    def _write_cached_base(self):
        paths = rawproc.base_paths(self.raw_id)
        paths.binary.parent.mkdir(parents=True, exist_ok=True)
        payload = rawproc.BASE_HEADER.pack(rawproc.BASE_MAGIC, 1, 1) + b"\0" * 6
        paths.binary.write_bytes(gzip.compress(payload))
        paths.preview.write_bytes(b"jpg")
        paths.metadata.write_text(json.dumps({"as_shot": {"temperature": 5500, "tint": 0}}))

    def test_base_cache_header_parse(self):
        rgb = np.arange(18, dtype=np.uint16).reshape(2, 3, 3)
        payload = rawproc.BASE_HEADER.pack(rawproc.BASE_MAGIC, 3, 2) + rgb.astype("<u2").tobytes()
        parsed, width, height = rawproc.parse_base_payload(payload)
        self.assertEqual((width, height), (3, 2))
        np.testing.assert_array_equal(parsed, rgb)

    def test_mired_white_balance_math(self):
        neutral = rawproc.estimate_as_shot_white_balance_mired([2.0, 1.0, 1.0, 0.0], [2.0, 1.0, 1.0, 0.0])
        cooler = rawproc.estimate_as_shot_white_balance_mired([4.0, 1.0, 1.0, 0.0], [2.0, 1.0, 1.0, 0.0])
        self.assertEqual(neutral["temperature"], 5500)
        self.assertLess(cooler["temperature"], neutral["temperature"])
        self.assertIn("camera_whitebalance", cooler)
        self.assertEqual(neutral["method"], "mired")

    def test_dng_mccamy_white_balance_math(self):
        # ColorMatrix2 + AsShotNeutral from a measured daylight-ish DNG.
        asn = [0.501961, 1.0, 0.554713]
        cm2 = [
            0.9766, -0.2953, -0.1254,
            -0.4276, 1.2116, 0.2433,
            -0.0437, 0.1336, 0.5131,
        ]
        result = rawproc.estimate_as_shot_white_balance(
            [asn[1] / asn[0], 1.0, asn[1] / asn[2], 0.0],
            [],
            as_shot_neutral=asn,
            color_matrix2=cm2,
        )
        self.assertEqual(result["method"], "dng_mccamy")
        self.assertAlmostEqual(result["temperature"], 5613, delta=25)
        self.assertGreaterEqual(result["tint"], -30)
        self.assertLessEqual(result["tint"], 30)

    def test_reset_restores_the_xmp_baseline(self):
        async def seed_xmp():
            conn = await db.get_db()
            try:
                await conn.execute(
                    "INSERT INTO develop_settings (image_id, settings, origin, updated_at) VALUES (?, ?, 'xmp', ?)",
                    (self.raw_id, json.dumps({"Exposure2012": 0.25, "FutureCrsKey": "kept"}), "2026-07-10T00:00:00+00:00"),
                )
                await conn.commit()
            finally:
                await conn.close()
        asyncio.run(seed_xmp())
        changed = self.client.put(f"/api/develop/{self.raw_id}", json={"settings": {"Exposure2012": 2}})
        reset = self.client.post(f"/api/develop/{self.raw_id}/reset")
        self.assertEqual(changed.status_code, 200)
        self.assertEqual(reset.status_code, 200)
        self.assertEqual(reset.json()["origin"], "xmp")
        self.assertEqual(reset.json()["settings"], {"Exposure2012": 0.25, "FutureCrsKey": "kept"})

    def test_honest_non_raw_and_missing_errors(self):
        non_raw = self.client.get(f"/api/develop/{self.jpg_id}")
        self.assertEqual(non_raw.status_code, 400)
        self.raw_path.unlink()
        missing = self.client.get(f"/api/develop/{self.raw_id}/base.jpg")
        self.assertEqual(missing.status_code, 404)


@pytest.mark.skipif(not RAW_ROOT.exists(), reason="expansion RAW library is not mounted")
def test_real_dng_decode_has_linear_uint16_base():
    # Some Lightroom-created DNGs contain only a reduced preview that LibRaw
    # correctly rejects. Probe until the mounted library yields one full RAW.
    rgb = meta = None
    for dng in RAW_ROOT.rglob("*.dng"):
        try:
            rgb, meta = rawproc.decode_base(dng)
            break
        except rawproc.RawDecodeError:
            continue
    assert rgb is not None and meta is not None, "mounted RAW library contains no LibRaw-decodable DNG"
    assert rgb.dtype == np.uint16
    assert rgb.ndim == 3 and rgb.shape[2] == 3
    assert max(rgb.shape[:2]) <= rawproc.MAX_BASE_EDGE
    assert meta["linear"] is True
