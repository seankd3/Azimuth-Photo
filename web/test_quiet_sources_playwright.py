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

from conftest import free_port, worker_scratch

pytestmark = [
    pytest.mark.skipif(
        os.name == "nt",
        reason="uses POSIX process groups for its isolated Playwright server",
    ),
    pytest.mark.slow,
    pytest.mark.playwright,
]

WEB_ROOT = Path(__file__).resolve().parent


def _seed_catalog(db_path: Path, cache_root: Path) -> None:
    scratch = db_path.parent
    if scratch.exists():
        for path in scratch.rglob("*"):
            if path.is_file():
                path.unlink()
    scratch.mkdir(parents=True, exist_ok=True)
    cache_root.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()

    env = os.environ.copy()
    env["PHOTOARCHIVE_SMOKE_MODE"] = "1"
    env["PHOTOARCHIVE_DB_PATH"] = str(db_path)
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
""" % (str(db_path), str(cache_root))
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
    scratch = worker_scratch("quiet-sources-playwright")
    db_path = scratch / "catalog.db"
    cache_root = scratch / "thumbs"
    screenshot = scratch / "quiet-sources-sidebar.png"
    port = free_port()
    base_url = f"http://127.0.0.1:{port}"
    _seed_catalog(db_path, cache_root)
    env = os.environ.copy()
    env.update({
        "PHOTOARCHIVE_SMOKE_MODE": "1",
        "PHOTOARCHIVE_DB_PATH": str(db_path),
        "PHOTOARCHIVE_CACHE_DIR": str(cache_root),
        "PHOTOARCHIVE_PORT": str(port),
        "PYTHONPATH": str(WEB_ROOT),
    })
    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=str(WEB_ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        _wait_ready(f"{base_url}/api/counts")
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.goto(f"{base_url}/", wait_until="networkidle")
            page.wait_for_selector('#folder-tree [data-folder-source-path]')
            personal = page.locator('#folder-tree [data-folder-source-path*="quiet-personal"]')
            personal.hover()
            personal.locator('[data-quiet-toggle]').click()
            page.wait_for_selector('#folder-tree .folder-source-row.is-quiet')
            page.locator('#folder-tree').screenshot(path=str(screenshot))
            assert screenshot.exists()
            assert personal.locator('.folder-count').inner_text().startswith('(')
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
