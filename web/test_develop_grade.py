"""Directional §22 contracts: grade, NR, and manual-only defringe."""

import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
from features.develop import lens, pipeline  # noqa: E402


class DevelopGradeTests(unittest.TestCase):
    def test_distortion_auto_crop_is_identity_for_identity_polynomials(self):
        for distortion in (None, {"model": "poly3", "terms": [0.0]}, {"model": "poly5", "terms": [0.0, 0.0]}):
            self.assertEqual(lens.distortion_auto_crop_scale(distortion, 6000, 4000), 1.0)
        self.assertLess(lens.distortion_auto_crop_scale({"model": "poly3", "terms": [0.25]}, 6000, 4000), 1.0)

    def test_global_wheel_pushes_neutral_toward_selected_hue(self):
        source = np.full((5, 5, 3), 0.36, dtype=np.float32)
        graded = pipeline._color_grade(source, {"ColorGradeGlobalHue": 0, "ColorGradeGlobalSat": 70})
        self.assertGreater(float(graded[..., 0].mean()), float(graded[..., 2].mean()))

    def test_shadow_and_highlight_wheels_are_luma_directional(self):
        source = np.array([[[.08, .08, .08], [.62, .62, .62]]], dtype=np.float32)
        graded = pipeline._color_grade(source, {"ColorGradeShadowHue": 0, "ColorGradeShadowSat": 80, "ColorGradeHighlightHue": 240, "ColorGradeHighlightSat": 80})
        self.assertGreater(float(graded[0, 0, 0] - graded[0, 0, 2]), 0.0)
        self.assertGreater(float(graded[0, 1, 2] - graded[0, 1, 0]), 0.0)

    def test_luminance_nr_reduces_luma_variance(self):
        rng = np.random.default_rng(4)
        source = np.clip(.45 + rng.normal(0, .08, (33, 35, 1)), 0, 1).repeat(3, axis=-1).astype(np.float32)
        reduced = pipeline._noise_reduction(source, {"LuminanceSmoothing": 100})
        self.assertLess(float(pipeline.luma(reduced).std()), float(pipeline.luma(source).std()))

    def test_defringe_only_desaturates_selected_hue_at_edges(self):
        source = np.zeros((9, 9, 3), dtype=np.float32)
        source[:, :4] = [.15, .15, .15]
        source[:, 4:] = [.8, .8, .8]
        source[4, 4] = [.75, .15, .75]  # purple edge fringe
        reduced = pipeline._defringe(source, {"DefringePurpleAmount": 100, "DefringePurpleHueLo": 70, "DefringePurpleHueHi": 90})
        before = pipeline.rgb_to_hsv(source)[1][4, 4]
        after = pipeline.rgb_to_hsv(reduced)[1][4, 4]
        self.assertLess(after, before)
        np.testing.assert_allclose(reduced[0, 0], source[0, 0], atol=1e-6)


if __name__ == "__main__":
    unittest.main()
