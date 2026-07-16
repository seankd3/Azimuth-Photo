"""Canonical per-content-hash clocks shared by every metadata ingestion path."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any


LEGACY_ORIGIN = "legacy-metadata"
ROW_CLOCK_TABLES = {
    "develop": "develop_settings",
    "iptc": "iptc_fields",
}
STATE_DDL = """
CREATE TABLE IF NOT EXISTS oplog_family_state (
    content_hash TEXT NOT NULL,
    family TEXT NOT NULL,
    ts REAL NOT NULL,
    origin TEXT NOT NULL,
    origin_seq INTEGER NOT NULL,
    PRIMARY KEY (content_hash, family)
);
"""


def timestamp_seconds(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    raw = str(value or "").strip()
    if not raw:
        return 0.0
    try:
        return float(raw)
    except ValueError:
        pass
    try:
        parsed = datetime.fromisoformat(raw[:-1] + "+00:00" if raw.endswith("Z") else raw)
    except ValueError:
        return 0.0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def winner_key(entry: Mapping[str, Any]) -> tuple[float, str, int]:
    return float(entry["ts"]), str(entry["origin"]), int(entry["origin_seq"])


def legacy_key(updated_at: Any) -> tuple[float, str, int]:
    return timestamp_seconds(updated_at), LEGACY_ORIGIN, 0


def newest_key(
    *keys: tuple[float, str, int] | None,
) -> tuple[float, str, int] | None:
    return max((key for key in keys if key is not None), default=None)


async def row_key(
    conn,
    image_id: int,
    family: str,
) -> tuple[float, str, int] | None:
    """Return the durable row clock for families that store one."""

    table = ROW_CLOCK_TABLES.get(family)
    if table is None:
        return None
    row = await (await conn.execute(
        f"SELECT updated_at FROM {table} WHERE image_id = ?",
        (image_id,),
    )).fetchone()
    return legacy_key(row["updated_at"]) if row else None


async def state_key(conn, content_hash: str, family: str) -> tuple[float, str, int] | None:
    row = await (await conn.execute(
        "SELECT ts, origin, origin_seq FROM oplog_family_state "
        "WHERE content_hash = ? AND family = ?",
        (content_hash, family),
    )).fetchone()
    return (float(row["ts"]), str(row["origin"]), int(row["origin_seq"])) if row else None


async def record_state(
    conn,
    content_hash: str,
    family: str,
    key: tuple[float, str, int],
) -> None:
    await conn.execute(
        "INSERT INTO oplog_family_state(content_hash, family, ts, origin, origin_seq) "
        "VALUES (?, ?, ?, ?, ?) ON CONFLICT(content_hash, family) DO UPDATE SET "
        "ts=excluded.ts, origin=excluded.origin, origin_seq=excluded.origin_seq",
        (content_hash, family, *key),
    )


async def migrate_legacy_states(conn) -> None:
    """Seed the canonical clock from clocks written by pre-unification hubs."""

    await conn.executescript(STATE_DDL)
    table = await (await conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'sync_metadata_state'"
    )).fetchone()
    if table is None:
        return
    rows = await (await conn.execute(
        "SELECT images.content_hash, state.family, state.updated_at "
        "FROM sync_metadata_state state JOIN images ON images.id = state.image_id "
        "WHERE images.content_hash IS NOT NULL"
    )).fetchall()
    for row in rows:
        incoming = legacy_key(row["updated_at"])
        current = await state_key(conn, str(row["content_hash"]), str(row["family"]))
        if current is None or incoming[0] > current[0]:
            await record_state(
                conn,
                str(row["content_hash"]),
                str(row["family"]),
                incoming,
            )
