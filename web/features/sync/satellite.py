"""Satellite identity, durable sync state, and local dirty tracking."""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Iterable

from data import connection
from features.sync.hashing import (
    compute_content_hash,
    compute_full_hash,
    compute_hash_pair,
)
from features.sync.executor import run_sync_work
from features.sync.validation import validate_content_hash


SYNC_STATE_DDL = """
CREATE TABLE IF NOT EXISTS sync_state (
    content_hash TEXT PRIMARY KEY,
    image_id INTEGER,
    last_local_change_at REAL,
    last_pushed_at REAL,
    uploaded INTEGER NOT NULL DEFAULT 0,
    full_hash TEXT,
    file_size INTEGER,
    file_modified_ns INTEGER,
    uploaded_at REAL,
    hub_image_id INTEGER
);
CREATE INDEX IF NOT EXISTS idx_sync_state_image_id ON sync_state(image_id);
"""

SYNC_STATE_COLUMNS = {
    "full_hash": "TEXT",
    "file_size": "INTEGER",
    "file_modified_ns": "INTEGER",
    "uploaded_at": "REAL",
    "hub_image_id": "INTEGER",
}


# Runtime-attached hub (standalone → satellite upgrade). Loaded from app
# settings at boot and updated by attach_hub(); env vars always win.
_stored_hub_url = ""
_stored_device_token = ""
_sync_starter = None


def hub_url() -> str:
    env = os.environ.get("AZIMUTH_HUB_URL", "").strip().rstrip("/")
    return env or _stored_hub_url


def device_token() -> str:
    return os.environ.get("AZIMUTH_DEVICE_TOKEN", "").strip() or _stored_device_token


def device_auth_headers() -> dict[str, str]:
    token = device_token()
    return {"X-Device-Token": token} if token else {}


def hub_request_headers() -> dict[str, str]:
    """Shared headers for every request a satellite sends to its hub."""

    from features.sync.contract import request_headers

    return {**device_auth_headers(), **request_headers()}


def is_satellite_mode() -> bool:
    """Standalone counts: satellite semantics do not require a hub."""

    mode = os.environ.get("AZIMUTH_MODE", "").strip().lower()
    if mode in ("satellite", "standalone"):
        return True
    if mode:
        return False
    return bool(hub_url())


def has_hub() -> bool:
    return is_satellite_mode() and bool(hub_url())


def defers_bulk_compute() -> bool:
    """Only a hub-backed peer defers bulk engine work to its hub.

    A standalone install holds the canonical library, so it arms everything a
    hub arms — AI, faces, captions, preview pregen — budgeted for its host.
    """

    mode = os.environ.get("AZIMUTH_MODE", "").strip().lower()
    return mode == "satellite" or has_hub()


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
        state_columns = {
            row["name"]
            for row in await (
                await conn.execute("PRAGMA table_info(sync_state)")
            ).fetchall()
        }
        for name, declaration in SYNC_STATE_COLUMNS.items():
            if name not in state_columns:
                await conn.execute(
                    f"ALTER TABLE sync_state ADD COLUMN {name} {declaration}"
                )
        await conn.commit()
    finally:
        await connection.close_async(conn, db_path=db_path)


def _stored_hash(value: object) -> str | None:
    try:
        return validate_content_hash(str(value or ""))
    except ValueError:
        return None


async def _persist_local_records(db_path: str, records: list[dict]) -> None:
    if not records:
        return
    conn = await connection.open_async(db_path)
    try:
        for record in records:
            previous_hash = record.get("previous_hash")
            if (
                previous_hash
                and previous_hash != record["content_hash"]
                and int(record.get("previous_image_id") or 0) == int(record["image_id"])
            ):
                await conn.execute(
                    "DELETE FROM sync_state WHERE content_hash = ? AND image_id = ?",
                    (previous_hash, int(record["image_id"])),
                )
            await conn.execute(
                """
                INSERT INTO sync_state(
                    content_hash, image_id, last_local_change_at, last_pushed_at,
                    uploaded, full_hash, file_size, file_modified_ns,
                    uploaded_at, hub_image_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(content_hash) DO UPDATE SET
                    image_id = excluded.image_id,
                    last_local_change_at = MAX(
                        COALESCE(sync_state.last_local_change_at, 0),
                        excluded.last_local_change_at
                    ),
                    last_pushed_at = COALESCE(
                        sync_state.last_pushed_at,
                        excluded.last_pushed_at
                    ),
                    uploaded = CASE
                        WHEN sync_state.full_hash IS NOT NULL
                          AND sync_state.full_hash = excluded.full_hash
                        THEN MAX(sync_state.uploaded, excluded.uploaded)
                        ELSE excluded.uploaded
                    END,
                    full_hash = COALESCE(sync_state.full_hash, excluded.full_hash),
                    file_size = excluded.file_size,
                    file_modified_ns = excluded.file_modified_ns,
                    uploaded_at = COALESCE(
                        sync_state.uploaded_at,
                        excluded.uploaded_at
                    ),
                    hub_image_id = COALESCE(
                        sync_state.hub_image_id,
                        excluded.hub_image_id
                    )
                """,
                (
                    record["content_hash"],
                    int(record["image_id"]),
                    float(record["last_local_change_at"]),
                    record.get("last_pushed_at"),
                    int(record["uploaded"]),
                    record["full_hash"],
                    int(record["file_size"]),
                    int(record["file_modified_ns"]),
                    record.get("uploaded_at"),
                    record.get("hub_image_id"),
                ),
            )
            await conn.execute(
                """
                UPDATE images
                SET content_hash = ?, file_size = ?, file_modified_at = ?
                WHERE id = ?
                """,
                (
                    record["content_hash"],
                    int(record["file_size"]),
                    float(record["file_modified_at"]),
                    int(record["image_id"]),
                ),
            )
        await conn.commit()
    finally:
        await connection.close_async(conn, db_path=db_path)


async def record_local_images(db_path: str) -> list[dict]:
    """Discover local originals and persist reusable full-file proofs.

    A file whose size and nanosecond mtime match its durable sync-state receipt
    is not re-read. New or changed files are hashed once, then the fingerprint
    and complete hash are committed in a short batch so a large laptop scan
    never holds SQLite's writer lock while reading original bytes.
    """

    await ensure_sync_state(db_path)
    conn = await connection.open_async(db_path)
    try:
        image_columns = {row["name"] for row in await (await conn.execute("PRAGMA table_info(images)")).fetchall()}
        # Hub-mirrored rows have no local original; hashing them wastes the
        # dedicated sync pool and holds a write lock across 100k+ no-ops.
        hub_filter = "AND COALESCE(i.hub_remote, 0) = 0" if "hub_remote" in image_columns else ""
        cursor = await conn.execute(
            """
            SELECT i.id, i.filename, i.filepath, i.file_size, i.file_modified_at,
                   i.date_taken, i.content_hash AS image_content_hash,
                   s.content_hash AS sync_content_hash, s.image_id AS sync_image_id,
                   s.last_local_change_at, s.last_pushed_at, s.uploaded,
                   s.full_hash, s.file_size AS sync_file_size,
                   s.file_modified_ns, s.uploaded_at,
                   s.hub_image_id AS sync_hub_image_id
            FROM images i
            LEFT JOIN sync_state s ON s.content_hash = i.content_hash
            WHERE (COALESCE(i.missing_at, 0) = 0 OR i.missing_at IS NULL)
            """
            + f" {hub_filter}"
        )
        images = [dict(row) for row in await cursor.fetchall()]
    finally:
        await connection.close_async(conn, db_path=db_path)

    items: list[dict] = []
    records: list[dict] = []
    for image in images:
        filepath = str(image.get("filepath") or "")
        if not filepath:
            continue
        try:
            stat = await run_sync_work(os.stat, filepath)
        except OSError:
            continue
        if not os.path.isfile(filepath):
            continue

        previous_hash = _stored_hash(
            image.get("sync_content_hash") or image.get("image_content_hash")
        )
        previous_full_hash = _stored_hash(image.get("full_hash"))
        fingerprint_matches = bool(
            previous_hash
            and image.get("sync_file_size") is not None
            and image.get("file_modified_ns") is not None
            and int(image["sync_file_size"]) == int(stat.st_size)
            and int(image["file_modified_ns"]) == int(stat.st_mtime_ns)
        )

        if fingerprint_matches:
            content_hash = previous_hash
            full_hash = previous_full_hash
        else:
            try:
                content_hash, full_hash = await run_sync_work(
                    compute_hash_pair,
                    filepath,
                )
            except OSError:
                continue
            # FIELD_SPEC's fast identity covers the first 8 MiB + size. If a
            # same-size tail edit collides with that fast identity, promote the
            # complete hash to the sync identity instead of treating changed
            # bytes as already uploaded.
            if (
                previous_hash == content_hash
                and previous_full_hash
                and previous_full_hash != full_hash
            ):
                content_hash = full_hash

        if full_hash is None:
            try:
                full_hash = await run_sync_work(compute_full_hash, filepath)
            except OSError:
                continue

        same_identity = bool(
            previous_hash == content_hash
            and (
                previous_full_hash is None
                or previous_full_hash == full_hash
            )
        )
        # A receipt created before full-file proofs existed is not deletion
        # authority. Mark it pending once so the next manifest teaches the hub
        # the complete hash; a known hub object then becomes uploaded again
        # without retransmitting the original.
        uploaded = (
            int(image.get("uploaded") or 0)
            if same_identity and previous_full_hash is not None
            else 0
        )
        last_local_change_at = (
            float(image["last_local_change_at"])
            if same_identity and image.get("last_local_change_at") is not None
            else float(stat.st_mtime)
        )
        record = {
            "content_hash": content_hash,
            "full_hash": full_hash,
            "image_id": int(image["id"]),
            "file_size": int(stat.st_size),
            "file_modified_at": float(stat.st_mtime),
            "file_modified_ns": int(stat.st_mtime_ns),
            "last_local_change_at": last_local_change_at,
            "last_pushed_at": image.get("last_pushed_at") if same_identity else None,
            "uploaded": uploaded,
            "uploaded_at": image.get("uploaded_at") if same_identity else None,
            "hub_image_id": image.get("sync_hub_image_id") if same_identity else None,
            "previous_hash": previous_hash,
            "previous_image_id": image.get("sync_image_id"),
        }
        records.append(record)
        items.append(
            {
                "content_hash": content_hash,
                "full_hash": None if uploaded else full_hash,
                "image_id": int(image["id"]),
                "filepath": filepath,
                "filename": image.get("filename") or os.path.basename(filepath),
                "bytes": int(stat.st_size),
                "date_taken": image.get("date_taken"),
                "uploaded": uploaded,
            }
        )
        if len(records) >= 25:
            await _persist_local_records(db_path, records)
            records.clear()

    await _persist_local_records(db_path, records)
    return items


async def pending_upload_snapshot(db_path: str) -> list[dict]:
    """Cheap queue depth read — no hashing, no write lock."""

    await ensure_sync_state(db_path)
    conn = await connection.open_async(db_path)
    try:
        cursor = await conn.execute(
            """
            SELECT s.content_hash, s.full_hash, s.image_id, i.filename,
                   i.filepath, i.file_size AS bytes, i.date_taken, s.uploaded,
                   s.uploaded_at, s.hub_image_id
            FROM sync_state s
            JOIN images i ON i.id = s.image_id
            WHERE COALESCE(s.uploaded, 0) = 0
            """
        )
        return [dict(row) for row in await cursor.fetchall()]
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
        return {"mode": "satellite", "hub": (hub_url or os.environ.get("AZIMUTH_HUB_URL", "")).rstrip("/"), "dry_run": True}
    import db
    from features.sync.sync_worker import SyncWorker

    worker = SyncWorker(db_path=db.DB_PATH, hub=hub_url)
    asyncio.run(worker.sync_once())
    return worker.status()


