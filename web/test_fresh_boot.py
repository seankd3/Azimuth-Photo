"""Standing release gate: a brand-new home must boot to a serving app.

Every other suite runs against a seeded, in-process library — which is exactly
how a startup handler that assumed schema existed bricked every fresh install
without a single red test (fix 5a1b5017). An in-process TestClient boot does
NOT reproduce real handler ordering (verified: it stays green with 5a1b5017
reverted), so this gate boots the app the way installs actually do: a
subprocess server_entry against a virgin PHOTOARCHIVE_HOME.

Doctrine this enforces (docs/background-work-behavior.md): startup handlers
have NO ordering guarantee relative to init_db — and under SMOKE_MODE init_db
never runs at all (core/background.py returns after warm_templates), so every
handler touching the catalog must tolerate missing schema. The smoke-mode
flavor below is the exact 5a1b5017 vector (Fix & Speed repro): a virgin home
plus SMOKE_MODE=1 crashed boot in ~2s before the guard.
"""

import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path

WEB_ROOT = Path(__file__).resolve().parent
ENTRY = WEB_ROOT.parent / "scripts" / "server_entry.py"


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class FreshHomeBootSmoke(unittest.TestCase):
    def test_fresh_hub_home_boots_clean(self):
        self._boot_fresh_home({})

    def test_fresh_smoke_mode_home_boots_clean_without_any_schema(self):
        # SMOKE_MODE skips init_db entirely: the strictest schema-tolerance
        # probe — any startup handler that queries the catalog dies here.
        self._boot_fresh_home({"PHOTOARCHIVE_SMOKE_MODE": "1", "PHOTOARCHIVE_ACCESS": "local"})

    def test_fresh_satellite_home_boots_clean_with_unreachable_hub(self):
        # Law 1: a satellite must boot and serve even when its hub is down.
        self._boot_fresh_home({
            "PHOTOARCHIVE_MODE": "satellite",
            "PHOTOARCHIVE_HUB_URL": "http://127.0.0.1:1",
        })

    def _boot_fresh_home(self, extra_env: dict):
        port = _free_port()
        with tempfile.TemporaryDirectory() as home:
            env = {
                key: value
                for key, value in os.environ.items()
                if not key.startswith("PHOTOARCHIVE_")
            }
            env.update({
                "PHOTOARCHIVE_HOME": home,
                "PHOTOARCHIVE_PORT": str(port),
                "PHOTOARCHIVE_HOST": "127.0.0.1",
                "PYTHONPATH": str(WEB_ROOT),
            })
            env.update(extra_env)
            process = subprocess.Popen(
                [sys.executable, str(ENTRY)],
                env=env,
                cwd=str(WEB_ROOT.parent),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            try:
                status = None
                deadline = time.time() + 45
                while time.time() < deadline:
                    if process.poll() is not None:
                        break
                    try:
                        with urllib.request.urlopen(
                            f"http://127.0.0.1:{port}/", timeout=2
                        ) as response:
                            status = response.status
                            break
                    except OSError:
                        time.sleep(0.5)
                exited_early = process.poll() is not None
            finally:
                process.terminate()
                try:
                    output, _ = process.communicate(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    output, _ = process.communicate()

            self.assertFalse(
                exited_early,
                f"fresh-home boot died before serving:\n{output[-3000:]}",
            )
            self.assertEqual(status, 200, f"fresh-home first request:\n{output[-3000:]}")
            self.assertNotIn("Traceback", output, f"startup traceback:\n{output[-3000:]}")


if __name__ == "__main__":
    unittest.main()
