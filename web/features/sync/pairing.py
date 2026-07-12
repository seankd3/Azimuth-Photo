"""Device pairing: one-time codes, device registry, hub identity."""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
import uuid
from typing import Any

from data import connection

PAIR_CODE_TTL_SECONDS = 10 * 60
PAIR_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O/1/I
_pending_codes: dict[str, dict[str, Any]] = {}


DEVICES_DDL = """
CREATE TABLE IF NOT EXISTS devices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    platform TEXT NOT NULL DEFAULT '',
    token_hash TEXT NOT NULL UNIQUE,
    created_at REAL NOT NULL,
    last_seen REAL,
    revoked_at REAL
);
CREATE INDEX IF NOT EXISTS idx_devices_token_hash ON devices(token_hash);
CREATE INDEX IF NOT EXISTS idx_devices_revoked_at ON devices(revoked_at);
"""


async def ensure_devices_schema(db_path: str) -> None:
    conn = await connection.open_async(db_path)
    try:
        await conn.executescript(DEVICES_DDL)
        await conn.commit()
    finally:
        await connection.close_async(conn, db_path=db_path)


async def _hub_id_on_conn(conn) -> str:
    row = await (await conn.execute(
        "SELECT value FROM oplog_settings WHERE key = 'hub_id'"
    )).fetchone()
    if row:
        return str(row["value"])
    value = str(uuid.uuid4())
    await conn.execute(
        "INSERT OR IGNORE INTO oplog_settings(key, value) VALUES ('hub_id', ?)",
        (value,),
    )
    row = await (await conn.execute(
        "SELECT value FROM oplog_settings WHERE key = 'hub_id'"
    )).fetchone()
    return str(row["value"])


async def get_hub_id(db_path: str) -> str:
    """Stable hub UUID, created once and persisted in oplog_settings."""
    await ensure_devices_schema(db_path)
    conn = await connection.open_async(db_path)
    try:
        # oplog_settings may not exist on a brand-new partial DB in tests
        await conn.execute(
            "CREATE TABLE IF NOT EXISTS oplog_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        value = await _hub_id_on_conn(conn)
        await conn.commit()
        return value
    finally:
        await connection.close_async(conn, db_path=db_path)


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _purge_expired_codes(now: float | None = None) -> None:
    now = time.time() if now is None else now
    expired = [code for code, row in _pending_codes.items() if float(row["expires_at"]) <= now]
    for code in expired:
        _pending_codes.pop(code, None)


def create_pair_code(*, hub_url: str) -> dict[str, Any]:
    """Create a one-time 8-char pair code (crypto-random, 10-min TTL)."""
    _purge_expired_codes()
    for _ in range(32):
        code = "".join(secrets.choice(PAIR_CODE_ALPHABET) for _ in range(8))
        if code not in _pending_codes:
            break
    else:
        raise RuntimeError("could not allocate a unique pair code")
    expires_at = time.time() + PAIR_CODE_TTL_SECONDS
    payload = json.dumps({"hub_url": hub_url.rstrip("/"), "code": code}, separators=(",", ":"))
    from features.sync import qr_encode

    png = qr_encode.encode_png(payload, scale=6, border=3)
    _pending_codes[code] = {
        "code": code,
        "hub_url": hub_url.rstrip("/"),
        "expires_at": expires_at,
        "used": False,
    }
    return {
        "code": code,
        "expires_at": expires_at,
        "hub_url": hub_url.rstrip("/"),
        "qr_payload": payload,
        "qr_png_base64": base64.b64encode(png).decode("ascii"),
    }


def peek_pair_code(code: str) -> dict[str, Any] | None:
    _purge_expired_codes()
    row = _pending_codes.get(str(code or "").strip().upper())
    if not row or row.get("used"):
        return None
    if float(row["expires_at"]) <= time.time():
        _pending_codes.pop(row["code"], None)
        return None
    return dict(row)


def _consume_pair_code(code: str) -> dict[str, Any]:
    _purge_expired_codes()
    normalized = str(code or "").strip().upper()
    row = _pending_codes.get(normalized)
    if row is None:
        raise LookupError("invalid or expired pair code")
    if row.get("used"):
        raise LookupError("pair code already used")
    if float(row["expires_at"]) <= time.time():
        _pending_codes.pop(normalized, None)
        raise LookupError("invalid or expired pair code")
    row["used"] = True
    _pending_codes.pop(normalized, None)
    return row


async def pair_device(
    db_path: str,
    *,
    code: str,
    device_name: str,
    platform: str = "",
) -> dict[str, Any]:
    """Redeem a one-time code and register a device. Returns device_token + hub_id."""
    name = str(device_name or "").strip() or "Device"
    plat = str(platform or "").strip()[:64]
    _consume_pair_code(code)
    token = secrets.token_urlsafe(32)
    token_hash = _hash_token(token)
    now = time.time()
    hub_id = await get_hub_id(db_path)
    await ensure_devices_schema(db_path)
    conn = await connection.open_async(db_path)
    try:
        cursor = await conn.execute(
            "INSERT INTO devices(name, platform, token_hash, created_at, last_seen, revoked_at) "
            "VALUES (?, ?, ?, ?, ?, NULL)",
            (name[:120], plat, token_hash, now, now),
        )
        device_id = int(cursor.lastrowid)
        await conn.commit()
    finally:
        await connection.close_async(conn, db_path=db_path)
    return {
        "device_token": token,
        "hub_id": hub_id,
        "device_id": device_id,
        "name": name[:120],
        "platform": plat,
    }


async def list_devices(db_path: str) -> list[dict[str, Any]]:
    await ensure_devices_schema(db_path)
    conn = await connection.open_async(db_path)
    try:
        rows = await (await conn.execute(
            "SELECT id, name, platform, created_at, last_seen, revoked_at "
            "FROM devices ORDER BY created_at DESC, id DESC"
        )).fetchall()
        return [
            {
                "id": int(row["id"]),
                "name": str(row["name"]),
                "platform": str(row["platform"] or ""),
                "created_at": float(row["created_at"]),
                "last_seen": float(row["last_seen"]) if row["last_seen"] is not None else None,
                "revoked": row["revoked_at"] is not None,
                "revoked_at": float(row["revoked_at"]) if row["revoked_at"] is not None else None,
            }
            for row in rows
        ]
    finally:
        await connection.close_async(conn, db_path=db_path)


async def revoke_device(db_path: str, device_id: int) -> dict[str, Any]:
    await ensure_devices_schema(db_path)
    conn = await connection.open_async(db_path)
    try:
        now = time.time()
        cursor = await conn.execute(
            "UPDATE devices SET revoked_at = COALESCE(revoked_at, ?) WHERE id = ?",
            (now, int(device_id)),
        )
        if cursor.rowcount <= 0:
            raise LookupError("device not found")
        await conn.commit()
        return {"ok": True, "id": int(device_id)}
    finally:
        await connection.close_async(conn, db_path=db_path)


async def authenticate_device_token(db_path: str, token: str | None) -> dict[str, Any] | None:
    """Return the active device row for a raw token, or None if unknown/revoked."""
    raw = str(token or "").strip()
    if not raw:
        return None
    await ensure_devices_schema(db_path)
    token_hash = _hash_token(raw)
    conn = await connection.open_async(db_path)
    try:
        row = await (await conn.execute(
            "SELECT id, name, platform, created_at, last_seen, revoked_at "
            "FROM devices WHERE token_hash = ?",
            (token_hash,),
        )).fetchone()
        if row is None or row["revoked_at"] is not None:
            return None
        now = time.time()
        await conn.execute("UPDATE devices SET last_seen = ? WHERE id = ?", (now, int(row["id"])))
        await conn.commit()
        return {
            "id": int(row["id"]),
            "name": str(row["name"]),
            "platform": str(row["platform"] or ""),
            "created_at": float(row["created_at"]),
            "last_seen": now,
            "revoked": False,
        }
    finally:
        await connection.close_async(conn, db_path=db_path)


def clear_pending_codes_for_tests() -> None:
    _pending_codes.clear()


def inject_pending_code_for_tests(code: str, *, expires_at: float, used: bool = False) -> None:
    _pending_codes[code.upper()] = {
        "code": code.upper(),
        "hub_url": "http://test",
        "expires_at": expires_at,
        "used": used,
    }
