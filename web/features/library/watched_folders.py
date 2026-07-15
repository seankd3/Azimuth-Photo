"""Watched-folder discovery that reuses the catalog's image registration path."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import Callable

import db
import scanner
from data import connection
from data.repositories import catalog as catalog_repository


log = logging.getLogger(__name__)
POLL_INTERVAL_SECONDS = 5 * 60
SCAN_BATCH_SIZE = 100
CREATE_WATCHED_FOLDERS = """
CREATE TABLE IF NOT EXISTS watched_folders (
    id INTEGER PRIMARY KEY,
    path TEXT NOT NULL UNIQUE,
    recursive INTEGER NOT NULL DEFAULT 1,
    enabled INTEGER NOT NULL DEFAULT 1,
    last_scan_at REAL DEFAULT NULL,
    created_at REAL NOT NULL DEFAULT (strftime('%s', 'now'))
)
"""

_scan_lock = asyncio.Lock()


def normalize_path(path: str) -> str:
    return os.path.realpath(os.path.abspath(os.path.expanduser(str(path or '').strip())))


def _as_folder(row) -> dict:
    folder = dict(row)
    folder['recursive'] = bool(folder.get('recursive'))
    folder['enabled'] = bool(folder.get('enabled'))
    folder['online'] = os.path.isdir(folder['path'])
    return folder


async def ensure_schema(db_path: str) -> None:
    conn = await connection.open_async(db_path)
    try:
        await conn.execute(CREATE_WATCHED_FOLDERS)
        await conn.execute('CREATE INDEX IF NOT EXISTS idx_watched_folders_enabled ON watched_folders(enabled)')
        await conn.commit()
    finally:
        await connection.close_async(conn, db_path=db_path)


async def list_folders(db_path: str) -> list[dict]:
    await ensure_schema(db_path)
    conn = await connection.open_async(db_path)
    try:
        cursor = await conn.execute(
            'SELECT id, path, recursive, enabled, last_scan_at, created_at FROM watched_folders ORDER BY created_at, id'
        )
        return [_as_folder(row) for row in await cursor.fetchall()]
    finally:
        await connection.close_async(conn, db_path=db_path)


async def add_folder(db_path: str, path: str, *, recursive: bool = True, enabled: bool = True) -> dict:
    normalized = normalize_path(path)
    if not normalized or not os.path.isdir(normalized):
        raise ValueError('Choose an accessible folder')
    await ensure_schema(db_path)
    conn = await connection.open_async(db_path)
    try:
        await conn.execute(
            'INSERT INTO watched_folders(path, recursive, enabled) VALUES (?, ?, ?) '
            'ON CONFLICT(path) DO UPDATE SET recursive = excluded.recursive, enabled = excluded.enabled',
            (normalized, int(bool(recursive)), int(bool(enabled))),
        )
        cursor = await conn.execute(
            'SELECT id, path, recursive, enabled, last_scan_at, created_at FROM watched_folders WHERE path = ?',
            (normalized,),
        )
        folder = _as_folder(await cursor.fetchone())
        await conn.commit()
        return folder
    finally:
        await connection.close_async(conn, db_path=db_path)


async def update_folder(db_path: str, folder_id: int, *, enabled: bool | None = None, recursive: bool | None = None) -> dict | None:
    await ensure_schema(db_path)
    updates = []
    values = []
    if enabled is not None:
        updates.append('enabled = ?')
        values.append(int(bool(enabled)))
    if recursive is not None:
        updates.append('recursive = ?')
        values.append(int(bool(recursive)))
    conn = await connection.open_async(db_path)
    try:
        if updates:
            values.append(int(folder_id))
            await conn.execute(f'UPDATE watched_folders SET {", ".join(updates)} WHERE id = ?', values)
            await conn.commit()
        cursor = await conn.execute(
            'SELECT id, path, recursive, enabled, last_scan_at, created_at FROM watched_folders WHERE id = ?',
            (int(folder_id),),
        )
        row = await cursor.fetchone()
        return _as_folder(row) if row else None
    finally:
        await connection.close_async(conn, db_path=db_path)


async def remove_folder(db_path: str, folder_id: int) -> bool:
    await ensure_schema(db_path)
    conn = await connection.open_async(db_path)
    try:
        cursor = await conn.execute('DELETE FROM watched_folders WHERE id = ?', (int(folder_id),))
        await conn.commit()
        return bool(cursor.rowcount)
    finally:
        await connection.close_async(conn, db_path=db_path)


def _newer_image_rows(
    path: str,
    *,
    recursive: bool,
    after: float | None,
    known_signatures: set[tuple[str, str, int]],
):
    root = normalize_path(path)
    for row in scanner.walk_images(root):
        if not recursive and os.path.dirname(row[1]) != root:
            continue
        modified_at = row[4]
        signature = (str(row[0]), str(row[1]), int(row[3] or 0))
        if (
            signature in known_signatures
            and after is not None
            and (modified_at is None or modified_at <= after)
        ):
            continue
        yield row


async def _mark_scanned(db_path: str, folder_id: int, scanned_at: float) -> None:
    conn = await connection.open_async(db_path)
    try:
        await conn.execute('UPDATE watched_folders SET last_scan_at = ? WHERE id = ?', (scanned_at, int(folder_id)))
        await conn.commit()
    finally:
        await connection.close_async(conn, db_path=db_path)


async def scan_folder(db_path: str, folder_id: int, *, allow_disabled: bool = False) -> dict:
    folder = await update_folder(db_path, folder_id)
    if not folder:
        raise LookupError('Watched folder not found')
    if not folder['enabled'] and not allow_disabled:
        return {'ok': True, 'folder': folder, 'registered': 0, 'skipped': 'disabled'}
    if not folder['online']:
        message = 'Folder is offline or no longer accessible'
        log.warning('worker=watched_folder_scan folder_id=%s path=%r skipped=%s', folder_id, folder['path'], message)
        return {'ok': False, 'folder': folder, 'registered': 0, 'error': message}

    async with _scan_lock:
        # A normal catalog scan owns scanner.scan_state and may mark unseen rows missing.
        # Yield this pass instead of racing it; the next poll remains lossless because last_scan_at is unchanged.
        if scanner.scan_state.get('scanning'):
            return {'ok': False, 'folder': folder, 'registered': 0, 'error': 'Catalog scan already in progress'}

        source = await db.add_or_restore_source(folder['path'])
        known_signatures = await catalog_repository.image_signatures_for_source(
            db_path, int(source['id'])
        )
        registered = 0
        batch = []
        started_at = time.time()
        try:
            for row in _newer_image_rows(
                folder['path'], recursive=folder['recursive'],
                after=folder.get('last_scan_at'), known_signatures=known_signatures,
            ):
                batch.append(row)
                if len(batch) >= SCAN_BATCH_SIZE:
                    await db.insert_images_batch(batch, source_id=int(source['id']))
                    registered += len(batch)
                    batch = []
                    await asyncio.sleep(0)
            if batch:
                await db.insert_images_batch(batch, source_id=int(source['id']))
                registered += len(batch)
            await _mark_scanned(db_path, folder_id, time.time())
        except Exception as exc:
            log.exception('worker=watched_folder_scan folder_id=%s path=%r failed', folder_id, folder['path'])
            return {'ok': False, 'folder': folder, 'registered': registered, 'error': str(exc)}

    refreshed = await update_folder(db_path, folder_id)
    log.info(
        'worker=watched_folder_scan folder_id=%s path=%r registered=%s duration_seconds=%.3f',
        folder_id, folder['path'], registered, time.time() - started_at,
    )
    return {'ok': True, 'folder': refreshed, 'registered': registered}


async def scan_enabled_folders(db_path: str) -> list[dict]:
    return [
        await scan_folder(db_path, folder['id'])
        for folder in await list_folders(db_path)
        if folder['enabled']
    ]


async def run_poller(db_path: Callable[[], str], *, interval_seconds: float = POLL_INTERVAL_SECONDS) -> None:
    log.info('worker=watched_folder_poller started interval_seconds=%s', interval_seconds)
    while True:
        try:
            await scan_enabled_folders(db_path())
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception('worker=watched_folder_poller pass failed')
        await asyncio.sleep(interval_seconds)
