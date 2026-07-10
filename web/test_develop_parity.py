"""Golden data contract for the Python half of develop renderer parity."""

import os
import re
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
from features.develop.pipeline import apply_pipeline, hsv_to_rgb  # noqa: E402
from features.develop import ops_constants as C  # noqa: E402


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
        "SharpenDetail": 31, "SharpenEdgeMasking": 48,
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


class DevelopParityTests(unittest.TestCase):
    def test_all_numeric_constant_names_match_javascript_twin(self):
        javascript = (Path(__file__).parent / "static/js/desktop/develop/ops_constants.js").read_text()
        for name, value in C.PARITY_TABLE.items():
            if not isinstance(value, (int, float)):
                continue
            match = re.search(rf"export const {name} = ([^;]+);", javascript)
            self.assertIsNotNone(match, name)
            actual = eval(match.group(1).strip(), {"__builtins__": {}}, {})
            self.assertAlmostEqual(float(actual), float(value), places=12, msg=name)

    def test_torture_settings_have_stable_golden_statistics(self):
        output = apply_pipeline(synthetic_linear_image(), torture_settings(), asshot_temperature=5150)
        stats = np.array(
            [
                output.mean(), output.std(), output.min(), output.max(), output.sum(),
                *np.percentile(output, [1, 50, 99]),
                *output[17, 23], *output[53, 41],
            ],
            dtype=np.float64,
        )
        expected = np.array(
            [
                # §22 intentional golden refresh: the torture fixture now exercises the four-wheel grade stage.
                0.6308119297027588, 0.25340598821640015, 0.0, 1.0, 7751.4169921875, 0.0,
                0.6497146487236023, 1.0, 0.6921233534812927,
                0.7832361459732056, 0.6242139935493469, 0.9238397479057312,
                0.9238397479057312, 0.9238397479057312,
            ],
            dtype=np.float64,
        )
        np.testing.assert_allclose(stats, expected, rtol=0.0, atol=2e-6)


if __name__ == "__main__":
    unittest.main()
