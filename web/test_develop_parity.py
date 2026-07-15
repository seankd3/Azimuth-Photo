"""Golden data contract for the Python half of develop renderer parity."""

import os
import re
import sys
import threading
import unittest
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
from features.develop import adobe_profiles, dng_pipeline  # noqa: E402
from features.develop.camera_profile import load_camera_profile  # noqa: E402
from features.develop.pipeline import apply_pipeline, hsv_to_rgb  # noqa: E402
from features.develop.render import default_render_color_profile, develop_default_render  # noqa: E402
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


def real_base_cache_meta() -> dict[str, object]:
    """Stable subset of the v4/68872 Canon EOS R5 base-cache metadata."""
    camera_profile = load_camera_profile("Canon EOS R5")
    assert camera_profile is not None
    return {
        "base_kind": "raw",
        "camera_model": "Canon EOS R5",
        "as_shot": {"temperature": 3793, "tint": 9, "method": "dng_mccamy"},
        "color": {
            "as_shot_neutral": [0.661926, 1.0, 0.42578],
            "forward_matrix": [
                0.4564, 0.288, 0.22,
                0.2522, 0.6781, 0.0697,
                0.1079, 0.0013, 0.716,
            ],
            "color_matrix1": [
                1.16, -0.6048, 0.0405,
                -0.3331, 1.0643, 0.3113,
                -0.0007, 0.0513, 0.6109,
            ],
            "color_matrix2": [
                0.9766, -0.2953, -0.1254,
                -0.4276, 1.2116, 0.2433,
                -0.0437, 0.1336, 0.5131,
            ],
            "camera_profile": camera_profile,
        },
    }


class _ParityHandler(SimpleHTTPRequestHandler):
    def log_message(self, _format, *_args):
        pass

    def do_GET(self):
        if self.path == "/develop-default-parity":
            body = (
                '<!doctype html><script type="module">'
                'import {renderSyntheticPixels} from "/static/js/desktop/develop/gl.js";'
                'window.renderDefault = async meta => Array.from(await renderSyntheticPixels({}, meta));'
                'window.parityReady = true;'
                '</script>'
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        super().do_GET()


def render_default_webgl(meta: dict[str, object]) -> np.ndarray:
    """Render the JS twin in Chromium and return top-down RGB8 pixels."""
    from playwright.sync_api import sync_playwright

    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        partial(_ParityHandler, directory=str(Path(__file__).parent)),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.goto(f"http://127.0.0.1:{server.server_port}/develop-default-parity")
                page.wait_for_function("window.parityReady === true")
                raw = page.evaluate("meta => window.renderDefault(meta)", meta)
            finally:
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    return np.flipud(np.asarray(raw, dtype=np.uint8).reshape(64, 64, 4)[..., :3])


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

    def test_default_empty_settings_match_webgl_with_real_cache_color(self):
        cache_meta = real_base_cache_meta()
        adobe = adobe_profiles.load_adobe_profile("Canon EOS R5")
        self.assertIsNotNone(adobe)
        enriched_meta = {
            **cache_meta,
            "as_shot_temperature": cache_meta["as_shot"]["temperature"],
            "as_shot_tint": cache_meta["as_shot"]["tint"],
            "adobe_profile": dng_pipeline.normalize_adobe_profile(adobe),
            "canvas_color_profile": default_render_color_profile(cache_meta),
        }
        self.assertNotIn("adobe_profile", enriched_meta["canvas_color_profile"])

        python_rgb = develop_default_render(synthetic_linear_image(), cache_meta, {})
        expected = np.asarray(np.clip(python_rgb * 255.0 + 0.5, 0, 255), dtype=np.uint8)
        actual = render_default_webgl(enriched_meta)
        delta = np.abs(actual.astype(np.int16) - expected.astype(np.int16))

        self.assertLess(float(delta.mean()), 1.0)
        self.assertLessEqual(float(np.percentile(delta, 95)), 2.0)
        self.assertLessEqual(int(delta.max()), 16)


if __name__ == "__main__":
    unittest.main()
