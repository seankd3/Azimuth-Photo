"""SQLite connection helpers shared by the compatibility DB facade."""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import os
import sqlite3
import tempfile
from collections.abc import Awaitable, Callable
from typing import TypeVar

import aiosqlite

from core.path_groups import safe_commonpath

_sqlite_timeout_seconds = contextvars.ContextVar("photoarchive_sqlite_timeout_seconds", default=None)

# User-facing writes: short busy retries so a transient embedding/thumb lock
# never surfaces as HTTP 500. Do not blanket-wrap background workers.
USER_WRITE_LOCK_RETRIES = 3
USER_WRITE_LOCK_BACKOFF_SECONDS = 0.25

T = TypeVar("T")


def _effective_timeout(timeout: float | None) -> float:
    if timeout is not None:
        return float(timeout)
    context_timeout = _sqlite_timeout_seconds.get()
    if context_timeout is not None:
        return float(context_timeout)
    return 30.0


@contextlib.contextmanager
def sqlite_timeout(seconds: float):
    token = _sqlite_timeout_seconds.set(max(0.001, float(seconds)))
    try:
        yield
    finally:
        _sqlite_timeout_seconds.reset(token)


def is_sqlite_locked_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    return "database is locked" in text or "database table is locked" in text or "database schema is locked" in text


async def run_with_busy_retry(
    operation: Callable[[], Awaitable[T]],
    *,
    retries: int = USER_WRITE_LOCK_RETRIES,
    backoff_seconds: float = USER_WRITE_LOCK_BACKOFF_SECONDS,
) -> T:
    """Retry a user-facing write a bounded number of times on SQLite lock storms."""

    attempt = 0
    while True:
        try:
            return await operation()
        except Exception as exc:
            if not is_sqlite_locked_error(exc) or attempt >= retries:
                raise
            attempt += 1
            await asyncio.sleep(backoff_seconds)


async def open_async(db_path: str, *, timeout: float | None = None) -> aiosqlite.Connection:
    """Open an async SQLite connection with the row shape expected by callers."""

    effective_timeout = _effective_timeout(timeout)
    conn = await aiosqlite.connect(db_path, timeout=effective_timeout)
    conn.row_factory = aiosqlite.Row
    await conn.execute(f"PRAGMA busy_timeout={int(effective_timeout * 1000)}")
    await conn.execute("PRAGMA foreign_keys=ON")
    await conn.execute("PRAGMA synchronous=NORMAL")
    await conn.execute("PRAGMA temp_store=MEMORY")
    try:
        # Best-effort tuning; some filesystems reject mmap or large caches.
        await conn.execute("PRAGMA cache_size=-32000")
        await conn.execute("PRAGMA mmap_size=268435456")
    except Exception:
        pass
    return conn


def open_sync(
    db_path: str,
    *,
    timeout: float | None = None,
    row_factory=sqlite3.Row,
) -> sqlite3.Connection:
    """Open a sync SQLite connection for worker-side bounded queries."""

    effective_timeout = _effective_timeout(timeout)
    conn = sqlite3.connect(db_path, timeout=effective_timeout)
    if row_factory is not None:
        conn.row_factory = row_factory
    conn.execute(f"PRAGMA busy_timeout={int(effective_timeout * 1000)}")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA temp_store=MEMORY")
    try:
        # Best-effort tuning; some filesystems reject mmap or large caches.
        conn.execute("PRAGMA cache_size=-32000")
        conn.execute("PRAGMA mmap_size=268435456")
    except Exception:
        pass
    return conn


async def enable_wal(conn, *, db_path: str | None = None) -> None:
    if db_path and is_ephemeral_db_path(db_path):
        return
    cursor = await conn.execute("PRAGMA journal_mode=WAL")
    try:
        await cursor.fetchone()
    finally:
        await cursor.close()


def is_ephemeral_db_path(db_path: str) -> bool:
    """Return True for temp DBs that should not leave WAL sidecars behind."""

    try:
        path = os.path.realpath(db_path)
        tmp = os.path.realpath(tempfile.gettempdir())
        common = safe_commonpath([tmp, path])
        return common == tmp if common is not None else False
    except Exception:
        return False


def _checkpoint_temp_wal_sync(conn: sqlite3.Connection, db_path: str | None) -> None:
    if not db_path or not is_ephemeral_db_path(db_path):
        return
    try:
        # PASSIVE (not TRUNCATE): never take the exclusive checkpoint lock, so a
        # large ephemeral WAL cannot stall concurrent writers on close. On the
        # final uncontended close this still checkpoints all committed frames.
        conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
    except sqlite3.Error:
        pass


async def _checkpoint_temp_wal_async(conn, db_path: str | None) -> None:
    if not db_path or not is_ephemeral_db_path(db_path):
        return
    try:
        # PASSIVE (not TRUNCATE): never take the exclusive checkpoint lock, so a
        # large ephemeral WAL cannot stall concurrent writers on close.
        await conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
    except Exception:
        pass


def close_sync(conn: sqlite3.Connection, *, db_path: str | None = None) -> None:
    _checkpoint_temp_wal_sync(conn, db_path)
    conn.close()


async def close_async(conn, *, db_path: str | None = None) -> None:
    await _checkpoint_temp_wal_async(conn, db_path)
    await conn.close()
