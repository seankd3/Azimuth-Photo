"""Analytic contracts for Phase 2 mask rasters and local pipeline placement."""

from __future__ import annotations

import os
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(__file__))
from features.develop import masks, ops_constants as C, pipeline  # noqa: E402
from test_develop_parity import synthetic_linear_image, torture_settings  # noqa: E402


def synthetic_local_settings() -> dict[str, object]:
    settings = torture_settings()
    settings["MaskGroupBasedCorrections"] = [
        {
            "CorrectionActive": True,
            "CorrectionAmount": 1.0,
            "LocalExposure2012": 0.22,
            "LocalHighlights2012": -0.18,
            "LocalTemperature": 0.12,
            "CorrectionMasks": [{
                "What": "Mask/Gradient", "ZeroX": 0.18, "ZeroY": 0.12,
                "FullX": 0.82, "FullY": 0.88, "MaskBlendMode": 0,
                "MaskInverted": False, "MaskValue": 0.85,
            }],
        },
        {
            "CorrectionActive": True,
            "CorrectionAmount": 0.9,
            "LocalContrast2012": 0.24,
            "LocalSaturation": 0.32,
            "LocalTexture": 0.15,
            "CorrectionMasks": [{
                "What": "Mask/Paint", "Dabs": [
                    "d 0.31 0.64", "r 0.13", "d 0.43 0.56", "r 0.11",
                ],
                "Flow": 0.7, "CenterWeight": 0.35, "MaskBlendMode": 0,
                "MaskInverted": False, "MaskValue": 1.0,
            }],
        },
    ]
    return settings


class MaskRasterTests(unittest.TestCase):
    def test_linear_gradient_has_analytic_smoothstep_values(self):
        raster = masks.rasterize_gradient(
            {"ZeroX": 0, "ZeroY": 0, "FullX": 1, "FullY": 0}, 5, 1
        )[0]
        t = np.array([0.1, 0.3, 0.5, 0.7, 0.9], dtype=np.float32)
        np.testing.assert_allclose(raster, t * t * (3 - 2 * t), atol=1e-7)

    def test_radial_gradient_is_solid_inside_then_feathers(self):
        raster = masks.rasterize_radial(
            {"Left": 0.2, "Top": 0.2, "Right": 0.8, "Bottom": 0.8, "Feather": 0.5}, 5, 5
        )
        self.assertAlmostEqual(float(raster[2, 2]), 1.0, places=6)
        self.assertEqual(float(raster[0, 0]), 0.0)
        self.assertGreater(float(raster[2, 4]), 0.0)
        self.assertLess(float(raster[2, 4]), 1.0)

    def test_brush_coordinates_are_width_and_height_normalized(self):
        raster = masks.rasterize_brush(
            {"Dabs": ["d 0.25 0.75", "r 0.05"], "Flow": 1, "CenterWeight": 1}, 150, 50
        )
        self.assertEqual(float(raster[37, 37]), 1.0)
        self.assertGreater(float(raster[37].sum()), 0.0)
        self.assertEqual(float(raster[10, 37]), 0.0)

    def test_luminance_range_is_a_smoothstep_window(self):
        values = np.array([0.0, 0.25, 0.5, 0.75, 1.0], dtype=np.float32)
        image = np.repeat(values[None, :, None], 3, axis=-1)
        raster = masks.rasterize_luminance_range({"LumRange": "0.2 0.4 0.6 0.8"}, image, 5, 1)[0]
        np.testing.assert_allclose(raster[[0, 2, 4]], [0.0, 1.0, 0.0], atol=1e-7)
        self.assertGreater(float(raster[1]), 0.0)
        self.assertGreater(float(raster[3]), 0.0)

    def test_color_range_uses_oklab_ab_distance(self):
        image = np.array([[[1, 0, 0], [0, 1, 0], [0.9, 0.08, 0.04]]], dtype=np.float32)
        raster = masks.rasterize_color_range(
            {"ColorAmount": 0.15, "SampledColors": ["1 0 0"]}, image, 3, 1
        )[0]
        self.assertGreater(float(raster[0]), 0.99)
        self.assertGreater(float(raster[2]), float(raster[1]) * 10)

    def test_ai_png_raster_loads_and_resizes_from_shared_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "ai-masks").mkdir()
            Image.fromarray(np.array([[0, 255], [255, 0]], dtype=np.uint8), mode="L").save(root / "ai-masks" / "abc.png")
            raster = masks.load_ai_raster({"pa_cache_key": "abc"}, 4, 4, cache_root=root)
        self.assertEqual(raster.shape, (4, 4))
        self.assertLess(float(raster[0, 0]), 0.1)
        self.assertGreater(float(raster[0, -1]), 0.9)

    def test_ai_raster_uses_global_coordinates_for_export_tiles(self):
        source = np.vstack((np.zeros((2, 4), dtype=np.float32), np.ones((2, 4), dtype=np.float32)))
        correction = {
            "CorrectionMasks": [{"What": "Mask/Image", "pa_cache_key": "synthetic"}],
        }
        bottom_tile = masks.rasterize_correction(
            correction,
            4,
            2,
            downsample=1,
            pixel_offset=(0, 2),
            canvas_size=(4, 4),
            ai_loader=lambda _mask: source,
        )
        np.testing.assert_allclose(bottom_tile, 1.0, atol=1e-7)

    def test_add_intersect_invert_opacity_and_amount_combine_in_order(self):
        correction = {
            "CorrectionActive": True,
            "CorrectionAmount": 1.5,
            "CorrectionMasks": [
                {"What": "Mask/Gradient", "ZeroX": 0, "ZeroY": 0, "FullX": 1, "FullY": 0, "MaskBlendMode": 0, "MaskValue": 1},
                {"What": "Mask/CircularGradient", "Left": 0.2, "Top": 0.2, "Right": 0.8, "Bottom": 0.8, "Feather": 0, "MaskBlendMode": 1, "MaskValue": 1},
            ],
        }
        raster = masks.rasterize_correction(correction, 20, 20, downsample=1)
        self.assertEqual(float(raster[0, 0]), 0.0)
        self.assertAlmostEqual(float(raster[10, 10]), 1.5 * masks._smoothstep(0, 1, np.array(0.525)), places=5)
        inverted = dict(correction)
        inverted["CorrectionMasks"] = [{**correction["CorrectionMasks"][0], "MaskInverted": True, "MaskValue": 0.5}]
        self.assertGreater(float(masks.rasterize_correction(inverted, 20, 20, downsample=1)[10, 1]), 0.5)

    def test_render_cap_preserves_but_only_rasterizes_first_sixteen(self):
        correction = {"CorrectionMasks": [{"What": "Mask/Gradient", "ZeroX": 0, "ZeroY": 0, "FullX": 1, "FullY": 0}]}
        settings = {"MaskGroupBasedCorrections": [correction] * 18}
        with self.assertLogs("features.develop.masks", level="WARNING"):
            rasters = masks.rasterize_corrections(settings, 8, 8)
        self.assertEqual(len(rasters), C.LOCAL_RENDER_CAP)

    def test_javascript_raster_math_matches_numpy_analytics(self):
        module_url = (Path(__file__).parent / "static/js/desktop/develop/mask_raster.js").as_uri()
        script = f"""
            import * as M from {json.dumps(module_url)};
            const image = {{width:3,height:1,data:new Float32Array([1,0,0, 0,1,0, .9,.08,.04])}};
            const gray = {{width:5,height:1,data:new Float32Array([0,0,0, .25,.25,.25, .5,.5,.5, .75,.75,.75, 1,1,1])}};
            const result = {{
              gradient:Array.from(M.rasterizeGradient({{ZeroX:0,ZeroY:0,FullX:1,FullY:0}},5,1)),
              radial:Array.from(M.rasterizeRadial({{Left:.2,Top:.2,Right:.8,Bottom:.8,Feather:.5}},5,5)),
              brush:Array.from(M.rasterizeBrush({{Dabs:['d .25 .75','r .05'],Flow:1,CenterWeight:1}},150,50)),
              luminance:Array.from(M.rasterizeLuminanceRange({{LumRange:'0.2 0.4 0.6 0.8'}},gray,5,1)),
              color:Array.from(M.rasterizeColorRange({{ColorAmount:.15,SampledColors:['1 0 0']}},image,3,1)),
            }};
            console.log(JSON.stringify(result));
        """
        completed = subprocess.run(
            ["node", "--experimental-default-type=module", "--input-type=module", "-e", script],
            check=True,
            capture_output=True,
            text=True,
        )
        javascript = json.loads(completed.stdout)
        image = np.array([[[1, 0, 0], [0, 1, 0], [0.9, 0.08, 0.04]]], dtype=np.float32)
        gray = np.repeat(np.array([[[0], [0.25], [0.5], [0.75], [1]]], dtype=np.float32), 3, axis=-1)
        expected = {
            "gradient": masks.rasterize_gradient({"ZeroX": 0, "ZeroY": 0, "FullX": 1, "FullY": 0}, 5, 1),
            "radial": masks.rasterize_radial({"Left": 0.2, "Top": 0.2, "Right": 0.8, "Bottom": 0.8, "Feather": 0.5}, 5, 5),
            "brush": masks.rasterize_brush({"Dabs": ["d .25 .75", "r .05"], "Flow": 1, "CenterWeight": 1}, 150, 50),
            "luminance": masks.rasterize_luminance_range({"LumRange": "0.2 0.4 0.6 0.8"}, gray, 5, 1),
            "color": masks.rasterize_color_range({"ColorAmount": 0.15, "SampledColors": ["1 0 0"]}, image, 3, 1),
        }
        for name, values in expected.items():
            np.testing.assert_allclose(np.asarray(javascript[name]).reshape(values.shape), values, atol=2e-6, err_msg=name)


class LocalCorrectionTests(unittest.TestCase):
    def test_catalog_exposure_fraction_maps_to_documented_ev(self):
        self.assertAlmostEqual(masks.localToSlider({"LocalExposure2012": -0.4835}, "LocalExposure2012"), -1.934)

    def test_pipeline_applies_gradient_exposure_after_global_hsl(self):
        source = np.full((32, 32, 3), 0.08, dtype=np.float32)
        settings = {
            "MaskGroupBasedCorrections": [{
                "CorrectionActive": True, "CorrectionAmount": 1,
                "LocalExposure2012": 0.25,
                "CorrectionMasks": [{"What": "Mask/Gradient", "ZeroX": 0.2, "ZeroY": 0, "FullX": 0.8, "FullY": 0}],
            }]
        }
        output = pipeline.apply_pipeline(source, settings)
        self.assertGreater(float(output[:, 24].mean()), float(output[:, 7].mean()) + 0.12)

    def test_pipeline_brush_local_correction_is_directional(self):
        source = np.full((64, 64, 3), (0.12, 0.08, 0.04), dtype=np.float32)
        settings = {
            "MaskGroupBasedCorrections": [{
                "CorrectionActive": True, "LocalExposure2012": 0.3, "LocalSaturation": 0.4,
                "CorrectionMasks": [{"What": "Mask/Paint", "Dabs": ["d 0.5 0.5", "r 0.2"], "Flow": 1, "CenterWeight": 1}],
            }]
        }
        baseline = pipeline.apply_pipeline(source, {})
        output = pipeline.apply_pipeline(source, settings)
        self.assertGreater(float(output[32, 32].mean()), float(baseline[32, 32].mean()) + 0.1)
        np.testing.assert_allclose(output[2, 2], baseline[2, 2], atol=2e-5)

    def test_python_parity_fixture_includes_gradient_and_brush_corrections(self):
        settings = synthetic_local_settings()
        self.assertEqual([mask["What"] for correction in settings["MaskGroupBasedCorrections"] for mask in correction["CorrectionMasks"]], ["Mask/Gradient", "Mask/Paint"])
        output = pipeline.apply_pipeline(synthetic_linear_image(), settings, asshot_temperature=5150)
        stats = [float(output.mean()), float(output.std()), *np.percentile(output, [10, 50, 90])]
        # GrainFrequency refresh: the torture fixture's Roughness value now scales
        # the shared Python/WebGL hash grid instead of being a dead setting.
        np.testing.assert_allclose(
            stats,
            [0.6623320579528809, 0.25693196058273315, 0.3028943568468094, 0.6797678470611572, 0.9878529727458955],
            rtol=0.0,
            atol=2e-6,
        )

    def test_local_constant_names_and_values_match_javascript_twin(self):
        javascript = (Path(__file__).parent / "static/js/desktop/develop/ops_constants.js").read_text()
        for name, value in C.PARITY_TABLE.items():
            if not name.startswith("LOCAL_") or not isinstance(value, (int, float)):
                continue
            match = re.search(rf"export const {name} = ([^;]+);", javascript)
            self.assertIsNotNone(match, name)
            expression = match.group(1).strip()
            actual = eval(expression, {"__builtins__": {}}, {})  # Constants are numeric literals or 1/3.
            self.assertAlmostEqual(float(actual), float(value), places=12, msg=name)


if __name__ == "__main__":
    unittest.main()
