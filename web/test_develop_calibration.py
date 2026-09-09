"""§28 Calibration's directional linear-RGB contract."""

import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
from pixels import pipeline  # noqa: E402


class DevelopCalibrationTests(unittest.TestCase):
    def test_red_primary_hue_moves_red_without_moving_blue(self):
        # Matrix columns are source primaries: only the red input column moves.
        source = np.array([[[0.70, 0.0, 0.0], [0.0, 0.0, 0.70]]], dtype=np.float32)
        calibrated = pipeline._apply_calibration(source, {"CalibrationRedPrimaryHue": 100})
        self.assertGreater(float(calibrated[0, 0, 1]), 0.0)
        self.assertLess(float(calibrated[0, 0, 0]), float(source[0, 0, 0]))
        np.testing.assert_allclose(calibrated[0, 1], source[0, 1], atol=1e-7)

    def test_shadow_tint_is_green_magenta_and_shadow_weighted(self):
        source = np.array([[[0.08, 0.08, 0.08], [0.70, 0.70, 0.70]]], dtype=np.float32)
        calibrated = pipeline._apply_calibration(source, {"CalibrationShadowTint": 100})
        self.assertGreater(float(calibrated[0, 0, 0]), float(calibrated[0, 0, 1]))
        np.testing.assert_allclose(calibrated[0, 1], source[0, 1], atol=1e-6)


if __name__ == "__main__":
    unittest.main()
