"""Focused contracts for measured camera-noise lookup and NR defaults."""

from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from features.develop import noise_profiles


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

    def test_unknown_camera_returns_none(self):
        self.assertIsNone(noise_profiles.lookup("Canon EOS Nevermade", 6400))

    def test_lookup_clamps_outside_measured_range(self):
        low = noise_profiles.lookup("EOS R7", 1)
        measured_low = noise_profiles.lookup("EOS R7", 100)
        high = noise_profiles.lookup("EOS R7", 999999)
        measured_high = noise_profiles.lookup("EOS R7", 51200)

        self.assertEqual(low, measured_low)
        self.assertEqual(high, measured_high)

    def test_default_resolution_preserves_explicit_nr_and_scales_with_iso(self):
        explicit = {"LuminanceSmoothing": 42, "ColorNoiseReduction": 17, "Exposure2012": 0.5}
        self.assertEqual(
            noise_profiles.resolve_defaults(explicit, {"camera_model": "EOS R5", "ISO": 12800}),
            explicit,
        )

        low = noise_profiles.resolve_defaults({}, {"camera_model": "EOS R5", "ISO": 100})
        high = noise_profiles.resolve_defaults({}, {"camera_model": "EOS R5", "ISO": 12800})
        self.assertGreater(high["LuminanceSmoothing"], low["LuminanceSmoothing"])
        self.assertGreater(high["ColorNoiseReduction"], low["ColorNoiseReduction"])


if __name__ == "__main__":
    unittest.main()
