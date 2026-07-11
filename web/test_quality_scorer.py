"""Unit tests for technical quality scorer (Pillar 2)."""

from __future__ import annotations

import io
import os
import unittest

import numpy as np
from PIL import Image

from test_support import BackendTestCase
import db
import thumbnails
from features.quality import routes as quality_routes
from features.quality import scorer as quality_scorer


def _jpeg_bytes(array: np.ndarray, *, quality: int = 92) -> bytes:
    if array.ndim == 2:
        image = Image.fromarray(array.astype(np.uint8), mode="L").convert("RGB")
    else:
        image = Image.fromarray(array.astype(np.uint8), mode="RGB")
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=quality)
    return buffer.getvalue()


_RNG = np.random.default_rng(0)


def _sharp_pattern(size: int = 160) -> np.ndarray:
    """Photo-like midtones + fine texture + edges (JPEG-stable)."""
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float32)
    base = 90 + 40 * np.sin(xx / 18) + 30 * np.cos(yy / 22)
    tex = _RNG.normal(0, 12, (size, size))
    base[40:42, :] = 220
    base[:, 70:72] = 30
    return np.clip(base + tex, 0, 255).astype(np.uint8)


def _blurry_pattern(size: int = 160) -> np.ndarray:
    import cv2

    return cv2.GaussianBlur(_sharp_pattern(size), (15, 15), 0)


def _dark_clipped(size: int = 160) -> np.ndarray:
    return np.zeros((size, size), dtype=np.uint8)


class QualityScorerUnitTests(unittest.TestCase):
    def test_sharp_scores_higher_than_blurry(self):
        sharp = quality_scorer.score_jpeg_bytes(_jpeg_bytes(_sharp_pattern()))
        blurry = quality_scorer.score_jpeg_bytes(_jpeg_bytes(_blurry_pattern()))
        self.assertGreater(sharp["sharpness"], blurry["sharpness"])
        self.assertGreater(sharp["score"], blurry["score"])

    def test_exposure_clip_detects_near_black(self):
        dark = quality_scorer.score_jpeg_bytes(_jpeg_bytes(_dark_clipped()))
        mid = np.full((128, 128), 128, dtype=np.uint8)
        mid_score = quality_scorer.score_jpeg_bytes(_jpeg_bytes(mid))
        self.assertGreater(dark["exposure_clip"], 0.9)
        self.assertLess(mid_score["exposure_clip"], 0.05)
        self.assertLess(dark["score"], mid_score["score"])

    def test_eyes_open_is_null_v1(self):
        result = quality_scorer.score_jpeg_bytes(_jpeg_bytes(_sharp_pattern()))
        self.assertIsNone(result["eyes_open"])
        self.assertIn("landmarks", quality_scorer.EYES_OPEN_V1_NOTE.lower())

    def test_face_boxes_drive_subject_region(self):
        size = 160
        # Soft gradient everywhere; sharp texture only in a corner "face"
        # that does not overlap the center-weighted 40% crop.
        xx = np.mgrid[0:size, 0:size][1].astype(np.float32)
        canvas = (80 + 20 * (xx / size)).astype(np.uint8)
        patch = _sharp_pattern(40)
        canvas[0:40, 0:40] = patch
        face_boxes = [(0.0, 0.0, 40.0, 40.0)]
        with_faces = quality_scorer.score_jpeg_bytes(
            _jpeg_bytes(canvas), face_boxes=face_boxes
        )
        without = quality_scorer.score_jpeg_bytes(_jpeg_bytes(canvas), face_boxes=None)
        self.assertEqual(with_faces["subject_region"], "faces")
        self.assertEqual(without["subject_region"], "center")
        self.assertGreater(with_faces["subject_sharpness"], 0.8)
        self.assertLess(without["subject_sharpness"], 0.5)
        self.assertLess(without["subject_sharpness"], with_faces["subject_sharpness"])

    def test_scale_face_boxes(self):
        scaled = quality_scorer.scale_face_boxes(
            [{"x": 10, "y": 20, "w": 40, "h": 50}],
            src_width=200,
            src_height=100,
            dst_width=100,
            dst_height=50,
        )
        self.assertEqual(len(scaled), 1)
        x, y, w, h = scaled[0]
        self.assertAlmostEqual(x, 5.0)
        self.assertAlmostEqual(y, 10.0)
        self.assertAlmostEqual(w, 20.0)
        self.assertAlmostEqual(h, 25.0)


class QualityRoutesTests(BackendTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        quality_routes.configure(db_path=lambda: db.DB_PATH)
        # Reset scan state between tests.
        with quality_routes._scan_lock:
            quality_routes._scan_state.update(
                {
                    "running": False,
                    "stop": False,
                    "limit": 0,
                    "scored": 0,
                    "skipped": 0,
                    "errors": 0,
                    "pending": 0,
                    "total_scored": 0,
                    "last_error": "",
                    "started_at": None,
                    "finished_at": None,
                    "message": "idle",
                }
            )

    async def _write_thumb(self, image_id: int, array: np.ndarray, size: str = "md") -> str:
        root = thumbnails.SSD_CACHE_DIR
        os.makedirs(os.path.join(root, size), exist_ok=True)
        path = os.path.join(root, size, f"{image_id}.jpg")
        data = _jpeg_bytes(array)
        with open(path, "wb") as handle:
            handle.write(data)
        conn = await db.get_db()
        try:
            await conn.execute(
                "INSERT OR REPLACE INTO cache_entries "
                "(cache_root, size, image_id, path, source_signature, size_bytes, "
                "last_accessed, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (root, size, int(image_id), path, f"sig-{image_id}", len(data), 1000.0, 1000.0),
            )
            await conn.commit()
        finally:
            await conn.close()
        return path

    async def test_ensure_table_and_get_scores_on_demand(self):
        source = await self._source()
        image_id = await self._image(source["id"], "sharp.jpg")
        await self._write_thumb(image_id, _sharp_pattern())

        status = await quality_routes.api_quality_status()
        self.assertIn("eyes_open_note", status)
        self.assertEqual(status["total_scored"], 0)
        self.assertGreaterEqual(status["pending"], 1)

        payload = await quality_routes.api_quality_image(image_id)
        self.assertTrue(payload["scored"])
        self.assertIsNone(payload["eyes_open"])
        self.assertGreater(payload["score"], 0)
        self.assertIn("landmarks", payload["eyes_open_note"].lower())

        status2 = await quality_routes.api_quality_status()
        self.assertEqual(status2["total_scored"], 1)

    async def test_scan_scores_unscored_images(self):
        source = await self._source()
        sharp_id = await self._image(source["id"], "a_sharp.jpg")
        blur_id = await self._image(source["id"], "b_blur.jpg")
        dark_id = await self._image(source["id"], "c_dark.jpg")
        await self._write_thumb(sharp_id, _sharp_pattern())
        await self._write_thumb(blur_id, _blurry_pattern())
        await self._write_thumb(dark_id, _dark_clipped())

        result = await quality_routes.api_quality_scan(quality_routes.ScanBody(limit=10))
        self.assertTrue(result["ok"])

        # Wait for threaded worker.
        for _ in range(100):
            status = await quality_routes.api_quality_status()
            if not status["running"] and status["scored"] >= 3:
                break
            await __import__("asyncio").sleep(0.05)
        else:
            self.fail(f"scan did not finish: {status}")

        self.assertEqual(status["errors"], 0)
        self.assertGreaterEqual(status["scored"], 3)

        sharp = await quality_routes.api_quality_image(sharp_id)
        blur = await quality_routes.api_quality_image(blur_id)
        dark = await quality_routes.api_quality_image(dark_id)
        self.assertGreater(sharp["score"], blur["score"])
        self.assertGreater(sharp["score"], dark["score"])

    async def test_scoped_scan_leaves_unrelated_images_unscored(self):
        source = await self._source()
        imported = await self._image(source["id"], "imported.jpg")
        unrelated = await self._image(source["id"], "unrelated.jpg")
        await self._write_thumb(imported, _sharp_pattern())
        await self._write_thumb(unrelated, _blurry_pattern())

        result = await quality_routes.scan_image_ids([imported])

        self.assertEqual(result["scored"], 1)
        self.assertTrue((await quality_routes.api_quality_image(imported))["scored"])
        conn = await db.get_db()
        try:
            row = await (await conn.execute(
                "SELECT 1 FROM image_quality WHERE image_id = ?", (unrelated,)
            )).fetchone()
        finally:
            await conn.close()
        self.assertIsNone(row)


if __name__ == "__main__":
    unittest.main()
