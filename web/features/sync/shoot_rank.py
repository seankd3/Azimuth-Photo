"""Per-shoot Elo rank for LR Best-of collections + contextual whisper.

elo_stars stays career-global. Local excellence is set membership + copy only.
Shoot grouping = parent capture-date folder (import date_shoot destination),
matching how imports already land files — no new grouping invented.
"""

from __future__ import annotations

import math
from typing import Any

from data import connection

# Top fraction of a shoot (by Elo) that counts as Best of <shoot>.
# Matches Azimuth desktop Best-of (ceil(n * 0.2)).
BEST_OF_SHOOT_FRACTION = 0.20

# Shoots smaller than this never get a Best-of collection / is_best_of_shoot.
# Below this, "top 20%" is noise, not a meaningful local ladder.
BEST_OF_SHOOT_MIN_SIZE = 5


def shoot_key_from_filepath(filepath: str) -> str:
    """Stable shoot key: parent directory of the photo path."""

    path = (filepath or "").replace("\\", "/").rstrip("/")
    if not path:
        return ""
    parent, sep, _name = path.rpartition("/")
    if not sep:
        return ""
    return parent


def shoot_title_from_key(shoot_key: str) -> str:
    """Human label for a shoot key — folder basename."""

    key = (shoot_key or "").replace("\\", "/").rstrip("/")
    if not key:
        return "Shoot"
    _parent, _sep, name = key.rpartition("/")
    return name or key or "Shoot"


def best_of_cutoff(shoot_size: int, fraction: float = BEST_OF_SHOOT_FRACTION) -> int:
    """How many photos in a shoot of ``shoot_size`` are Best-of (0 if too small)."""

    size = int(shoot_size or 0)
    if size < BEST_OF_SHOOT_MIN_SIZE:
        return 0
    return max(1, int(math.ceil(size * float(fraction))))


def annotate_shoot_ranks(
    rows: list[dict[str, Any]],
    *,
    fraction: float = BEST_OF_SHOOT_FRACTION,
    min_size: int = BEST_OF_SHOOT_MIN_SIZE,
) -> list[dict[str, Any]]:
    """Assign rank_in_shoot / shoot_size / is_best_of_shoot.

    ``rows`` need filepath + elo (+ optional id for stable ties).
    rank_in_shoot is 1-based (highest Elo = 1) for whisper copy.
    """

    buckets: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        filepath = str(row.get("filepath") or "")
        key = shoot_key_from_filepath(filepath)
        if not key or not filepath:
            continue
        buckets.setdefault(key, []).append(dict(row))

    out: list[dict[str, Any]] = []
    for key, members in buckets.items():
        members.sort(
            key=lambda r: (-float(r.get("elo") or 0.0), int(r.get("id") or 0), str(r.get("filepath") or ""))
        )
        size = len(members)
        cutoff = best_of_cutoff(size, fraction) if size >= min_size else 0
        title = shoot_title_from_key(key)
        for index, member in enumerate(members):
            rank = index + 1
            out.append(
                {
                    "filepath": str(member.get("filepath") or ""),
                    "content_hash": str(member.get("content_hash") or "") or None,
                    "image_id": int(member["id"]) if member.get("id") is not None else None,
                    "shoot_key": key,
                    "shoot_title": title,
                    "rank_in_shoot": rank,
                    "shoot_size": size,
                    "is_best_of_shoot": bool(cutoff and rank <= cutoff),
                }
            )
    out.sort(key=lambda item: (item["shoot_key"], item["rank_in_shoot"], item["filepath"]))
    return out


def best_of_collections_from_ranks(
    ranks: list[dict[str, Any]],
    *,
    min_size: int = BEST_OF_SHOOT_MIN_SIZE,
) -> list[dict[str, Any]]:
    """Group is_best_of_shoot members into collection payloads for the plugin."""

    by_shoot: dict[str, dict[str, Any]] = {}
    for row in ranks:
        if not row.get("is_best_of_shoot"):
            continue
        if int(row.get("shoot_size") or 0) < min_size:
            continue
        key = str(row["shoot_key"])
        bucket = by_shoot.setdefault(
            key,
            {
                "shoot_key": key,
                "shoot_title": str(row.get("shoot_title") or shoot_title_from_key(key)),
                "shoot_size": int(row.get("shoot_size") or 0),
                "filepaths": [],
            },
        )
        path = str(row.get("filepath") or "")
        if path and path not in bucket["filepaths"]:
            bucket["filepaths"].append(path)
    collections = list(by_shoot.values())
    collections.sort(key=lambda item: item["shoot_title"].lower())
    return collections


# The ranked-library scan is too heavy to run per poll / per rating request.
# A short TTL keeps both consumers O(1) between refreshes; membership and
# whisper copy lagging up to a minute is imperceptible in LR.
_PAYLOAD_TTL_SECONDS = 60.0
_payload_cache: dict[str, Any] = {"db": None, "at": 0.0, "payload": None, "by_path": {}}


def invalidate_payload_cache() -> None:
    _payload_cache.update(db=None, at=0.0, payload=None, by_path={})


async def shoot_rank_payload(db_path: str) -> dict[str, Any]:
    """Ranked-photo shoot context for bridge status / deltas (TTL-cached)."""

    import time as time_mod

    now = time_mod.monotonic()
    if (
        _payload_cache["payload"] is not None
        and _payload_cache["db"] == db_path
        and now - _payload_cache["at"] < _PAYLOAD_TTL_SECONDS
    ):
        return _payload_cache["payload"]

    from features.sync import elo_stars as elo_stars_mod

    min_n, _thresholds = elo_stars_mod._settings_projection()
    conn = await connection.open_async(db_path)
    try:
        rows = await (
            await conn.execute(
                """
                SELECT id, filepath, content_hash, elo, comparisons
                FROM images
                WHERE filepath IS NOT NULL AND TRIM(filepath) != ''
                  AND COALESCE(missing_at, 0) = 0
                  AND LOWER(COALESCE(status, 'kept')) NOT IN ('trashed', 'deleted')
                  AND COALESCE(comparisons, 0) >= ?
                ORDER BY elo DESC, id ASC
                """,
                (min_n,),
            )
        ).fetchall()
    finally:
        await connection.close_async(conn, db_path=db_path)

    annotated = annotate_shoot_ranks([dict(row) for row in rows])
    collections = best_of_collections_from_ranks(annotated)
    payload = {
        "fraction": BEST_OF_SHOOT_FRACTION,
        "min_size": BEST_OF_SHOOT_MIN_SIZE,
        "photos": annotated,
        "best_of_shoots": collections,
    }
    _payload_cache.update(
        db=db_path,
        at=now,
        payload=payload,
        by_path={str(r["filepath"]).replace("\\", "/"): int(r["rank_in_shoot"]) for r in annotated},
    )
    return payload


def compose_star_whisper(global_whisper: str | None, rank_in_shoot: int | None) -> str | None:
    """Mirror of Lua Core.compose_star_whisper — global band + optional shoot rank."""

    base = (global_whisper or "").strip()
    if not base:
        return None
    try:
        rank = int(rank_in_shoot) if rank_in_shoot is not None else 0
    except (TypeError, ValueError):
        rank = 0
    if rank > 0:
        return f"{base} · #{rank} in this shoot"
    return base


async def rank_in_shoot_for_filepath(db_path: str, filepath: str) -> int | None:
    """1-based Elo rank within the photo's shoot, or None if unranked / unknown."""

    path = (filepath or "").replace("\\", "/")
    if not path:
        return None
    await shoot_rank_payload(db_path)  # refresh cache if stale
    return _payload_cache["by_path"].get(path)

