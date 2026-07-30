import asyncio
import os
import sqlite3
import threading
import time
from dataclasses import dataclass
from typing import Callable

from . import disk_store


@dataclass
class _Providers:
    meta_lock: threading.Lock
    cache_root: Callable[[], str]
    disk_allocations: Callable[[], dict[str, int]]
    all_tiers: Callable[[], tuple[str, ...]]
    thumb_tiers: Callable[[], tuple[str, ...]]
    db_path: Callable[[], str]
    is_ephemeral_db_path: Callable[[str], bool]
    current_time: Callable[[], float]
    sqlite_locked: Callable[[Exception], bool]
    thumbnail_disk_path: Callable[[str, int], str]
    cache_access_time: Callable[..., float]
    memory_put: Callable[[str, int, str, bytes], None]
    invalidate_cached_image_ids_cache: Callable[..., None]
    note_cached_image_ids_added: Callable[[str, str, object], None]


_providers: _Providers | None = None

# Write-behind queue for cache DB entries. This reduces metadata lock contention.
# Entries are (size, image_id, source_signature, path, size_bytes, access_time)
# with an optional trailing "touch" marker for LRU-touch-only entries.
_write_queue: list[tuple] = []
_write_queue_lock = threading.Lock()
_WRITE_FLUSH_SIZE = 96

_persistent_conn: sqlite3.Connection | None = None
_cache_metadata_retry_after = 0.0
_cache_metadata_lock_failures = 0

_tier_byte_totals: dict[str, int] = {}
_disk_stats_cache = {"data": None, "expires": 0.0, "stale_until": 0.0}
_disk_stats_cache_ttl_seconds = 5.0
_disk_stats_cache_max_stale_seconds = 5.0

# In-memory index: (size, image_id) -> (disk path, source signature).
_disk_path_index: dict[tuple[str, int], tuple[str, str]] = {}
_disk_index_lock = threading.Lock()
_disk_index_built = False


def configure(
    *,
    meta_lock: threading.Lock,
    cache_root: Callable[[], str],
    disk_allocations: Callable[[], dict[str, int]],
    all_tiers: Callable[[], tuple[str, ...]],
    thumb_tiers: Callable[[], tuple[str, ...]],
    db_path: Callable[[], str],
    is_ephemeral_db_path: Callable[[str], bool],
    current_time: Callable[[], float],
    sqlite_locked: Callable[[Exception], bool],
    thumbnail_disk_path: Callable[[str, int], str],
    cache_access_time: Callable[..., float],
    memory_put: Callable[[str, int, str, bytes], None],
    invalidate_cached_image_ids_cache: Callable[..., None],
    note_cached_image_ids_added: Callable[[str, str, object], None],
) -> None:
    global _providers
    _providers = _Providers(
        meta_lock=meta_lock,
        cache_root=cache_root,
        disk_allocations=disk_allocations,
        all_tiers=all_tiers,
        thumb_tiers=thumb_tiers,
        db_path=db_path,
        is_ephemeral_db_path=is_ephemeral_db_path,
        current_time=current_time,
        sqlite_locked=sqlite_locked,
        thumbnail_disk_path=thumbnail_disk_path,
        cache_access_time=cache_access_time,
        memory_put=memory_put,
        invalidate_cached_image_ids_cache=invalidate_cached_image_ids_cache,
        note_cached_image_ids_added=note_cached_image_ids_added,
    )


def _p() -> _Providers:
    if _providers is None:
        raise RuntimeError("thumbnail cache entries are not configured")
    return _providers


def _db_connect() -> sqlite3.Connection:
    """Return persistent connection under the configured metadata lock."""
    global _persistent_conn
    if _persistent_conn is not None:
        try:
            _persistent_conn.execute("SELECT 1")
            return _persistent_conn
        except sqlite3.ProgrammingError:
            _persistent_conn = None

    if _persistent_conn is None:
        providers = _p()
        db_path = providers.db_path()
        conn = sqlite3.connect(db_path, timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        if not providers.is_ephemeral_db_path(db_path):
            conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS cache_metadata ("
            "cache_root TEXT PRIMARY KEY, "
            "thumb_config_signature TEXT NOT NULL, "
            "thumb_config_changed_at REAL NOT NULL, "
            "replace_stale_thumbnails INTEGER NOT NULL DEFAULT 0"
            ")"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS cache_entries ("
            "cache_root TEXT NOT NULL, "
            "size TEXT NOT NULL, "
            "image_id INTEGER NOT NULL, "
            "path TEXT NOT NULL, "
            "source_signature TEXT NOT NULL, "
            "size_bytes INTEGER NOT NULL, "
            "last_accessed REAL NOT NULL, "
            "created_at REAL NOT NULL, "
            "PRIMARY KEY (cache_root, size, image_id)"
            ")"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_cache_entries_root_size_access "
            "ON cache_entries(cache_root, size, last_accessed)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_cache_entries_root_size_bytes "
            "ON cache_entries(cache_root, size, size_bytes)"
        )
        try:
            conn.execute(
                "ALTER TABLE cache_metadata "
                "ADD COLUMN replace_stale_thumbnails INTEGER NOT NULL DEFAULT 0"
            )
        except sqlite3.OperationalError:
            pass
        conn.commit()
        _persistent_conn = conn
    return _persistent_conn


def _is_touch_entry(entry: tuple) -> bool:
    return len(entry) > 6 and entry[6] == "touch"


def _note_cache_metadata_lock():
    global _cache_metadata_retry_after, _cache_metadata_lock_failures
    _cache_metadata_lock_failures = min(_cache_metadata_lock_failures + 1, 6)
    delay = min(8.0, 0.25 * (2 ** (_cache_metadata_lock_failures - 1)))
    _cache_metadata_retry_after = time.monotonic() + delay


def _clear_cache_metadata_lock_backoff():
    global _cache_metadata_retry_after, _cache_metadata_lock_failures
    _cache_metadata_retry_after = 0.0
    _cache_metadata_lock_failures = 0


def _cache_metadata_backoff_active() -> bool:
    return time.monotonic() < _cache_metadata_retry_after


def _remove_cache_entry_locked(conn: sqlite3.Connection, row: sqlite3.Row):
    path = row["path"]
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
    except OSError:
        pass
    conn.execute(
        "DELETE FROM cache_entries WHERE cache_root = ? AND size = ? AND image_id = ?",
        (row["cache_root"], row["size"], row["image_id"]),
    )
    _unindex_disk_entry(row["size"], row["image_id"])


def _invalidate_disk_stats_cache(*, soft: bool = False):
    if soft and _disk_stats_cache["data"] is not None:
        now = _p().current_time()
        if now < float(_disk_stats_cache.get("stale_until") or 0.0):
            _disk_stats_cache["expires"] = max(
                float(_disk_stats_cache.get("expires") or 0.0),
                now + 1.0,
            )
            return
    _disk_stats_cache["expires"] = 0.0


def _tier_bytes(conn: sqlite3.Connection, size: str) -> int:
    """Get running total for a tier, initializing from DB if needed."""
    cache_root = _p().cache_root()
    total = _tier_byte_totals.get(size)
    if total is None:
        row = conn.execute(
            "SELECT COALESCE(SUM(size_bytes), 0) AS t FROM cache_entries "
            "WHERE cache_root = ? AND size = ?",
            (cache_root, size),
        ).fetchone()
        total = int(row["t"])
        _tier_byte_totals[size] = total
    return total


def _enforce_tier_budget_locked(conn: sqlite3.Connection, size: str) -> list[int]:
    providers = _p()
    cache_root = providers.cache_root()
    budget = providers.disk_allocations().get(size, 0)
    total = _tier_bytes(conn, size)
    removed_ids: list[int] = []

    if budget <= 0:
        rows = conn.execute(
            "SELECT cache_root, size, image_id, path, size_bytes FROM cache_entries "
            "WHERE cache_root = ? AND size = ?",
            (cache_root, size),
        ).fetchall()
        for row in rows:
            removed_ids.append(int(row["image_id"]))
            _remove_cache_entry_locked(conn, row)
            total -= int(row["size_bytes"])
        conn.commit()
        _tier_byte_totals[size] = max(0, total)
        return removed_ids

    if total <= budget:
        return removed_ids

    evict_rows = conn.execute(
        "SELECT c.cache_root, c.size, c.image_id, c.path, c.size_bytes "
        "FROM cache_entries c "
        "LEFT JOIN images i ON c.image_id = i.id "
        "WHERE c.cache_root = ? AND c.size = ? "
        "ORDER BY c.last_accessed ASC, COALESCE(i.elo, 1200) ASC",
        (cache_root, size),
    ).fetchall()
    for row in evict_rows:
        removed_ids.append(int(row["image_id"]))
        _remove_cache_entry_locked(conn, row)
        total -= int(row["size_bytes"])
        if total <= budget:
            break
    conn.commit()
    _tier_byte_totals[size] = max(0, total)
    return removed_ids


def _enforce_all_disk_budgets():
    _tier_byte_totals.clear()
    providers = _p()
    with providers.meta_lock:
        conn = _db_connect()
        for size in providers.all_tiers():
            _enforce_tier_budget_locked(conn, size)


def _get_disk_entry(
    size: str,
    image_id: int,
    source_signature: str,
    touch: bool = True,
) -> sqlite3.Row | None:
    providers = _p()
    cache_root = providers.cache_root()
    if not cache_root or providers.disk_allocations().get(size, 0) <= 0:
        return None

    pending_touch: tuple | None = None
    result: sqlite3.Row | None = None
    with providers.meta_lock:
        try:
            conn = _db_connect()
            row = conn.execute(
                "SELECT cache_root, size, image_id, path, source_signature, size_bytes "
                "FROM cache_entries WHERE cache_root = ? AND size = ? AND image_id = ?",
                (cache_root, size, image_id),
            ).fetchone()
            _clear_cache_metadata_lock_backoff()
            if row is None:
                return None
            if row["source_signature"] != source_signature:
                return None
            if not os.path.exists(row["path"]):
                try:
                    stale_row = conn.execute(
                        "SELECT cache_root, size, image_id, path FROM cache_entries "
                        "WHERE cache_root = ? AND size = ? AND image_id = ?",
                        (cache_root, size, image_id),
                    ).fetchone()
                    if stale_row is not None:
                        _remove_cache_entry_locked(conn, stale_row)
                        conn.commit()
                except sqlite3.OperationalError as exc:
                    try:
                        conn.rollback()
                    except Exception:
                        pass
                    if providers.sqlite_locked(exc):
                        _note_cache_metadata_lock()
                    else:
                        raise
                return None
            if touch:
                # Route the LRU touch through the write-behind queue instead
                # of an inline UPDATE+commit; timestamps may lag by one flush.
                pending_touch = (
                    size,
                    image_id,
                    row["source_signature"],
                    row["path"],
                    int(row["size_bytes"]),
                    providers.current_time(),
                    "touch",
                )
            _index_disk_entry(size, image_id, row["path"], row["source_signature"])
            result = row
        except sqlite3.OperationalError as exc:
            if providers.sqlite_locked(exc):
                _note_cache_metadata_lock()
                return None
            raise
    if pending_touch is not None:
        with _write_queue_lock:
            _write_queue.append(pending_touch)
        _maybe_flush_write_queue()
    return result


def touch_cached_signature(size: str, image_id: int, source_signature: str | None = None) -> bool:
    providers = _p()
    cache_root = providers.cache_root()
    if not cache_root or providers.disk_allocations().get(size, 0) <= 0:
        return False
    with providers.meta_lock:
        conn = None
        try:
            conn = _db_connect()
            if source_signature:
                cursor = conn.execute(
                    "UPDATE cache_entries SET last_accessed = ? "
                    "WHERE cache_root = ? AND size = ? AND image_id = ? AND source_signature = ?",
                    (providers.current_time(), cache_root, size, image_id, source_signature),
                )
            else:
                cursor = conn.execute(
                    "UPDATE cache_entries SET last_accessed = ? "
                    "WHERE cache_root = ? AND size = ? AND image_id = ?",
                    (providers.current_time(), cache_root, size, image_id),
                )
            conn.commit()
            _clear_cache_metadata_lock_backoff()
            return cursor.rowcount > 0
        except sqlite3.OperationalError as exc:
            if conn is not None:
                try:
                    conn.rollback()
                except Exception:
                    pass
            if providers.sqlite_locked(exc):
                _note_cache_metadata_lock()
                return False
            raise


def _build_disk_path_index() -> bool:
    """Load all cache entry paths into memory for fast lookup."""
    global _disk_index_built
    providers = _p()
    cache_root = providers.cache_root()
    if not cache_root:
        _clear_disk_index()
        _disk_index_built = True
        return True
    if _cache_metadata_backoff_active():
        return False
    try:
        with providers.meta_lock:
            conn = _db_connect()
            rows = conn.execute(
                "SELECT size, image_id, path, source_signature FROM cache_entries WHERE cache_root = ?",
                (cache_root,),
            ).fetchall()
    except sqlite3.OperationalError as exc:
        if providers.sqlite_locked(exc):
            _note_cache_metadata_lock()
            return False
        raise
    new_index = disk_store.index_from_rows(rows)
    with _disk_index_lock:
        _disk_path_index.clear()
        _disk_path_index.update(new_index)
    _disk_index_built = True
    _clear_cache_metadata_lock_backoff()
    return True


def disk_index_ready() -> bool:
    """True when the in-memory cache path index is usable without a rebuild."""
    return _disk_index_built


async def warm_disk_path_index() -> bool:
    """Build the disk path index off the event loop.

    The synchronous build reads every cache_entries row for this cache root, so
    request handlers must never trigger it inline.
    """
    return await asyncio.to_thread(_build_disk_path_index)


def _index_disk_entry(size: str, image_id: int, path: str, source_signature: str):
    """Update the in-memory index when a new cache entry is written."""
    disk_store.index_entry(_disk_path_index, _disk_index_lock, size, image_id, path, source_signature)


def _unindex_disk_entry(size: str, image_id: int):
    disk_store.unindex_entry(_disk_path_index, _disk_index_lock, size, image_id)


def _clear_disk_index(tiers: tuple[str, ...] | None = None):
    global _disk_index_built
    disk_store.clear_index(_disk_path_index, _disk_index_lock, tiers)
    if tiers is None:
        _disk_index_built = False


def fast_disk_has(size: str, image_id: int, source_signature: str | None = None) -> bool:
    if not _disk_index_built:
        if not _build_disk_path_index():
            return False
    entry = disk_store.lookup_index_entry(
        _disk_path_index,
        _disk_index_lock,
        size,
        image_id,
        source_signature,
    )
    if entry is None:
        return False
    path, _cached_signature = entry
    if os.path.exists(path):
        return True
    _unindex_disk_entry(size, image_id)
    return False


def fast_disk_path_entry(
    size: str,
    image_id: int,
    source_signature: str | None = None,
) -> tuple[str, str] | None:
    if not _disk_index_built:
        if not _build_disk_path_index():
            return None
    entry = disk_store.lookup_index_entry(
        _disk_path_index,
        _disk_index_lock,
        size,
        image_id,
        source_signature,
    )
    if entry is None:
        return None
    path, cached_signature = entry
    if os.path.exists(path):
        return cached_signature, path
    _unindex_disk_entry(size, image_id)
    return None


def fast_disk_read_entry(
    size: str,
    image_id: int,
    source_signature: str | None = None,
    *,
    populate_memory: bool = False,
) -> tuple[str, bytes] | None:
    if not _disk_index_built:
        if not _build_disk_path_index():
            return None
    entry = disk_store.lookup_index_entry(
        _disk_path_index,
        _disk_index_lock,
        size,
        image_id,
        source_signature,
    )
    if entry is None:
        return None
    path, cached_signature = entry
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        _unindex_disk_entry(size, image_id)
        return None
    if populate_memory and size in _p().thumb_tiers():
        _p().memory_put(size, image_id, cached_signature, data)
    return cached_signature, data


def fast_disk_read(size: str, image_id: int) -> bytes | None:
    """Fast path: read thumbnail from SSD via in-memory index."""
    entry = fast_disk_read_entry(size, image_id)
    return entry[1] if entry is not None else None


def _read_disk_thumbnail(size: str, image_id: int, source_signature: str) -> bytes | None:
    providers = _p()
    cache_root = providers.cache_root()
    row = _get_disk_entry(size, image_id, source_signature)
    if row is None:
        return None

    try:
        with open(row["path"], "rb") as f:
            data = f.read()
    except OSError:
        with providers.meta_lock:
            conn = None
            try:
                conn = _db_connect()
                stale_row = conn.execute(
                    "SELECT cache_root, size, image_id, path FROM cache_entries "
                    "WHERE cache_root = ? AND size = ? AND image_id = ?",
                    (cache_root, size, image_id),
                ).fetchone()
                if stale_row is not None:
                    _remove_cache_entry_locked(conn, stale_row)
                    conn.commit()
                    _clear_cache_metadata_lock_backoff()
            except sqlite3.OperationalError as exc:
                if conn is not None:
                    try:
                        conn.rollback()
                    except Exception:
                        pass
                if providers.sqlite_locked(exc):
                    _note_cache_metadata_lock()
                else:
                    raise
            finally:
                if conn is not None:
                    conn.close()
        return None

    providers.memory_put(size, image_id, source_signature, data)
    return data


def _store_disk_entry(
    size: str,
    image_id: int,
    source_signature: str,
    path: str,
    size_bytes: int,
    *,
    hot: bool = True,
):
    providers = _p()
    cache_root = providers.cache_root()
    now = providers.current_time()
    access_time = providers.cache_access_time(hot=hot)
    with providers.meta_lock:
        conn = _db_connect()
        previous = conn.execute(
            "SELECT cache_root, size, image_id, path, size_bytes FROM cache_entries "
            "WHERE cache_root = ? AND size = ? AND image_id = ?",
            (cache_root, size, image_id),
        ).fetchone()
        old_bytes = 0
        removed_cache_ids = []
        if previous is not None:
            old_bytes = int(previous["size_bytes"])
            if previous["path"] != path:
                removed_cache_ids.append(int(previous["image_id"]))
                _remove_cache_entry_locked(conn, previous)

        conn.execute(
            "INSERT OR REPLACE INTO cache_entries "
            "(cache_root, size, image_id, path, source_signature, size_bytes, last_accessed, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                cache_root,
                size,
                image_id,
                path,
                source_signature,
                int(size_bytes),
                access_time,
                now,
            ),
        )
        if size in _tier_byte_totals:
            _tier_byte_totals[size] += int(size_bytes) - old_bytes
        removed_cache_ids.extend(_enforce_tier_budget_locked(conn, size))
        conn.commit()
        _invalidate_disk_stats_cache(soft=True)
        _index_disk_entry(size, image_id, path, source_signature)
        if removed_cache_ids:
            providers.invalidate_cached_image_ids_cache(cache_root=cache_root, size=size)
        else:
            providers.note_cached_image_ids_added(cache_root, size, [image_id])


def _write_thumbnail_to_disk(size: str, image_id: int, source_signature: str, data: bytes, *, hot: bool) -> bool:
    providers = _p()
    cache_root = providers.cache_root()
    budget = providers.disk_allocations().get(size, 0)
    if not cache_root or budget <= 0 or len(data) > budget:
        return False

    path = providers.thumbnail_disk_path(size, image_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temp_path = f"{path}.{threading.get_ident()}.tmp"
    with open(temp_path, "wb") as f:
        f.write(data)
    os.replace(temp_path, path)
    _index_disk_entry(size, image_id, path, source_signature)
    with _write_queue_lock:
        _write_queue.append((size, image_id, source_signature, path, len(data), providers.cache_access_time(hot=hot)))
    _maybe_flush_write_queue()
    return True


def _flush_write_queue() -> bool:
    """Flush pending cache DB writes in a single transaction."""
    if _cache_metadata_backoff_active():
        return False
    with _write_queue_lock:
        if not _write_queue:
            return True
        latest = {}
        for entry in _write_queue:
            key = (entry[0], entry[1])
            existing = latest.get(key)
            if _is_touch_entry(entry) and existing is not None:
                # Keep the pending entry's data; only bump its access time so
                # a touch never clobbers a queued store with stale row data.
                latest[key] = existing[:5] + (entry[5],) + existing[6:]
            else:
                latest[key] = entry
        batch = list(latest.values())
        _write_queue.clear()

    providers = _p()
    cache_root = providers.cache_root()
    now = providers.current_time()
    with providers.meta_lock:
        conn = None
        try:
            conn = _db_connect()
            now = providers.current_time()
            removed_by_size: dict[str, list[int]] = {}
            added_by_size: dict[str, list[int]] = {}
            for entry in batch:
                size, image_id, source_signature, path, size_bytes, access_time = entry[:6]
                if _is_touch_entry(entry):
                    # LRU touch: only bump last_accessed; never rewrite the row
                    # (preserves created_at and tier byte accounting).
                    conn.execute(
                        "UPDATE cache_entries SET last_accessed = ? "
                        "WHERE cache_root = ? AND size = ? AND image_id = ?",
                        (access_time, cache_root, size, image_id),
                    )
                    continue
                previous = conn.execute(
                    "SELECT cache_root, size, image_id, path, size_bytes FROM cache_entries "
                    "WHERE cache_root = ? AND size = ? AND image_id = ?",
                    (cache_root, size, image_id),
                ).fetchone()
                old_bytes = int(previous["size_bytes"]) if previous is not None else 0
                if previous is not None and previous["path"] != path:
                    removed_by_size.setdefault(size, []).append(int(previous["image_id"]))
                    _remove_cache_entry_locked(conn, previous)

                conn.execute(
                    "INSERT OR REPLACE INTO cache_entries "
                    "(cache_root, size, image_id, path, source_signature, size_bytes, last_accessed, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (cache_root, size, image_id, path, source_signature, int(size_bytes), access_time, now),
                )
                if size in _tier_byte_totals:
                    _tier_byte_totals[size] += int(size_bytes) - old_bytes
                _index_disk_entry(size, image_id, path, source_signature)
                added_by_size.setdefault(size, []).append(int(image_id))
            for size in {entry[0] for entry in batch}:
                removed = _enforce_tier_budget_locked(conn, size)
                if removed:
                    removed_by_size.setdefault(size, []).extend(removed)
            conn.commit()
            _invalidate_disk_stats_cache(soft=True)
            for size in {entry[0] for entry in batch}:
                if removed_by_size.get(size):
                    providers.invalidate_cached_image_ids_cache(cache_root=cache_root, size=size)
                else:
                    providers.note_cached_image_ids_added(
                        cache_root,
                        size,
                        added_by_size.get(size, ()),
                    )
            _clear_cache_metadata_lock_backoff()
            return True
        except sqlite3.OperationalError as exc:
            if conn is not None:
                try:
                    conn.rollback()
                except Exception:
                    pass
            _tier_byte_totals.clear()
            with _write_queue_lock:
                _write_queue[0:0] = batch
            if providers.sqlite_locked(exc):
                _note_cache_metadata_lock()
                return False
            raise


def _maybe_flush_write_queue():
    """Flush if enough writes have accumulated."""
    with _write_queue_lock:
        should_flush = len(_write_queue) >= _WRITE_FLUSH_SIZE
    if should_flush:
        _flush_write_queue()


def close_persistent_conn() -> None:
    global _persistent_conn
    providers = _p()
    with providers.meta_lock:
        if _persistent_conn is not None:
            try:
                _persistent_conn.close()
            finally:
                _persistent_conn = None
