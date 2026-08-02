"""Canonical ``AZIMUTH_*`` environment resolution."""

from __future__ import annotations

import os
import unittest
from unittest import mock

from core import env_names
from core.env_names import env_bytes, env_float


class EnvNamesTests(unittest.TestCase):
    def test_reads_canonical_value(self):
        self.assertEqual(
            env_names.env_get("HOME", environ={"AZIMUTH_HOME": "/library"}),
            "/library",
        )

    def test_default_when_missing_or_blank(self):
        self.assertEqual(env_names.env_get("HOME", "/default", environ={}), "/default")
        self.assertEqual(
            env_names.env_get("HOME", "/default", environ={"AZIMUTH_HOME": "  "}),
            "/default",
        )

    def test_truthy(self):
        self.assertTrue(
            env_names.env_truthy("SMOKE_MODE", environ={"AZIMUTH_SMOKE_MODE": "true"})
        )
        self.assertFalse(env_names.env_truthy("SMOKE_MODE", environ={}))

    def test_setdefault_preserves_deployment_value(self):
        with mock.patch.dict(os.environ, {"AZIMUTH_HOME": "/configured"}, clear=False):
            env_names.setdefault("HOME", "/fallback")
            self.assertEqual(os.environ["AZIMUTH_HOME"], "/configured")

    def test_empty_suffix_is_rejected(self):
        with self.assertRaises(ValueError):
            env_names.env_key("")


if __name__ == "__main__":
    unittest.main()


class OneWayToReadTheEnvironmentTests(unittest.TestCase):
    """Sizes and durations arrive as text; parsing them is this module's job."""

    def setUp(self):
        os.environ["AZIMUTH_TEST_VALUE"] = ""

    def tearDown(self):
        os.environ.pop("AZIMUTH_TEST_VALUE", None)

    def test_a_size_can_be_written_the_way_people_write_one(self):
        for text, expected in (("4g", 4 * 1024**3), ("512m", 512 * 1024**2), ("64k", 64 * 1024)):
            os.environ["AZIMUTH_TEST_VALUE"] = text
            self.assertEqual(env_bytes("TEST_VALUE", 0), expected, text)

    def test_a_plain_count_is_bytes(self):
        os.environ["AZIMUTH_TEST_VALUE"] = "2048"
        self.assertEqual(env_bytes("TEST_VALUE", 0), 2048)

    def test_unset_or_nonsense_falls_back(self):
        del os.environ["AZIMUTH_TEST_VALUE"]
        self.assertEqual(env_bytes("TEST_VALUE", 7), 7)
        self.assertEqual(env_float("TEST_VALUE", 1.5), 1.5)
        os.environ["AZIMUTH_TEST_VALUE"] = "soon"
        self.assertEqual(env_bytes("TEST_VALUE", 7), 7)
        self.assertEqual(env_float("TEST_VALUE", 1.5), 1.5)

    def test_a_duration_is_never_negative(self):
        os.environ["AZIMUTH_TEST_VALUE"] = "-30"
        self.assertEqual(env_float("TEST_VALUE", 1.5), 0.0)

    def test_nobody_parses_the_environment_privately(self):
        """`os.environ.get` for config belongs here, not in each consumer."""

        import pathlib

        web = pathlib.Path(__file__).parent
        strays = [
            path.name
            for path in (web / "core").glob("*.py")
            if path.name != "env_names.py"
            and any(
                f"\ndef {name}(" in path.read_text(encoding="utf-8", errors="replace")
                for name in ("_env_bytes", "_env_float")
            )
        ]
        self.assertEqual(strays, [], f"private copies remain: {strays}")
