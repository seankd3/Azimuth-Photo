"""Playwright regression proof for leaving Trash on a satellite-sized catalog."""

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


WEB_ROOT = Path(__file__).resolve().parent
SCRATCH = Path("/mnt/expansion/tmp/navhang")
DB_PATH = SCRATCH / "catalog.db"
PORT = 8148
BASE_URL = f"http://127.0.0.1:{PORT}"
ACTIVE_IMAGES = 139_000
TRASHED_IMAGES = 12


def _seed_catalog() -> None:
    SCRATCH.mkdir(parents=True, exist_ok=True)
    (SCRATCH / "source").mkdir(exist_ok=True)
    for suffix in ("", "-wal", "-shm"):
        Path(f"{DB_PATH}{suffix}").unlink(missing_ok=True)
    env = os.environ.copy()
    env.update(
        {
            "PHOTOARCHIVE_SMOKE_MODE": "1",
            "PHOTOARCHIVE_DB_PATH": str(DB_PATH),
            "PHOTOARCHIVE_MODE": "satellite",
            "PHOTOARCHIVE_HUB_URL": "http://stub-hub",
            "PYTHONPATH": str(WEB_ROOT),
        }
    )
    script = r"""
import asyncio
import time

import db


async def main():
    await db.init_db()
    conn = await db.get_db()
    await conn.execute("PRAGMA synchronous = OFF")
    await conn.execute("PRAGMA journal_mode = OFF")
    await conn.execute("DROP TRIGGER IF EXISTS images_metadata_fts_ai")
    await conn.execute("DROP TRIGGER IF EXISTS images_metadata_fts_ad")
    await conn.execute("DROP TRIGGER IF EXISTS images_metadata_fts_au")
    await conn.execute("DROP TRIGGER IF EXISTS images_row_version_ai")
    indexes = await (await conn.execute(
        "SELECT name, sql FROM sqlite_master "
        "WHERE type = 'index' AND tbl_name = 'images' AND sql IS NOT NULL"
    )).fetchall()
    for name, _sql in indexes:
        await conn.execute(f'DROP INDEX IF EXISTS "{name}"')
    now = time.time()
    await conn.execute(
        "INSERT INTO catalog_sources "
        "(path, display_name, included, online, image_count, active_image_count, created_at, last_seen_at) "
        "VALUES (?, ?, 1, 1, ?, ?, ?, ?)",
        (str(SOURCE), "Satellite library", ACTIVE + TRASHED, ACTIVE, now, now),
    )
    source_id = int((await (await conn.execute("SELECT last_insert_rowid()")).fetchone())[0])
    rows = []
    for index in range(ACTIVE + TRASHED):
        trashed = index >= ACTIVE
        rows.append((
            source_id,
            f"photo-{index:06d}.jpg",
            f"{SOURCE}/photo-{index:06d}.jpg",
            "trashed" if trashed else "kept",
            1200 + (index %% 700),
            f"2026-01-{(index %% 28) + 1:02d}T12:00:00",
            now if trashed else None,
        ))
    await conn.executemany(
        "INSERT INTO images "
        "(source_id, filename, filepath, status, file_ext, elo, date_taken, trashed_at) "
        "VALUES (?, ?, ?, ?, '.jpg', ?, ?, ?)",
        rows,
    )
    await conn.commit()
    for _name, sql in indexes:
        await conn.execute(sql)
    await conn.execute("PRAGMA journal_mode = WAL")
    await conn.close()


ACTIVE = %d
TRASHED = %d
SOURCE = %r
asyncio.run(main())
""" % (ACTIVE_IMAGES, TRASHED_IMAGES, str(SCRATCH / "source"))
    subprocess.run(
        [str(WEB_ROOT / ".venv/bin/python"), "-c", script],
        check=True,
        cwd=str(WEB_ROOT),
        env=env,
    )


def _wait_healthy(timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    last_error = ""
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"{BASE_URL}/api/dev/status", timeout=1) as response:
                if response.status == 200:
                    return
        except (urllib.error.URLError, TimeoutError) as exc:
            last_error = str(exc)
        time.sleep(0.25)
    raise RuntimeError(f"server on {BASE_URL} did not become healthy: {last_error}")


@pytest.fixture(scope="module")
def satellite_server():
    _seed_catalog()
    env = os.environ.copy()
    env.update(
        {
            "PHOTOARCHIVE_SMOKE_MODE": "1",
            "PHOTOARCHIVE_DB_PATH": str(DB_PATH),
            "PHOTOARCHIVE_MODE": "satellite",
            "PHOTOARCHIVE_HUB_URL": "http://stub-hub",
            "PHOTOARCHIVE_PORT": str(PORT),
            "PHOTOARCHIVE_HOST": "127.0.0.1",
            "PHOTOARCHIVE_ACCESS": "local",
            "TMPDIR": "/mnt/expansion/tmp",
            "PYTHONPATH": str(WEB_ROOT),
        }
    )
    log_path = SCRATCH / "server.log"
    with log_path.open("w", encoding="utf-8") as log_file:
        proc = subprocess.Popen(
            [
                str(WEB_ROOT / ".venv/bin/uvicorn"),
                "app:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(PORT),
            ],
            cwd=str(WEB_ROOT),
            env=env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            _wait_healthy()
            yield BASE_URL
        finally:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait(timeout=5)


def test_empty_trash_then_all_photos_loads_promptly(satellite_server):
    playwright = pytest.importorskip("playwright.sync_api").sync_playwright

    with playwright() as manager:
        browser = manager.chromium.launch(headless=True, args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.route("**/api/thumb/**", lambda route: route.abort())
        ranking_requests: list[float] = []
        page.on(
            "request",
            lambda request: ranking_requests.append(time.monotonic())
            if "/api/rankings?" in request.url
            else None,
        )
        page.goto(f"{satellite_server}/d", wait_until="domcontentloaded", timeout=60_000)
        page.wait_for_selector("#grid-flow .cell[data-id]", timeout=30_000)

        page.locator('[data-lib="trash"]').click()
        page.wait_for_selector("#view-trash.active .trash-cell", timeout=10_000)
        assert str(TRASHED_IMAGES) in page.locator("#trash-count").inner_text()

        requests_before_view_navigation = len(ranking_requests)
        view_started = time.monotonic()
        page.locator('[data-lib="all"]').click()
        page.wait_for_function(
            "() => document.querySelector('#view-grid')?.classList.contains('active')",
            timeout=500,
        )
        assert len(ranking_requests) > requests_before_view_navigation
        page.wait_for_selector("#grid-flow .cell[data-id]", timeout=10_000)
        view_navigation_ms = (time.monotonic() - view_started) * 1000

        page.locator('[data-lib="trash"]').click()
        page.wait_for_selector("#view-trash.active .trash-cell", timeout=10_000)

        empty_started = time.monotonic()
        with page.expect_response(lambda response: response.url.endswith("/api/trash/empty")) as response_info:
            page.locator("#trash-empty").click()
            page.locator(".typed-confirm input").fill(str(TRASHED_IMAGES))
            page.locator(".typed-confirm [data-confirm]").click()
        empty_response = response_info.value
        empty_payload = empty_response.json()
        empty_ms = (time.monotonic() - empty_started) * 1000
        assert empty_response.ok
        assert empty_payload["deleted_count"] == TRASHED_IMAGES
        page.wait_for_function(
            "() => document.querySelector('#trash-count')?.textContent?.trim().startsWith('0 photos')",
            timeout=10_000,
        )

        requests_before_navigation = len(ranking_requests)
        started = time.monotonic()
        page.locator('[data-lib="all"]').click()
        page.wait_for_function(
            "() => document.querySelector('#view-grid')?.classList.contains('active')",
            timeout=500,
        )
        assert len(ranking_requests) > requests_before_navigation
        page.wait_for_selector("#grid-flow .cell[data-id]", timeout=10_000)
        navigation_ms = (time.monotonic() - started) * 1000
        assert view_navigation_ms < 10_000
        assert navigation_ms < 10_000
        print(
            f"empty_ms={empty_ms:.1f} view_navigation_ms={view_navigation_ms:.1f} "
            f"post_empty_navigation_ms={navigation_ms:.1f}"
        )
        browser.close()


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q", "-s", "--tb=short"]))
