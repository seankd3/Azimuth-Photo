"""The shaping curves the pipeline blends with, and the NaN one of them made.

Three copies of `smoothstep` existed. They had drifted: one divided by the gap
between its edges without checking it was non-zero, which hands a NaN to
whatever it was about to weight — a corrupt pixel rather than a wrong one.
"""

import pathlib
import unittest

import numpy as np

from pixels import ops_constants as C
from pixels.ramps import gaussian_ev, smoothstep

VALUES = np.array([-1.0, 0.0, 0.25, 0.5, 0.75, 1.0, 2.0], dtype=np.float32)


class SmoothstepTests(unittest.TestCase):
    def test_it_eases_from_zero_to_one_between_the_edges(self):
        ramp = smoothstep(0.0, 1.0, VALUES)
        self.assertEqual(ramp[1], 0.0)
        self.assertEqual(ramp[3], 0.5)
        self.assertEqual(ramp[5], 1.0)

    def test_it_clamps_outside_the_edges(self):
        ramp = smoothstep(0.0, 1.0, VALUES)
        self.assertEqual(ramp[0], 0.0)
        self.assertEqual(ramp[-1], 1.0)

    def test_coincident_edges_give_a_step_rather_than_a_nan(self):
        """The bug: one copy divided by a zero gap and produced NaN."""

        ramp = smoothstep(0.5, 0.5, VALUES)
        self.assertFalse(np.isnan(ramp).any(), "a NaN here is a corrupt pixel")
        self.assertEqual(ramp[0], 0.0)
        self.assertEqual(ramp[-1], 1.0)

    def test_it_never_leaves_zero_to_one(self):
        for edges in ((0.0, 1.0), (0.5, 0.5), (1.0, 0.0), (-2.0, 3.0)):
            ramp = smoothstep(*edges, VALUES)
            self.assertTrue(np.all((ramp >= 0.0) & (ramp <= 1.0)), f"edges {edges}")
            self.assertFalse(np.isnan(ramp).any(), f"edges {edges}")

    def test_it_returns_the_pipeline_dtype(self):
        self.assertEqual(smoothstep(0.0, 1.0, VALUES).dtype, np.float32)


class GaussianTests(unittest.TestCase):
    def test_it_peaks_at_its_centre(self):
        self.assertAlmostEqual(float(gaussian_ev(np.array([0.0], dtype=np.float32), 0.0)[0]), 1.0)

    def test_it_falls_away_either_side(self):
        bell = gaussian_ev(np.array([-2.0, 0.0, 2.0], dtype=np.float32), 0.0)
        self.assertLess(bell[0], bell[1])
        self.assertLess(bell[2], bell[1])

    def test_a_zero_width_bell_does_not_divide_by_zero(self):
        bell = gaussian_ev(np.array([0.0, 1.0], dtype=np.float32), 0.0, sigma=0.0)
        self.assertFalse(np.isnan(bell).any())

    def test_the_default_width_is_the_shared_constant(self):
        self.assertTrue(
            np.allclose(
                gaussian_ev(np.array([1.0], dtype=np.float32), 0.0),
                gaussian_ev(np.array([1.0], dtype=np.float32), 0.0, sigma=C.TONE_EV_SIGMA),
            )
        )


class NobodyKeepsAPrivateCopyTests(unittest.TestCase):
    def test_no_develop_module_redefines_these(self):
        package = pathlib.Path(__file__).with_name("pixels")
        strays = [
            path.name
            for path in package.glob("*.py")
            if path.name != "ramps.py"
            and "\ndef _smoothstep(" in path.read_text(encoding="utf-8", errors="replace")
        ]
        self.assertEqual(strays, [], f"private copies remain: {strays}")


if __name__ == "__main__":
    unittest.main()
