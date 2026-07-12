"""Satellite identity, durable sync state, and local dirty tracking."""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Iterable

from data import connection
from features.sync.hashing import HASH_PREFIX_BYTES, compute_content_hash, compute_full_hash


HASH_BYTES = HASH_PREFIX_BYTES
SYNC_STATE_DDL = """
CREATE TABLE IF NOT EXISTS sync_state (
    content_hash TEXT PRIMARY KEY,
    image_id INTEGER,
    last_local_change_at REAL,
    last_pushed_at REAL,
    uploaded INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_sync_state_image_id ON sync_state(image_id);
"""


# Runtime-attached hub (standalone → satellite upgrade). Loaded from app
# settings at boot and updated by attach_hub(); env vars always win.
_stored_hub_url = ""
_stored_device_token = ""
_sync_starter = None


def hub_url() -> str:
    env = os.environ.get("PHOTOARCHIVE_HUB_URL", "").strip().rstrip("/")
    return env or _stored_hub_url


def device_token() -> str:
    return os.environ.get("PHOTOARCHIVE_DEVICE_TOKEN", "").strip() or _stored_device_token


def device_auth_headers() -> dict[str, str]:
    token = device_token()
    return {"X-Device-Token": token} if token else {}


def is_satellite_mode() -> bool:
    """Standalone counts: satellite semantics do not require a hub."""

    mode = os.environ.get("PHOTOARCHIVE_MODE", "").strip().lower()
    if mode in ("satellite", "standalone"):
        return True
    if mode:
        return False
    return bool(hub_url())


def has_hub() -> bool:
    return is_satellite_mode() and bool(hub_url())


def bootstrap_payload() -> dict:
    return {
        "mode": "satellite" if is_satellite_mode() else "hub",
        "has_hub": has_hub(),
    }


def load_stored_hub() -> None:
    global _stored_hub_url, _stored_device_token
    try:
        import settings as app_settings

        config = app_settings.get_settings()
    except Exception:
        return
    _stored_hub_url = str(config.get("hub_url") or "").strip().rstrip("/")
    _stored_device_token = str(config.get("device_token") or "").strip()


def register_sync_starter(starter) -> None:
    """App wiring hands us an async callable that boots the sync worker."""

    global _sync_starter
    _sync_starter = starter


async def attach_hub(url: str, token: str = "", hub_id: str = "") -> dict:
    """Runtime standalone → satellite upgrade: persist and start syncing now."""

    global _stored_hub_url, _stored_device_token
    clean = (url or "").strip().rstrip("/")
    if not clean.startswith(("http://", "https://")):
        raise ValueError("Hub URL must start with http:// or https://")
    import settings as app_settings

    config = dict(app_settings.get_settings())
    config["hub_url"] = clean
    config["device_token"] = (token or "").strip()
    if hub_id:
        config["paired_hub_id"] = hub_id
    app_settings.save_settings(config)
    _stored_hub_url = clean
    _stored_device_token = config["device_token"]
    started = False
    if _sync_starter is not None:
        started = bool(await _sync_starter())
    return {"hub": clean, "sync_started": started}


def content_hash_for_file(filepath: str) -> str:
    """Return the FIELD_SPEC identity: BLAKE2b-128(first 8MiB + byte size)."""

    return compute_content_hash(filepath)


async def ensure_sync_state(db_path: str) -> None:
    conn = await connection.open_async(db_path)
    try:
        cursor = await conn.execute("PRAGMA table_info(images)")
        image_columns = {row["name"] for row in await cursor.fetchall()}
        if "content_hash" not in image_columns:
            await conn.execute("ALTER TABLE images ADD COLUMN content_hash TEXT")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_images_content_hash ON images(content_hash)")
        await conn.executescript(SYNC_STATE_DDL)
        await conn.commit()
    finally:
        await connection.close_async(conn, db_path=db_path)


async def record_local_images(db_path: str) -> list[dict]:
    """Discover local originals and persist their stable sync identity."""

    await ensure_sync_state(db_path)
    conn = await connection.open_async(db_path)
    try:
        cursor = await conn.execute(
            "SELECT id, filename, filepath, file_size, date_taken FROM images "
            "WHERE COALESCE(missing_at, 0) = 0 OR missing_at IS NULL"
        )
        images = [dict(row) for row in await cursor.fetchall()]
        items: list[dict] = []
        for image in images:
            filepath = str(image.get("filepath") or "")
            if not filepath or not os.path.isfile(filepath):
                continue
            try:
                stat = os.stat(filepath)
                content_hash = await asyncio.to_thread(content_hash_for_file, filepath)
            except OSError:
                continue
            cursor = await conn.execute(
                "SELECT content_hash FROM sync_state WHERE image_id = ?", (image["id"],)
            )
            previous = await cursor.fetchone()
            if previous is not None and previous["content_hash"] != content_hash:
                await conn.execute("DELETE FROM sync_state WHERE content_hash = ?", (previous["content_hash"],))
            await conn.execute(
                """
                INSERT INTO sync_state(content_hash, image_id, last_local_change_at, uploaded)
                VALUES (?, ?, ?, 0)
                ON CONFLICT(content_hash) DO UPDATE SET image_id = excluded.image_id
                """,
                (content_hash, image["id"], float(stat.st_mtime)),
            )
            await conn.execute(
                "UPDATE images SET content_hash = ? WHERE id = ?", (content_hash, image["id"])
            )
            cursor = await conn.execute(
                "SELECT uploaded FROM sync_state WHERE content_hash = ?", (content_hash,)
            )
            state = await cursor.fetchone()
            uploaded = int(state["uploaded"] if state else 0)
            items.append(
                {
                    "content_hash": content_hash,
                    "image_id": image["id"],
                    "filepath": filepath,
                    "filename": image.get("filename") or os.path.basename(filepath),
                    "bytes": int(stat.st_size),
                    "date_taken": image.get("date_taken"),
                    "uploaded": uploaded,
                    "full_hash": (
                        None
                        if uploaded
                        else await asyncio.to_thread(compute_full_hash, filepath)
                    ),
                }
            )
        await conn.commit()
        return items
    finally:
        await connection.close_async(conn, db_path=db_path)


async def mark_images_dirty(image_ids: Iterable[int], *, db_path: str | None = None) -> None:
    """Mark metadata changed by local user writes without affecting hub mode."""

    if not is_satellite_mode():
        return
    if db_path is None:
        import db
        db_path = db.DB_PATH
    ids = sorted({int(image_id) for image_id in image_ids if int(image_id) > 0})
    if not ids:
        return
    await ensure_sync_state(db_path)
    conn = await connection.open_async(db_path)
    try:
        placeholders = ",".join("?" for _ in ids)
        now = time.time()
        await conn.execute(
            f"UPDATE sync_state SET last_local_change_at = ? WHERE image_id IN ({placeholders})",
            (now, *ids),
        )
        await conn.commit()
    finally:
        await connection.close_async(conn, db_path=db_path)


async def mark_image_dirty(image_id: int, *, db_path: str | None = None) -> None:
    await mark_images_dirty([image_id], db_path=db_path)


def run_sync_pass(*, hub_url: str | None = None, dry_run: bool = False) -> dict:
    """Run one bounded pass for the field-recovery CLI."""

    if dry_run:
        return {"mode": "satellite", "hub": (hub_url or os.environ.get("PHOTOARCHIVE_HUB_URL", "")).rstrip("/"), "dry_run": True}
    import db
    from features.sync.sync_worker import SyncWorker

    worker = SyncWorker(db_path=db.DB_PATH, hub=hub_url)
    asyncio.run(worker.sync_once())
    return worker.status()


run_sync_once = run_sync_pass
