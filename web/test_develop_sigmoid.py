"""HDR sigmoid view transform: curve contract + pipeline routing."""

import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
from features.develop import ops_constants as C  # noqa: E402
from features.develop import pipeline, sigmoid_view  # noqa: E402


class SigmoidViewTests(unittest.TestCase):
    def test_curve_contract(self):
        """Mid-grey locked, monotone, asymptotic to white without overshoot."""
        grey = float(sigmoid_view.sigmoid_view(np.full((1, 1, 3), 0.1845, np.float32))[0, 0, 0])
        self.assertAlmostEqual(grey, 0.1845, delta=2e-3)
        xs = np.array([0.0, 0.05, 0.1845, 0.5, 1.0, 2.0, 8.0, 1000.0], np.float32)
        ys = sigmoid_view.sigmoid_view(xs.reshape(-1, 1, 1).repeat(3, -1))[:, 0, 0]
        self.assertTrue(np.all(np.diff(ys) > 0.0), "sigmoid must be strictly increasing")
        self.assertGreater(float(ys[-2]), 0.98)  # 8.0 rolls off near white
        self.assertLessEqual(float(ys.max()), 1.0)  # never overshoots

    def test_baked_constants_match_derivation(self):
        """ops_constants SIGMOID_* are the derive_params output (twin contract)."""
        p = sigmoid_view.derive_params()
        self.assertAlmostEqual(C.SIGMOID_FILM_POWER, p["film_power"], places=5)
        self.assertAlmostEqual(C.SIGMOID_PAPER_POWER, p["paper_power"], places=5)
        self.assertAlmostEqual(C.SIGMOID_PAPER_EXP, p["paper_exp"], places=5)
        self.assertAlmostEqual(C.SIGMOID_FILM_FOG, p["film_fog"], places=5)

    def test_pipeline_routes_hdr_bases_through_sigmoid(self):
        """An hdr-flagged base keeps highlight separation where SDR clips it."""
        base = np.full((8, 8, 3), 0.5, np.float32)
        base[:4] = 2.0  # restored HDR headroom (hdr.scale applied upstream)
        base[4:6] = 4.0
        hdr_out = pipeline.apply_pipeline(base, {}, color_profile={"hdr": {"scale": 4.0}})
        sdr_out = pipeline.apply_pipeline(base, {}, color_profile={})
        # SDR path crushes 2.0 and 4.0 to (nearly) the same white...
        self.assertLess(abs(float(sdr_out[0, 0, 0]) - float(sdr_out[4, 0, 0])), 0.02)
        # ...the HDR sigmoid path keeps them separated and unclipped.
        self.assertGreater(float(hdr_out[4, 0, 0]) - float(hdr_out[0, 0, 0]), 0.03)
        self.assertLess(float(hdr_out[4, 0, 0]), 1.0)
        # and non-HDR renders are untouched by the new branch (routing guard).
        again = pipeline.apply_pipeline(base, {}, color_profile={})
        np.testing.assert_allclose(sdr_out, again, atol=0.0)


if __name__ == "__main__":
    unittest.main()
