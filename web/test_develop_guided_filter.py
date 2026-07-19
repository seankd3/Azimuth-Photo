"""Contracts for the shared Develop guided-filter primitive."""

from __future__ import annotations

import os
import sys
import time
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
from features.develop import guided_filter as gf  # noqa: E402
from features.develop import masks, ops_constants as C  # noqa: E402


def _step_image(height: int = 64, width: int = 64) -> tuple[np.ndarray, np.ndarray]:
    """Synthetic high-contrast step: dark left / bright right guide + soft mask."""

    guide = np.zeros((height, width), dtype=np.float32)
    guide[:, width // 2 :] = 1.0
    mask = np.zeros((height, width), dtype=np.float32)
    # Soft gaussian-like falloff centered on the dark half, bleeding across the edge.
    xs = (np.arange(width, dtype=np.float32) + 0.5) / width
    mask[:] = np.clip(1.0 - (xs - 0.25) / 0.35, 0.0, 1.0)[None, :]
    return guide, mask


class GuidedFilterMathTests(unittest.TestCase):
    def test_constant_region_stays_flat(self) -> None:
        guide = np.full((48, 48), 0.4, dtype=np.float32)
        source = np.full((48, 48), 0.7, dtype=np.float32)
        out = gf.guided_filter(guide, source, radius=5, epsilon=0.01)
        np.testing.assert_allclose(out, 0.7, atol=1e-5)

    def test_self_guided_constant_is_identity(self) -> None:
        field = np.full((32, 32), 0.55, dtype=np.float32)
        out = gf.guided_filter(field, field, radius=4, epsilon=1e-4)
        np.testing.assert_allclose(out, field, atol=1e-5)

    def test_edge_preservation_beats_gaussian_on_step(self) -> None:
        guide, mask = _step_image()
        radius = 6
        guided = gf.guided_filter(guide, mask, radius=radius, epsilon=0.001)
        blurred = gf.soft_mask(mask, radius, guide=None)
        # Far from the edge, both should be smooth; at the step, guided stays closer
        # to the guide discontinuity (less halo bleed into the bright half).
        edge = guide.shape[1] // 2
        guided_bleed = float(guided[:, edge : edge + 4].mean())
        gaussian_bleed = float(blurred[:, edge : edge + 4].mean())
        self.assertLess(guided_bleed, gaussian_bleed - 0.02)
        # Dark-side interior stays high for both.
        self.assertGreater(float(guided[:, : edge // 2].mean()), 0.85)

    def test_eigf_tracks_classic_on_unit_guide(self) -> None:
        guide, mask = _step_image(40, 40)
        classic = gf.guided_filter(guide, mask, radius=3, epsilon=0.01)
        eigf = gf.eigf_guided_filter(guide, mask, radius=3, epsilon=0.01)
        # Same qualitative edge stick; EIGF need not match classic bit-exact.
        self.assertLess(float(eigf[:, 20:24].mean()), float(classic[:, 20:24].mean()) + 0.15)

    def test_feather_maps_to_larger_radius_and_smaller_epsilon(self) -> None:
        soft_r, soft_eps = gf.feather_to_guided_params(0.2, 200, 100)
        hard_r, hard_eps = gf.feather_to_guided_params(0.9, 200, 100)
        self.assertLess(soft_r, hard_r)
        self.assertGreater(soft_eps, hard_eps)

    def test_soft_mask_wraps_gaussian_without_guide(self) -> None:
        field = np.zeros((32, 32), dtype=np.float32)
        field[8:24, 8:24] = 1.0
        soft = gf.soft_mask(field, radius=4, guide=None)
        self.assertLess(float(soft[0, 0]), 0.05)
        self.assertGreater(float(soft[16, 16]), 0.9)
        self.assertLess(float(soft.max()), 1.0 + 1e-5)


class GuidedMaskRefineTests(unittest.TestCase):
    def test_radial_feather_refine_sticks_to_luma_edge(self) -> None:
        height = width = 64
        image = np.zeros((height, width, 3), dtype=np.float32)
        image[:, width // 2 :] = 1.0
        correction = {
            "CorrectionActive": True,
            "CorrectionAmount": 1.0,
            "CorrectionMasks": [{
                "What": "Mask/CircularGradient",
                "Left": 0.05, "Top": 0.05, "Right": 0.95, "Bottom": 0.95,
                "Feather": 0.6, "MaskValue": 1.0,
            }],
        }
        plain = masks.rasterize_correction(correction, width, height, downsample=1, edge_aware=False)
        refined = masks.rasterize_correction(correction, width, height, image=image, downsample=1)
        edge = width // 2
        # Refined mask should drop faster across the bright half near the step.
        self.assertLess(float(refined[:, edge + 2].mean()), float(plain[:, edge + 2].mean()))

    def test_hard_feather_skips_refine(self) -> None:
        image = np.linspace(0, 1, 16 * 16, dtype=np.float32).reshape(16, 16)
        image = np.stack([image] * 3, axis=-1)
        correction = {
            "CorrectionMasks": [{
                "What": "Mask/CircularGradient",
                "Left": 0.2, "Top": 0.2, "Right": 0.8, "Bottom": 0.8,
                "Feather": 0.0, "MaskValue": 1.0,
            }],
        }
        plain = masks.rasterize_correction(correction, 16, 16, downsample=1, edge_aware=False)
        refined = masks.rasterize_correction(correction, 16, 16, image=image, downsample=1)
        np.testing.assert_allclose(refined, plain, atol=1e-7)


class GuidedFilterPerfTests(unittest.TestCase):
    def test_24mp_equivalent_apply_vs_gaussian(self) -> None:
        # Quarter-res of ~24MP (6000×4000 → 1500×1000) matches LOCAL_MASK_DOWNSAMPLE=4.
        height, width = 1000, 1500
        guide = np.linspace(0, 1, height * width, dtype=np.float32).reshape(height, width)
        mask = (guide > 0.45).astype(np.float32)
        radius = max(1, int(round(0.5 * min(height, width) * C.GUIDED_RADIUS_FRACTION)))

        # Warmup
        gf.guided_filter(guide, mask, radius=radius, epsilon=0.003)
        gf.soft_mask(mask, radius, guide=None)

        start = time.perf_counter()
        for _ in range(3):
            gf.guided_filter(guide, mask, radius=radius, epsilon=0.003)
        guided_ms = (time.perf_counter() - start) / 3 * 1000.0

        start = time.perf_counter()
        for _ in range(3):
            gf.soft_mask(mask, radius, guide=None)
        gaussian_ms = (time.perf_counter() - start) / 3 * 1000.0

        # Recorded for the report. Interactive mask atlas rebuilds once per mask
        # edit (not per tone scrub); guided must beat the old gaussian softener.
        print(f"PERF guided={guided_ms:.1f}ms gaussian={gaussian_ms:.1f}ms radius={radius} size={width}x{height}")
        self.assertLess(guided_ms, 200.0)
        self.assertLessEqual(guided_ms, gaussian_ms * 1.05)


if __name__ == "__main__":
    unittest.main()
