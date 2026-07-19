import json
import subprocess
import unittest
import xml.etree.ElementTree as etree
from pathlib import Path

import numpy as np

from features.develop import camera_profile, looks
from features.develop import ops_constants as C
from features.develop.pipeline import build_monotone_cubic_lut


LOOK_XMP = """<x:xmpmeta xmlns:x="adobe:ns:meta/"
 xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
 xmlns:crs="http://ns.adobe.com/camera-raw-settings/1.0/">
 <rdf:RDF><rdf:Description>
  <crs:ToneCurvePV2012><rdf:Seq><rdf:li>0, 0</rdf:li><rdf:li>255, 255</rdf:li></rdf:Seq></crs:ToneCurvePV2012>
  <crs:Look><rdf:Description crs:Name="Agfa Precisa 100 II" crs:Amount="0.5" crs:Cluster="RNI">
   <crs:Parameters><rdf:Description crs:Clarity2012="20" crs:ConvertToGrayscale="False" crs:LookTable="missing-table">
    <crs:ToneCurvePV2012><rdf:Seq><rdf:li>0, 0</rdf:li><rdf:li>128, 160</rdf:li><rdf:li>255, 255</rdf:li></rdf:Seq></crs:ToneCurvePV2012>
   </rdf:Description></crs:Parameters>
  </rdf:Description></crs:Look>
 </rdf:Description></rdf:RDF>
</x:xmpmeta>"""


class DevelopLookTests(unittest.TestCase):
    def test_camera_tone_requires_literal_trusted_flag(self):
        payload = {
            "model": "Test Camera",
            "tone_nodes": [index / (C.CAMERA_PROFILE_TONE_NODES - 1) for index in range(C.CAMERA_PROFILE_TONE_NODES)],
            "tone_values": [0.25] * C.CAMERA_PROFILE_TONE_NODES,
            "chroma_edges": [0.02, 0.06, 0.12, 1.0],
            "oklab_ab_delta": np.zeros((C.CAMERA_PROFILE_HUE_BINS, C.CAMERA_PROFILE_CHROMA_BINS, 2)).tolist(),
        }

        untrusted = camera_profile._validated_profile({**payload, "tone_trusted": "true"})
        trusted = camera_profile._validated_profile({**payload, "tone_trusted": True})

        self.assertFalse(untrusted["tone_trusted"])
        self.assertNotEqual(untrusted["tone_values"], payload["tone_values"])
        self.assertTrue(trusted["tone_trusted"])
        self.assertEqual(trusted["tone_values"], payload["tone_values"])

    def test_extracts_nested_xmp_look_without_flattening(self):
        look = looks.extract_xmp_look(etree.fromstring(LOOK_XMP))

        self.assertEqual(look["Name"], "Agfa Precisa 100 II")
        self.assertEqual(look["Amount"], 0.5)
        self.assertEqual(look["Cluster"], "RNI")
        self.assertEqual(look["Parameters"]["Clarity2012"], 20)
        self.assertFalse(look["Parameters"]["ConvertToGrayscale"])
        self.assertEqual(look["Parameters"]["ToneCurvePV2012"], ["0, 0", "128, 160", "255, 255"])

    def test_effective_settings_scale_supported_parameters_without_mutation(self):
        stored = {
            "Clarity2012": -5,
            "ConvertToGrayscale": False,
            "Look": {
                "Name": "Mono",
                "Amount": 50,
                "Parameters": {"Clarity2012": 20, "ConvertToGrayscale": "True", "RGBTable": "unavailable"},
            },
        }

        effective = looks.effective_settings(stored)

        self.assertEqual(effective["Clarity2012"], 5)
        self.assertTrue(effective["ConvertToGrayscale"])
        self.assertEqual(stored["Clarity2012"], -5)
        self.assertFalse(stored["ConvertToGrayscale"])

    def test_curve_composition_is_look_after_base_and_amount_blended(self):
        base = build_monotone_cubic_lut(["0, 0", "128, 180", "255, 255"])
        look = build_monotone_cubic_lut(["0, 0", "128, 96", "255, 255"])
        composed = looks.compose_curve_luts(base, look, 0.5)
        full = looks.compose_curve_luts(base, look, 1.0)
        position = np.linspace(0.0, 1.0, 256, dtype=np.float32)
        expected_full = np.interp(base, position, look)

        np.testing.assert_allclose(full, expected_full, atol=1e-7)
        np.testing.assert_allclose(composed, base + (expected_full - base) * 0.5, atol=1e-7)

    def test_javascript_lut_and_effective_settings_match_python(self):
        settings = {
            "Clarity2012": -5,
            "Look": {
                "Amount": 0.5,
                "Parameters": {
                    "Clarity2012": 20,
                    "ConvertToGrayscale": True,
                    "ToneCurvePV2012": ["0, 0", "128, 96", "255, 255"],
                },
            },
        }
        script = f"""
import {{ buildBaseProfileLut, effectiveLookSettings }} from './static/js/desktop/develop/curve_lut.js';
const settings = {json.dumps(settings)};
console.log(JSON.stringify({{ lut: Array.from(buildBaseProfileLut(null, settings)), effective: effectiveLookSettings(settings) }}));
"""
        result = subprocess.run(
            ["node", "--input-type=module", "-e", script],
            cwd=Path(__file__).parent,
            check=True,
            capture_output=True,
            text=True,
        )
        actual = json.loads(result.stdout)
        base = build_monotone_cubic_lut(
            [(0, 0), (20, 20), (40, 39), (64, 81), (96, 156), (128, 205), (176, 232), (216, 245), (255, 255)]
        )
        look_lut = build_monotone_cubic_lut(settings["Look"]["Parameters"]["ToneCurvePV2012"])
        expected = looks.compose_curve_luts(base, look_lut, 0.5)

        np.testing.assert_allclose(actual["lut"], expected, atol=2e-6)
        self.assertEqual(actual["effective"]["Clarity2012"], 5)
        self.assertTrue(actual["effective"]["ConvertToGrayscale"])


if __name__ == "__main__":
    unittest.main()
