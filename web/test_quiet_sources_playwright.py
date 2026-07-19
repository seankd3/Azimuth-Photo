"""Playwright proof: sidebar quiet-source eye toggle + dimmed hidden state."""

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
REPO_ROOT = WEB_ROOT.parent
SCRATCH = Path("/mnt/expansion/tmp/quiet-sources-playwright")
DB_PATH = SCRATCH / "catalog.db"
CACHE_ROOT = SCRATCH / "thumbs"
SCREENSHOT = Path("/mnt/expansion/tmp/quiet-sources-sidebar.png")
PORT = 8147
BASE_URL = f"http://127.0.0.1:{PORT}"


def _seed_catalog() -> None:
    if SCRATCH.exists():
        for path in SCRATCH.rglob("*"):
            if path.is_file():
                path.unlink()
    SCRATCH.mkdir(parents=True, exist_ok=True)
    CACHE_ROOT.mkdir(parents=True, exist_ok=True)
    if DB_PATH.exists():
        DB_PATH.unlink()

    env = os.environ.copy()
    env["PHOTOARCHIVE_SMOKE_MODE"] = "1"
    env["PHOTOARCHIVE_DB_PATH"] = str(DB_PATH)
    env["PYTHONPATH"] = str(WEB_ROOT)

    script = r"""
import asyncio, time
import db, thumbnails

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
        ("/tmp/quiet-personal", "Personal Photos", now, now),
    )
    personal_id = int((await (await conn.execute("SELECT last_insert_rowid()")).fetchone())[0])
    await conn.execute(
        "INSERT INTO catalog_sources "
        "(path, display_name, included, online, image_count, active_image_count, "
        "created_at, last_seen_at) VALUES (?, ?, 1, 1, 1, 1, ?, ?)",
        ("/tmp/quiet-archive", "Archive", now, now),
    )
    archive_id = int((await (await conn.execute("SELECT last_insert_rowid()")).fetchone())[0])
    for index in range(2):
        await conn.execute(
            "INSERT INTO images "
            "(source_id, filename, filepath, status, file_ext, elo, date_taken) "
            "VALUES (?, ?, ?, 'kept', '.jpg', ?, ?)",
            (personal_id, f"snap-{index}.jpg",
             f"/tmp/quiet-personal/snap-{index}.jpg", 1200 + index,
             f"2026-07-12T1{index}:00:00"),
        )
    await conn.execute(
        "INSERT INTO images "
        "(source_id, filename, filepath, status, file_ext, elo, date_taken) "
        "VALUES (?, ?, ?, 'kept', '.jpg', ?, ?)",
        (archive_id, "keep.jpg", "/tmp/quiet-archive/keep.jpg", 1500, "2026-07-12T12:00:00"),
    )
    await conn.commit()
    await conn.close()
    print("seeded", personal_id, archive_id)

asyncio.run(main())
""" % (str(DB_PATH), str(CACHE_ROOT))
    subprocess.check_call(
        [sys.executable, "-c", script],
        cwd=str(WEB_ROOT),
        env=env,
    )


def _wait_ready(url: str, timeout: float = 30.0) -> None:
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
def test_quiet_source_sidebar_toggle_screenshot():
    _seed_catalog()
    env = os.environ.copy()
    env.update({
        "PHOTOARCHIVE_SMOKE_MODE": "1",
        "PHOTOARCHIVE_DB_PATH": str(DB_PATH),
        "PHOTOARCHIVE_CACHE_DIR": str(CACHE_ROOT),
        "PHOTOARCHIVE_PORT": str(PORT),
        "PYTHONPATH": str(WEB_ROOT),
    })
    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app:app", "--host", "127.0.0.1", "--port", str(PORT)],
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
            page.wait_for_selector('#source-list [data-source]')
            personal = page.locator('#source-list [data-source*="quiet-personal"]')
            personal.hover()
            personal.locator('[data-quiet-toggle]').click()
            page.wait_for_selector('#source-list .source-row.is-quiet')
            page.locator('#source-list').screenshot(path=str(SCREENSHOT))
            assert SCREENSHOT.exists()
            assert personal.locator('.nr-count').inner_text().startswith('(')
            browser.close()
    finally:
        os.killpg(server.pid, signal.SIGTERM)
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(server.pid, signal.SIGKILL)


if __name__ == "__main__":
    # Allow `python test_quiet_sources_playwright.py` for a one-shot lane proof.
    raise SystemExit(pytest.main([__file__, "-q"]))
