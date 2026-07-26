"""Canonical ``AZIMUTH_*`` environment resolution."""

from __future__ import annotations

import os
import unittest
from unittest import mock

from core import env_names


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
