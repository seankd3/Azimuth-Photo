"""Elo → LR star projection (read-time, short-cached).

Thresholds default to top 2% / next 8% / next 20% (cumulative 2/10/30).
Only photos with ≥N comparisons project; predicted-only Elo does not.

Outbound also emits clear-to-zero when a previously projected photo drops
below threshold — the server tracks last-emitted projections so demotions
reach Lightroom even though the live projection map omits zeros.
"""

from __future__ import annotations

import threading
import time
from typing import Any

from data import connection

DEFAULT_MIN_COMPARISONS = 3
# Cumulative percentile ceilings for 5★ / 4★ / 3★.
DEFAULT_THRESHOLDS = (0.02, 0.10, 0.30)
_CACHE_TTL_SECONDS = 15.0

# Hover whisper copy for each projected band (matches default thresholds).
_WHISPER_BY_STARS = {
    5: "Top 2% of your ranked photos",
    4: "Top 10% of your ranked photos",
    3: "Top 30% of your ranked photos",
}


def whisper_for_stars(stars: int, thresholds: tuple[float, float, float] | None = None) -> str | None:
    """Title/tooltip copy for an elo_stars projection band."""

    value = int(stars or 0)
    if value <= 0:
        return None
    bands = thresholds if thresholds is not None else _settings_projection()[1]
    # Map star tier → cumulative ceiling label ("top N%").
    if value >= 5:
        pct = max(1, int(round(bands[0] * 100)))
    elif value == 4:
        pct = max(1, int(round(bands[1] * 100)))
    elif value == 3:
        pct = max(1, int(round(bands[2] * 100)))
    else:
        return _WHISPER_BY_STARS.get(value)
    return f"Top {pct}% of your ranked photos"

PROJECTION_LEDGER_DDL = """
CREATE TABLE IF NOT EXISTS elo_stars_last_projected (
    content_hash TEXT PRIMARY KEY,
    filepath TEXT NOT NULL,
    stars INTEGER NOT NULL,
    updated_at REAL NOT NULL
);
"""

_lock = threading.Lock()
_cache: dict[str, Any] = {"key": None, "expires": 0.0, "by_hash": {}, "by_id": {}}


def _settings_projection() -> tuple[int, tuple[float, float, float]]:
    try:
        import settings as app_settings

        cfg = app_settings.get_settings()
    except Exception:
        return DEFAULT_MIN_COMPARISONS, DEFAULT_THRESHOLDS
    try:
        min_n = max(1, int(cfg.get("elo_stars_min_comparisons", DEFAULT_MIN_COMPARISONS)))
    except (TypeError, ValueError):
        min_n = DEFAULT_MIN_COMPARISONS
    raw = cfg.get("elo_stars_thresholds")
    if isinstance(raw, (list, tuple)) and len(raw) >= 3:
        try:
            thresholds = tuple(sorted(float(raw[i]) for i in range(3)))
            if 0 < thresholds[0] <= thresholds[1] <= thresholds[2] <= 1.0:
                return min_n, thresholds  # type: ignore[return-value]
        except (TypeError, ValueError):
            pass
    return min_n, DEFAULT_THRESHOLDS


def project_stars(rank_index: int, eligible_count: int, thresholds: tuple[float, float, float]) -> int:
    """Map a 0-based rank (0 = highest Elo) into 0–5 stars."""

    if eligible_count <= 0 or rank_index < 0 or rank_index >= eligible_count:
        return 0
    # Cut by count so "top 2%" of 100 is two photos; a lone eligible photo is top.
    if rank_index < eligible_count * thresholds[0]:
        return 5
    if rank_index < eligible_count * thresholds[1]:
        return 4
    if rank_index < eligible_count * thresholds[2]:
        return 3
    return 0


def _cache_key(db_path: str, min_n: int, thresholds: tuple[float, float, float]) -> tuple:
    return (db_path, min_n, thresholds)


def invalidate_elo_stars_cache() -> None:
    with _lock:
        _cache["key"] = None
        _cache["expires"] = 0.0
        _cache["by_hash"] = {}
        _cache["by_id"] = {}


async def _ensure_projection_ledger(db_path: str) -> None:
    conn = await connection.open_async(db_path)
    try:
        await conn.executescript(PROJECTION_LEDGER_DDL)
        await conn.commit()
    finally:
        await connection.close_async(conn, db_path=db_path)


async def _load_last_projected(db_path: str) -> dict[str, dict[str, Any]]:
    await _ensure_projection_ledger(db_path)
    conn = await connection.open_async(db_path)
    try:
        rows = await (
            await conn.execute(
                "SELECT content_hash, filepath, stars FROM elo_stars_last_projected"
            )
        ).fetchall()
        return {
            str(row["content_hash"]): {
                "filepath": str(row["filepath"] or ""),
                "stars": int(row["stars"]),
            }
            for row in rows
        }
    finally:
        await connection.close_async(conn, db_path=db_path)


async def _persist_projected(
    db_path: str,
    updates: list[tuple[str, str, int]],
) -> None:
    """Upsert last-emitted projections. stars=0 stays so we do not re-emit clears."""

    if not updates:
        return
    await _ensure_projection_ledger(db_path)
    now = time.time()
    conn = await connection.open_async(db_path)
    try:
        await conn.execute("BEGIN IMMEDIATE")
        for content_hash, filepath, stars in updates:
            await conn.execute(
                "INSERT INTO elo_stars_last_projected(content_hash, filepath, stars, updated_at) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(content_hash) DO UPDATE SET "
                "filepath=excluded.filepath, stars=excluded.stars, updated_at=excluded.updated_at",
                (content_hash, filepath, int(stars), now),
            )
        await conn.commit()
    except Exception:
        await conn.rollback()
        raise
    finally:
        await connection.close_async(conn, db_path=db_path)


async def _load_projection(db_path: str) -> tuple[dict[str, int], dict[int, int]]:
    min_n, thresholds = _settings_projection()
    key = _cache_key(db_path, min_n, thresholds)
    now = time.monotonic()
    with _lock:
        if _cache["key"] == key and now < float(_cache["expires"]):
            return dict(_cache["by_hash"]), dict(_cache["by_id"])

    conn = await connection.open_async(db_path)
    try:
        rows = await (
            await conn.execute(
                "SELECT id, content_hash, elo, comparisons FROM images "
                "WHERE content_hash IS NOT NULL "
                "AND COALESCE(missing_at, 0) = 0 "
                "AND LOWER(COALESCE(status, 'kept')) NOT IN ('trashed', 'deleted') "
                "AND COALESCE(comparisons, 0) >= ? "
                "ORDER BY elo DESC, id ASC",
                (min_n,),
            )
        ).fetchall()
    finally:
        await connection.close_async(conn, db_path=db_path)

    by_hash: dict[str, int] = {}
    by_id: dict[int, int] = {}
    eligible = len(rows)
    for index, row in enumerate(rows):
        stars = project_stars(index, eligible, thresholds)
        if stars <= 0:
            continue
        content_hash = str(row["content_hash"])
        image_id = int(row["id"])
        by_hash[content_hash] = stars
        by_id[image_id] = stars

    with _lock:
        _cache["key"] = key
        _cache["expires"] = time.monotonic() + _CACHE_TTL_SECONDS
        _cache["by_hash"] = by_hash
        _cache["by_id"] = by_id
    return dict(by_hash), dict(by_id)


async def elo_stars_for_hashes(db_path: str, content_hashes: list[str] | None = None) -> dict[str, int]:
    by_hash, _by_id = await _load_projection(db_path)
    if content_hashes is None:
        return by_hash
    wanted = {str(value) for value in content_hashes if value}
    return {key: value for key, value in by_hash.items() if key in wanted}


async def outbound_elo_star_deltas(
    db_path: str,
    *,
    since: float = 0.0,
    limit: int = 500,
) -> list[dict[str, Any]]:
    """Project Elo into star deltas for photos the LR plugin can path-match.

    Emits new/changed projections and clear-to-zero demotions for anything
    previously projected that dropped below threshold. ``since`` is accepted
    so the GET deltas cursor stays one number; projection freshness is the
    server-side last-emitted ledger, not the poll clock.
    """

    del since  # demotion tracking is ledger-based, not cursor-based
    by_hash, _ = await _load_projection(db_path)
    last = await _load_last_projected(db_path)
    limit = max(1, min(int(limit), 2000))

    conn = await connection.open_async(db_path)
    try:
        path_by_hash: dict[str, str] = {}
        needed = set(by_hash) | set(last)
        if needed:
            hashes = list(needed)
            for start in range(0, len(hashes), 400):
                chunk = hashes[start : start + 400]
                placeholders = ",".join("?" for _ in chunk)
                rows = await (
                    await conn.execute(
                        f"SELECT filepath, content_hash FROM images "
                        f"WHERE content_hash IN ({placeholders}) "
                        f"AND filepath IS NOT NULL AND TRIM(filepath) != ''",
                        chunk,
                    )
                ).fetchall()
                for row in rows:
                    path_by_hash[str(row["content_hash"])] = str(row["filepath"])
    finally:
        await connection.close_async(conn, db_path=db_path)

    now = time.time()
    candidates: list[tuple[str, str, int]] = []  # content_hash, filepath, stars

    for content_hash, stars in by_hash.items():
        filepath = path_by_hash.get(content_hash) or (last.get(content_hash) or {}).get("filepath") or ""
        if not filepath:
            continue
        prev = last.get(content_hash)
        if prev is not None and int(prev["stars"]) == int(stars):
            continue
        candidates.append((content_hash, filepath, int(stars)))

    for content_hash, prev in last.items():
        if content_hash in by_hash:
            continue
        if int(prev["stars"]) <= 0:
            continue
        filepath = path_by_hash.get(content_hash) or str(prev.get("filepath") or "")
        if not filepath:
            continue
        candidates.append((content_hash, filepath, 0))

    # Stable order: demotions first (clear sticky stars), then by filepath.
    candidates.sort(key=lambda item: (0 if item[2] == 0 else 1, item[1], item[0]))
    selected = candidates[:limit]

    deltas = [
        {
            "filepath": filepath,
            "content_hash": content_hash,
            "family": "elo_stars",
            "value": int(stars),
            "ts": now,
            "origin": "hub-projection",
            "origin_seq": 0,
        }
        for content_hash, filepath, stars in selected
    ]
    await _persist_projected(db_path, selected)
    return deltas
