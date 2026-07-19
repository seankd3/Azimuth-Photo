"""Quiet LR bridge status — last delta exchange + new-export batches.

Kept tiny and additive so sync/integrity status can piggyback without a
parallel poll loop.
"""

from __future__ import annotations

import hashlib
import threading
import time
from typing import Any

from data import connection
from features.stacks import builders

_lock = threading.Lock()
_state: dict[str, Any] = {
    "last_delta_at": None,  # unix seconds of last successful plugin delta exchange
    "last_delta_direction": None,  # "in" | "out" | "both"
}


def note_delta_exchange(*, direction: str = "out") -> float:
    """Record that the plugin exchanged deltas with the satellite."""

    now = time.time()
    with _lock:
        previous = _state.get("last_delta_at")
        _state["last_delta_at"] = now
        prior_dir = _state.get("last_delta_direction")
        if prior_dir and prior_dir != direction and previous and (now - float(previous)) < 30:
            _state["last_delta_direction"] = "both"
        else:
            _state["last_delta_direction"] = direction
    return now


def delta_exchange_status() -> dict[str, Any]:
    with _lock:
        last = _state.get("last_delta_at")
        direction = _state.get("last_delta_direction")
    age_hours = None
    if last is not None:
        age_hours = max(0.0, (time.time() - float(last)) / 3600.0)
    return {
        "last_delta_at": last,
        "last_delta_direction": direction,
        "age_hours": age_hours,
        "stale": bool(last is not None and age_hours is not None and age_hours > 24.0),
        # Never-synced is not a failure line — only persistent silence after contact.
        "health_line": _health_line(last, age_hours),
    }


def _health_line(last: float | None, age_hours: float | None) -> str | None:
    if last is None or age_hours is None or age_hours <= 24.0:
        return None
    local = time.localtime(float(last))
    stamp = time.strftime("%b ", local) + str(local.tm_mday) + time.strftime(", %Y", local)
    return f"Lightroom bridge hasn't synced since {stamp}"


def _batch_id(image_ids: list[int]) -> str:
    payload = ",".join(str(i) for i in image_ids)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]


async def new_export_batch(
    db_path: str,
    *,
    since: float = 0.0,
    limit: int = 200,
) -> dict[str, Any] | None:
    """export_of version-stack edits created after ``since`` (stack created_at)."""

    since_ts = float(since or 0.0)
    conn = await connection.open_async(db_path)
    try:
        rows = await (
            await conn.execute(
                """
                SELECT s.id AS stack_id, s.created_at, sm.image_id,
                       i.file_ext, i.filename, i.filepath
                FROM stacks s
                JOIN stack_members sm ON sm.stack_id = s.id
                JOIN images i ON i.id = sm.image_id
                WHERE s.kind = 'version'
                  AND s.created_at > ?
                  AND COALESCE(i.missing_at, 0) = 0
                  AND LOWER(COALESCE(i.status, 'kept')) NOT IN ('trashed', 'deleted')
                ORDER BY s.created_at ASC, sm.image_id ASC
                """,
                (since_ts,),
            )
        ).fetchall()
    finally:
        await connection.close_async(conn, db_path=db_path)

    if not rows:
        return None

    by_stack: dict[int, dict[str, Any]] = {}
    for row in rows:
        stack_id = int(row["stack_id"])
        bucket = by_stack.setdefault(
            stack_id,
            {"created_at": float(row["created_at"] or 0.0), "exports": [], "raws": []},
        )
        image_id = int(row["image_id"])
        member = {
            "file_ext": row["file_ext"],
            "filename": row["filename"],
            "filepath": row["filepath"],
        }
        if builders._is_raw(member):
            bucket["raws"].append(image_id)
        else:
            bucket["exports"].append(image_id)

    export_ids: list[int] = []
    newest = since_ts
    for bucket in by_stack.values():
        if not bucket["raws"] or not bucket["exports"]:
            continue
        newest = max(newest, float(bucket["created_at"]))
        for image_id in bucket["exports"]:
            if image_id not in export_ids:
                export_ids.append(image_id)
            if len(export_ids) >= limit:
                break
        if len(export_ids) >= limit:
            break

    if not export_ids:
        return None
    return {
        "batch_id": _batch_id(export_ids),
        "image_ids": export_ids,
        "count": len(export_ids),
        "newest_at": newest,
    }


async def bridge_status_payload(
    db_path: str,
    *,
    exports_since: float = 0.0,
) -> dict[str, Any]:
    """Combined LR quiet-status block for sync/integrity piggyback."""

    from features.sync import shoot_rank

    exchange = delta_exchange_status()
    batch = await new_export_batch(db_path, since=exports_since)
    ranks = await shoot_rank.shoot_rank_payload(db_path)
    return {
        "last_delta_at": exchange["last_delta_at"],
        "age_hours": exchange["age_hours"],
        "stale": exchange["stale"],
        "health_line": exchange["health_line"],
        "new_exports": batch,
        "shoot_context": ranks,
    }
