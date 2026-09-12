"""Invariant sweep for every numeric control in the Python Develop contract."""

from __future__ import annotations

import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
from pixels import ops_constants as C  # noqa: E402
from pixels.pipeline import apply_pipeline, hsv_to_rgb  # noqa: E402


def synthetic_linear_image() -> np.ndarray:
    """64x64 hue field with an independent linear value gradient."""
    y, x = np.mgrid[0:64, 0:64].astype(np.float32)
    hue = np.mod(x * (360.0 / 63.0) + y * 0.75, 360.0)
    saturation = 0.15 + 0.85 * (x / 63.0)
    value = 0.03 + 0.97 * (y / 63.0)
    return hsv_to_rgb(hue, saturation, value).astype(np.float32)


def torture_settings() -> dict[str, object]:
    settings: dict[str, object] = {
        "WhiteBalance": "Custom", "Temperature": 6850, "Tint": -31,
        "Exposure2012": 0.73, "Contrast2012": -37, "Highlights2012": -48,
        "Shadows2012": 62, "Whites2012": 29, "Blacks2012": -34,
        "Texture": 41, "Clarity2012": -28, "Dehaze": 36, "Vibrance": 47, "Saturation": -22,
        "ColorGradeShadowHue": 28, "ColorGradeShadowSat": 43, "ColorGradeShadowLum": -17,
        "ColorGradeMidtoneHue": 192, "ColorGradeMidtoneSat": 28, "ColorGradeMidtoneLum": 12,
        "ColorGradeHighlightHue": 236, "ColorGradeHighlightSat": 36, "ColorGradeHighlightLum": 9,
        "ColorGradeGlobalHue": 328, "ColorGradeGlobalSat": 14, "ColorGradeGlobalLum": -4,
        "ColorGradeBlending": 57, "ColorGradeBalance": -18,
        "ToneCurvePV2012": ["0, 0", "96, 80", "255, 255"],
        "ToneCurvePV2012Red": ["0, 0", "128, 154", "255, 255"],
        "ToneCurvePV2012Green": ["0, 0", "128, 105", "255, 255"],
        "ToneCurvePV2012Blue": ["0, 0", "128, 139", "255, 255"],
        "ConvertToGrayscale": "False", "Sharpness": 92, "SharpenRadius": 1.7,
        "SharpenEdgeMasking": 48,
        "PostCropVignetteAmount": -42, "PostCropVignetteMidpoint": 44,
        "PostCropVignetteFeather": 59, "PostCropVignetteRoundness": -27,
        "GrainAmount": 37, "GrainSize": 53, "GrainFrequency": 61,
        "CropLeft": 0.08, "CropTop": 0.11, "CropRight": 0.91, "CropBottom": 0.87,
    }
    for index, name in enumerate(("Red", "Orange", "Yellow", "Green", "Aqua", "Blue", "Purple", "Magenta")):
        settings[f"HueAdjustment{name}"] = -70 + index * 19
        settings[f"SaturationAdjustment{name}"] = 64 - index * 15
        settings[f"LuminanceAdjustment{name}"] = -45 + index * 12
        settings[f"GrayMixer{name}"] = -50 + index * 13
    return settings


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
