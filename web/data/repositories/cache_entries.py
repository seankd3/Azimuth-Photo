"""Thumbnail cache-entry read queries."""

import time as _time

from data import connection


_cached_image_ids_cache: dict[tuple[str, str], dict] = {}
_cache_entry_count_cache: dict[tuple[str, str], dict] = {}
CACHED_IMAGE_IDS_TTL_SECONDS = 30.0
CACHE_ENTRY_COUNT_TTL_SECONDS = 30.0


def _cache_scope_matches(cache_root: str, size: str, target_root: str | None, target_size: str | None) -> bool:
    if target_root is not None and cache_root != target_root:
        return False
    if target_size is not None and size != target_size:
        return False
    return True


def invalidate_cached_image_ids_cache(cache_root: str | None = None, size: str | None = None) -> None:
    if cache_root is None and size is None:
        _cached_image_ids_cache.clear()
        _cache_entry_count_cache.clear()
        return
    for key in list(_cached_image_ids_cache.keys()):
        key_root, key_size = key
        if _cache_scope_matches(key_root, key_size, cache_root, size):
            _cached_image_ids_cache.pop(key, None)
    for key in list(_cache_entry_count_cache.keys()):
        key_root, key_size = key
        if _cache_scope_matches(key_root, key_size, cache_root, size):
            _cache_entry_count_cache.pop(key, None)


def note_cached_image_ids_added(cache_root: str, size: str, image_ids) -> None:
    key = (cache_root, size)
    cached_entry = _cached_image_ids_cache.get(key)
    if not cached_entry:
        return
    try:
        additions = frozenset(int(image_id) for image_id in image_ids)
    except (TypeError, ValueError):
        _cached_image_ids_cache.pop(key, None)
        return
    if not additions:
        return
    cached_entry["ids"] = frozenset(cached_entry["ids"]) | additions


async def cached_image_id_set(db_path: str, *, size: str, cache_root: str) -> frozenset[int]:
    conn = await connection.open_async(db_path)
    try:
        cursor = await conn.execute(
            "SELECT image_id FROM cache_entries WHERE cache_root = ? AND size = ?",
            (cache_root, size),
        )
        return frozenset(int(row["image_id"]) for row in await cursor.fetchall())
    finally:
        await connection.close_async(conn, db_path=db_path)


async def cached_image_id_set_cached(
    db_path: str,
    *,
    size: str,
    cache_root: str,
    ttl_seconds: float = CACHED_IMAGE_IDS_TTL_SECONDS,
) -> frozenset[int]:
    if not size or not cache_root:
        return frozenset()
    key = (cache_root, size)
    now = _time.time()
    cached_entry = _cached_image_ids_cache.get(key)
    if cached_entry and now < cached_entry["expires"]:
        return cached_entry["ids"]

    frozen = await cached_image_id_set(db_path, size=size, cache_root=cache_root)
    _cached_image_ids_cache[key] = {
        "ids": frozen,
        "expires": _time.time() + ttl_seconds,
    }
    return frozen


async def cached_image_ids(
    db_path: str,
    image_ids: list[int],
    size: str,
    cache_root: str,
    *,
    ttl_seconds: float = CACHED_IMAGE_IDS_TTL_SECONDS,
) -> set[int]:
    if not image_ids or not size or not cache_root:
        return set()
    unique_ids = list(dict.fromkeys(int(image_id) for image_id in image_ids))
    del ttl_seconds  # targeted lookups do not need the full-set TTL cache
    present: set[int] = set()
    conn = await connection.open_async(db_path)
    try:
        for start in range(0, len(unique_ids), 900):
            chunk = unique_ids[start:start + 900]
            placeholders = ",".join("?" for _ in chunk)
            cursor = await conn.execute(
                "SELECT image_id FROM cache_entries "
                f"WHERE cache_root = ? AND size = ? AND image_id IN ({placeholders})",
                (cache_root, size, *chunk),
            )
            present.update(int(row["image_id"]) for row in await cursor.fetchall())
    finally:
        await connection.close_async(conn, db_path=db_path)
    return present


async def cache_entry_count(db_path: str, *, size: str, cache_root: str) -> int:
    conn = await connection.open_async(db_path)
    try:
        cursor = await conn.execute(
            "SELECT COUNT(*) AS count FROM cache_entries WHERE cache_root = ? AND size = ?",
            (cache_root, size),
        )
        return int((await cursor.fetchone())["count"] or 0)
    finally:
        await connection.close_async(conn, db_path=db_path)


async def cache_entry_count_cached(
    db_path: str,
    *,
    size: str,
    cache_root: str,
    ttl_seconds: float = CACHE_ENTRY_COUNT_TTL_SECONDS,
) -> int:
    key = (cache_root, size)
    now = _time.time()
    cached = _cache_entry_count_cache.get(key)
    if cached and cached["expires"] > now:
        return int(cached["count"])
    count = await cache_entry_count(db_path, size=size, cache_root=cache_root)
    _cache_entry_count_cache[key] = {
        "count": count,
        "expires": _time.time() + ttl_seconds,
    }
    return count
