import pytest
import os
import shutil
import subprocess
import unittest
from pathlib import Path

pytestmark = pytest.mark.slow

@unittest.skipUnless(
    os.environ.get("AZIMUTH_BROWSER_SMOKE") == "1",
    "set AZIMUTH_BROWSER_SMOKE=1 to run the opt-in browser smoke test",
)
@unittest.skipIf(os.name == "nt", "uses the Linux google-chrome-stable binary")
class BrowserSmokeTests(unittest.TestCase):
    def test_browser_smoke_harness(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node is required for browser smoke")
        if not shutil.which("google-chrome-stable"):
            self.skipTest("google-chrome-stable is required for browser smoke")

        base_url = os.environ.get("AZIMUTH_SMOKE_URL", "http://127.0.0.1:8000")
        script = Path(__file__).resolve().parents[1] / "scripts" / "azimuth-browser-smoke"
        # When targeting a local dev server, start it with AZIMUTH_SMOKE_MODE=1
        # so the browser gate skips archive DB startup and heavyweight model workers.
        result = subprocess.run(
            [node, str(script), "--base-url", base_url],
            cwd=Path(__file__).resolve().parent,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=90,
            check=False,
        )
        if result.returncode != 0:
            self.fail(result.stdout)


if __name__ == "__main__":
    unittest.main()
