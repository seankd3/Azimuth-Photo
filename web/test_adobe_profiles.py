"""Contract tests for the DNG embedded/harvested Adobe profile library."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent))

from features.develop import adobe_profiles as profiles


class _Tags(dict):
    def get(self, key, default=None):
        return super().get(key, default)


class AdobeProfilesTests(unittest.TestCase):
    def _profile_from_tags(self, tags):
        page = SimpleNamespace(tags=_Tags(tags))
        return profiles._profile_from_pages((page,), "/photos/source.dng")

    @staticmethod
    def _tag(value, dtype=11):
        return SimpleNamespace(value=value, dtype=dtype)

    def test_extract_normalizes_srational_matrices_and_keeps_float32_tables(self):
        matrix = (10000, 10000, 0, 10000, 0, 10000, 0, 10000, 10000, 10000, 0, 10000, 0, 10000, 0, 10000, 10000, 10000)
        tags = {
            "UniqueCameraModel": self._tag("Canon Test"),
            "ProfileName": self._tag("Adobe Standard"),
            "ColorMatrix1": self._tag(matrix, 10),
            "ColorMatrix2": self._tag(matrix, 10),
            "ForwardMatrix1": self._tag(matrix, 10),
            "ForwardMatrix2": self._tag(matrix, 10),
            "CalibrationIlluminant1": self._tag(17, 3),
            "CalibrationIlluminant2": self._tag(21, 3),
            "BaselineExposure": self._tag((1, 2), 10),
            "ProfileHueSatMapDims": self._tag((1, 1, 1), 4),
            "ProfileHueSatMapData1": self._tag((1.0, 1.0, 1.0)),
            "ProfileHueSatMapData2": self._tag((2.0, 1.0, 1.0)),
            "ProfileLookTableDims": self._tag((1, 1, 1), 4),
            "ProfileLookTableData": self._tag((0.0, 1.0, 1.0)),
            "ProfileToneCurve": self._tag((0.0, 0.0, 1.0, 1.0)),
        }
        profile = self._profile_from_tags(tags)
        self.assertEqual(profile["matrices"]["color_matrix1"], [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0])
        self.assertEqual(profile["baseline_exposure"], 0.5)
        self.assertEqual(profile["hue_sat_map"], {"dims": [1, 1, 1], "data1": [1.0, 1.0, 1.0], "data2": [2.0, 1.0, 1.0]})
        self.assertEqual(profile["tone_curve"], [[0.0, 0.0], [1.0, 1.0]])
        self.assertEqual(profile["content_hash"], profiles.profile_content_hash(profile))

    def test_library_prefers_adobe_standard_and_resolver_preserves_embedded_precedence(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            original = profiles.ADOBE_PROFILES_DIR
            try:
                profiles.ADOBE_PROFILES_DIR = directory
                for name in ("Camera Faithful", "Adobe Standard"):
                    payload = {
                        "camera_model": "Canon Test",
                        "profile_name": name,
                        "matrices": {key: [1.0] * 9 for key in ("color_matrix1", "color_matrix2", "forward_matrix1", "forward_matrix2")},
                        "illuminants": {"calibration_illuminant1": 17, "calibration_illuminant2": 21},
                    }
                    (directory / f"{profiles.slugify(name)}.json").write_text(json.dumps(payload), encoding="utf-8")
                profiles.clear_adobe_profile_cache()
                self.assertEqual(profiles.load_adobe_profile("Canon Test")["profile_name"], "Adobe Standard")
                embedded = {"camera_model": "Canon Test", "profile_name": "Camera Portrait"}
                original_extract = profiles.extract_embedded_profile
                original_baseline = profiles.read_baseline_exposure
                try:
                    profiles.extract_embedded_profile = lambda path: dict(embedded)
                    profiles.read_baseline_exposure = lambda path: 0.75
                    resolved = profiles.resolve_adobe_profile("/photos/test.dng", "Canon Test")
                finally:
                    profiles.extract_embedded_profile = original_extract
                    profiles.read_baseline_exposure = original_baseline
                self.assertEqual(resolved["profile_name"], "Camera Portrait")
                self.assertEqual(resolved["resolution"], "embedded")
                self.assertEqual(resolved["baseline_exposure"], 0.75)
            finally:
                profiles.ADOBE_PROFILES_DIR = original
                profiles.clear_adobe_profile_cache()

    def test_stale_profile_index_rebuilds_before_loading_changed_profile(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            original = profiles.ADOBE_PROFILES_DIR
            try:
                profiles.ADOBE_PROFILES_DIR = directory
                payload = {
                    "camera_model": "Canon Test",
                    "profile_name": "Adobe Standard",
                    "matrices": {key: [1.0] * 9 for key in ("color_matrix1", "color_matrix2", "forward_matrix1", "forward_matrix2")},
                    "illuminants": {"calibration_illuminant1": 17, "calibration_illuminant2": 21},
                }
                profile_path = directory / "test.json"
                profile_path.write_text(json.dumps(payload), encoding="utf-8")
                profiles.clear_adobe_profile_cache()
                profiles._profile_index()
                self.assertTrue((directory / "_index.json").is_file())

                payload["profile_name"] = "Adobe Standard Updated"
                payload["tone_curve"] = [[0.0, 0.0], [1.0, 0.75]]
                profile_path.write_text(json.dumps(payload), encoding="utf-8")

                updated = profiles.load_adobe_profile("Canon Test")
                self.assertEqual(updated["profile_name"], "Adobe Standard Updated")
                self.assertEqual(updated["tone_curve"][-1], [1.0, 0.75])
            finally:
                profiles.ADOBE_PROFILES_DIR = original
                profiles.clear_adobe_profile_cache()

    def test_resolver_falls_back_to_library_then_none(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            original = profiles.ADOBE_PROFILES_DIR
            try:
                profiles.ADOBE_PROFILES_DIR = directory
                payload = {
                    "camera_model": "Canon Test",
                    "profile_name": "Adobe Standard",
                    "matrices": {key: [1.0] * 9 for key in ("color_matrix1", "color_matrix2", "forward_matrix1", "forward_matrix2")},
                    "illuminants": {"calibration_illuminant1": 17, "calibration_illuminant2": 21},
                }
                (directory / "test.json").write_text(json.dumps(payload), encoding="utf-8")
                profiles.clear_adobe_profile_cache()
                self.assertEqual(profiles.resolve_adobe_profile("/photos/test.cr3", "Canon Test")["resolution"], "library")
                self.assertIsNone(profiles.resolve_adobe_profile("/photos/test.cr3", "Unknown"))
            finally:
                profiles.ADOBE_PROFILES_DIR = original
                profiles.clear_adobe_profile_cache()


if __name__ == "__main__":
    unittest.main()
