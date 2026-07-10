"""Focused contracts for Develop's cached subject and sky mask service."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from features.develop import ai_mask_routes, ai_masks, rawproc


class AiMaskServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.preview = self.root / "base.jpg"
        image = np.zeros((32, 48, 3), dtype=np.uint8)
        image[:18, :, 2] = 220
        image[:18, :, 1] = 130
        image[18:, :, :] = (70, 95, 55)
        Image.fromarray(image, mode="RGB").save(self.preview, quality=95)
        self.old_mask_directory = ai_masks.MASK_DIRECTORY
        ai_masks.MASK_DIRECTORY = self.root / "ai-masks"

    def tearDown(self) -> None:
        ai_masks.MASK_DIRECTORY = self.old_mask_directory
        self.temporary.cleanup()

    def test_subject_mask_is_content_idempotent_and_is_a_base_sized_png(self) -> None:
        calls = []
        original = ai_masks._subject_mask

        def subject_stub(rgb: np.ndarray) -> np.ndarray:
            calls.append(rgb.shape)
            return np.full(rgb.shape[:2], 0.625, dtype=np.float32)

        ai_masks._subject_mask = subject_stub
        try:
            first = ai_masks.create_mask(self.preview, "subject")
            second = ai_masks.create_mask(self.preview, "subject")
        finally:
            ai_masks._subject_mask = original
        self.assertEqual(first.cache_key, second.cache_key)
        self.assertEqual(calls, [(32, 48, 3)])
        with Image.open(first.path) as mask:
            self.assertEqual(mask.mode, "L")
            self.assertEqual(mask.size, (48, 32))
            self.assertAlmostEqual(float(np.asarray(mask).mean()) / 255.0, 0.625, places=2)
        self.assertNotEqual(first.cache_key, ai_masks.create_mask(self.preview, "sky").cache_key)

    def test_sky_heuristic_prefers_a_blue_upper_frame_and_preserves_edges(self) -> None:
        with Image.open(self.preview) as image:
            mask = ai_masks._sky_mask(np.asarray(image.convert("RGB"), dtype=np.float32) / 255.0)
        self.assertEqual(mask.shape, (32, 48))
        self.assertGreater(float(mask[:12].mean()), float(mask[22:].mean()) * 2.0)
        self.assertTrue(np.all((mask >= 0.0) & (mask <= 1.0)))

    def test_routes_return_a_cache_key_and_serve_the_png(self) -> None:
        app = FastAPI()
        app.include_router(ai_mask_routes.router)
        client = TestClient(app)
        original_image = ai_mask_routes._image_or_error
        original_base = rawproc.ensure_base_cache
        original_create = ai_masks.create_mask
        output = ai_masks.MASK_DIRECTORY / ("a" * 64 + ".png")
        output.parent.mkdir(parents=True)
        Image.new("L", (48, 32), color=128).save(output)

        async def image_stub(_image_id: int):
            return {"filepath": str(self.root / "sample.dng")}, None

        def base_stub(_image_id: int, _path: str):
            return rawproc.BasePaths(self.root / "base.bin.gz", self.root / "base.json", self.preview), {}

        def create_stub(_preview: Path, kind: str):
            return ai_masks.AiMaskResult("a" * 64, output, kind, 48, 32)

        ai_mask_routes._image_or_error = image_stub
        rawproc.ensure_base_cache = base_stub
        ai_masks.create_mask = create_stub
        try:
            created = client.post("/api/develop/9/ai-mask", json={"kind": "subject"})
            fetched = client.get(created.json()["url"])
        finally:
            ai_mask_routes._image_or_error = original_image
            rawproc.ensure_base_cache = original_base
            ai_masks.create_mask = original_create
        self.assertEqual(created.status_code, 200)
        self.assertEqual(created.json()["cache_key"], "a" * 64)
        self.assertEqual(fetched.status_code, 200)
        self.assertEqual(fetched.headers["content-type"], "image/png")
