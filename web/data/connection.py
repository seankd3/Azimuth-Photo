"""SQLite connection helpers shared by the compatibility DB facade."""

from __future__ import annotations

import os
import sqlite3
import tempfile

import aiosqlite


async def open_async(db_path: str, *, timeout: int = 30) -> aiosqlite.Connection:
    """Open an async SQLite connection with the row shape expected by callers."""

    conn = await aiosqlite.connect(db_path, timeout=timeout)
    conn.row_factory = aiosqlite.Row
    return conn


def open_sync(
    db_path: str,
    *,
    timeout: int = 30,
    row_factory=sqlite3.Row,
) -> sqlite3.Connection:
    """Open a sync SQLite connection for worker-side bounded queries."""

    conn = sqlite3.connect(db_path, timeout=timeout)
    if row_factory is not None:
        conn.row_factory = row_factory
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


async def enable_wal(conn, *, db_path: str | None = None) -> None:
    if db_path and is_ephemeral_db_path(db_path):
        return
    await conn.execute("PRAGMA journal_mode=WAL")


def is_ephemeral_db_path(db_path: str) -> bool:
    """Return True for temp DBs that should not leave WAL sidecars behind."""

    try:
        path = os.path.realpath(db_path)
        tmp = os.path.realpath(tempfile.gettempdir())
        return os.path.commonpath([tmp, path]) == tmp
    except Exception:
        return False


def _checkpoint_temp_wal_sync(conn: sqlite3.Connection, db_path: str | None) -> None:
    if not db_path or not is_ephemeral_db_path(db_path):
        return
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except sqlite3.Error:
        pass


async def _checkpoint_temp_wal_async(conn, db_path: str | None) -> None:
    if not db_path or not is_ephemeral_db_path(db_path):
        return
    try:
        await conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except Exception:
        pass


def close_sync(conn: sqlite3.Connection, *, db_path: str | None = None) -> None:
    _checkpoint_temp_wal_sync(conn, db_path)
    conn.close()


async def close_async(conn, *, db_path: str | None = None) -> None:
    await _checkpoint_temp_wal_async(conn, db_path)
    await conn.close()
