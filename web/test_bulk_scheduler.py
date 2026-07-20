"""Bulk HDD stream sequencing — preview pregen before cloud vault."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core import bulk_scheduler


class VaultDecisionTests(unittest.TestCase):
    def test_previews_pending_vault_waits(self):
        self.assertEqual(
            bulk_scheduler.vault_decision(
                sequencing=True,
                previews_pending=True,
                vault_desired=True,
                manual_override=False,
            ),
            "wait",
        )

    def test_previews_done_vault_runs(self):
        self.assertEqual(
            bulk_scheduler.vault_decision(
                sequencing=True,
                previews_pending=False,
                vault_desired=True,
                manual_override=False,
            ),
            "run",
        )

    def test_manual_override_runs_while_previews_pending(self):
        self.assertEqual(
            bulk_scheduler.vault_decision(
                sequencing=True,
                previews_pending=True,
                vault_desired=False,
                manual_override=True,
            ),
            "run",
        )

    def test_vault_not_desired_is_idle(self):
        self.assertEqual(
            bulk_scheduler.vault_decision(
                sequencing=True,
                previews_pending=False,
                vault_desired=False,
                manual_override=False,
            ),
            "idle",
        )

    def test_sequencing_off_runs_even_with_previews(self):
        self.assertEqual(
            bulk_scheduler.vault_decision(
                sequencing=False,
                previews_pending=True,
                vault_desired=True,
                manual_override=False,
            ),
            "run",
        )


class PreviewsHoldDiskTests(unittest.TestCase):
    def test_running_and_waiting_hold_disk(self):
        self.assertTrue(
            bulk_scheduler.previews_hold_disk(
                manual_mode=True, manual_pause=False, state="running"
            )
        )
        self.assertTrue(
            bulk_scheduler.previews_hold_disk(
                manual_mode=True, manual_pause=False, state="waiting"
            )
        )

    def test_complete_idle_paused_release_disk(self):
        for state in ("complete", "idle", "paused", "error"):
            self.assertFalse(
                bulk_scheduler.previews_hold_disk(
                    manual_mode=True, manual_pause=False, state=state
                ),
                msg=state,
            )

    def test_disabled_or_paused_release_disk(self):
        self.assertFalse(
            bulk_scheduler.previews_hold_disk(
                manual_mode=False, manual_pause=False, state="running"
            )
        )
        self.assertFalse(
            bulk_scheduler.previews_hold_disk(
                manual_mode=True, manual_pause=True, state="running"
            )
        )


class SequencingEnvTests(unittest.TestCase):
    def test_default_on(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PHOTOARCHIVE_BULK_SEQUENCING", None)
            self.assertTrue(bulk_scheduler.sequencing_enabled())

    def test_explicit_off(self):
        with mock.patch.dict(os.environ, {"PHOTOARCHIVE_BULK_SEQUENCING": "0"}):
            self.assertFalse(bulk_scheduler.sequencing_enabled())


class DesiredFlagPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        path = Path(self.tmp.name) / "bulk_desired.json"
        bulk_scheduler.reset_for_tests(desired_path=path)
        self.addCleanup(bulk_scheduler.reset_for_tests)

    def test_restart_resumes_prior_desired_state(self):
        self.assertFalse(bulk_scheduler.pregen_desired())
        self.assertFalse(bulk_scheduler.vault_desired())

        bulk_scheduler.set_pregen_desired(True)
        bulk_scheduler.set_vault_desired(True)
        self.assertTrue(bulk_scheduler.pregen_desired())
        self.assertTrue(bulk_scheduler.vault_desired())

        # Simulate process restart: clear in-memory override wiring, keep the file.
        path = bulk_scheduler.desired_path()
        bulk_scheduler.reset_for_tests(desired_path=path)
        self.assertTrue(bulk_scheduler.pregen_desired())
        self.assertTrue(bulk_scheduler.vault_desired())

        bulk_scheduler.set_pregen_desired(False)
        bulk_scheduler.set_vault_desired(False)
        bulk_scheduler.reset_for_tests(desired_path=path)
        self.assertFalse(bulk_scheduler.pregen_desired())
        self.assertFalse(bulk_scheduler.vault_desired())


class DecideVaultStartTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        path = Path(self.tmp.name) / "bulk_desired.json"
        bulk_scheduler.reset_for_tests(desired_path=path)
        self.addCleanup(bulk_scheduler.reset_for_tests)

    def test_desired_with_previews_waits(self):
        bulk_scheduler.set_vault_desired(True)
        bulk_scheduler.configure(previews_pending=lambda: True)
        with mock.patch.dict(os.environ, {"PHOTOARCHIVE_BULK_SEQUENCING": "1"}):
            decision = bulk_scheduler.decide_vault_start(manual_override=False)
        self.assertEqual(decision["action"], "wait")
        self.assertEqual(decision["message"], bulk_scheduler.VAULT_WAIT_MESSAGE)

    def test_desired_without_previews_runs(self):
        bulk_scheduler.set_vault_desired(True)
        bulk_scheduler.configure(previews_pending=lambda: False)
        decision = bulk_scheduler.decide_vault_start(manual_override=False)
        self.assertEqual(decision["action"], "run")
        self.assertEqual(decision["message"], "")

    def test_manual_override_warns_when_previews_pending(self):
        bulk_scheduler.configure(previews_pending=lambda: True)
        with mock.patch.dict(os.environ, {"PHOTOARCHIVE_BULK_SEQUENCING": "1"}):
            decision = bulk_scheduler.decide_vault_start(manual_override=True)
        self.assertEqual(decision["action"], "run")
        self.assertIn("share the disk", decision["message"])


if __name__ == "__main__":
    unittest.main()
