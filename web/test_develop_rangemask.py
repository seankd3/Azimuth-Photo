"""Frozen §29.4 contracts for Color and Luminance Range masks."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
from features.develop import masks  # noqa: E402


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

    def test_js_raster_default_color_math_matches_numpy_with_max_delta(self):
        module_url = (Path(__file__).parent / "static/js/desktop/develop/mask_raster.js").as_uri()
        script = f"""
            import * as M from {json.dumps(module_url)};
            const image = {{width:3,height:1,data:new Float32Array([1,0,0, 0,0,1, .9,.08,.04])}};
            console.log(JSON.stringify(Array.from(M.rasterizeColorRange({{Type:2,ColorAmount:.15,PointModels:['1 0 0']}}, image, 3, 1))));
        """
        result = subprocess.run(
            ["node", "--experimental-default-type=module", "--input-type=module", "-e", script],
            check=True, capture_output=True, text=True,
        )
        javascript = np.asarray(json.loads(result.stdout), dtype=np.float32)
        image = np.array([[[1.0, 0.0, 0.0], [0.0, 0.0, 1.0], [0.9, 0.08, 0.04]]], dtype=np.float32)
        numpy = masks.rasterize_color_range({"Type": 2, "ColorAmount": .15, "PointModels": ["1 0 0"]}, image, 3, 1)[0]
        max_delta = float(np.max(np.abs(javascript - numpy)))
        self.assertLessEqual(max_delta, 2e-6, f"color-range JS/numpy max delta {max_delta:.8g}")

    def test_range_panel_has_luminance_histogram_and_rendered_canvas_eyedropper(self):
        source = (Path(__file__).parent / "static/js/desktop/develop/masking.js").read_text(encoding="utf-8")
        for marker in ("data-range-histogram", "data-range-eyedropper", "data-range-anchor", "PointModels", "linear OKLab working space"):
            self.assertIn(marker, source)


if __name__ == "__main__":
    unittest.main()
