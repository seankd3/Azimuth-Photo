"""SQLite connection helpers shared by the compatibility DB facade."""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import functools
import os
import sqlite3
import tempfile
from collections.abc import Awaitable, Callable
from typing import TypeVar

import aiosqlite

from core.path_groups import safe_commonpath

_sqlite_timeout_seconds = contextvars.ContextVar("azimuth_sqlite_timeout_seconds", default=None)

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


# Each aiosqlite connection is a Python thread plus seven PRAGMAs (including a
# 256MB mmap). "Open per query, close after" meant boot warmers churned through
# hundreds of such threads in seconds (py-spy 07-30: worker threads #77-#365
# alive at once), and the GIL contention queued interactive requests for whole
# seconds. Durable connections go back on a per-catalog idle shelf instead of
# being torn down; callers keep the exact open/close API they always had.
_POOL_MAX_IDLE = 8
_idle_connections: dict[str, list[aiosqlite.Connection]] = {}


async def open_async(db_path: str, *, timeout: float | None = None) -> aiosqlite.Connection:
    """Open (or reuse) an async SQLite connection with the expected row shape.

    How long to wait for a busy database is set with a PRAGMA on checkout, not
    baked in when the connection is opened. It used to be the latter, which
    quietly opted every caller asking for a short wait out of the pool — and the
    callers asking for a short wait are the interactive ones: the grid, compare,
    trash. So the requests that most need to be quick were the only ones opening
    a fresh connection, and opening a fresh connection to a large WAL catalog is
    not quick. Measured on the owner's laptop: 29 of them alive at once, each
    with its own thread, while the first page of photos waited.
    """

    effective_timeout = _effective_timeout(timeout)
    pool_key = None if is_ephemeral_db_path(db_path) else db_path
    if pool_key is not None:
        idle = _idle_connections.get(pool_key)
        if idle:
            conn = idle.pop()
            conn._azimuth_shelved = False
            await conn.execute(f"PRAGMA busy_timeout={int(effective_timeout * 1000)}")
            return conn
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
    conn._azimuth_pool_key = pool_key
    return conn


async def release_database(db_path: str) -> None:
    """Let go of every connection this process holds to one database file.

    Windows will not delete a file another handle still has open, so a test that
    unlinks its temporary catalog while the pool still holds an idle connection
    fails — randomly, and in a different test each run, which teaches everyone
    reading the suite to ignore red. Releasing by path makes it deterministic,
    and gives production a way to hand a database back (a library switch) that
    does not mean tearing down every other one too.
    """

    drop_inline_reader(db_path)
    for conn in _idle_connections.pop(db_path, []):
        try:
            await conn.close()
        except Exception:
            pass


async def close_shared_readers() -> None:
    for db_path in list(_inline_readers):
        drop_inline_reader(db_path)
    for db_path, idle in list(_idle_connections.items()):
        _idle_connections.pop(db_path, None)
        for conn in idle:
            try:
                await conn.close()
            except Exception:
                pass


# A tile's catalog row is a primary-key fetch measured in tenths of a
# millisecond, but riding aiosqlite means two hops through one worker thread —
# and during boot that queue backs up to 0.6-1.5s per tile (measured 07-30).
# For sub-millisecond reads the loop itself is the fastest executor there is.
_inline_readers: dict[str, sqlite3.Connection] = {}
_inline_unsuitable: set[str] = set()


def inline_reader(db_path: str) -> sqlite3.Connection:
    """Long-lived read-only connection for sub-millisecond lookups on the loop.

    Read-only mode can never hold a write lock, and WAL readers see every
    commit, so sharing one autocommit connection across requests is safe.
    Callers run queries directly on the event loop — only primary-key-shaped
    reads belong here.
    """

    conn = _inline_readers.get(db_path)
    if conn is not None:
        return conn
    if db_path in _inline_unsuitable:
        raise sqlite3.OperationalError(f"catalog is not WAL: {db_path}")
    conn = sqlite3.connect(
        f"file:{db_path}?mode=ro",
        uri=True,
        timeout=_effective_timeout(None),
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA mmap_size=268435456")
    except Exception:
        pass
    # Only WAL catalogs are safe to read on the loop — anywhere else a busy
    # writer blocks readers, and the loop must never inherit that wait.
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    if str(mode).lower() != "wal":
        conn.close()
        _inline_unsuitable.add(db_path)
        raise sqlite3.OperationalError(f"catalog is not WAL: {db_path}")
    _inline_readers[db_path] = conn
    return conn


def drop_inline_reader(db_path: str) -> None:
    """Forget a reader whose connection went bad; the next call reopens."""
    conn = _inline_readers.pop(db_path, None)
    if conn is None:
        return
    try:
        conn.close()
    except Exception:
        pass


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


@functools.lru_cache(maxsize=4096)
def is_ephemeral_db_path(db_path: str) -> bool:
    """Return True for temp DBs that should not leave WAL sidecars behind.

    Cached: realpath is a filesystem call, and this used to run on the event
    loop for every connection close (py-spy caught it mid-boot 07-30).
    """

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
    pool_key = getattr(conn, "_azimuth_pool_key", None)
    if pool_key is not None and not getattr(conn, "_azimuth_shelved", False):
        try:
            if conn.in_transaction:
                await conn.rollback()
            # Temp tables used to die with the connection; a shelved
            # connection must not carry one caller's staging into the next
            # (filter facets CREATE TEMP TABLE and rely on it being gone).
            cursor = await conn.execute(
                "SELECT name FROM sqlite_temp_master WHERE type='table'"
            )
            for (name,) in await cursor.fetchall():
                await conn.execute(f'DROP TABLE temp."{name}"')
            idle = _idle_connections.setdefault(pool_key, [])
            if len(idle) < _POOL_MAX_IDLE:
                conn._azimuth_shelved = True
                idle.append(conn)
                return
        except Exception:
            # A connection that cannot be returned clean is not worth keeping.
            pass
    await _checkpoint_temp_wal_async(conn, db_path)
    await conn.close()
