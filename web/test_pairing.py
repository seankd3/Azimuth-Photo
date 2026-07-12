"""Pairing, device auth, and discovery contract tests."""

from __future__ import annotations

import asyncio
import base64
import io
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

import db
import settings
from features.sync import device_auth, hub_routes, mdns, pairing, pair_routes


class PairingTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.db_path = str(self.root / "hub.db")
        self.settings_path = str(self.root / "settings.json")
        self.old_db_path = db.DB_PATH
        self.old_settings_path = settings.SETTINGS_PATH
        self.old_settings = settings._settings
        db.DB_PATH = self.db_path
        settings.SETTINGS_PATH = self.settings_path
        settings._settings = None
        settings.save_settings(settings.DEFAULT_SETTINGS)
        asyncio.run(db.init_db())
        pairing.clear_pending_codes_for_tests()
        pair_routes.configure(db_path=lambda: self.db_path)
        hub_routes.configure(db_path=lambda: self.db_path)
        api = FastAPI()
        api.include_router(pair_routes.router)
        api.include_router(hub_routes.router)
        self.client_context = TestClient(api)
        self.client = self.client_context.__enter__()

    def tearDown(self):
        self.client_context.__exit__(None, None, None)
        pairing.clear_pending_codes_for_tests()
        db.DB_PATH = self.old_db_path
        settings.SETTINGS_PATH = self.old_settings_path
        settings._settings = self.old_settings
        self.tempdir.cleanup()

    def test_pair_round_trip(self):
        link = self.client.post("/api/devices/link")
        self.assertEqual(link.status_code, 200, link.text)
        body = link.json()
        self.assertEqual(len(body["code"]), 8)
        self.assertTrue(body["qr_png_base64"])
        png = base64.b64decode(body["qr_png_base64"])
        image = Image.open(io.BytesIO(png))
        self.assertGreaterEqual(image.size[0], 64)

        paired = self.client.post(
            "/api/pair",
            json={"code": body["code"], "device_name": "Pixel 10a", "platform": "android"},
        )
        self.assertEqual(paired.status_code, 200, paired.text)
        payload = paired.json()
        self.assertTrue(payload["device_token"])
        self.assertTrue(payload["hub_id"])

        devices = self.client.get("/api/devices").json()
        self.assertEqual(devices["hub_id"], payload["hub_id"])
        self.assertEqual(len(devices["devices"]), 1)
        self.assertEqual(devices["devices"][0]["name"], "Pixel 10a")
        self.assertFalse(devices["devices"][0]["revoked"])

    def test_pair_code_single_use(self):
        code = self.client.post("/api/devices/link").json()["code"]
        first = self.client.post("/api/pair", json={"code": code, "device_name": "One"})
        self.assertEqual(first.status_code, 200, first.text)
        second = self.client.post("/api/pair", json={"code": code, "device_name": "Two"})
        self.assertEqual(second.status_code, 400, second.text)

    def test_pair_code_ttl_expiry(self):
        code = self.client.post("/api/devices/link").json()["code"]
        pairing.inject_pending_code_for_tests(code, expires_at=time.time() - 1)
        expired = self.client.post("/api/pair", json={"code": code, "device_name": "Late"})
        self.assertEqual(expired.status_code, 400, expired.text)

    def test_revoke_and_require_device_token(self):
        code = self.client.post("/api/devices/link").json()["code"]
        paired = self.client.post(
            "/api/pair",
            json={"code": code, "device_name": "Laptop", "platform": "linux"},
        ).json()
        token = paired["device_token"]
        device_id = paired["device_id"]

        # Default off: sync works without token.
        ok = self.client.post("/api/sync/manifest", json={"items": []})
        self.assertEqual(ok.status_code, 200, ok.text)

        settings.save_settings({**settings.get_settings(), "require_device_token": True})
        denied = self.client.post("/api/sync/manifest", json={"items": []})
        self.assertEqual(denied.status_code, 401, denied.text)

        allowed = self.client.post(
            "/api/sync/manifest",
            json={"items": []},
            headers={"X-Device-Token": token},
        )
        self.assertEqual(allowed.status_code, 200, allowed.text)

        revoked = self.client.post(f"/api/devices/{device_id}/revoke")
        self.assertEqual(revoked.status_code, 200, revoked.text)
        blocked = self.client.post(
            "/api/sync/manifest",
            json={"items": []},
            headers={"X-Device-Token": token},
        )
        self.assertEqual(blocked.status_code, 401, blocked.text)

    def test_discover_endpoint_mocked(self):
        fake = [{"name": "SEAN-NAS", "url": "http://192.168.1.10:8000", "hub_id": "abc"}]
        with mock.patch.object(mdns, "browse_hubs", new=mock.AsyncMock(return_value=fake)):
            response = self.client.get("/api/discover")
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["hubs"], fake)

    def test_hub_id_stable(self):
        first = asyncio.run(pairing.get_hub_id(self.db_path))
        second = asyncio.run(pairing.get_hub_id(self.db_path))
        self.assertEqual(first, second)


class QrEncodeTests(unittest.TestCase):
    def test_encode_png_round_trip_size(self):
        from features.sync import qr_encode

        png = qr_encode.encode_png('{"hub_url":"http://127.0.0.1:8132","code":"ABCD2345"}')
        image = Image.open(io.BytesIO(png))
        self.assertEqual(image.mode, "L")
        self.assertGreaterEqual(min(image.size), 100)


if __name__ == "__main__":
    unittest.main()
