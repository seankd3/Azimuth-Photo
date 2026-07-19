"""Focused contracts for measured camera-noise lookup and NR defaults."""

from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent))

from features.develop import noise_profiles
from features.develop import pipeline


class NoiseProfileTests(unittest.TestCase):
    def test_exact_iso_lookup_accepts_canon_prefixed_model(self):
        profile = noise_profiles.lookup("Canon EOS R5", 6400)

        self.assertIsNotNone(profile)
        self.assertAlmostEqual(profile["a"][1], 6.21831223088232e-05)
        self.assertAlmostEqual(profile["b"][1], 1.6753661187751e-07)

    def test_lookup_interpolates_coefficients_in_log_iso(self):
        lower = noise_profiles.lookup("EOS R5", 100)
        upper = noise_profiles.lookup("EOS R5", 125)
        interpolated = noise_profiles.lookup("EOS R5", math.sqrt(100 * 125))

        self.assertIsNotNone(lower)
        self.assertIsNotNone(upper)
        self.assertIsNotNone(interpolated)
        for key in ("a", "b"):
            for low, high, actual in zip(lower[key], upper[key], interpolated[key]):
                self.assertAlmostEqual(actual, (low + high) / 2.0)

    def test_unknown_camera_returns_none_generic_fallback(self):
        self.assertIsNone(noise_profiles.lookup("Canon EOS Nevermade", 6400))
        self.assertIsNone(noise_profiles.vst_parameters("Pixel 10a", 6400))
        self.assertEqual(
            noise_profiles.resolve_file_defaults({}, {"camera_model": "Pixel 10a", "ISO": 6400}),
            {},
        )

    def test_lookup_clamps_outside_measured_range(self):
        low = noise_profiles.lookup("EOS R7", 1)
        measured_low = noise_profiles.lookup("EOS R7", 100)
        high = noise_profiles.lookup("EOS R7", 999999)
        measured_high = noise_profiles.lookup("EOS R7", 51200)

        self.assertEqual(low, measured_low)
        self.assertEqual(high, measured_high)

    def test_fixture_aliases_resolve_to_shipped_profiles(self):
        cases = (
            ("EOS R5m2", "EOS R5 Mark II"),
            ("EOS R6m2", "EOS R6 Mark II"),
            ("ILCE-7RM4A", "ILCE-7RM4"),
            ("EOS Rebel T7", "EOS 2000D"),
            ("DJI FC3170", "FC3170"),
            ("EOS RP", "EOS RP"),
        )
        for catalog_model, canonical in cases:
            with self.subTest(catalog_model=catalog_model):
                self.assertEqual(noise_profiles.canonicalize_model(catalog_model), canonical)
                self.assertIsNotNone(noise_profiles.lookup(catalog_model, 1600))

    def test_vst_parameters_drive_nr_slider_strength(self):
        params = noise_profiles.vst_parameters("EOS R5", 6400)
        self.assertIsNotNone(params)
        self.assertAlmostEqual(params["sigma"], math.sqrt(params["variance"]))
        strength = noise_profiles.nr_strength_from_vst(params)
        self.assertIsNotNone(strength)
        self.assertAlmostEqual(strength["LuminanceSmoothing"], 35.0, places=3)
        self.assertAlmostEqual(strength["ColorNoiseReduction"], 20.0, places=3)

        low = noise_profiles.suggested_defaults("EOS R5", 100)
        high = noise_profiles.suggested_defaults("EOS R7", 6400)
        self.assertIsNotNone(low)
        self.assertIsNotNone(high)
        self.assertGreater(high["LuminanceSmoothing"], low["LuminanceSmoothing"])
        # Crop-sensor R7 at the same ISO is noisier than FF R5.
        r5 = noise_profiles.suggested_defaults("EOS R5", 6400)
        self.assertGreater(high["LuminanceSmoothing"], r5["LuminanceSmoothing"])

    def test_parameter_application_changes_pipeline_pixels(self):
        import numpy as np

        rng = np.random.default_rng(7)
        source = rng.random((64, 64, 3), dtype=np.float32)
        untouched = pipeline._noise_reduction(source, {})
        defaults = noise_profiles.suggested_defaults("EOS R7", 6400)
        self.assertIsNotNone(defaults)
        denoised = pipeline._noise_reduction(source, defaults)
        self.assertFalse(np.allclose(untouched, denoised))
        self.assertTrue(np.allclose(untouched, source))

    def test_default_resolution_preserves_explicit_nr_and_scales_with_iso(self):
        explicit = {"LuminanceSmoothing": 42, "ColorNoiseReduction": 17, "Exposure2012": 0.5}
        self.assertEqual(
            noise_profiles.resolve_file_defaults(explicit, {"camera_model": "EOS R5", "ISO": 12800}),
            explicit,
        )

        low = noise_profiles.resolve_file_defaults({}, {"camera_model": "EOS R5", "ISO": 100})
        high = noise_profiles.resolve_file_defaults({}, {"camera_model": "EOS R5", "ISO": 12800})
        self.assertGreater(high["LuminanceSmoothing"], low["LuminanceSmoothing"])
        self.assertGreater(high["ColorNoiseReduction"], low["ColorNoiseReduction"])

    def test_unprofiled_camera_keeps_default_render_settings_identical(self):
        settings = {"Exposure2012": 0.25, "Contrast2012": 10}
        resolved = noise_profiles.resolve_file_defaults(
            settings, {"camera_model": "Pixel 8 Pro", "ISO": 12800}
        )
        self.assertEqual(resolved, settings)

    def test_file_default_resolution_never_reads_exif_for_iso_less_metadata(self):
        with mock.patch("features.develop.lens.read_exif", side_effect=AssertionError("request-time EXIF")) as read_exif:
            resolved = noise_profiles.resolve_file_defaults(
                {"Exposure2012": 0.5},
                {"camera_model": "EOS R5"},
                "/photos/source.dng",
            )

        self.assertEqual(resolved, {"Exposure2012": 0.5})
        read_exif.assert_not_called()


if __name__ == "__main__":
    unittest.main()
