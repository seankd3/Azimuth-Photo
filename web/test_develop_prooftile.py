"""Focused contracts for §29.1 original-resolution proof tiles."""

from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
from PIL import Image

from features.develop import render


class DevelopProofTileTests(unittest.TestCase):
    def setUp(self) -> None:
        render.clear_proof_decode_cache()
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        render.clear_proof_decode_cache()
        self.temp.cleanup()

    def source(self, name: str = "source.raw") -> Path:
        path = self.root / name
        path.write_bytes(name.encode("ascii"))
        return path

    def test_native_tile_location_dimensions_and_pipeline_context(self) -> None:
        source = self.source()
        linear = np.linspace(0, 1, 240 * 300 * 3, dtype=np.float32).reshape(240, 300, 3)
        captured = {}

        def pipeline(tile, settings, **kwargs):
            captured.update(kwargs)
            return np.array(tile, copy=True)

        with mock.patch.object(render, "decode_full_resolution", return_value=linear), mock.patch.object(
            render, "apply_pipeline", side_effect=pipeline
        ):
            tile = render.render_proof_tile(source, {}, u=.75, v=.25, edge=128)

        self.assertEqual((tile.left, tile.top), (160, 0))
        self.assertEqual((tile.width, tile.height), (128, 128))
        self.assertEqual((tile.source_width, tile.source_height), (300, 240))
        self.assertEqual(captured["pixel_offset"], (160, 0))
        self.assertEqual(captured["canvas_size"], (300, 240))
        with Image.open(io.BytesIO(tile.png)) as image:
            self.assertEqual(image.format, "PNG")
            self.assertEqual(image.size, (128, 128))

    def test_decode_cache_is_lru_bounded_to_two_originals(self) -> None:
        sources = [self.source(f"{index}.raw") for index in range(3)]
        decoded = np.zeros((8, 8, 3), dtype=np.float32)
        with mock.patch.object(render, "decode_full_resolution", return_value=decoded) as decode:
            render._proof_decode(sources[0])
            render._proof_decode(sources[1])
            render._proof_decode(sources[0])
            self.assertEqual(decode.call_count, 2)
            render._proof_decode(sources[2])
            render._proof_decode(sources[1])
            self.assertEqual(decode.call_count, 4)
        self.assertEqual(len(render._PROOF_DECODE_CACHE), render.PROOF_DECODE_CACHE_SIZE)


if __name__ == "__main__":
    unittest.main()
