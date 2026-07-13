"""Release-version and satellite compatibility coverage."""

from __future__ import annotations

import asyncio
import json
import os
import unittest
from unittest import mock

from fastapi.testclient import TestClient

import app as app_module
from core.version import app_version
from data.schema import SCHEMA_VERSION
from features.sync.sync_worker import SyncWorker
from features.sync.versioning import compare_semver, hub_compatibility, parse_semver
from features.system import version_routes


class SemVerTests(unittest.TestCase):
    def test_compare_equal_older_newer_and_prerelease(self):
        self.assertEqual(compare_semver("0.1.0", "0.1.0"), 0)
        self.assertEqual(compare_semver("0.1.0", "0.2.0"), -1)
        self.assertEqual(compare_semver("0.2.0", "0.1.0"), 1)
        self.assertEqual(compare_semver("0.1.0-rc.1", "0.1.0"), -1)

    def test_malformed_versions_are_rejected(self):
        self.assertIsNone(parse_semver("0.1"))
        self.assertIsNone(parse_semver("v0.1.0"))
        self.assertIsNone(parse_semver("0.1.0-01"))
        self.assertIsNone(compare_semver("not-a-version", "0.1.0"))


class HandshakeTests(unittest.TestCase):
    def test_older_hub_sets_update_flag_and_newer_hub_clears_it(self):
        async def probe(version: str) -> dict:
            async def request(method, url, *, body=None, headers=None):
                self.assertEqual((method, url), ("GET", "http://hub/api/version"))
                return 200, {}, json.dumps({"version": version}).encode()

            worker = SyncWorker(db_path=":memory:", hub="http://hub", request=request)
            await worker.refresh_hub_version()
            return worker.status()

        older = asyncio.run(probe("0.0.9"))
        newer = asyncio.run(probe("0.2.0"))
        self.assertTrue(older["server_update_available"])
        self.assertFalse(older["server_incompatible"])
        self.assertFalse(newer["server_update_available"])
        self.assertFalse(newer["server_incompatible"])

    def test_malformed_hub_version_is_explicitly_incompatible(self):
        self.assertEqual(
            hub_compatibility("development"),
            {
                "hub_version": "development",
                "minimum_compatible_hub": "0.1.0",
                "server_update_available": False,
                "server_incompatible": True,
            },
        )


class VersionEndpointTests(unittest.TestCase):
    def test_version_endpoint_reports_release_schema_and_mode(self):
        with TestClient(app_module.app) as client:
            response = client.get("/api/version")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"version": app_version(), "schema_version": SCHEMA_VERSION, "mode": "hub"},
        )

    def test_version_endpoint_names_an_unpaired_satellite_standalone(self):
        with mock.patch.dict(os.environ, {"PHOTOARCHIVE_MODE": "standalone"}, clear=False):
            self.assertEqual(version_routes._mode(), "standalone")
