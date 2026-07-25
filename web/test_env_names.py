"""AZIMUTH_* / PHOTOARCHIVE_* dual env resolution."""
from __future__ import annotations

import unittest

from core import env_names


class EnvNamesTests(unittest.TestCase):
    def setUp(self):
        env_names._warned.clear()

    def test_prefers_azimuth_over_legacy(self):
        env = {
            "AZIMUTH_HOME": "/new",
            "PHOTOARCHIVE_HOME": "/old",
        }
        self.assertEqual(env_names.env_get("HOME", environ=env), "/new")

    def test_falls_back_to_legacy(self):
        env = {"PHOTOARCHIVE_HOME": "/old"}
        self.assertEqual(env_names.env_get("HOME", environ=env), "/old")

    def test_default_when_missing(self):
        self.assertEqual(env_names.env_get("HOME", "/d", environ={}), "/d")

    def test_truthy(self):
        self.assertTrue(env_names.env_truthy("SMOKE_MODE", environ={"AZIMUTH_SMOKE_MODE": "1"}))
        self.assertTrue(
            env_names.env_truthy("SMOKE_MODE", environ={"PHOTOARCHIVE_SMOKE_MODE": "true"})
        )
        self.assertFalse(env_names.env_truthy("SMOKE_MODE", environ={}))


if __name__ == "__main__":
    unittest.main()
