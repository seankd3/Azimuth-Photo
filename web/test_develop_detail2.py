"""§28 Detail completion and proofing contracts."""

import os
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
from features.develop import pipeline  # noqa: E402


class DevelopDetail2Tests(unittest.TestCase):
    def test_sharpen_masking_gates_residual_to_edges(self):
        source = np.full((31, 31, 3), .28, dtype=np.float32)
        source[:, 15:] = .72
        base = pipeline._detail(source, {"Sharpness": 150, "SharpenRadius": 1, "SharpenEdgeMasking": 0})
        masked = pipeline._detail(source, {"Sharpness": 150, "SharpenRadius": 1, "SharpenEdgeMasking": 100})
        # In an otherwise flat patch, masking removes the unsharp residual;
        # at the actual edge it still permits a meaningful sharpen response.
        self.assertLess(abs(float(masked[15, 3, 0] - source[15, 3, 0])), abs(float(base[15, 3, 0] - source[15, 3, 0])) + 1e-7)
        self.assertGreater(abs(float(masked[15, 14, 0] - source[15, 14, 0])), 1e-4)

    def test_nr_contrast_restores_part_of_luma_residual(self):
        rng = np.random.default_rng(7)
        source = np.clip(.5 + rng.normal(0, .08, (33, 35, 1)), 0, 1).repeat(3, axis=-1).astype(np.float32)
        smooth = pipeline._noise_reduction(source, {"LuminanceSmoothing": 100, "LuminanceContrast": 0})
        contrast = pipeline._noise_reduction(source, {"LuminanceSmoothing": 100, "LuminanceContrast": 100})
        self.assertLess(float(np.abs(contrast - source).mean()), float(np.abs(smooth - source).mean()))

    def test_paper_proof_compresses_white_and_exposes_warning_mask(self):
        source = np.array([[[1.0, .8, .2], [0.4, .4, .4]]], dtype=np.float32)
        paper, warning = pipeline.soft_proof_transform(source, "paper")
        self.assertLess(float(paper[0, 0, 0]), float(source[0, 0, 0]))
        self.assertEqual(warning.shape, source.shape[:2])

    def test_compare_and_proof_ui_owns_the_declared_interactions(self):
        javascript = (Path(__file__).parent / "static/js/desktop/develop/compare_view.js").read_text()
        self.assertIn("DevelopCompareView", javascript)
        self.assertIn("holdReference", javascript)
        self.assertIn("SoftProofPopover", javascript)
        self.assertIn("gamut", javascript.lower())


if __name__ == "__main__":
    unittest.main()
