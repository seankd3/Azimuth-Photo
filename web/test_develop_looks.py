import unittest
import xml.etree.ElementTree as etree

import numpy as np

from pixels import looks
from pixels.pipeline import build_monotone_cubic_lut


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

if __name__ == "__main__":
    unittest.main()
