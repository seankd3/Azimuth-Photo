"""Invariant sweep for every numeric control in the Python Develop contract."""

from __future__ import annotations

import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
from features.develop import ops_constants as C  # noqa: E402
from features.develop.pipeline import apply_pipeline  # noqa: E402
from test_develop_parity import synthetic_linear_image, torture_settings  # noqa: E402


class DevelopSliderInvariantTests(unittest.TestCase):
    def test_every_pipeline_slider_is_finite_bounded_and_exact_at_default(self):
        source = synthetic_linear_image()[::2, ::2]
        fixture = torture_settings()
        names = [key for key, *_ in C.PIPELINE_SLIDER_SPECS]
        self.assertEqual(len(names), len(set(names)))
        self.assertNotIn("SharpenDetail", names)
        baseline = apply_pipeline(source, {"WhiteBalance": "As Shot"}, asshot_temperature=5500)

        for key, minimum, maximum, default in C.PIPELINE_SLIDER_SPECS:
            explicit = apply_pipeline(
                source, {"WhiteBalance": "As Shot", key: default}, asshot_temperature=5500
            )
            with self.subTest(key=key, value="default"):
                np.testing.assert_array_equal(explicit, baseline)

            for value in (minimum, (minimum + maximum) * 0.5, maximum):
                output = apply_pipeline(source, {**fixture, key: value}, asshot_temperature=5500)
                with self.subTest(key=key, value=value):
                    self.assertTrue(np.isfinite(output).all())
                    self.assertGreaterEqual(float(output.min()), 0.0)
                    self.assertLessEqual(float(output.max()), 1.0)


if __name__ == "__main__":
    unittest.main()
