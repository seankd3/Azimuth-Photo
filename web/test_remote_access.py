"""Guided Tailscale remote-access probe + Serve apply."""

from __future__ import annotations

import json
import os
import subprocess
import unittest
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from features.access import routes as access_routes
from features.access import tailscale as ts


def _completed(stdout: str = "", stderr: str = "", returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


class TailscaleProbeTests(unittest.TestCase):
    def setUp(self):
        self._old_env = {
            key: os.environ.get(key)
            for key in (
                "PHOTOARCHIVE_MODE",
                "PHOTOARCHIVE_HUB_URL",
                "PHOTOARCHIVE_TS_DRYRUN",
                "PHOTOARCHIVE_TS_FORCE_STATE",
                "PHOTOARCHIVE_HTTPS_PORT",
                "PHOTOARCHIVE_PORT",
            )
        }
        for key in list(self._old_env):
            os.environ.pop(key, None)
        ts.set_runner(None)

    def tearDown(self):
        ts.set_runner(None)
        for key, value in self._old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_hub_mode_predicate(self):
        self.assertTrue(ts.is_hub_mode())
        os.environ["PHOTOARCHIVE_MODE"] = "hub"
        self.assertTrue(ts.is_hub_mode())
        os.environ["PHOTOARCHIVE_MODE"] = "standalone"
        self.assertFalse(ts.is_hub_mode())
        os.environ["PHOTOARCHIVE_MODE"] = "satellite"
        self.assertFalse(ts.is_hub_mode())
        os.environ.pop("PHOTOARCHIVE_MODE", None)
        os.environ["PHOTOARCHIVE_HUB_URL"] = "http://hub.example"
        self.assertFalse(ts.is_hub_mode())

    def test_force_states(self):
        os.environ["PHOTOARCHIVE_TS_FORCE_STATE"] = "absent"
        self.assertEqual(ts.probe()["state"], "absent")
        os.environ["PHOTOARCHIVE_TS_FORCE_STATE"] = "logged-out"
        self.assertEqual(ts.probe()["state"], "logged-out")
        os.environ["PHOTOARCHIVE_TS_FORCE_STATE"] = "up"
        up = ts.probe()
        self.assertEqual(up["state"], "up")
        self.assertTrue(up["https_url"].startswith("https://"))

    def test_absent_when_binary_missing(self):
        with mock.patch("features.access.tailscale.shutil.which", return_value=None):
            result = ts.probe()
        self.assertEqual(result["state"], "absent")
        self.assertFalse(result["available"])

    def test_logged_out_when_backend_needs_login(self):
        payload = json.dumps({"BackendState": "NeedsLogin", "HaveNodeKey": False, "Self": {}})

        def runner(argv, timeout):
            if argv[:3] == ["tailscale", "status", "--json"]:
                return _completed(stdout=payload)
            return _completed(returncode=1)

        with mock.patch("features.access.tailscale.shutil.which", return_value="/usr/bin/tailscale"):
            ts.set_runner(runner)
            result = ts.probe()
        self.assertEqual(result["state"], "logged-out")

    def test_up_when_running(self):
        payload = json.dumps({
            "BackendState": "Running",
            "HaveNodeKey": True,
            "TailscaleIPs": ["100.64.0.2"],
            "Self": {"DNSName": "studio.tailnet.ts.net."},
        })

        def runner(argv, timeout):
            if argv[:2] == ["tailscale", "ip"]:
                return _completed(stdout="100.64.0.2\n")
            return _completed(stdout=payload)

        with mock.patch("features.access.tailscale.shutil.which", return_value="/usr/bin/tailscale"):
            ts.set_runner(runner)
            result = ts.probe()
        self.assertEqual(result["state"], "up")
        self.assertEqual(result["dns_name"], "studio.tailnet.ts.net")
        self.assertEqual(result["https_url"], "https://studio.tailnet.ts.net:8443")

    def test_apply_serve_dry_run_never_invokes_cli(self):
        os.environ["PHOTOARCHIVE_TS_DRYRUN"] = "1"
        os.environ["PHOTOARCHIVE_TS_FORCE_STATE"] = "up"
        calls: list[tuple] = []

        def runner(argv, timeout):
            calls.append(tuple(argv))
            raise AssertionError("runner must not be called in dry-run apply")

        ts.set_runner(runner)
        result = ts.apply_serve(local_target="http://127.0.0.1:8133")
        self.assertTrue(result["ok"])
        self.assertTrue(result["dry_run"])
        self.assertIn("tailscale serve --bg --https=8443", result["command"])
        self.assertEqual(result["https_url"], "https://photoarchive.example.ts.net:8443")
        self.assertEqual(calls, [])

    def test_apply_serve_runs_exact_argv_when_live(self):
        os.environ["PHOTOARCHIVE_TS_FORCE_STATE"] = "up"
        calls: list[list[str]] = []

        def runner(argv, timeout):
            calls.append(list(argv))
            return _completed(stdout="ok\n")

        ts.set_runner(runner)
        result = ts.apply_serve(local_target="http://127.0.0.1:8000")
        self.assertTrue(result["ok"])
        self.assertFalse(result["dry_run"])
        self.assertEqual(
            calls,
            [["tailscale", "serve", "--bg", "--https=8443", "http://127.0.0.1:8000"]],
        )


class RemoteAccessRouteTests(unittest.TestCase):
    def setUp(self):
        self._old = {key: os.environ.get(key) for key in (
            "PHOTOARCHIVE_MODE", "PHOTOARCHIVE_HUB_URL", "PHOTOARCHIVE_TS_DRYRUN",
            "PHOTOARCHIVE_TS_FORCE_STATE", "PHOTOARCHIVE_PORT",
        )}
        for key in list(self._old):
            os.environ.pop(key, None)
        os.environ["PHOTOARCHIVE_PORT"] = "8133"
        os.environ["PHOTOARCHIVE_TS_DRYRUN"] = "1"
        ts.set_runner(None)
        app = FastAPI()
        app.include_router(access_routes.router)
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        ts.set_runner(None)
        for key, value in self._old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_get_includes_hub_mode_and_states(self):
        os.environ["PHOTOARCHIVE_TS_FORCE_STATE"] = "logged-out"
        response = self.client.get("/api/remote-access")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["hub_mode"])
        self.assertEqual(body["mode"], "hub")
        self.assertEqual(body["tailscale"]["state"], "logged-out")
        self.assertIn("serve_command", body["tailscale"])
        self.assertIn("8133", body["tailscale"]["serve_command"])

    def test_hidden_semantics_for_standalone(self):
        os.environ["PHOTOARCHIVE_MODE"] = "standalone"
        response = self.client.get("/api/remote-access")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertFalse(body["hub_mode"])
        self.assertEqual(body["mode"], "standalone")

    def test_post_serve_dry_run(self):
        os.environ["PHOTOARCHIVE_TS_FORCE_STATE"] = "up"
        response = self.client.post("/api/remote-access/serve")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertTrue(body["dry_run"])
        self.assertTrue(body["https_url"].startswith("https://"))
        self.assertTrue(body["tailscale"]["serve_applied"])


if __name__ == "__main__":
    unittest.main()
