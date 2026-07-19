"""Pairing, device auth, and discovery contract tests."""

from __future__ import annotations

import asyncio
import base64
import io
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

import db
import settings
from features.sync import hub_routes, mdns, pairing, pair_routes, satellite


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

    def test_sync_requires_live_device_token_when_enabled(self):
        import settings
        settings.save_settings({**settings.get_settings(), "require_device_token": True})
        code = self.client.post("/api/devices/link").json()["code"]
        paired = self.client.post(
            "/api/pair",
            json={"code": code, "device_name": "Laptop", "platform": "linux"},
        ).json()
        token = paired["device_token"]
        device_id = paired["device_id"]

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


class PairConnectTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.db_path = str(self.root / "satellite.db")
        self.settings_path = str(self.root / "settings.json")
        self.old_db_path = db.DB_PATH
        self.old_settings_path = settings.SETTINGS_PATH
        self.old_settings = settings._settings
        self.old_env = {key: os.environ.get(key) for key in ("PHOTOARCHIVE_MODE", "PHOTOARCHIVE_HUB_URL", "PHOTOARCHIVE_DEVICE_TOKEN")}
        self.old_stored = (satellite._stored_hub_url, satellite._stored_device_token)
        self.old_starter = satellite._sync_starter
        db.DB_PATH = self.db_path
        settings.SETTINGS_PATH = self.settings_path
        settings._settings = None
        settings.save_settings(settings.DEFAULT_SETTINGS)
        os.environ["PHOTOARCHIVE_MODE"] = "standalone"
        os.environ.pop("PHOTOARCHIVE_HUB_URL", None)
        os.environ.pop("PHOTOARCHIVE_DEVICE_TOKEN", None)
        satellite._stored_hub_url = ""
        satellite._stored_device_token = ""
        asyncio.run(db.init_db())
        pair_routes.configure(db_path=lambda: self.db_path)
        api = FastAPI()
        api.include_router(pair_routes.router)
        self.client_context = TestClient(api)
        self.client = self.client_context.__enter__()

    def tearDown(self):
        self.client_context.__exit__(None, None, None)
        satellite.register_sync_starter(self.old_starter)
        satellite._stored_hub_url, satellite._stored_device_token = self.old_stored
        for key, value in self.old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        db.DB_PATH = self.old_db_path
        settings.SETTINGS_PATH = self.old_settings_path
        settings._settings = self.old_settings
        self.tempdir.cleanup()

    def test_connect_redeems_a_stubbed_hub_pair_code(self):
        started = []
        observed = {}

        async def start_sync():
            started.append(True)
            return True

        class StubHubResponse:
            status = 200

            def read(self):
                return b'{"device_token":"device-token","hub_id":"hub-123"}'

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        def stub_hub(request, timeout):
            observed["url"] = request.full_url
            observed["payload"] = request.data.decode("utf-8")
            observed["timeout"] = timeout
            return StubHubResponse()

        satellite.register_sync_starter(start_sync)
        with mock.patch.object(pair_routes, "urlopen", side_effect=stub_hub):
            response = self.client.post(
                "/api/pair/connect",
                json={
                    "hub_url": "http://hub.local:8000/",
                    "code": "ABCD1234",
                    "device_name": "Azimuth Photo on Windows",
                    "platform": "Win32",
                },
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["hub_url"], "http://hub.local:8000")
        self.assertTrue(response.json()["has_hub"])
        self.assertEqual(started, [True])
        self.assertEqual(observed["url"], "http://hub.local:8000/api/pair")
        self.assertEqual(observed["timeout"], 5)
        self.assertIn('"code": "ABCD1234"', observed["payload"])
        self.assertEqual(settings.get_settings()["hub_url"], "http://hub.local:8000")
        self.assertEqual(settings.get_settings()["device_token"], "device-token")

    def test_connect_to_black_hole_does_not_block_other_requests(self):
        connect_started = threading.Event()
        connect_result = {}

        def black_hole(_request, timeout):
            self.assertEqual(timeout, 5)
            connect_started.set()
            time.sleep(1.25)
            raise pair_routes.URLError("timed out")

        def connect():
            connect_result["response"] = self.client.post(
                "/api/pair/connect",
                json={"hub_url": "http://black-hole.invalid", "code": "ABCD1234"},
            )

        with mock.patch.object(pair_routes, "urlopen", side_effect=black_hole):
            thread = threading.Thread(target=connect)
            thread.start()
            self.assertTrue(connect_started.wait(timeout=1))
            started = time.perf_counter()
            status = self.client.get("/api/pair/status")
            elapsed = time.perf_counter() - started
            thread.join(timeout=2)

        self.assertEqual(status.status_code, 200, status.text)
        self.assertLess(elapsed, 1.0)
        self.assertFalse(thread.is_alive())
        self.assertEqual(connect_result["response"].status_code, 400)


if __name__ == "__main__":
    unittest.main()
