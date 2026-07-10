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
    def test_white_balance_temperature_and_tint_have_the_specified_direction(self):
        source = np.ones((1, 1, 3), dtype=np.float32)
        result = pipeline._apply_white_balance(
            source, {"Temperature": 6500, "Tint": 10, "WhiteBalance": "Custom"}, 5500
        )
        dm = 1_000_000 / 5500 - 1_000_000 / 6500
        self.assertAlmostEqual(result[0, 0, 0], math.exp2(dm * C.K_TEMP), places=6)
        self.assertAlmostEqual(result[0, 0, 2], math.exp2(-dm * C.K_TEMP), places=6)
        self.assertAlmostEqual(result[0, 0, 1], math.exp2(-10 * C.K_TINT), places=6)
        self.assertGreater(result[0, 0, 0], result[0, 0, 2])

    def test_exposure_is_exact_exp2(self):
        source = np.full((2, 2, 3), 0.125, dtype=np.float32)
        result = pipeline.apply_pipeline(source, {"Exposure2012": 2})
        expected = pipeline.linear_to_srgb(np.full_like(source, 0.5))
        np.testing.assert_allclose(result, expected, atol=2e-6)

    def test_region_weights_at_known_t_values(self):
        t = np.array([0.0, 0.25, 0.55, 0.75, 1.0], dtype=np.float32)
        highlights = pipeline._smoothstep(0.45, 1.0, t)
        shadows = 1.0 - pipeline._smoothstep(0.0, 0.55, t)
        whites = pipeline._smoothstep(0.75, 1.0, t)
        blacks = 1.0 - pipeline._smoothstep(0.0, 0.25, t)
        np.testing.assert_allclose(highlights[[0, -1]], [0.0, 1.0])
        np.testing.assert_allclose(shadows[[0, 2]], [1.0, 0.0])
        np.testing.assert_allclose(whites[[0, 3, 4]], [0.0, 0.0, 1.0])
        np.testing.assert_allclose(blacks[[0, 1]], [1.0, 0.0])

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
        np.testing.assert_allclose(result, saturated_red, atol=1e-7)

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
