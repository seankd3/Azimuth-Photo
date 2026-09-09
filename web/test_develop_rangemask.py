"""Frozen §29.4 contracts for Color and Luminance Range masks."""

from __future__ import annotations

import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
from pixels import masks  # noqa: E402


class RangeMaskTests(unittest.TestCase):
    def test_red_anchor_is_more_than_five_times_blue_weight(self):
        image = np.array([[[1.0, 0.0, 0.0], [0.0, 0.0, 1.0]]], dtype=np.float32)
        weights = masks.rasterize_color_range(
            {"Type": 2, "ColorAmount": 0.15, "PointModels": [{"Color": "1 0 0"}]}, image, 2, 1
        )[0]
        self.assertGreater(float(weights[0]), float(weights[1]) * 5.0)

    def test_color_range_keeps_adobe_luminance_feather(self):
        image = np.array([[[1.0, 0.0, 0.0], [0.18, 0.0, 0.0]]], dtype=np.float32)
        weights = masks.rasterize_color_range(
            {"Type": 2, "ColorAmount": 1.0, "PointModels": ["1 0 0"], "LumRange": "0.10 0.18 0.30 0.38"}, image, 2, 1
        )[0]
        self.assertGreater(float(weights[0]), 0.9)
        self.assertLess(float(weights[1]), 0.05)

if __name__ == "__main__":
    unittest.main()
