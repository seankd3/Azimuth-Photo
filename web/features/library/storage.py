"""Archive storage facts for the Library surface."""

from __future__ import annotations

import shutil
import time
from pathlib import Path

import db
from data import connection
from features.imports import staging


OVERVIEW_CACHE_TTL_SECONDS = 60.0
_overview_cache = {"value": None, "expires": 0.0}


def invalidate_overview_cache() -> None:
    _overview_cache["value"] = None
    _overview_cache["expires"] = 0.0


def _disk_path(home_path: Path) -> Path:
    candidate = home_path
    while not candidate.exists() and candidate.parent != candidate:
        candidate = candidate.parent
    return candidate


async def _archive_totals() -> tuple[int, int]:
    conn = await connection.open_async(db.DB_PATH)
    try:
        cursor = await conn.execute(
            "SELECT COUNT(*) AS photo_count, COALESCE(SUM(file_size), 0) AS originals_bytes "
            "FROM images WHERE status IN ('kept', 'maybe') AND missing_at IS NULL"
        )
        row = await cursor.fetchone()
        return int(row["photo_count"] or 0), int(row["originals_bytes"] or 0)
    finally:
        await connection.close_async(conn, db_path=db.DB_PATH)


async def overview_payload() -> dict:
    now = time.monotonic()
    cached = _overview_cache["value"]
    if cached is not None and float(_overview_cache["expires"]) > now:
        return dict(cached)

    home = staging.originals_root()
    photo_count, originals_bytes = await _archive_totals()
    disk_path = _disk_path(home)
    try:
        usage = shutil.disk_usage(disk_path)
        disk_free_bytes = int(usage.free)
        disk_total_bytes = int(usage.total)
    except OSError:
        disk_free_bytes = None
        disk_total_bytes = None

    payload = {
        "home_path": str(home),
        "photo_count": photo_count,
        "originals_bytes": originals_bytes,
        "disk_free_bytes": disk_free_bytes,
        "disk_total_bytes": disk_total_bytes,
        "disk_label": home.name or home.anchor or None,
    }
    _overview_cache["value"] = payload
    _overview_cache["expires"] = now + OVERVIEW_CACHE_TTL_SECONDS
    return dict(payload)
