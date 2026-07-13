"""Regression proof for the failure states users see when the archive is degraded."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import unittest
from unittest import mock

from fastapi.testclient import TestClient
from starlette.requests import Request


sys.path.insert(0, os.path.dirname(__file__))
import app as app_module  # noqa: E402
from core.error_responses import SERVER_ERROR_MESSAGE  # noqa: E402
from features.cache import routes as cache_routes  # noqa: E402
from features.media import routes as media_routes  # noqa: E402
from features.sync.sync_worker import SyncWorker  # noqa: E402


class ResilienceTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app_module.app, raise_server_exceptions=False)

    def tearDown(self):
        self.client.close()

    def test_unknown_page_is_a_calm_html_recovery_surface(self):
        response = self.client.get("/this-page-is-not-here")

        self.assertEqual(response.status_code, 404)
        self.assertIn("That page isn’t here.", response.text)
        self.assertIn('href="/"', response.text)

    def test_unknown_api_route_uses_the_error_envelope(self):
        response = self.client.get("/api/this-route-is-not-here")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"error": "Not Found"})

    def test_unwritable_preview_cache_is_a_clear_retriable_state(self):
        with mock.patch.object(cache_routes.thumbnails, "start_pregeneration", side_effect=PermissionError("read-only")):
            response = self.client.post("/api/cache/pregen/start")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["error"], cache_routes.CACHE_UNAVAILABLE_MESSAGE)
        self.assertFalse(response.json()["available"])

    def test_offline_source_returns_a_recoverable_error_instead_of_crashing(self):
        image = {
            "id": 7,
            "missing_at": None,
            "hub_remote": 0,
            "filepath": "/unmounted-archive/photo.jpg",
            "source_path": "/unmounted-archive",
            "source_online": 0,
        }
        request = Request({"type": "http", "method": "GET", "path": "/api/thumb/sm/7", "headers": []})

        with mock.patch.object(media_routes, "_configured_db_path", return_value="unused"), mock.patch.object(
            media_routes.image_repository, "get_media_image_by_id", new=mock.AsyncMock(return_value=image)
        ):
            response = asyncio.run(media_routes.thumbnail_response(request, "sm", 7))

        self.assertEqual(response.status_code, 404)
        self.assertEqual(json.loads(response.body)["error"], "Source drive is offline")

    def test_unreachable_satellite_hub_keeps_a_friendly_retrying_status(self):
        worker = SyncWorker(db_path="unused", hub="https://hub.invalid")
        worker._error(ConnectionError("[Errno 111] connection refused"))

        self.assertEqual(worker.status()["recent_errors"], ["Can't reach Azimuth Photo. Retrying…"])


class ErrorCopyTests(unittest.TestCase):
    def test_generic_server_message_is_stable_for_client_recovery(self):
        self.assertEqual(
            SERVER_ERROR_MESSAGE,
            "Something went wrong on the server. Your photos are safe — try again.",
        )
