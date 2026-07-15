"""Version-skew regression: a new satellite must never guess at old-hub Trash."""

from __future__ import annotations

import sqlite3

from qa.config import CATALOG_DB


_HUB_UPDATE_TOOLTIP = "The hub is running an older version — some actions are paused until it updates."


def _seed_mirrored_trash() -> None:
    """Make the UI process attempt a real scoped-forward decision, locally only."""

    with sqlite3.connect(CATALOG_DB, timeout=5) as conn:
        row = conn.execute("SELECT id FROM catalog_sources WHERE path = 'hub://' LIMIT 1").fetchone()
        if row is None:
            source_id = int(
                conn.execute(
                    "INSERT INTO catalog_sources(path, display_name, included, online) "
                    "VALUES ('hub://', 'Hub library', 1, 1)"
                ).lastrowid
            )
        else:
            source_id = int(row[0])
        conn.execute(
            "INSERT INTO images "
            "(source_id, filename, filepath, content_hash, status, trashed_at, file_ext, file_size, hub_image_id, hub_remote) "
            "VALUES (?, 'old-hub-trash.jpg', '/hub/old-hub-trash.jpg', ?, 'trashed', 1000, 'jpg', 1, 909, 1)",
            (source_id, "c" * 32),
        )
        conn.commit()


def handshake_skew(qa) -> None:
    assert qa.old_hub is not None, "skew scenario requires the isolated old-hub stub"
    _seed_mirrored_trash()
    qa.goto_desktop()

    qa.mark("wait for the amber hub-update sync chip")
    chip = qa.page.locator(".sync-chip.needs-update .sync-chip-button")
    chip.wait_for(state="visible")
    assert chip.get_attribute("title") == _HUB_UPDATE_TOOLTIP

    qa.mark("refuse scoped Empty Trash before any old-hub forward")
    response = qa.page.request.post(f"{qa.base_url}/api/trash/empty")
    assert response.status == 409, f"scoped Empty Trash should be paused: {response.status} {response.text()}"
    assert "older version" in response.json().get("detail", "")
    assert ("POST", "/api/trash/empty") not in qa.old_hub.requests, qa.old_hub.requests
