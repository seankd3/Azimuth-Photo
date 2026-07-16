"""Durable, order-independent metadata convergence for hub and satellites."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from datetime import datetime, timezone
from typing import Any

from data import connection
from features.sync import family_clock
from features.sync.develop_merge import preserve_local_rating
from features.sync.validation import validate_content_hash


log = logging.getLogger(__name__)
MAX_CLOCK_SKEW_SECONDS = 24 * 60 * 60
PAGE_SIZE = 1000
COLLECTION_CONTENT_HASH = "0" * 32
FAMILIES = frozenset({
    "flag", "rating", "keywords", "iptc", "develop",
    "collection_meta", "collection_membership",
})
JsonRequest = Callable[[str, str, dict[str, Any]], Awaitable[dict[str, Any]]]


# The schema is defensive-called from every public operation. Remember the
# physical database so steady-state calls avoid opening SQLite solely for DDL.
_schema_ready: dict[str, tuple[int, int]] = {}
_schema_locks: dict[str, asyncio.Lock] = {}
_pending_entry_counts: dict[str, int] = {}
_pending_entry_count_versions: dict[str, int] = {}

OPLOG_DDL = """
CREATE TABLE IF NOT EXISTS oplog (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    origin TEXT NOT NULL,
    origin_seq INTEGER NOT NULL,
    content_hash TEXT NOT NULL,
    family TEXT NOT NULL,
    payload TEXT NOT NULL,
    ts REAL NOT NULL,
    applied_from TEXT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_oplog_origin_seq ON oplog(origin, origin_seq);
CREATE INDEX IF NOT EXISTS idx_oplog_content_family ON oplog(content_hash, family);
CREATE TABLE IF NOT EXISTS oplog_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS oplog_cursors (
    origin TEXT PRIMARY KEY,
    last_seen_origin_seq INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS oplog_pending (
    origin TEXT NOT NULL,
    origin_seq INTEGER NOT NULL,
    reason TEXT NOT NULL,
    recorded_at REAL NOT NULL,
    PRIMARY KEY (origin, origin_seq)
);
"""

KEYWORD_IPTC_DDL = """
CREATE TABLE IF NOT EXISTS keywords (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    parent_id INTEGER REFERENCES keywords(id) ON DELETE RESTRICT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_oplog_keywords_parent_name
ON keywords(parent_id, name COLLATE NOCASE);
CREATE TABLE IF NOT EXISTS image_keywords (
    image_id INTEGER NOT NULL REFERENCES images(id) ON DELETE CASCADE,
    keyword_id INTEGER NOT NULL REFERENCES keywords(id) ON DELETE CASCADE,
    origin TEXT NOT NULL DEFAULT 'user',
    PRIMARY KEY (image_id, keyword_id)
);
CREATE TABLE IF NOT EXISTS iptc_fields (
    image_id INTEGER PRIMARY KEY REFERENCES images(id) ON DELETE CASCADE,
    title TEXT NOT NULL DEFAULT '',
    caption TEXT NOT NULL DEFAULT '',
    copyright TEXT NOT NULL DEFAULT '',
    creator TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);
"""


def _json_payload(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def _entry_dict(row) -> dict[str, Any]:
    return {
        "origin": str(row["origin"]),
        "origin_seq": int(row["origin_seq"]),
        "content_hash": str(row["content_hash"]),
        "family": str(row["family"]),
        "payload": json.loads(row["payload"]),
        "ts": float(row["ts"]),
        "applied_from": row["applied_from"],
    }


def _iso_timestamp(value: float) -> str:
    return datetime.fromtimestamp(value, timezone.utc).isoformat().replace("+00:00", "Z")


def _database_identity(db_path: str) -> tuple[int, int] | None:
    if db_path == ":memory:":
        return None
    try:
        stat_result = os.stat(db_path)
    except OSError:
        return None
    return (int(stat_result.st_dev), int(stat_result.st_ino))


def _invalidate_pending_entry_count(db_path: str) -> None:
    _pending_entry_counts.pop(db_path, None)
    _pending_entry_count_versions[db_path] = _pending_entry_count_versions.get(db_path, 0) + 1


async def ensure_schema(db_path: str) -> None:
    identity = _database_identity(db_path)
    if identity is not None and _schema_ready.get(db_path) == identity:
        return
    if identity is not None:
        _invalidate_pending_entry_count(db_path)
    if db_path == ":memory:":
        conn = await connection.open_async(db_path)
        try:
            await conn.executescript(OPLOG_DDL)
            await family_clock.migrate_legacy_states(conn)
            await conn.commit()
        finally:
            await connection.close_async(conn, db_path=db_path)
        return

    lock = _schema_locks.setdefault(db_path, asyncio.Lock())
    async with lock:
        identity = _database_identity(db_path)
        if identity is not None and _schema_ready.get(db_path) == identity:
            return
        conn = await connection.open_async(db_path)
        try:
            await conn.executescript(OPLOG_DDL)
            await family_clock.migrate_legacy_states(conn)
            await conn.commit()
        finally:
            await connection.close_async(conn, db_path=db_path)
        identity = _database_identity(db_path)
        if identity is not None:
            _schema_ready[db_path] = identity


async def _device_id_on_conn(conn) -> str:
    row = await (await conn.execute(
        "SELECT value FROM oplog_settings WHERE key = 'device_id'"
    )).fetchone()
    if row:
        return str(row["value"])
    value = uuid.uuid4().hex
    await conn.execute(
        "INSERT OR IGNORE INTO oplog_settings(key, value) VALUES ('device_id', ?)",
        (value,),
    )
    row = await (await conn.execute(
        "SELECT value FROM oplog_settings WHERE key = 'device_id'"
    )).fetchone()
    return str(row["value"])


async def device_id(db_path: str) -> str:
    await ensure_schema(db_path)
    conn = await connection.open_async(db_path)
    try:
        value = await _device_id_on_conn(conn)
        await conn.commit()
        return value
    finally:
        await connection.close_async(conn, db_path=db_path)


def _normalize_entry(entry: Mapping[str, Any], *, receive_time: float) -> dict[str, Any]:
    origin = str(entry.get("origin") or "").strip()
    if not origin:
        raise ValueError("oplog origin is required")
    origin_seq = int(entry.get("origin_seq") or 0)
    if origin_seq <= 0:
        raise ValueError("oplog origin_seq must be positive")
    family = str(entry.get("family") or "").strip()
    if family not in FAMILIES:
        raise ValueError(f"unsupported oplog family: {family}")
    content_hash = validate_content_hash(str(entry.get("content_hash") or ""))
    timestamp = float(entry.get("ts") or 0)
    if timestamp <= 0:
        raise ValueError("oplog ts must be positive")
    if timestamp > receive_time + MAX_CLOCK_SKEW_SECONDS:
        log.warning(
            "oplog clock clamp origin=%s origin_seq=%s authored_ts=%s receive_ts=%s",
            origin,
            origin_seq,
            timestamp,
            receive_time,
        )
        timestamp = receive_time
    payload = entry.get("payload")
    if not isinstance(payload, (dict, list)):
        raise ValueError("oplog payload must be a JSON object or array")
    return {
        "origin": origin,
        "origin_seq": origin_seq,
        "content_hash": content_hash,
        "family": family,
        "payload": payload,
        "ts": timestamp,
        "applied_from": entry.get("applied_from"),
    }


def _winner_key(entry: Mapping[str, Any]) -> tuple[float, str, int]:
    return family_clock.winner_key(entry)


async def _state_key(conn, identity: str, family: str) -> tuple[float, str, int] | None:
    return await family_clock.state_key(conn, identity, family)


async def _record_winner(conn, entry: Mapping[str, Any], *, identity: str | None = None) -> None:
    await family_clock.record_state(
        conn,
        identity or str(entry["content_hash"]),
        str(entry["family"]),
        _winner_key(entry),
    )


async def _resolve_keyword_path(conn, path: str) -> int:
    parts = [" ".join(part.strip().split()) for part in str(path).split(">")]
    if not parts or any(not part for part in parts):
        raise ValueError("keyword paths must contain non-empty components")
    parent_id: int | None = None
    for part in parts:
        row = await (await conn.execute(
            "SELECT id FROM keywords WHERE parent_id IS ? AND lower(name) = lower(?)",
            (parent_id, part),
        )).fetchone()
        if row:
            parent_id = int(row["id"])
            continue
        cursor = await conn.execute(
            "INSERT INTO keywords(name, parent_id, created_at) VALUES (?, ?, ?)",
            (part, parent_id, _iso_timestamp(time.time())),
        )
        parent_id = int(cursor.lastrowid)
    return int(parent_id)


async def _apply_keywords(conn, image_id: int, payload: Any) -> None:
    paths = payload.get("paths", []) if isinstance(payload, dict) else payload
    if not isinstance(paths, list):
        raise ValueError("keywords payload must contain a paths list")
    await conn.executescript(KEYWORD_IPTC_DDL)
    for path in sorted({str(value).strip() for value in paths if str(value).strip()}):
        keyword_id = await _resolve_keyword_path(conn, path)
        await conn.execute(
            "INSERT INTO image_keywords(image_id, keyword_id, origin) VALUES (?, ?, 'sync') "
            "ON CONFLICT(image_id, keyword_id) DO NOTHING",
            (image_id, keyword_id),
        )


async def _apply_lww_family(conn, image_id: int, entry: Mapping[str, Any]) -> None:
    family = str(entry["family"])
    payload = entry["payload"]
    if not isinstance(payload, dict):
        raise ValueError(f"{family} payload must be an object")
    if family == "flag":
        value = str(payload.get("value") or "")
        if value not in {"picked", "unflagged", "rejected"}:
            raise ValueError("invalid flag payload")
        await conn.execute("UPDATE images SET flag = ? WHERE id = ?", (value, image_id))
    elif family == "rating":
        value = payload.get("value")
        columns = {row["name"] for row in await (await conn.execute("PRAGMA table_info(images)")).fetchall()}
        if "rating" in columns:
            await conn.execute("UPDATE images SET rating = ? WHERE id = ?", (value, image_id))
        else:
            settings = _json_payload({"_lr_rating": value})
            await conn.execute(
                "INSERT INTO develop_settings(image_id, settings, origin, updated_at) VALUES (?, ?, 'sync', '') "
                "ON CONFLICT(image_id) DO UPDATE SET settings=json_set("
                "CASE WHEN json_valid(develop_settings.settings) THEN "
                "  CASE WHEN json_type(develop_settings.settings) = 'object' "
                "    THEN develop_settings.settings ELSE '{}' END "
                "ELSE '{}' END, '$._lr_rating', ?)",
                (image_id, settings, value),
            )
    elif family == "develop":
        settings_value = payload.get("settings")
        if not isinstance(settings_value, dict):
            raise ValueError("develop payload must contain full settings")
        row = await (await conn.execute(
            "SELECT settings FROM develop_settings WHERE image_id = ?", (image_id,)
        )).fetchone()
        settings_value = preserve_local_rating(settings_value, row["settings"] if row else None)
        updated_at = str(payload.get("updated_at") or _iso_timestamp(float(entry["ts"])))
        await conn.execute(
            "INSERT INTO develop_settings(image_id, settings, origin, updated_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(image_id) DO UPDATE SET settings=excluded.settings, origin=excluded.origin, updated_at=excluded.updated_at",
            (image_id, _json_payload(settings_value), str(payload.get("origin") or "sync"), updated_at),
        )
    elif family == "iptc":
        await conn.executescript(KEYWORD_IPTC_DDL)
        updated_at = str(payload.get("updated_at") or _iso_timestamp(float(entry["ts"])))
        await conn.execute(
            "INSERT INTO iptc_fields(image_id, title, caption, copyright, creator, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(image_id) DO UPDATE SET "
            "title=excluded.title, caption=excluded.caption, copyright=excluded.copyright, "
            "creator=excluded.creator, updated_at=excluded.updated_at",
            (
                image_id, str(payload.get("title") or ""), str(payload.get("caption") or ""),
                str(payload.get("copyright") or ""), str(payload.get("creator") or ""), updated_at,
            ),
        )


def _collection_uuid(value: Any, *, field: str) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError(f"{field} must be a UUID") from exc


def _collection_meta_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("collection_meta payload must be an object")
    collection_uuid = _collection_uuid(payload.get("collection_uuid"), field="collection_uuid")
    parent_value = payload.get("parent_uuid")
    parent_uuid = None if parent_value is None else _collection_uuid(parent_value, field="parent_uuid")
    name = payload.get("name")
    if not isinstance(name, str):
        raise ValueError("collection_meta name must be a string")
    deleted = payload.get("deleted")
    if not isinstance(deleted, bool):
        raise ValueError("collection_meta deleted must be a boolean")
    return {
        "collection_uuid": collection_uuid,
        "name": name,
        "parent_uuid": parent_uuid,
        "deleted": deleted,
    }


def _collection_membership_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("collection_membership payload must be an object")
    collection_uuid = _collection_uuid(payload.get("collection_uuid"), field="collection_uuid")
    content_hash = validate_content_hash(str(payload.get("content_hash") or ""))
    member = payload.get("member")
    if not isinstance(member, bool):
        raise ValueError("collection_membership member must be a boolean")
    return {"collection_uuid": collection_uuid, "content_hash": content_hash, "member": member}


async def _reconcile_collection_links(conn) -> None:
    """Materialize the one-parent collection tree from LWW metadata winners."""

    rows = await (await conn.execute(
        "SELECT origin, origin_seq, payload, ts FROM oplog WHERE family = 'collection_meta'"
    )).fetchall()
    winners: dict[str, tuple[tuple[float, str, int], dict[str, Any]]] = {}
    for row in rows:
        payload = _collection_meta_payload(json.loads(row["payload"]))
        key = (float(row["ts"]), str(row["origin"]), int(row["origin_seq"]))
        current = winners.get(payload["collection_uuid"])
        if current is None or key > current[0]:
            winners[payload["collection_uuid"]] = (key, payload)

    current_rows = await (await conn.execute("SELECT id, uuid FROM collections")).fetchall()
    collection_ids = {str(row["uuid"]): int(row["id"]) for row in current_rows}
    child_ids = [collection_ids[value] for value in winners if value in collection_ids]
    if child_ids:
        placeholders = ",".join("?" for _ in child_ids)
        await conn.execute(
            f"DELETE FROM collection_links WHERE child_id IN ({placeholders})",
            child_ids,
        )
    for collection_uuid, (_, payload) in winners.items():
        if payload["deleted"] or payload["parent_uuid"] is None:
            continue
        child_id = collection_ids.get(collection_uuid)
        parent_id = collection_ids.get(payload["parent_uuid"])
        if child_id is None or parent_id is None or child_id == parent_id:
            continue
        await conn.execute(
            "INSERT OR IGNORE INTO collection_links(parent_id, child_id, position, added_at) VALUES (?, ?, 0, ?)",
            (parent_id, child_id, time.time()),
        )


async def _apply_collection_meta(conn, entry: Mapping[str, Any]) -> str:
    payload = _collection_meta_payload(entry["payload"])
    identity = payload["collection_uuid"]
    current = await _state_key(conn, identity, "collection_meta")
    if current is not None and _winner_key(entry) <= current:
        return "stale-or-replayed"
    if payload["deleted"]:
        await conn.execute("DELETE FROM collections WHERE uuid = ?", (identity,))
    else:
        await conn.execute(
            "INSERT INTO collections(uuid, name, created_at, updated_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(uuid) DO UPDATE SET name=excluded.name, updated_at=excluded.updated_at",
            (identity, payload["name"], float(entry["ts"]), float(entry["ts"])),
        )
    await _record_winner(conn, entry, identity=identity)
    await _reconcile_collection_links(conn)
    return "applied"


async def _apply_collection_membership(conn, entry: Mapping[str, Any]) -> str:
    payload = _collection_membership_payload(entry["payload"])
    identity = f"{payload['collection_uuid']}:{payload['content_hash']}"
    current = await _state_key(conn, identity, "collection_membership")
    if current is not None and _winner_key(entry) <= current:
        return "stale-or-replayed"
    collection = await (await conn.execute(
        "SELECT id FROM collections WHERE uuid = ?", (payload["collection_uuid"],)
    )).fetchone()
    if collection is None:
        return "unknown-collection-uuid"
    image = await (await conn.execute(
        "SELECT id FROM images WHERE content_hash = ? ORDER BY id LIMIT 1",
        (payload["content_hash"],),
    )).fetchone()
    if image is None:
        return "unknown-content-hash"
    collection_id = int(collection["id"])
    image_id = int(image["id"])
    if payload["member"]:
        row = await (await conn.execute(
            "SELECT COALESCE(MAX(position), -1) + 1 AS next_position FROM collection_images WHERE collection_id = ?",
            (collection_id,),
        )).fetchone()
        await conn.execute(
            "INSERT OR IGNORE INTO collection_images(collection_id, image_id, position, added_at) VALUES (?, ?, ?, ?)",
            (collection_id, image_id, int(row["next_position"]), float(entry["ts"])),
        )
    else:
        await conn.execute(
            "DELETE FROM collection_images WHERE collection_id = ? AND image_id = ?",
            (collection_id, image_id),
        )
    await conn.execute(
        "UPDATE collections SET cover_image_id = ("
        "SELECT image_id FROM collection_images WHERE collection_id = ? "
        "ORDER BY position, added_at, image_id LIMIT 1), updated_at = ? WHERE id = ?",
        (collection_id, float(entry["ts"]), collection_id),
    )
    await _record_winner(conn, entry, identity=identity)
    return "applied"


async def _apply_entry_on_conn(conn, entry: Mapping[str, Any]) -> str:
    family = str(entry["family"])
    if family == "collection_meta":
        return await _apply_collection_meta(conn, entry)
    if family == "collection_membership":
        return await _apply_collection_membership(conn, entry)
    image = await (await conn.execute(
        "SELECT id FROM images WHERE content_hash = ? ORDER BY id LIMIT 1",
        (entry["content_hash"],),
    )).fetchone()
    if image is None:
        return "unknown-content-hash"
    if family == "keywords":
        await _apply_keywords(conn, int(image["id"]), entry["payload"])
        current = await _state_key(conn, str(entry["content_hash"]), family)
        if current is None or _winner_key(entry) > current:
            await _record_winner(conn, entry)
        return "applied"
    current = await _state_key(conn, str(entry["content_hash"]), family)
    if current is not None and _winner_key(entry) <= current:
        return "stale-or-replayed"
    await _apply_lww_family(conn, int(image["id"]), entry)
    await _record_winner(conn, entry)
    return "applied"


_SOFT_FAILURES = frozenset({"unknown-content-hash", "unknown-collection-uuid"})


async def _record_apply_result(
    conn,
    entry: Mapping[str, Any],
    result: str,
    *,
    recorded_at: float,
) -> None:
    identity = (str(entry["origin"]), int(entry["origin_seq"]))
    if result in _SOFT_FAILURES:
        await conn.execute(
            "INSERT INTO oplog_pending(origin, origin_seq, reason, recorded_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(origin, origin_seq) DO UPDATE SET "
            "reason=excluded.reason, recorded_at=excluded.recorded_at",
            (*identity, result, recorded_at),
        )
        return
    await conn.execute(
        "DELETE FROM oplog_pending WHERE origin = ? AND origin_seq = ?",
        identity,
    )


async def retry_pending_entries(db_path: str) -> dict[str, int]:
    """Re-apply durable soft failures whose catalog dependencies may now exist."""

    await ensure_schema(db_path)
    conn = await connection.open_async(db_path)
    retried = 0
    applied = 0
    still_pending = 0
    try:
        await conn.execute("BEGIN IMMEDIATE")
        rows = await (await conn.execute(
            "SELECT oplog.origin, oplog.origin_seq, oplog.content_hash, oplog.family, "
            "oplog.payload, oplog.ts, oplog.applied_from "
            "FROM oplog_pending JOIN oplog USING (origin, origin_seq) "
            "ORDER BY oplog_pending.recorded_at, oplog_pending.origin, oplog_pending.origin_seq"
        )).fetchall()
        for row in rows:
            entry = _entry_dict(row)
            result = await _apply_entry_on_conn(conn, entry)
            await _record_apply_result(conn, entry, result, recorded_at=time.time())
            retried += 1
            if result in _SOFT_FAILURES:
                still_pending += 1
            else:
                applied += 1
        await conn.commit()
    except Exception:
        await conn.rollback()
        raise
    finally:
        await connection.close_async(conn, db_path=db_path)
    if retried:
        _invalidate_pending_entry_count(db_path)
    return {"retried": retried, "applied": applied, "still_pending": still_pending}


async def pending_entry_count(db_path: str) -> int:
    await ensure_schema(db_path)
    cached = _pending_entry_counts.get(db_path)
    if cached is not None:
        return cached
    version = _pending_entry_count_versions.get(db_path, 0)
    conn = await connection.open_async(db_path)
    try:
        row = await (await conn.execute("SELECT COUNT(*) AS count FROM oplog_pending")).fetchone()
        count = int(row["count"])
    finally:
        await connection.close_async(conn, db_path=db_path)
    if version == _pending_entry_count_versions.get(db_path, 0):
        _pending_entry_counts[db_path] = count
    return count


async def apply_entries(
    db_path: str,
    entries: Iterable[Mapping[str, Any]],
    *,
    applied_from: str | None,
    receive_time: float | None = None,
) -> dict[str, Any]:
    """Persist original identities and apply through the one shared catalog path."""

    await ensure_schema(db_path)
    received_at = float(receive_time or time.time())
    normalized = [_normalize_entry(entry, receive_time=received_at) for entry in entries]
    conn = await connection.open_async(db_path)
    inserted = 0
    results: list[dict[str, Any]] = []
    try:
        await conn.execute("BEGIN IMMEDIATE")
        for entry in normalized:
            cursor = await conn.execute(
                "INSERT OR IGNORE INTO oplog(origin, origin_seq, content_hash, family, payload, ts, applied_from) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    entry["origin"], entry["origin_seq"], entry["content_hash"], entry["family"],
                    _json_payload(entry["payload"]), entry["ts"], applied_from,
                ),
            )
            inserted += max(int(cursor.rowcount or 0), 0)
            stored = await (await conn.execute(
                "SELECT origin, origin_seq, content_hash, family, payload, ts, applied_from "
                "FROM oplog WHERE origin = ? AND origin_seq = ?",
                (entry["origin"], entry["origin_seq"]),
            )).fetchone()
            canonical = _entry_dict(stored)
            result = await _apply_entry_on_conn(conn, canonical)
            await _record_apply_result(conn, canonical, result, recorded_at=received_at)
            results.append({"origin": canonical["origin"], "origin_seq": canonical["origin_seq"], "result": result})
        await conn.commit()
    except Exception:
        await conn.rollback()
        raise
    finally:
        await connection.close_async(conn, db_path=db_path)
    if normalized:
        _invalidate_pending_entry_count(db_path)
    if any(item["result"] == "applied" for item in results):
        await retry_pending_entries(db_path)
    return {
        "received": len(normalized),
        "inserted": inserted,
        "skipped_unhashed": sum(item["result"] == "unknown-content-hash" for item in results),
        "entries": results,
    }


# Compatibility name for callers that describe the persistence side of the
# operation. Hub pushes and satellite pulls both use ``apply_entries``.
ingest_entries = apply_entries


async def append_entry(
    db_path: str,
    *,
    content_hash: str,
    family: str,
    payload: dict[str, Any] | list[Any],
    ts: float | None = None,
) -> dict[str, Any]:
    await ensure_schema(db_path)
    authored_at = float(ts or time.time())
    conn = await connection.open_async(db_path)
    try:
        await conn.execute("BEGIN IMMEDIATE")
        origin = await _device_id_on_conn(conn)
        row = await (await conn.execute(
            "SELECT COALESCE(MAX(origin_seq), 0) + 1 AS next_seq FROM oplog WHERE origin = ?",
            (origin,),
        )).fetchone()
        entry = _normalize_entry(
            {
                "origin": origin,
                "origin_seq": int(row["next_seq"]),
                "content_hash": content_hash,
                "family": family,
                "payload": payload,
                "ts": authored_at,
            },
            receive_time=authored_at,
        )
        await conn.execute(
            "INSERT INTO oplog(origin, origin_seq, content_hash, family, payload, ts, applied_from) "
            "VALUES (?, ?, ?, ?, ?, ?, NULL)",
            (
                entry["origin"], entry["origin_seq"], entry["content_hash"], entry["family"],
                _json_payload(entry["payload"]), entry["ts"],
            ),
        )
        await _apply_entry_on_conn(conn, entry)
        await conn.commit()
        _invalidate_pending_entry_count(db_path)
        return entry
    except Exception:
        await conn.rollback()
        raise
    finally:
        await connection.close_async(conn, db_path=db_path)


async def _image_hashes(conn, image_ids: Sequence[int]) -> dict[int, str]:
    ids = sorted({int(value) for value in image_ids if int(value) > 0})
    if not ids:
        return {}
    placeholders = ",".join("?" for _ in ids)
    rows = await (await conn.execute(
        f"SELECT id, content_hash FROM images WHERE id IN ({placeholders}) AND content_hash IS NOT NULL",
        ids,
    )).fetchall()
    return {int(row["id"]): str(row["content_hash"]) for row in rows}


async def collection_meta_payload(db_path: str, collection_id: int) -> dict[str, Any] | None:
    """Read the regular collection state that is safe to share across devices."""

    conn = await connection.open_async(db_path)
    try:
        row = await (await conn.execute(
            "SELECT uuid, name, query FROM collections WHERE id = ?", (int(collection_id),)
        )).fetchone()
        if row is None or row["query"] is not None or not row["uuid"]:
            return None
        parent = await (await conn.execute(
            "SELECT parent.uuid FROM collection_links links "
            "JOIN collections parent ON parent.id = links.parent_id "
            "WHERE links.child_id = ? ORDER BY links.added_at, links.parent_id LIMIT 1",
            (int(collection_id),),
        )).fetchone()
        return {
            "collection_uuid": _collection_uuid(row["uuid"], field="collection_uuid"),
            "name": str(row["name"]),
            "parent_uuid": _collection_uuid(parent["uuid"], field="parent_uuid") if parent else None,
            "deleted": False,
        }
    finally:
        await connection.close_async(conn, db_path=db_path)


async def append_collection_meta(db_path: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    normalized = _collection_meta_payload(dict(payload))
    return await append_entry(
        db_path,
        content_hash=COLLECTION_CONTENT_HASH,
        family="collection_meta",
        payload=normalized,
    )


async def append_collection_memberships(
    db_path: str,
    collection_id: int,
    image_ids: Sequence[int],
    *,
    member: bool,
) -> list[dict[str, Any]]:
    metadata = await collection_meta_payload(db_path, collection_id)
    if metadata is None:
        return []
    conn = await connection.open_async(db_path)
    try:
        hashes = await _image_hashes(conn, image_ids)
    finally:
        await connection.close_async(conn, db_path=db_path)
    return [
        await append_entry(
            db_path,
            content_hash=COLLECTION_CONTENT_HASH,
            family="collection_membership",
            payload={
                "collection_uuid": metadata["collection_uuid"],
                "content_hash": content_hash,
                "member": member,
            },
        )
        for _, content_hash in sorted(hashes.items())
    ]


async def append_flags(db_path: str, image_ids: Sequence[int], flag: str) -> list[dict[str, Any]]:
    await ensure_schema(db_path)
    conn = await connection.open_async(db_path)
    try:
        hashes = await _image_hashes(conn, image_ids)
    finally:
        await connection.close_async(conn, db_path=db_path)
    return [
        await append_entry(db_path, content_hash=value, family="flag", payload={"value": flag})
        for _, value in sorted(hashes.items())
    ]


async def append_rating(db_path: str, image_id: int, rating: int | float | None) -> dict[str, Any] | None:
    await ensure_schema(db_path)
    conn = await connection.open_async(db_path)
    try:
        hashes = await _image_hashes(conn, [image_id])
    finally:
        await connection.close_async(conn, db_path=db_path)
    content_hash = hashes.get(int(image_id))
    if content_hash is None:
        return None
    return await append_entry(
        db_path, content_hash=content_hash, family="rating", payload={"value": rating}
    )


async def append_develop(db_path: str, image_id: int) -> dict[str, Any] | None:
    await ensure_schema(db_path)
    conn = await connection.open_async(db_path)
    try:
        row = await (await conn.execute(
            "SELECT i.content_hash, d.settings, d.origin, d.updated_at FROM images i "
            "JOIN develop_settings d ON d.image_id = i.id WHERE i.id = ?",
            (image_id,),
        )).fetchone()
    finally:
        await connection.close_async(conn, db_path=db_path)
    if not row or not row["content_hash"]:
        return None
    payload = {
        "settings": json.loads(row["settings"] or "{}"),
        "origin": str(row["origin"] or "user"),
        "updated_at": str(row["updated_at"]),
    }
    return await append_entry(db_path, content_hash=str(row["content_hash"]), family="develop", payload=payload)


async def append_iptc(db_path: str, image_id: int) -> dict[str, Any] | None:
    await ensure_schema(db_path)
    conn = await connection.open_async(db_path)
    try:
        row = await (await conn.execute(
            "SELECT i.content_hash, p.title, p.caption, p.copyright, p.creator, p.updated_at "
            "FROM images i JOIN iptc_fields p ON p.image_id = i.id WHERE i.id = ?",
            (image_id,),
        )).fetchone()
    finally:
        await connection.close_async(conn, db_path=db_path)
    if not row or not row["content_hash"]:
        return None
    payload = {key: row[key] for key in ("title", "caption", "copyright", "creator", "updated_at")}
    return await append_entry(db_path, content_hash=str(row["content_hash"]), family="iptc", payload=payload)


async def append_keywords(db_path: str, image_ids: Sequence[int]) -> list[dict[str, Any]]:
    await ensure_schema(db_path)
    conn = await connection.open_async(db_path)
    try:
        hashes = await _image_hashes(conn, image_ids)
        entries: list[tuple[str, list[str]]] = []
        for image_id, content_hash in sorted(hashes.items()):
            rows = await (await conn.execute(
                "WITH RECURSIVE tree(id, parent_id, path) AS ("
                "SELECT id, parent_id, name FROM keywords WHERE parent_id IS NULL UNION ALL "
                "SELECT child.id, child.parent_id, tree.path || ' > ' || child.name "
                "FROM keywords child JOIN tree ON child.parent_id = tree.id) "
                "SELECT tree.path FROM image_keywords JOIN tree ON tree.id = image_keywords.keyword_id "
                "WHERE image_keywords.image_id = ? ORDER BY tree.path COLLATE NOCASE",
                (image_id,),
            )).fetchall()
            entries.append((content_hash, [str(row["path"]) for row in rows]))
    finally:
        await connection.close_async(conn, db_path=db_path)
    return [
        await append_entry(db_path, content_hash=content_hash, family="keywords", payload={"paths": paths})
        for content_hash, paths in entries
    ]


async def pull_entries(
    db_path: str,
    *,
    device: str,
    cursors: Mapping[str, int],
    limit: int = PAGE_SIZE,
) -> dict[str, Any]:
    await ensure_schema(db_path)
    bounded = max(1, min(int(limit), PAGE_SIZE))
    normalized_cursors = {str(key): max(0, int(value)) for key, value in cursors.items()}
    conn = await connection.open_async(db_path)
    try:
        rows = await (await conn.execute(
            "SELECT origin, origin_seq, content_hash, family, payload, ts, applied_from "
            "FROM oplog ORDER BY seq"
        )).fetchall()
    finally:
        await connection.close_async(conn, db_path=db_path)
    unseen = [row for row in rows if int(row["origin_seq"]) > normalized_cursors.get(str(row["origin"]), 0)]
    page = unseen[:bounded]
    entries = [_entry_dict(row) for row in page]
    next_cursors = dict(normalized_cursors)
    for entry in entries:
        next_cursors[entry["origin"]] = max(next_cursors.get(entry["origin"], 0), entry["origin_seq"])
    return {"device_id": device, "entries": entries, "cursors": next_cursors, "has_more": len(unseen) > len(page)}


async def local_cursors(db_path: str) -> dict[str, int]:
    await ensure_schema(db_path)
    conn = await connection.open_async(db_path)
    try:
        rows = await (await conn.execute(
            "SELECT origin, last_seen_origin_seq FROM oplog_cursors"
        )).fetchall()
        return {str(row["origin"]): int(row["last_seen_origin_seq"]) for row in rows}
    finally:
        await connection.close_async(conn, db_path=db_path)


async def advance_cursors(db_path: str, cursors: Mapping[str, int]) -> None:
    await ensure_schema(db_path)
    conn = await connection.open_async(db_path)
    try:
        await conn.executemany(
            "INSERT INTO oplog_cursors(origin, last_seen_origin_seq) VALUES (?, ?) "
            "ON CONFLICT(origin) DO UPDATE SET last_seen_origin_seq = "
            "MAX(oplog_cursors.last_seen_origin_seq, excluded.last_seen_origin_seq)",
            [(str(origin), max(0, int(value))) for origin, value in cursors.items()],
        )
        await conn.commit()
    finally:
        await connection.close_async(conn, db_path=db_path)


async def push_cursor(db_path: str) -> int:
    await ensure_schema(db_path)
    conn = await connection.open_async(db_path)
    try:
        row = await (await conn.execute(
            "SELECT value FROM oplog_settings WHERE key = 'last_pushed_origin_seq'"
        )).fetchone()
        return max(0, int(row["value"])) if row else 0
    finally:
        await connection.close_async(conn, db_path=db_path)


async def advance_push_cursor(db_path: str, origin_seq: int) -> None:
    await ensure_schema(db_path)
    conn = await connection.open_async(db_path)
    try:
        current = await (await conn.execute(
            "SELECT value FROM oplog_settings WHERE key = 'last_pushed_origin_seq'"
        )).fetchone()
        value = max(max(0, int(origin_seq)), int(current["value"]) if current else 0)
        await conn.execute(
            "INSERT INTO oplog_settings(key, value) VALUES ('last_pushed_origin_seq', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(value),),
        )
        await conn.commit()
    finally:
        await connection.close_async(conn, db_path=db_path)


async def origin_entries(db_path: str, origin: str, *, after: int = 0, limit: int = PAGE_SIZE) -> list[dict[str, Any]]:
    await ensure_schema(db_path)
    conn = await connection.open_async(db_path)
    try:
        rows = await (await conn.execute(
            "SELECT origin, origin_seq, content_hash, family, payload, ts, applied_from FROM oplog "
            "WHERE origin = ? AND origin_seq > ? ORDER BY origin_seq LIMIT ?",
            (origin, max(0, int(after)), max(1, min(int(limit), PAGE_SIZE))),
        )).fetchall()
        return [_entry_dict(row) for row in rows]
    finally:
        await connection.close_async(conn, db_path=db_path)


async def exchange_with_hub(db_path: str, request: JsonRequest) -> dict[str, int]:
    """Push locally authored entries, then pull and apply every unseen hub page."""

    await retry_pending_entries(db_path)
    local_device = await device_id(db_path)
    pushed = 0
    after = await push_cursor(db_path)
    while True:
        entries = await origin_entries(db_path, local_device, after=after)
        if not entries:
            break
        await request(
            "POST",
            "/api/sync/oplog/push",
            {"device_id": local_device, "entries": entries},
        )
        after = max(int(entry["origin_seq"]) for entry in entries)
        await advance_push_cursor(db_path, after)
        pushed += len(entries)
        if len(entries) < PAGE_SIZE:
            break

    pulled = 0
    while True:
        cursors = await local_cursors(db_path)
        page = await request(
            "POST",
            "/api/sync/oplog/pull",
            {"device_id": local_device, "cursors": cursors},
        )
        entries = list(page.get("entries") or [])
        await apply_entries(db_path, entries, applied_from="hub")
        next_cursors = page.get("cursors") or cursors
        await advance_cursors(db_path, next_cursors)
        pulled += len(entries)
        if not page.get("has_more"):
            break
    return {"pushed": pushed, "pulled": pulled}
