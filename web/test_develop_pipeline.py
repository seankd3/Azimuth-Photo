"""Focused operation-level contracts for the pure NumPy develop pipeline."""

import math
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
from features.develop import ops_constants as C  # noqa: E402
from features.develop import pipeline  # noqa: E402


class DevelopPipelineTests(unittest.TestCase):
    def test_white_balance_is_planckian_white_point_adaptation(self):
        source = np.ones((1, 1, 3), dtype=np.float32)
        identity = pipeline._apply_white_balance(
            source, {"Temperature": 5500, "Tint": 12, "WhiteBalance": "Custom"}, 5500, 12
        )
        np.testing.assert_allclose(identity[0, 0], [1.0, 1.0, 1.0], atol=1e-5)

        warmer = pipeline._apply_white_balance(
            source, {"Temperature": 9969, "Tint": 0, "WhiteBalance": "Custom"}, 5613, 0
        )
        self.assertGreater(warmer[0, 0, 0], 1.15)
        self.assertLess(warmer[0, 0, 2], 0.85)
        self.assertAlmostEqual(warmer[0, 0, 1], 1.0, places=2)

        magenta = pipeline._apply_white_balance(
            source, {"Temperature": 5500, "Tint": 60, "WhiteBalance": "Custom"}, 5500, 0
        )
        self.assertLess(magenta[0, 0, 1], min(magenta[0, 0, 0], magenta[0, 0, 2]))

    def test_exposure_is_exact_exp2(self):
        # Exposure is exact exp2 in linear; the shared base profile then remaps gamma.
        dark = np.full((2, 2, 3), 0.125, dtype=np.float32)
        bright = np.full((2, 2, 3), 0.5, dtype=np.float32)
        boosted = pipeline.apply_pipeline(dark, {"Exposure2012": 2})
        reference = pipeline.apply_pipeline(bright, {})
        np.testing.assert_allclose(boosted, reference, atol=2e-6)

    def test_region_weights_at_known_ev_centers(self):
        # Gaussian weights peak at their EV centers and fall off by ~1σ.
        centers = {
            "hl": C.TONE_EV_HIGHLIGHTS_CENTER,
            "sh": C.TONE_EV_SHADOWS_CENTER,
            "wh": C.TONE_EV_WHITES_CENTER,
            "bl": C.TONE_EV_BLACKS_CENTER,
        }
        for name, center in centers.items():
            peak = pipeline._gaussian_ev(np.array([center], dtype=np.float32), center)[0]
            side = pipeline._gaussian_ev(np.array([center + C.TONE_EV_SIGMA], dtype=np.float32), center)[0]
            self.assertAlmostEqual(float(peak), 1.0, places=5)
            self.assertAlmostEqual(float(side), math.exp(-0.5), places=5)

    def test_negative_highlights_recover_about_two_ev(self):
        # At the highlights Gaussian center, −100 recovers ~2 EV (ratio-preserving).
        y = float(2.0 ** C.TONE_EV_HIGHLIGHTS_CENTER)
        source = np.full((1, 1, 3), y, dtype=np.float32)
        result = pipeline._region_tone_map(source, {"Highlights2012": -100, "Contrast2012": 0})
        recovered = float(pipeline.luma(result)[0, 0])
        expected = y * (2.0 ** -C.TONE_HIGHLIGHTS_FACTOR)
        self.assertAlmostEqual(recovered, expected, delta=expected * 0.08)

    def test_zero_contrast_is_region_tone_identity(self):
        source = np.array([[[0.07, 0.19, 0.42], [0.8, 0.1, 0.3]]], dtype=np.float32)
        result = pipeline._region_tone_map(source, {"Contrast2012": 0})
        np.testing.assert_allclose(result, source, atol=3e-6)

    def test_monotone_cubic_lut_goes_through_points_and_stays_monotone(self):
        lut = pipeline.build_monotone_cubic_lut(["0, 0", "64, 32", "128, 160", "255, 255"])
        np.testing.assert_allclose(lut[[0, 64, 128, 255]], [0.0, 32 / 255, 160 / 255, 1.0], atol=2e-6)
        self.assertTrue(np.all(np.diff(lut) >= -1e-7))

    def test_hsl_band_weights_sum_and_neutral_protect(self):
        weights = pipeline.hsl_band_weights(np.array([0, 30, 260, 350], dtype=np.float32))
        np.testing.assert_allclose(weights.sum(axis=-1), 1.0, atol=1e-7)
        np.testing.assert_allclose(weights[2, [5, 6]], [0.5, 0.5], atol=1e-6)
        self.assertGreater(float(pipeline.hsl_band_weights(np.array([359.0], dtype=np.float32))[0, 0]), 0.99)
        neutral = np.full((1, 1, 3), 0.5, dtype=np.float32)
        adjusted = pipeline._hsl_and_black_white(neutral, {"HueAdjustmentRed": 100, "Saturation": 0}, 0.0)
        np.testing.assert_allclose(adjusted, neutral, atol=1e-7)

    def test_vibrance_protects_already_saturated_colors(self):
        saturated_red = np.array([[[1.0, 0.0, 0.0]]], dtype=np.float32)
        result = pipeline._hsl_and_black_white(saturated_red, {"Vibrance": 100}, 0.0)
        # OKLab vibrance weights by (1−satness); fully chromatic red barely moves.
        np.testing.assert_allclose(result, saturated_red, atol=3e-2)

    def test_vignette_has_center_and_corner_radial_values(self):
        image = np.ones((101, 101, 3), dtype=np.float32)
        result = pipeline._vignette(
            image,
            {"PostCropVignetteAmount": -100, "PostCropVignetteMidpoint": 0, "PostCropVignetteFeather": 0},
        )
        self.assertAlmostEqual(float(result[50, 50, 0]), 1.0, places=6)
        self.assertAlmostEqual(float(result[0, 0, 0]), 0.1, places=6)

    def test_grain_hash_has_exact_unsigned_integer_results(self):
        values = pipeline.grain_hash_u32(np.array([0, 1, 7], dtype=np.uint32), np.array([0, 1, 11], dtype=np.uint32))
        np.testing.assert_array_equal(values, np.array([2464270018, 1541986488, 477531945], dtype=np.uint32))

    def test_soft_clamp_is_continuous_at_one(self):
        epsilon = np.float32(1e-5)
        result = pipeline._soft_clamp(np.array([1.0 - epsilon, 1.0, 1.0 + epsilon], dtype=np.float32))
        self.assertAlmostEqual(float(result[1]), 1.0, places=7)
        self.assertLess(float(result[2] - result[1]), 1.1e-5)
        self.assertLess(float(result[1] - result[0]), 1.1e-5)


if __name__ == "__main__":
    unittest.main()
