"""Playwright proof: Connect Lightroom button state + ranking chip."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.skipif(
        os.name == "nt",
        reason="uses POSIX process groups for its isolated Playwright server",
    ),
    pytest.mark.slow,
    pytest.mark.playwright,
]

# Guard at import time — before the test boots uvicorn — so a missing
# playwright is one visible module skip, not a boot-then-fail mid-test.
pytest.importorskip(
    "playwright.sync_api",
    reason="playwright is not installed; browser proofs are opt-in",
)

WEB_ROOT = Path(__file__).resolve().parent
REPO_ROOT = WEB_ROOT.parent
SCRATCH = Path(tempfile.gettempdir()) / "azimuth-lrux-playwright"
DB_PATH = SCRATCH / "catalog.db"
CACHE_ROOT = SCRATCH / "thumbs"
MODULES = SCRATCH / "Modules"
SCREENSHOT_CONNECT = SCRATCH / "connect-button.png"
SCREENSHOT_CHIP = SCRATCH / "ranking-chip.png"
PORT = 8157
BASE_URL = f"http://127.0.0.1:{PORT}"

HASH_RAW = "a" * 32
HASH_EDIT = "b" * 32


def _seed_catalog() -> None:
    if SCRATCH.exists():
        for path in SCRATCH.rglob("*"):
            if path.is_file():
                path.unlink()
    SCRATCH.mkdir(parents=True, exist_ok=True)
    CACHE_ROOT.mkdir(parents=True, exist_ok=True)
    MODULES.mkdir(parents=True, exist_ok=True)
    if DB_PATH.exists():
        DB_PATH.unlink()

    env = os.environ.copy()
    env["AZIMUTH_SMOKE_MODE"] = "1"
    env["AZIMUTH_DB_PATH"] = str(DB_PATH)
    env["PYTHONPATH"] = str(WEB_ROOT)

    script = r"""
import asyncio, time
import db, thumbnails
from features.sync import export_relation

async def main():
    db.DB_PATH = %r
    thumbnails.SSD_CACHE_DIR = %r
    await db.init_db()
    conn = await db.get_db()
    now = time.time()
    await conn.execute(
        "INSERT INTO catalog_sources "
        "(path, display_name, included, online, image_count, active_image_count, "
        "created_at, last_seen_at) VALUES (?, ?, 1, 1, 2, 2, ?, ?)",
        ("/tmp/lrux-photos", "LRUX", now, now),
    )
    source_id = int((await (await conn.execute("SELECT last_insert_rowid()")).fetchone())[0])
    await conn.execute(
        "INSERT INTO images "
        "(source_id, filename, filepath, content_hash, status, file_ext, elo, comparisons, date_taken) "
        "VALUES (?, ?, ?, ?, 'kept', 'dng', 1500, 8, ?)",
        (source_id, "raw.dng", "/tmp/lrux-photos/raw.dng", %r, "2026-07-12T10:00:00"),
    )
    raw_id = int((await (await conn.execute("SELECT last_insert_rowid()")).fetchone())[0])
    await conn.execute(
        "INSERT INTO images "
        "(source_id, filename, filepath, content_hash, status, file_ext, elo, comparisons, date_taken) "
        "VALUES (?, ?, ?, ?, 'kept', 'jpg', 1400, 4, ?)",
        (source_id, "edit.jpg", "/tmp/lrux-photos/edit.jpg", %r, "2026-07-12T11:00:00"),
    )
    edit_id = int((await (await conn.execute("SELECT last_insert_rowid()")).fetchone())[0])
    await conn.commit()
    await conn.close()
    linked = await export_relation.link_export(
        db.DB_PATH, source_image_id=raw_id, export_image_id=edit_id,
    )
    print("seeded", raw_id, edit_id, linked.get("linked"))

asyncio.run(main())
""" % (str(DB_PATH), str(CACHE_ROOT), HASH_RAW, HASH_EDIT)
    subprocess.check_call(
        [sys.executable, "-c", script],
        cwd=str(WEB_ROOT),
        env=env,
    )


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
def test_lr_ux_connect_and_ranking_chip_screenshots():
    _seed_catalog()
    env = os.environ.copy()
    env.update({
        "AZIMUTH_SMOKE_MODE": "1",
        "AZIMUTH_MODE": "satellite",
        "AZIMUTH_DB_PATH": str(DB_PATH),
        "AZIMUTH_CACHE_DIR": str(CACHE_ROOT),
        "AZIMUTH_PORT": str(PORT),
        "AZIMUTH_LR_MODULES_DIR": str(MODULES),
        "AZIMUTH_LR_FORCE_DETECT": "1",
        "PYTHONPATH": str(WEB_ROOT),
    })
    py = sys.executable
    server = subprocess.Popen(
        [py, "-m", "uvicorn", "app:app", "--host", "127.0.0.1", "--port", str(PORT)],
        cwd=str(WEB_ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        _wait_ready(f"{BASE_URL}/api/counts")
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.goto(f"{BASE_URL}/", wait_until="networkidle")

            # Ranking chip (piggybacks sync status)
            page.wait_for_selector('.lr-ranking-chip-button', timeout=15000)
            page.locator('#lr-ranking-chip-slot').screenshot(path=str(SCREENSHOT_CHIP))
            assert SCREENSHOT_CHIP.exists()
            assert "new edit" in page.locator('.lr-ranking-chip-button').inner_text().lower()

            # Connect Lightroom in System → Connectivity
            page.locator('#system-btn').click()
            page.wait_for_selector('#view-system.active [data-system-section="connectivity"]', timeout=15000)
            page.locator('[data-system-section="connectivity"]').click()
            page.wait_for_selector('#connect-lightroom-panel #lr-connect-btn', timeout=15000)
            page.locator('#connect-lightroom-panel').screenshot(path=str(SCREENSHOT_CONNECT))
            assert SCREENSHOT_CONNECT.exists()
            assert page.locator('#lr-connect-btn').is_visible()
            browser.close()
    finally:
        os.killpg(server.pid, signal.SIGTERM)
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(server.pid, signal.SIGKILL)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
