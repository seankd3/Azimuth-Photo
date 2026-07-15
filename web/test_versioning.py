"""Satellite-to-hub contract handshake coverage."""

from __future__ import annotations

import asyncio
import json
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import app as app_module
from core.version import API_REV, CAPABILITIES, app_version
from features.sync import contract
from features.sync import satellite_routes
from features.sync.sync_worker import SyncWorker


class HubContractTests(unittest.TestCase):
    def setUp(self):
        contract.reset_hub_contract_cache()

    def tearDown(self):
        contract.reset_hub_contract_cache()

    def test_worker_probes_contract_with_api_revision_header(self):
        async def probe():
            async def request(method, url, *, body=None, headers=None):
                self.assertEqual((method, url), ("GET", "http://hub/api/version"))
                self.assertEqual(headers[contract.API_REV_HEADER], str(API_REV))
                return 200, {}, json.dumps(
                    {
                        "app_version": "0.1.0",
                        "api_rev": API_REV,
                        "capabilities": sorted(CAPABILITIES),
                    }
                ).encode()

            worker = SyncWorker(db_path=":memory:", hub="http://hub", request=request)
            await worker.refresh_hub_contract(force=True)
            return worker.status()

        status = asyncio.run(probe())
        self.assertEqual(status["hub_health"], "ok")
        self.assertEqual(status["api_rev"], API_REV)
        self.assertEqual(status["app_version"], "0.1.0")

    def test_gate_refuses_missing_capability_from_reachable_old_hub(self):
        async def probe():
            async def request(method, url, *, body=None, headers=None):
                self.assertEqual(headers[contract.API_REV_HEADER], str(API_REV))
                return 404, {}, b"not found"

            allowed = await contract.hub_supports(
                "trash.scoped_empty", hub="http://old-hub", request=request
            )
            return allowed, contract.hub_status("http://old-hub")

        allowed, status = asyncio.run(probe())
        self.assertFalse(allowed)
        self.assertEqual(status, {"hub_health": "needs_update", "api_rev": None, "app_version": None})

    def test_sync_status_reports_needs_update_for_an_older_hub_contract(self):
        async def probe():
            async def request(method, url, *, body=None, headers=None):
                return 200, {}, json.dumps(
                    {
                        "app_version": "0.0.9",
                        "api_rev": API_REV - 1,
                        "capabilities": [],
                    }
                ).encode()

            worker = SyncWorker(db_path=":memory:", hub="http://old-hub", request=request)
            await worker.refresh_hub_contract(force=True)
            with patch("features.sync.satellite_routes.get_worker", return_value=worker), patch(
                "features.sync.satellite_routes.satellite.is_satellite_mode", return_value=True
            ):
                return await satellite_routes.sync_status()

        status = asyncio.run(probe())
        self.assertEqual(
            {key: status[key] for key in ("hub_health", "api_rev", "app_version")},
            {"hub_health": "needs_update", "api_rev": API_REV - 1, "app_version": "0.0.9"},
        )


class VersionEndpointTests(unittest.TestCase):
    def test_version_endpoint_reports_the_versioned_contract_shape(self):
        with TestClient(app_module.app) as client:
            response = client.get("/api/version")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "app_version": app_version(),
                "api_rev": API_REV,
                "capabilities": sorted(CAPABILITIES),
            },
        )

    def test_newer_peer_is_refused_on_scoped_empty_trash(self):
        with TestClient(app_module.app) as client:
            response = client.post(
                "/api/trash/empty",
                headers={contract.API_REV_HEADER: str(API_REV + 1)},
                json={"hub_image_ids": []},
            )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            response.json(),
            {
                "detail": "This hub needs an update before it can safely perform that action.",
                "code": "api_rev_mismatch",
            },
        )
