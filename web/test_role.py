"""One role value, resolved for every mode x hub combination that can exist."""

from __future__ import annotations

import itertools
import os
import unittest

from archive import role
from features.sync import satellite

MODES = ["", "hub", "satellite", "standalone"]
HUBS = ["", "http://hub.local:8000"]


class _RoleEnv(unittest.TestCase):
    def setUp(self):
        self._env = os.environ.get("AZIMUTH_MODE"), os.environ.get("AZIMUTH_HUB_URL")
        self._stored = satellite._stored_hub_url

    def tearDown(self):
        for name, value in zip(("AZIMUTH_MODE", "AZIMUTH_HUB_URL"), self._env):
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        satellite._stored_hub_url = self._stored

    def _configure(self, mode: str, env_hub: str, stored_hub: str) -> None:
        os.environ["AZIMUTH_MODE"] = mode
        if env_hub:
            os.environ["AZIMUTH_HUB_URL"] = env_hub
        else:
            os.environ.pop("AZIMUTH_HUB_URL", None)
        satellite._stored_hub_url = stored_hub


class RoleTests(_RoleEnv):
    def test_every_combination_resolves_to_exactly_one_role(self):
        for mode, env_hub, stored_hub in itertools.product(MODES, HUBS, HUBS):
            with self.subTest(mode=mode or "(unset)", env=env_hub, stored=stored_hub):
                self._configure(mode, env_hub, stored_hub)
                self.assertIn(role.role(), {role.HUB, role.SATELLITE, role.STANDALONE})
                # The old code could answer "hub" and "satellite" at the same
                # time, because the two questions were asked of different
                # sources. They are one question with two spellings.
                self.assertNotEqual(
                    role.serves_the_archive(),
                    role.works_in_someone_elses_archive(),
                )

    def test_pairing_at_runtime_stops_the_node_advertising_itself_as_a_hub(self):
        """The bug this module exists to kill.

        Pairing from the UI stores the hub in settings, not the environment.
        Two `is_hub_mode()` copies read os.environ, so a paired laptop kept
        advertising over mDNS and kept showing the Remote-access panel.
        """
        self._configure("", "", "")
        self.assertTrue(role.serves_the_archive())

        self._configure("", "", "http://hub.local:8000")
        self.assertFalse(role.serves_the_archive())
        self.assertEqual(role.role(), role.SATELLITE)
        self.assertTrue(role.has_hub())

    def test_a_declared_hub_ignores_a_stale_stored_hub_url(self):
        self._configure("hub", "", "http://leftover.local:8000")
        self.assertEqual(role.role(), role.HUB)
        self.assertFalse(role.has_hub())

    def test_standalone_holds_its_own_archive_so_it_defers_nothing(self):
        self._configure("standalone", "", "")
        self.assertEqual(role.role(), role.STANDALONE)
        self.assertFalse(role.defers_bulk_compute())
        self.assertFalse(role.serves_the_archive())
        self.assertFalse(role.has_hub())

    def test_only_a_node_with_its_archive_elsewhere_defers_bulk_compute(self):
        for mode, env_hub, stored_hub in itertools.product(MODES, HUBS, HUBS):
            with self.subTest(mode=mode or "(unset)", env=env_hub, stored=stored_hub):
                self._configure(mode, env_hub, stored_hub)
                self.assertEqual(
                    role.defers_bulk_compute(), role.role() == role.SATELLITE
                )


class PregenRegateTests(_RoleEnv):
    """Preview generation must follow the role, not the role at import time."""

    def test_pairing_pauses_local_preview_generation(self):
        import thumbnails

        self._configure("standalone", "", "")
        self.assertFalse(thumbnails.regate_previews_for_role())

        # What attach_hub() does: store the hub, then re-gate.
        self._configure("standalone", "", "http://hub.local:8000")
        self.assertTrue(
            thumbnails.regate_previews_for_role(),
            "a paired laptop kept decoding originals that now live on the hub",
        )

        self._configure("standalone", "", "")
        self.assertFalse(thumbnails.regate_previews_for_role())


if __name__ == "__main__":
    unittest.main()
