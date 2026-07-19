"""Launcher two-failed-boots rollback — fails-without / passes-with."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from features.sync import launcher
from features.sync.client_update import UI_ROLLED_BACK, atomic_write_text


class FakeProcess:
    def __init__(self, *, exit_code: int | None):
        # None means still running; int means already exited.
        self._code = exit_code

    def poll(self):
        return self._code

    def terminate(self):
        if self._code is None:
            self._code = 1

    def kill(self):
        self._code = 1

    def wait(self, timeout=None):
        if self._code is None:
            self._code = 0
        return self._code


class TwoFailedBootsRollbackTests(unittest.TestCase):
    def _seed(self, root: Path):
        old = root / "versions" / "oldsha"
        new = root / "versions" / "newsha"
        old.mkdir(parents=True)
        new.mkdir(parents=True)
        (old / "web").mkdir()
        (new / "web").mkdir()
        atomic_write_text(root / "previous.txt", str(old) + "\n")
        atomic_write_text(root / "current.txt", str(new) + "\n")
        return old, new

    def test_fails_without_rollback_rule_stays_on_crash_looping_sha(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _old, new = self._seed(root)
            launcher.record_boot_attempt(root, "newsha")
            launcher.record_boot_attempt(root, "newsha")
            self.assertEqual(launcher.read_boot_attempts(root), ("newsha", 2))
            # Fails-without: skip apply_two_failed_boots_rule → pointer stays on newsha.
            self.assertEqual(
                (root / "current.txt").read_text(encoding="utf-8").strip(),
                str(new),
            )
            self.assertFalse((root / "rollback_notice.txt").exists())

    def test_passes_with_rollback_after_two_failed_boots(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old, new = self._seed(root)
            seen: list[str] = []

            def spawn(*, web_dir, **kwargs):
                sha = Path(web_dir).parent.name if Path(web_dir).name == "web" else Path(web_dir).name
                seen.append(sha)
                # Crash-before-ready for newsha; stay alive for oldsha.
                return FakeProcess(exit_code=1 if sha == "newsha" else None)

            def wait_ready(*, process, **kwargs):
                return process.poll() is None

            code = launcher.run_supervised(
                install_root=root,
                ready_timeout=0.01,
                spawn=spawn,
                wait_ready=wait_ready,
            )

            self.assertEqual(code, 0)
            self.assertGreaterEqual(seen.count("newsha"), 2)
            self.assertIn("oldsha", seen)
            self.assertEqual(
                (root / "current.txt").read_text(encoding="utf-8").strip(),
                str(old),
            )
            self.assertNotEqual(
                (root / "current.txt").read_text(encoding="utf-8").strip(),
                str(new),
            )
            self.assertEqual(launcher.consume_rollback_notice(root), UI_ROLLED_BACK)

    def test_ready_clears_boot_attempt_counter(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _old, new = self._seed(root)

            def spawn(**kwargs):
                return FakeProcess(exit_code=None)

            def wait_ready(*, process, **kwargs):
                return True

            code = launcher.run_supervised(
                install_root=root,
                spawn=spawn,
                wait_ready=wait_ready,
            )
            self.assertEqual(code, 0)
            self.assertFalse((root / "boot_attempts.txt").exists())
            self.assertEqual(
                (root / "current.txt").read_text(encoding="utf-8").strip(),
                str(new),
            )

    def test_slow_but_alive_boot_never_increments_failure_counter(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _old, new = self._seed(root)
            polls = {"n": 0}

            def spawn(**kwargs):
                return FakeProcess(exit_code=None)

            def wait_ready(*, process, timeout, **kwargs):
                # Simulate wait_until_ready: alive past timeout keeps waiting, then succeeds.
                self.assertGreaterEqual(float(timeout), 0.01)
                polls["n"] += 1
                self.assertIsNone(process.poll())
                return True

            code = launcher.run_supervised(
                install_root=root,
                ready_timeout=0.01,
                spawn=spawn,
                wait_ready=wait_ready,
            )
            self.assertEqual(code, 0)
            self.assertEqual(polls["n"], 1)
            self.assertFalse((root / "boot_attempts.txt").exists())
            self.assertEqual(
                (root / "current.txt").read_text(encoding="utf-8").strip(),
                str(new),
            )

    def test_ready_timeout_env_defaults_to_120(self):
        self.assertEqual(launcher.ready_timeout_seconds({}), 120.0)
        self.assertEqual(launcher.ready_timeout_seconds({"PHOTOARCHIVE_READY_TIMEOUT": "90"}), 90.0)


if __name__ == "__main__":
    unittest.main()
