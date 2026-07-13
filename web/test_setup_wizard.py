"""Standalone-mode predicates, first-run wizard gating, and runtime hub attach."""

import os
import unittest
from unittest import mock

from test_support import BackendTestCase
import settings as app_settings
from features.pages import routes as pages_routes
from features.sync import satellite


MODE_KEYS = ("PHOTOARCHIVE_MODE", "PHOTOARCHIVE_HUB_URL", "PHOTOARCHIVE_DEVICE_TOKEN")


class ModeEnv:
    """Save/clear the mode env vars and satellite's stored-hub cache."""

    def __enter__(self):
        self._env = {key: os.environ.pop(key, None) for key in MODE_KEYS}
        self._stored = (satellite._stored_hub_url, satellite._stored_device_token)
        satellite._stored_hub_url = ""
        satellite._stored_device_token = ""
        return self

    def __exit__(self, *exc):
        for key, value in self._env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        satellite._stored_hub_url, satellite._stored_device_token = self._stored


class ModePredicateTests(unittest.TestCase):
    def test_default_is_hub(self):
        with ModeEnv():
            self.assertFalse(satellite.is_satellite_mode())
            self.assertFalse(satellite.has_hub())
            self.assertEqual(satellite.bootstrap_payload(), {"mode": "hub", "has_hub": False})

    def test_satellite_without_hub_is_standalone(self):
        with ModeEnv():
            os.environ["PHOTOARCHIVE_MODE"] = "satellite"
            self.assertTrue(satellite.is_satellite_mode())
            self.assertFalse(satellite.has_hub())
            self.assertEqual(
                satellite.bootstrap_payload(), {"mode": "satellite", "has_hub": False}
            )

    def test_standalone_alias(self):
        with ModeEnv():
            os.environ["PHOTOARCHIVE_MODE"] = "standalone"
            self.assertTrue(satellite.is_satellite_mode())
            self.assertFalse(satellite.has_hub())

    def test_hub_url_alone_implies_satellite(self):
        with ModeEnv():
            os.environ["PHOTOARCHIVE_HUB_URL"] = "http://hub:8000/"
            self.assertTrue(satellite.is_satellite_mode())
            self.assertTrue(satellite.has_hub())
            self.assertEqual(satellite.hub_url(), "http://hub:8000")

    def test_explicit_hub_mode_wins_over_hub_url(self):
        with ModeEnv():
            os.environ["PHOTOARCHIVE_MODE"] = "hub"
            os.environ["PHOTOARCHIVE_HUB_URL"] = "http://hub:8000"
            self.assertFalse(satellite.is_satellite_mode())
            self.assertFalse(satellite.has_hub())

    def test_stored_hub_counts_without_env(self):
        with ModeEnv():
            satellite._stored_hub_url = "http://stored-hub:8000"
            self.assertTrue(satellite.is_satellite_mode())
            self.assertTrue(satellite.has_hub())


class SetupGateTests(BackendTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        pages_routes.reset_setup_cache()
        self._smoke = mock.patch.dict(os.environ, {}, clear=False)
        self._smoke.start()
        os.environ.pop("PHOTOARCHIVE_SMOKE_MODE", None)

    async def asyncTearDown(self):
        self._smoke.stop()
        pages_routes.reset_setup_cache()
        await super().asyncTearDown()

    async def test_fresh_install_needs_setup(self):
        self.assertTrue(pages_routes.needs_setup())

    async def test_smoke_mode_never_needs_setup(self):
        os.environ["PHOTOARCHIVE_SMOKE_MODE"] = "1"
        pages_routes.reset_setup_cache()
        self.assertFalse(pages_routes.needs_setup())

    async def test_completing_setup_sticks(self):
        self.assertTrue(pages_routes.needs_setup())
        await pages_routes.setup_complete()
        self.assertFalse(pages_routes.needs_setup())
        self.assertTrue(app_settings.get_settings()["setup_completed"])
        # Sticky across a cache reset too — the flag is persisted.
        pages_routes.reset_setup_cache()
        self.assertFalse(pages_routes.needs_setup())

    async def test_existing_sources_skip_setup(self):
        await self._source("existing")
        self.assertFalse(pages_routes.needs_setup())


class WizardHubCardContracts(unittest.TestCase):
    def test_hub_card_stays_hidden_until_satellite_settings_arrive(self):
        template_path = os.path.join(os.path.dirname(__file__), "templates", "setup.html")
        with open(template_path, encoding="utf-8") as handle:
            template = handle.read()

        self.assertIn('id="su-connect-card" hidden', template)
        self.assertIn("status?.sync?.mode !== 'satellite'", template)
        self.assertIn("json('/api/settings')", template)
        self.assertIn("json('/api/discover')", template)
        self.assertIn("Connected — your library will sync in the background.", template)


class AttachHubTests(BackendTestCase):
    async def test_attach_persists_and_starts_sync(self):
        with ModeEnv():
            os.environ["PHOTOARCHIVE_MODE"] = "standalone"
            started = []

            async def starter():
                started.append(True)
                return True

            old_starter = satellite._sync_starter
            satellite.register_sync_starter(starter)
            try:
                result = await satellite.attach_hub("http://hub:8000/", "tok-123")
            finally:
                satellite.register_sync_starter(old_starter)

            self.assertEqual(result, {"hub": "http://hub:8000", "sync_started": True})
            self.assertEqual(started, [True])
            self.assertTrue(satellite.has_hub())
            self.assertEqual(satellite.hub_url(), "http://hub:8000")
            self.assertEqual(satellite.device_token(), "tok-123")
            config = app_settings.get_settings()
            self.assertEqual(config["hub_url"], "http://hub:8000")
            self.assertEqual(config["device_token"], "tok-123")
            # A fresh process would pick the same hub back up from settings.
            satellite._stored_hub_url = ""
            satellite.load_stored_hub()
            self.assertEqual(satellite.hub_url(), "http://hub:8000")

    async def test_attach_rejects_bad_url(self):
        with ModeEnv():
            os.environ["PHOTOARCHIVE_MODE"] = "standalone"
            with self.assertRaises(ValueError):
                await satellite.attach_hub("hub:8000")
            self.assertFalse(satellite.has_hub())


if __name__ == "__main__":
    unittest.main()
