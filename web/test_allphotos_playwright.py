"""Playwright proof: All Photos grid count matches catalog total."""

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

# Guard at import time — before the module-scoped fixture boots uvicorn — so a
# missing playwright is one visible module skip, not a 30s boot-then-skip.
pytest.importorskip(
    "playwright.sync_api",
    reason="playwright is not installed; browser proofs are opt-in",
)

WEB_ROOT = Path(__file__).resolve().parent
EXPECTED_TOTAL = 27  # 3 local + 24 hub across many dates


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
    env["AZIMUTH_SMOKE_MODE"] = "1"
    env["AZIMUTH_DB_PATH"] = str(db_path)
    env["AZIMUTH_MODE"] = "satellite"
    env["AZIMUTH_HUB_URL"] = "http://stub-hub"
    env["PYTHONPATH"] = str(WEB_ROOT)

    script = r"""
import asyncio, time
from pathlib import Path
import db, thumbnails
from data.repositories import catalog as catalog_repository

async def main():
    db.DB_PATH = %r
    thumbnails.SSD_CACHE_DIR = %r
    await db.init_db()
    conn = await db.get_db()
    now = time.time()
    await conn.execute(
        "INSERT INTO catalog_sources "
        "(path, display_name, included, online, image_count, active_image_count, "
        "created_at, last_seen_at) VALUES (?, ?, 1, 1, 3, 3, ?, ?)",
        ("/tmp/allphotos-local", "Local imports", now, now),
    )
    local_id = int((await (await conn.execute("SELECT last_insert_rowid()")).fetchone())[0])
    # Intentionally stale hub:// counters — the bug under test.
    await conn.execute(
        "INSERT INTO catalog_sources "
        "(path, display_name, included, online, image_count, active_image_count, "
        "created_at, last_seen_at) VALUES (?, ?, 1, 1, 0, 0, ?, ?)",
        (catalog_repository.HUB_MIRROR_SOURCE_PATH, "Hub library", now, now),
    )
    hub_id = int((await (await conn.execute("SELECT last_insert_rowid()")).fetchone())[0])
    for index in range(3):
        await conn.execute(
            "INSERT INTO images "
            "(source_id, filename, filepath, status, file_ext, elo, date_taken, hub_remote) "
            "VALUES (?, ?, ?, 'kept', '.jpg', ?, ?, 0)",
            (local_id, f"local-{index}.jpg",
             f"/tmp/allphotos-local/local-{index}.jpg", 1500 + index,
             f"2026-07-12T1{index}:00:00"),
        )
    for index in range(24):
        year = 2020 + (index // 12)
        month = (index %% 12) + 1
        await conn.execute(
            "INSERT INTO images "
            "(source_id, filename, filepath, status, file_ext, elo, date_taken, "
            "hub_remote, hub_image_id) "
            "VALUES (?, ?, ?, 'kept', '.jpg', ?, ?, 1, ?)",
            (hub_id, f"hub-{index}.jpg", f"/hub/archive/hub-{index}.jpg",
             1100 + index, f"{year}-{month:02d}-15T12:00:00", 9000 + index),
        )
    await conn.commit()
    await conn.close()
    print("seeded", %d)

asyncio.run(main())
""" % (str(db_path), str(cache_root), EXPECTED_TOTAL)

    subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        cwd=str(WEB_ROOT),
        env=env,
    )


def _wait_healthy(base_url: str, timeout: float = 30.0) -> None:
    deadline = time.time() + timeout
    last_error = ""
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{base_url}/api/dev/status", timeout=1) as response:
                if response.status == 200:
                    return
        except (urllib.error.URLError, TimeoutError) as exc:
            last_error = str(exc)
        time.sleep(0.25)
    raise RuntimeError(f"server on {base_url} did not become healthy: {last_error}")


@pytest.fixture(scope="module")
def allphotos_server():
    scratch = worker_scratch("allphotos-playwright")
    db_path = scratch / "catalog.db"
    cache_root = scratch / "thumbs"
    screenshot = scratch / "allphotos-grid.png"
    port = free_port()
    base_url = f"http://127.0.0.1:{port}"
    _seed_catalog(db_path, cache_root)
    env = os.environ.copy()
    env.update(
        {
            "AZIMUTH_SMOKE_MODE": "1",
            "AZIMUTH_DB_PATH": str(db_path),
            "AZIMUTH_MODE": "satellite",
            "AZIMUTH_HUB_URL": "http://stub-hub",
            "AZIMUTH_PORT": str(port),
            "AZIMUTH_HOST": "127.0.0.1",
            "AZIMUTH_ACCESS": "local",
            "TMPDIR": str(scratch),
            "PYTHONPATH": str(WEB_ROOT),
        }
    )
    log_path = scratch / "server.log"
    log_file = open(log_path, "w", encoding="utf-8")
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=str(WEB_ROOT),
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    try:
        _wait_healthy(base_url)
        yield base_url, screenshot
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
        log_file.close()


def test_all_photos_grid_matches_catalog_total(allphotos_server):
    base_url, screenshot = allphotos_server
    playwright = pytest.importorskip("playwright.sync_api").sync_playwright

    with urllib.request.urlopen(f"{base_url}/api/rankings?limit=100&sort=elo") as response:
        payload = __import__("json").loads(response.read().decode())
    assert payload["visible_images"] == EXPECTED_TOTAL
    assert payload["total_images"] == EXPECTED_TOTAL
    assert len(payload["images"]) == EXPECTED_TOTAL
    months = {str(img.get("date_taken") or "")[:7] for img in payload["images"]}
    assert "2020-01" in months
    assert "2021-12" in months
    assert "2026-07" in months

    with playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        context = browser.new_context(viewport={"width": 1440, "height": 1100})
        page = context.new_page()
        page.goto(f"{base_url}/d", wait_until="networkidle", timeout=60000)
        page.wait_for_selector("#library-list .nav-item, #library-list button, #library-list [data-id]", timeout=30000)
        # Prefer explicit All photos control; fall back to clearing scope via label text.
        all_photos = page.locator("#library-list").get_by_text("All photos", exact=False)
        if all_photos.count():
            all_photos.first.click()
        page.wait_for_function(
            """() => {
                const count = document.getElementById('ctx-count');
                return count && /\\d+/.test(count.textContent || '');
            }""",
            timeout=30000,
        )
        # Scroll the grid so virtualization can settle, then read live meta.
        page.locator("#grid-flow, #stage, main").first.evaluate(
            "el => { el.scrollTop = el.scrollHeight; }"
        )
        page.wait_for_timeout(500)
        visible = page.evaluate(
            """() => {
                const text = document.getElementById('ctx-count')?.textContent || '';
                const match = text.replace(/,/g, '').match(/(\\d+)/);
                return match ? Number(match[1]) : 0;
            }"""
        )
        cell_count = page.locator(".cell[data-id]").count()
        screenshot.parent.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(screenshot), full_page=True)
        browser.close()

    assert visible == EXPECTED_TOTAL, f"ctx-count={visible} expected={EXPECTED_TOTAL}"
    assert cell_count >= min(EXPECTED_TOTAL, 20), f"grid cells={cell_count}"


if __name__ == "__main__":
    # Allow `python test_allphotos_playwright.py` for a one-shot lane proof.
    sys.exit(pytest.main([__file__, "-q", "--tb=short"]))
