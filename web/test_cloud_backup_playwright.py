"""Playwright smoke: Cloud Backup panel renders and config saves."""

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
SCRATCH = Path("/tmp/cloud-backup-playwright")
DB_PATH = SCRATCH / "catalog.db"
CACHE_ROOT = SCRATCH / "thumbs"
SETTINGS_PATH = SCRATCH / "settings.json"
RCLONE_CONF = SCRATCH / "rclone.conf"
TREE = SCRATCH / "library" / "RAWS"
PORT = 8159
BASE_URL = f"http://127.0.0.1:{PORT}"


def _seed() -> None:
    if SCRATCH.exists():
        for path in sorted(SCRATCH.rglob("*"), reverse=True):
            if path.is_file():
                path.unlink()
            elif path.is_dir():
                path.rmdir()
    TREE.mkdir(parents=True, exist_ok=True)
    CACHE_ROOT.mkdir(parents=True, exist_ok=True)
    (TREE / "sample.jpg").write_bytes(b"sample")
    RCLONE_CONF.write_text("[cloudproof]\ntype = local\n", encoding="utf-8")
    SETTINGS_PATH.write_text("{}", encoding="utf-8")

    env = os.environ.copy()
    env.update(
        {
            "AZIMUTH_SMOKE_MODE": "1",
            "AZIMUTH_DB_PATH": str(DB_PATH),
            "AZIMUTH_SETTINGS_PATH": str(SETTINGS_PATH),
            "PYTHONPATH": str(WEB_ROOT),
            "RCLONE_CONFIG": str(RCLONE_CONF),
        }
    )
    script = r"""
import asyncio, time
import db, thumbnails, settings

async def main():
    db.DB_PATH = %r
    thumbnails.SSD_CACHE_DIR = %r
    settings.SETTINGS_PATH = %r
    settings.reset_settings()
    await db.init_db()
    conn = await db.get_db()
    now = time.time()
    await conn.execute(
        "INSERT INTO catalog_sources "
        "(path, display_name, included, online, image_count, active_image_count, "
        "created_at, last_seen_at) VALUES (?, ?, 1, 1, 1, 1, ?, ?)",
        (%r, "RAWS", now, now),
    )
    source_id = int((await (await conn.execute("SELECT last_insert_rowid()")).fetchone())[0])
    await conn.execute(
        "INSERT INTO images "
        "(source_id, filename, filepath, status, file_ext, elo, date_taken) "
        "VALUES (?, ?, ?, 'kept', '.jpg', 1200, ?)",
        (source_id, "sample.jpg", %r, "2026-07-19T12:00:00"),
    )
    await conn.commit()
    await conn.close()
    print("seeded", source_id)

asyncio.run(main())
""" % (
        str(DB_PATH),
        str(CACHE_ROOT),
        str(SETTINGS_PATH),
        str(TREE),
        str(TREE / "sample.jpg"),
    )
    subprocess.check_call([sys.executable, "-c", script], cwd=str(WEB_ROOT), env=env)


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
def test_cloud_backup_panel_renders_and_saves_config():
    _seed()
    env = os.environ.copy()
    env.update(
        {
            "AZIMUTH_SMOKE_MODE": "1",
            "AZIMUTH_DB_PATH": str(DB_PATH),
            "AZIMUTH_CACHE_DIR": str(CACHE_ROOT),
            "AZIMUTH_SETTINGS_PATH": str(SETTINGS_PATH),
            "AZIMUTH_PORT": str(PORT),
            "PYTHONPATH": str(WEB_ROOT),
            "RCLONE_CONFIG": str(RCLONE_CONF),
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
        _wait_ready(f"{BASE_URL}/api/backup/cloud/status")
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.goto(f"{BASE_URL}/", wait_until="networkidle")
            page.click("#system-btn")
            page.wait_for_selector("#cloud-backup-remote", timeout=15000)
            heading = page.locator("#cloud-backup-panel h3").inner_text().strip().lower()
            assert heading == "cloud backup"
            page.select_option("#cloud-backup-remote", "cloudproof")
            page.fill("#cloud-backup-prefix", "AzimuthVault")
            page.locator("#cloud-backup-panel .cloud-backup-tree").first.click()
            page.click("#cloud-backup-panel [data-cloud-save]")
            page.wait_for_function(
                """() => fetch('/api/backup/cloud/status').then(r => r.json())
                    .then(s => s.config && s.config.remote === 'cloudproof')""",
                timeout=10000,
            )
            status = page.request.get(f"{BASE_URL}/api/backup/cloud/status").json()
            assert status["config"]["remote"] == "cloudproof"
            assert status["config"]["dest_prefix"] == "AzimuthVault"
            assert any(path.endswith("RAWS") for path in status["config"]["trees"])
            browser.close()
    finally:
        os.killpg(server.pid, signal.SIGTERM)
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(server.pid, signal.SIGKILL)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
