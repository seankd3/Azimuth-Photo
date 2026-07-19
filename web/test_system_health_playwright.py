"""Playwright smoke: System Health panel renders with live /api/health/details data."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    os.name == "nt",
    reason="uses POSIX process groups for its isolated Playwright server",
)

WEB_ROOT = Path(__file__).resolve().parent
SCRATCH = Path("/tmp/system-health-playwright")
DB_PATH = SCRATCH / "catalog.db"
CACHE_ROOT = SCRATCH / "thumbs"
SETTINGS_PATH = SCRATCH / "settings.json"
PORT = 8161
BASE_URL = f"http://127.0.0.1:{PORT}"
SCREENSHOT = Path(__file__).resolve().parents[1] / "receipts" / "system-health-panel.png"


def _seed() -> None:
    if SCRATCH.exists():
        for path in sorted(SCRATCH.rglob("*"), reverse=True):
            if path.is_file():
                path.unlink()
            elif path.is_dir():
                path.rmdir()
    CACHE_ROOT.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text("{}", encoding="utf-8")
    SCREENSHOT.parent.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env.update(
        {
            "PHOTOARCHIVE_SMOKE_MODE": "1",
            "PHOTOARCHIVE_DB_PATH": str(DB_PATH),
            "PHOTOARCHIVE_SETTINGS_PATH": str(SETTINGS_PATH),
            "PYTHONPATH": str(WEB_ROOT),
        }
    )
    script = r"""
import asyncio, time
import db, thumbnails, settings
from features.system import backups

async def main():
    db.DB_PATH = %r
    thumbnails.SSD_CACHE_DIR = %r
    settings.SETTINGS_PATH = %r
    settings.reset_settings()
    await db.init_db()
    backups.catalog_quick_check(db.DB_PATH)
    print("seeded")

asyncio.run(main())
""" % (
        str(DB_PATH),
        str(CACHE_ROOT),
        str(SETTINGS_PATH),
    )
    subprocess.check_call([sys.executable, "-c", script], cwd=str(WEB_ROOT), env=env)


def _wait_ready(url: str, timeout: float = 40.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status == 200:
                    return
        except (urllib.error.URLError, TimeoutError):
            time.sleep(0.25)
    raise RuntimeError(f"server not ready: {url}")


@pytest.mark.playwright
def test_system_health_panel_renders_live_data():
    _seed()
    env = os.environ.copy()
    env.update(
        {
            "PHOTOARCHIVE_SMOKE_MODE": "1",
            "PHOTOARCHIVE_DB_PATH": str(DB_PATH),
            "PHOTOARCHIVE_CACHE_DIR": str(CACHE_ROOT),
            "PHOTOARCHIVE_SETTINGS_PATH": str(SETTINGS_PATH),
            "PHOTOARCHIVE_PORT": str(PORT),
            "PYTHONPATH": str(WEB_ROOT),
        }
    )
    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app:app", "--host", "127.0.0.1", "--port", str(PORT)],
        cwd=str(WEB_ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        _wait_ready(f"{BASE_URL}/api/health/details")
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.goto(f"{BASE_URL}/", wait_until="networkidle")
            page.click("#system-btn")
            page.wait_for_selector("#system-health-panel", timeout=15000)
            page.wait_for_function(
                """() => {
                    const panel = document.querySelector('#system-health-panel');
                    return panel && panel.querySelectorAll('[data-health-check]').length >= 5;
                }""",
                timeout=15000,
            )
            heading = page.locator("#system-health-panel h3").inner_text().strip().lower()
            assert heading == "system health"
            rows = page.locator("#system-health-panel [data-health-check]")
            assert rows.count() >= 5
            labels = [rows.nth(i).locator("b").inner_text().strip() for i in range(min(rows.count(), 9))]
            assert any("catalog" in label.lower() for label in labels)
            assert any("memory" in label.lower() or "disk" in label.lower() for label in labels)
            api = page.request.get(f"{BASE_URL}/api/health/details").json()
            assert api["overall"] in {"ok", "warn", "bad"}
            assert len(api["checks"]) >= 5
            vault = next(item for item in api["checks"] if item["id"] == "cloud_vault")
            assert "not configured" in vault["detail"].lower()
            page.locator("#system-health-panel").screenshot(path=str(SCREENSHOT))
            browser.close()
    finally:
        os.killpg(server.pid, signal.SIGTERM)
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(server.pid, signal.SIGKILL)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
