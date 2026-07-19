"""Lightroom peer deltas — filepath identity in, family-clock apply out.

Inbound flag/lr_rating changes reuse ``oplog.apply_entries`` verbatim with
origin ``lr``. No parallel clock or apply path.
"""

from __future__ import annotations

import os
import time
from typing import Any

from data import connection
from features.sync import family_clock, oplog

LR_ORIGIN = "lr"
SUPPORTED_INBOUND = {"flag": "flag", "lr_rating": "rating"}
FLAG_VALUES = {"picked", "unflagged", "rejected"}


def _normalize_path(filepath: str) -> str:
    raw = str(filepath or "").strip()
    if not raw:
        return ""
    return os.path.normpath(raw)


async def resolve_filepath(db_path: str, filepath: str) -> dict[str, Any] | None:
    """Map an absolute LR filepath to the satellite's local-image identity."""

    path = _normalize_path(filepath)
    if not path:
        return None
    conn = await connection.open_async(db_path)
    try:
        row = await (
            await conn.execute(
                "SELECT id, content_hash, filepath, flag FROM images "
                "WHERE filepath = ? AND content_hash IS NOT NULL "
                "ORDER BY id LIMIT 1",
                (path,),
            )
        ).fetchone()
        if row is None or not row["content_hash"]:
            return None
        return {
            "image_id": int(row["id"]),
            "content_hash": str(row["content_hash"]),
            "filepath": str(row["filepath"] or path),
            "flag": str(row["flag"] or "unflagged"),
        }
    finally:
        await connection.close_async(conn, db_path=db_path)


def _rating_value(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or int(value) != value:
        raise ValueError("lr_rating must be an integer 0-5")
    rating = int(value)
    if not 0 <= rating <= 5:
        raise ValueError("lr_rating must be an integer 0-5")
    return rating


async def apply_inbound_deltas(
    db_path: str,
    items: list[dict[str, Any]],
) -> dict[str, Any]:
    """Apply a batch of LR-observed flag/rating deltas through the shared oplog path."""

    pending: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    prepared: list[tuple[dict[str, Any], dict[str, Any]]] = []

    for index, raw in enumerate(items):
        if not isinstance(raw, dict):
            errors.append({"index": index, "error": "delta must be an object"})
            continue
        filepath = str(raw.get("filepath") or "").strip()
        family_in = str(raw.get("family") or "").strip()
        if family_in not in SUPPORTED_INBOUND:
            errors.append({"index": index, "filepath": filepath, "error": f"unsupported family: {family_in}"})
            continue
        identity = await resolve_filepath(db_path, filepath)
        if identity is None:
            pending.append({"filepath": filepath, "family": family_in, "value": raw.get("value")})
            continue
        try:
            if family_in == "flag":
                value = str(raw.get("value") or "")
                if value not in FLAG_VALUES:
                    raise ValueError("invalid flag value")
                payload = {"value": value}
            else:
                payload = {"value": _rating_value(raw.get("value"))}
            observed = raw.get("observed_at")
            ts = family_clock.timestamp_seconds(observed) if observed not in (None, "") else time.time()
            if ts <= 0:
                ts = time.time()
            prepared.append(
                (
                    identity,
                    {
                        "content_hash": identity["content_hash"],
                        "family": SUPPORTED_INBOUND[family_in],
                        "payload": payload,
                        "ts": ts,
                        "filepath": identity["filepath"],
                        "inbound_family": family_in,
                    },
                )
            )
        except ValueError as error:
            errors.append({"index": index, "filepath": filepath, "error": str(error)})

    if prepared:
        drafts = [
            {
                "content_hash": draft["content_hash"],
                "family": draft["family"],
                "payload": draft["payload"],
                "ts": draft["ts"],
            }
            for _identity, draft in prepared
        ]
        applied = await oplog.apply_origin_batch(
            db_path, drafts, origin=LR_ORIGIN, applied_from="lr-bridge"
        )
        entries = [
            {
                "origin": item["origin"],
                "origin_seq": item["origin_seq"],
                "content_hash": drafts[index]["content_hash"],
                "family": drafts[index]["family"],
                "ts": drafts[index]["ts"],
            }
            for index, item in enumerate(applied.get("entries") or [])
        ]
    else:
        applied = {"received": 0, "inserted": 0, "skipped_unhashed": 0, "entries": []}
        entries = []

    return {
        "applied": applied,
        "pending": pending,
        "pending_count": len(pending),
        "errors": errors,
        "entries": entries,
    }


async def outbound_flag_deltas(
    db_path: str,
    *,
    since: float = 0.0,
    limit: int = 500,
) -> list[dict[str, Any]]:
    """Flag winners from non-LR origins newer than ``since`` (family clock ts)."""

    await oplog.ensure_schema(db_path)
    conn = await connection.open_async(db_path)
    try:
        await conn.executescript(family_clock.STATE_DDL)
        rows = await (
            await conn.execute(
                "SELECT i.filepath, i.content_hash, i.flag, s.ts, s.origin, s.origin_seq "
                "FROM oplog_family_state s "
                "JOIN images i ON i.content_hash = s.content_hash "
                "WHERE s.family = 'flag' AND s.ts > ? AND s.origin != ? "
                "AND i.filepath IS NOT NULL AND TRIM(i.filepath) != '' "
                "ORDER BY s.ts ASC, i.id ASC LIMIT ?",
                (float(since or 0.0), LR_ORIGIN, max(1, min(int(limit), 2000))),
            )
        ).fetchall()
        return [
            {
                "filepath": str(row["filepath"]),
                "content_hash": str(row["content_hash"]),
                "family": "flag",
                "value": str(row["flag"] or "unflagged"),
                "ts": float(row["ts"]),
                "origin": str(row["origin"]),
                "origin_seq": int(row["origin_seq"]),
            }
            for row in rows
        ]
    finally:
        await connection.close_async(conn, db_path=db_path)
