"""Focused contracts for Phase 3 panorama discovery, stitch, and cache output."""

from __future__ import annotations

import gzip
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import imagecodecs
import numpy as np
from fastapi import FastAPI
from fastapi.testclient import TestClient

from features.develop import pano, pano_routes, rawproc


def _frame(image_id: int, timestamp: float, *, exposure: float = 1 / 60, lens: str = "RF24-70mm F2.8 L IS USM", focal: float = 35.0) -> dict:
    return {
        "id": image_id,
        "filepath": f"/tmp/{image_id}.dng",
        "capture_timestamp": timestamp,
        "lens_model": lens,
        "focal_length": focal,
        "ExposureTime": exposure,
        "FNumber": 4.0,
        "ISO": 100,
        "ExposureBiasValue": 0.0,
        "relative_ev": np.log2(exposure * 100 / 16),
    }


def _wide_scene(height: int = 240, width: int = 800) -> np.ndarray:
    """Synthetic wide scene with corners/texture so OpenCV can find matches."""

    rng = np.random.default_rng(0)
    scene = np.zeros((height, width, 3), dtype=np.float32)
    noise = rng.random((height, width), dtype=np.float32)
    for _ in range(48):
        x0 = int(rng.integers(0, max(1, width - 40)))
        y0 = int(rng.integers(0, max(1, height - 40)))
        box_w = int(rng.integers(10, 50))
        box_h = int(rng.integers(10, 50))
        scene[y0 : y0 + box_h, x0 : x0 + box_w] = rng.random(3).astype(np.float32)
    scene = 0.35 * scene + 0.65 * np.stack([noise, np.roll(noise, 3, axis=1), np.roll(noise, 7, axis=0)], axis=-1)
    for x in range(0, width, 25):
        scene[:, x : x + 2] = (0.9, 0.2, 0.1)
    return np.clip(scene, 0.0, 1.0)


def _overlapping_crops(scene: np.ndarray, count: int = 3, crop_frac: float = 0.6) -> list[np.ndarray]:
    height, width = scene.shape[:2]
    crop_w = max(8, int(round(width * crop_frac)))
    max_start = width - crop_w
    starts = [int(round(i * max_start / (count - 1))) for i in range(count)]
    return [np.ascontiguousarray(scene[:, start : start + crop_w]) for start in starts]


class PanoDetectTests(unittest.TestCase):
    def test_detects_same_lens_same_exposure_within_10s(self):
        frames = [_frame(20, 1000.0), _frame(21, 1005.0), _frame(22, 1009.0)]
        with mock.patch.object(pano.hdr, "image_exposure_rows", return_value=frames):
            with mock.patch.object(pano.hdr, "detect_brackets", return_value=[]):
                sequences = pano.detect_sequences("unused.db")
        self.assertEqual(len(sequences), 1)
        self.assertEqual(sequences[0]["image_ids"], [20, 21, 22])
        self.assertEqual(sequences[0]["frame_count"], 3)

    def test_excludes_hdr_bracket_exposure_spread(self):
        frames = [
            _frame(30, 2000.0, exposure=1 / 60),
            _frame(31, 2001.0, exposure=1 / 15),
            _frame(32, 2002.0, exposure=1 / 4),
        ]
        for frame, bias in zip(frames, (-2.0, 0.0, 2.0)):
            frame["ExposureBiasValue"] = bias
        with mock.patch.object(pano.hdr, "image_exposure_rows", return_value=frames):
            with mock.patch.object(
                pano.hdr,
                "detect_brackets",
                return_value=[{"image_ids": [30, 31, 32]}],
            ):
                sequences = pano.detect_sequences("unused.db")
        self.assertEqual(sequences, [])

    def test_rejects_runs_outside_2_to_8(self):
        frames = [_frame(40, 3000.0)]
        with mock.patch.object(pano.hdr, "image_exposure_rows", return_value=frames):
            with mock.patch.object(pano.hdr, "detect_brackets", return_value=[]):
                self.assertEqual(pano.detect_sequences("unused.db"), [])


class PanoStitchTests(unittest.TestCase):
    def test_gamma_roundtrip_is_approximate(self):
        linear = np.linspace(0.01, 0.95, 32, dtype=np.float32).reshape(4, 8)
        rgb = np.stack([linear, linear * 0.9, linear * 0.8], axis=-1)
        recovered = pano.srgb_u8_to_linear(pano.linear_to_srgb_u8(rgb))
        self.assertLess(float(np.mean(np.abs(recovered - rgb))), 0.01)

    def test_synthetic_horizontal_crops_stitch_wider_than_crop(self):
        scene = _wide_scene()
        crops = _overlapping_crops(scene, count=3, crop_frac=0.6)
        crop_w = crops[0].shape[1]
        merged, info = pano.stitch_linear_arrays(crops, max_edge=800)
        self.assertEqual(info["stitch_status"], "ok")
        self.assertGreater(merged.shape[1], crop_w)
        self.assertEqual(merged.dtype, np.float32)


class PanoMergeCacheTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.old_pano_dir = pano.PANO_CACHE_DIR
        self.old_cache_root = rawproc.BASE_CACHE_ROOT
        self.old_cache_dir = rawproc.BASE_CACHE_DIR
        pano.PANO_CACHE_DIR = Path(self.tempdir.name) / "pano"
        rawproc.BASE_CACHE_ROOT = Path(self.tempdir.name) / "develop"
        rawproc.BASE_CACHE_DIR = rawproc.BASE_CACHE_ROOT / "base" / "v3"
        self.db_path = os.path.join(self.tempdir.name, "catalog.db")
        conn = sqlite3.connect(self.db_path)
        conn.executescript("""
            CREATE TABLE catalog_sources (id INTEGER PRIMARY KEY, path TEXT UNIQUE, display_name TEXT, included INTEGER, online INTEGER, image_count INTEGER DEFAULT 0, active_image_count INTEGER DEFAULT 0, created_at REAL, last_scan_at REAL, last_seen_at REAL, removed_at REAL);
            CREATE TABLE images (id INTEGER PRIMARY KEY, source_id INTEGER, filename TEXT, filepath TEXT UNIQUE, status TEXT, file_ext TEXT, file_size INTEGER, file_modified_at REAL, width INTEGER, height INTEGER, date_taken TEXT, camera_make TEXT, camera_model TEXT, lens TEXT, missing_at REAL);
            CREATE TABLE develop_settings (image_id INTEGER PRIMARY KEY, settings TEXT NOT NULL DEFAULT '{}', origin TEXT NOT NULL DEFAULT 'user', xmp_path TEXT, xmp_mtime REAL, updated_at TEXT NOT NULL);
            CREATE TABLE develop_history (id INTEGER PRIMARY KEY, image_id INTEGER, settings TEXT NOT NULL, label TEXT, created_at TEXT NOT NULL);
        """)
        conn.commit()
        conn.close()

    def tearDown(self):
        pano.PANO_CACHE_DIR = self.old_pano_dir
        rawproc.BASE_CACHE_ROOT = self.old_cache_root
        rawproc.BASE_CACHE_DIR = self.old_cache_dir
        self.tempdir.cleanup()

    def test_merge_registers_float_exr_and_pabase(self):
        frames = [_frame(1, 1000.0), _frame(2, 1004.0), _frame(3, 1008.0)]
        scene = _wide_scene(height=160, width=500)
        crops = _overlapping_crops(scene, count=3, crop_frac=0.6)
        source = {
            frames[i]["filepath"]: (np.clip(crops[i], 0, 1) * 65535).astype(np.uint16)
            for i in range(3)
        }

        def decode(path):
            return source[path], {}

        with mock.patch.object(pano.hdr, "image_exposure_rows", return_value=frames):
            result = pano.merge_images(self.db_path, [1, 2, 3], decode=decode)
        self.assertGreater(result["image_id"], 0)
        self.assertEqual(result["stitch"]["stitch_status"], "ok")
        self.assertGreater(result["stitch"]["result_size"][0], crops[0].shape[1])
        exr = next(pano.PANO_CACHE_DIR.glob("*.exr"))
        self.assertEqual(imagecodecs.exr_decode(exr.read_bytes()).dtype, np.float32)
        paths = rawproc.base_paths(result["image_id"])
        payload = gzip.decompress(paths.binary.read_bytes())
        base, width, height = rawproc.parse_base_payload(payload)
        self.assertEqual(base.dtype, np.dtype("<u2"))
        self.assertEqual((width, height), (result["stitch"]["result_size"][0], result["stitch"]["result_size"][1]))
        meta = rawproc.read_base_metadata(result["image_id"])
        self.assertEqual(meta["pano"]["kind"], "pano")
        self.assertEqual(meta["pano"]["format"], "float32-exr")


class PanoRouteTests(unittest.TestCase):
    def test_detect_merge_and_status_routes(self):
        app = FastAPI()
        pano_routes.configure(db_path=lambda: "unused.db")
        app.include_router(pano_routes.router)
        with mock.patch.object(pano, "detect_sequences", return_value=[{"image_ids": [1, 2, 3]}]):
            with mock.patch.object(pano, "begin_merge", return_value=True) as begin:
                with mock.patch.object(pano, "pano_status", return_value={"running": True, "phase": "queued"}):
                    with TestClient(app) as client:
                        detected = client.post("/api/develop/pano/detect", json={"image_ids": [1, 2, 3]})
                        queued = client.post("/api/develop/pano/merge", json={"image_ids": [1, 2, 3]})
                        status = client.get("/api/develop/pano/status")
        self.assertEqual(detected.status_code, 200)
        self.assertEqual(detected.json()["sequences"][0]["image_ids"], [1, 2, 3])
        self.assertEqual(queued.status_code, 202)
        begin.assert_called_once()
        self.assertEqual(status.status_code, 200)
        self.assertTrue(status.json()["running"])
