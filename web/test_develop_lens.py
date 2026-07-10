"""Focused contracts for fitted Lensfun correction data and twin NumPy math."""

import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
from features.develop import lens, pipeline  # noqa: E402


class DevelopLensTests(unittest.TestCase):
    def test_vignetting_gain_uses_lensfun_pa_polynomial(self):
        radius = np.array([0.0, 0.5, 1.0], dtype=np.float32)
        correction = {"model": "pa", "terms": [-0.2, 0.1, -0.05]}
        gain = pipeline.lens_vignetting_gain(radius, correction)
        expected = 1.0 - 0.2 * radius**2 + 0.1 * radius**4 - 0.05 * radius**6
        np.testing.assert_allclose(gain, expected, atol=1e-7)
        self.assertEqual(float(gain[0]), 1.0)

    def test_uv_polynomial_is_identity_at_optical_center(self):
        for distortion in (
            {"model": "poly3", "terms": [0.25, 0.0, 0.0]},
            {"model": "ptlens", "terms": [0.1, -0.2, 0.3]},
        ):
            corrected_radius = np.array([0.0], dtype=np.float32)
            source_radius = corrected_radius * pipeline.lens_radial_scale(corrected_radius, distortion)
            np.testing.assert_array_equal(source_radius, corrected_radius)

    def test_enable_flag_is_explicit_and_defaults_off(self):
        y, x = np.mgrid[0:9, 0:9].astype(np.float32)
        source = np.stack((x / 8.0, y / 8.0, (x + y) / 16.0), axis=-1)
        color = {
            "lens_correction": {
                "camera_crop_factor": 1.0,
                "lens_crop_factor": 1.0,
                "distortion": {"model": "poly3", "terms": [0.3, 0.0, 0.0]},
            }
        }
        baseline = pipeline.apply_pipeline(source, {}, color_profile=color)
        absent = pipeline.apply_pipeline(source, {"LensProfileEnable": False}, color_profile=color)
        enabled = pipeline.apply_pipeline(source, {"LensProfileEnable": True}, color_profile=color)
        np.testing.assert_array_equal(absent, baseline)
        self.assertGreater(float(np.max(np.abs(enabled - baseline))), 0.01)
        np.testing.assert_allclose(enabled[4, 4], baseline[4, 4], atol=1e-6)

    def test_export_style_tiles_sample_the_full_source_without_seams(self):
        y, x = np.mgrid[0:24, 0:32].astype(np.float32)
        source = np.stack((x / 31.0, y / 23.0, (x + y) / 54.0), axis=-1)
        settings = {"LensProfileEnable": True}
        color = {
            "lens_correction": {
                "camera_crop_factor": 1.0,
                "lens_crop_factor": 1.0,
                "distortion": {"model": "ptlens", "terms": [0.02, -0.08, 0.06]},
                "vignetting": {"model": "pa", "terms": [-0.1, 0.05, -0.01]},
            }
        }
        full = pipeline.apply_pipeline(source, settings, color_profile=color)
        tiled = np.empty_like(full)
        for top in range(0, 24, 6):
            tiled[top : top + 6] = pipeline.apply_pipeline(
                source[top : top + 6],
                settings,
                color_profile=color,
                pixel_offset=(0, top),
                canvas_size=(32, 24),
            )
        np.testing.assert_allclose(tiled, full, atol=2e-6)

    def test_modern_rf_lens_resolves_from_exif_names(self):
        correction = lens.resolve_lens_correction(
            {
                "camera_make": "Canon",
                "camera_model": "Canon EOS R5",
                "lens_model": "RF24-105mm F4 L IS USM",
                "focal_length": 37,
                "aperture": 11,
            }
        )
        self.assertIsNotNone(correction)
        self.assertEqual(correction["distortion"]["model"], "ptlens")
        self.assertIn("Canon RF 24-105mm", correction["lens"])


if __name__ == "__main__":
    unittest.main()
