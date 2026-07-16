from __future__ import annotations

import unittest

import numpy as np

from features.develop.highlights_recon import reconstruct_highlights
from features.develop.rawproc import derive_libraw_clip_levels


class HighlightReconstructionTests(unittest.TestCase):
    def test_unclipped_image_is_byte_exact(self) -> None:
        rng = np.random.default_rng(42)
        source = rng.uniform(0.0, 0.99, size=(12, 9, 3)).astype(np.float32)

        reconstructed = reconstruct_highlights(source, (1.0, 1.0, 1.0))

        self.assertEqual(reconstructed.tobytes(), source.tobytes())

    def test_clipped_sky_channel_is_recovered_without_magenta_shift(self) -> None:
        sensor = np.empty((32, 32, 3), dtype=np.float32)
        sensor[...] = np.array([0.85, 0.90, 1.00], dtype=np.float32)
        sensor[:4] = np.array([0.42, 0.52, 0.78], dtype=np.float32)
        sensor[-4:] = np.array([0.42, 0.52, 0.78], dtype=np.float32)

        reconstructed = reconstruct_highlights(sensor, (1.0, 1.0, 1.0))

        center = reconstructed[16, 16]
        self.assertGreater(center[2], 1.05)
        self.assertGreaterEqual(center[2], center[1])
        self.assertGreaterEqual(center[1], center[0])
        self.assertEqual(reconstructed[0, 0].tobytes(), sensor[0, 0].tobytes())

    def test_libraw_clip_levels_follow_white_range_and_wb_gain(self) -> None:
        clips = derive_libraw_clip_levels(
            [12000, 11000, 10000, 11000],
            [2000, 1000, 1000, 1000],
            [2.0, 1.0, 1.5, 1.0],
            saturation_level=12000,
        )

        np.testing.assert_allclose(
            clips / 65535.0,
            [2.0, 10.0 / 11.0, 13.5 / 11.0],
            rtol=1e-6,
        )
