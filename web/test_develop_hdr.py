"""Focused contracts for Phase 2 HDR discovery, alignment, and cache output."""

from __future__ import annotations
from core.catalog_path import catalog_path, use as catalog_path_use

import gzip
import os
import sqlite3
import tempfile
import unittest

import db
from pathlib import Path
from unittest import mock

import imagecodecs
import numpy as np
from fastapi import FastAPI
from fastapi.testclient import TestClient

from data import connection as data_connection
from features.develop import hdr, rawproc, routes as develop_routes


def _frame(image_id: int, timestamp: float, exposure: float) -> dict:
    return {
        "id": image_id,
        "filepath": f"/tmp/{image_id}.dng",
        "capture_timestamp": timestamp,
        "lens_model": "RF24-70mm F2.8 L IS USM",
        "focal_length": 35.0,
        "ExposureTime": exposure,
        "FNumber": 4.0,
        "ISO": 100,
        "relative_ev": np.log2(exposure * 100 / 16),
    }


class HdrMathTests(unittest.TestCase):
    def test_detects_same_lens_three_frame_bracket(self):
        frames = [_frame(10, 1000.0, 1 / 60), _frame(11, 1001.0, 1 / 15), _frame(12, 1002.0, 1 / 4)]
        with mock.patch.object(hdr, "image_exposure_rows", return_value=frames):
            brackets = hdr.detect_brackets("unused.db")
        self.assertEqual(len(brackets), 1)
        self.assertEqual(brackets[0]["image_ids"], [10, 11, 12])
        self.assertAlmostEqual(brackets[0]["ev_span"], np.log2(15), places=5)

    def test_phase_correlation_recovers_translation_without_wrap(self):
        reference = np.zeros((64, 64), dtype=np.float32)
        reference[21:35, 27:42] = 1.0
        source = np.roll(np.roll(reference, 5, axis=0), -7, axis=1)
        self.assertEqual(hdr.phase_correlation_shift(reference, source), (-5, 7))
        aligned = hdr.translate_rgb(source[..., None].repeat(3, axis=2), -5, 7)
        np.testing.assert_allclose(aligned[..., 0], reference)

    def test_synthetic_exposures_recover_linear_radiance(self):
        scene = np.zeros((48, 64, 3), dtype=np.float32)
        scene[..., 0] = np.linspace(0.01, 1.5, 64, dtype=np.float32)
        scene[..., 1] = np.linspace(0.02, 1.2, 64, dtype=np.float32)
        scene[..., 2] = np.linspace(0.01, 0.9, 64, dtype=np.float32)
        relative_evs = [-2.0, 0.0, 2.0]
        exposures = [np.clip(scene * (2.0 ** ev), 0.0, 1.0) for ev in relative_evs]
        merged, shifts = hdr.merge_linear_arrays(exposures, relative_evs)
        self.assertEqual(shifts, [(0, 0), (0, 0), (0, 0)])
        # Midtones and highlights have at least one valid hat-weighted source;
        # shadows below the sensor floor are not asserted as recovered detail.
        valid = (scene[..., 0] > 0.04) & (scene[..., 0] < 1.3)
        self.assertLess(float(np.mean(np.abs(merged[..., 0][valid] - scene[..., 0][valid]))), 0.01)
        self.assertGreater(float(merged[..., 0].max()), 1.0)


class HdrMergeCacheTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.old_hdr_dir = hdr.HDR_CACHE_DIR
        self.old_cache_root = rawproc.BASE_CACHE_ROOT
        self.old_cache_dir = rawproc.BASE_CACHE_DIR
        hdr.HDR_CACHE_DIR = Path(self.tempdir.name) / "hdr"
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
        hdr.HDR_CACHE_DIR = self.old_hdr_dir
        rawproc.BASE_CACHE_ROOT = self.old_cache_root
        rawproc.BASE_CACHE_DIR = self.old_cache_dir
        self.tempdir.cleanup()

    def test_merge_registers_float_exr_and_scaled_pabase(self):
        frames = [_frame(1, 1000.0, 1 / 60), _frame(2, 1001.0, 1 / 15), _frame(3, 1002.0, 1 / 4)]
        scene = np.full((20, 24, 3), 1.5, dtype=np.float32)
        baseline = np.median([frame["relative_ev"] for frame in frames])
        source = {frame["filepath"]: (np.clip(scene * (2.0 ** (frame["relative_ev"] - baseline)), 0, 1) * 65535).astype(np.uint16) for frame in frames}

        def decode(path):
            return source[path], {}

        with mock.patch.object(hdr, "image_exposure_rows", return_value=frames):
            result = hdr.merge_images(self.db_path, [1, 2, 3], decode=decode)
        self.assertGreater(result["image_id"], 0)
        exr = next(hdr.HDR_CACHE_DIR.glob("*.exr"))
        self.assertEqual(imagecodecs.exr_decode(exr.read_bytes()).dtype, np.float32)
        paths = rawproc.base_paths(result["image_id"])
        payload = gzip.decompress(paths.binary.read_bytes())
        base, width, height = rawproc.parse_base_payload(payload)
        self.assertEqual((width, height), (24, 20))
        meta = rawproc.read_base_metadata(result["image_id"])
        self.assertGreater(meta["hdr"]["scale"], 1.0)
        self.assertEqual(base.dtype, np.dtype("<u2"))

        app = FastAPI()
        catalog_path_use(self.db_path)
        app.include_router(develop_routes.router)
        original_is_raw = rawproc.is_raw_path
        with mock.patch.object(rawproc, "is_raw_path", side_effect=lambda path: original_is_raw(path) or Path(path).suffix.lower() == ".exr"):
            with TestClient(app) as client:
                opened = client.get(f"/api/develop/{result['image_id']}")
        self.assertEqual(opened.status_code, 200)
        self.assertGreater(opened.json()["meta"]["hdr"]["scale"], 1.0)

    def test_catalog_reads_use_fk_enabled_connection_helper(self):
        with mock.patch.object(
            data_connection,
            "open_sync",
            wraps=data_connection.open_sync,
        ) as open_sync:
            self.assertEqual(hdr._catalog_rows(self.db_path, None), [])
        open_sync.assert_called_once_with(self.db_path)
