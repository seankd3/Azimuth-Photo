"""Thumbnail cache-root maintenance helpers."""

import os
import shutil
import sqlite3
import time
from collections.abc import Callable

from . import disk_store


def cache_marker_path(cache_root: str, cache_marker: str) -> str:
    return disk_store.cache_marker_path(cache_root, cache_marker)


def cache_dir_has_marker(cache_root: str, cache_marker: str) -> bool:
    return disk_store.cache_dir_has_marker(cache_root, cache_marker)


def cache_dir_is_legacy_cache_layout(
    cache_root: str,
    tiers: tuple[str, ...],
    cache_marker: str,
) -> bool:
    return disk_store.cache_dir_is_legacy_cache_layout(cache_root, tiers, cache_marker)


def write_cache_marker(
    cache_root: str,
    cache_marker: str,
    *,
    log: Callable[[str], None] = print,
) -> None:
    try:
        disk_store.write_cache_marker(cache_root, cache_marker)
    except OSError as exc:
        log(f"Could not write cache marker for {cache_root}: {exc}")


def cache_dir_safe_to_clear(
    cache_root: str,
    tiers: tuple[str, ...],
    cache_marker: str,
    *,
    write_marker: Callable[[str, str], None] = write_cache_marker,
) -> tuple[bool, str]:
    safe, reason, should_mark = disk_store.cache_dir_safe_to_clear(
        cache_root,
        tiers,
        cache_marker,
    )
    if should_mark:
        write_marker(cache_root, cache_marker)
    return safe, reason


def ensure_disk_cache_dirs(
    cache_root: str,
    thumb_tiers: tuple[str, ...],
    full_tier: str,
    cache_marker: str,
    *,
    write_marker: Callable[[str, str], None] = write_cache_marker,
) -> None:
    if disk_store.ensure_cache_dirs(cache_root, thumb_tiers, full_tier, cache_marker):
        write_marker(cache_root, cache_marker)


def cleanup_stale_cache_temps(
    cache_root: str,
    tiers: tuple[str, ...],
    *,
    max_age_seconds: float = 30 * 60,
    current_time: Callable[[], float] = time.time,
    invalidate_disk_stats_cache: Callable[[], None] | None = None,
) -> dict:
    cutoff = current_time() - max(60.0, float(max_age_seconds))
    result = disk_store.cleanup_stale_cache_temps(cache_root, tiers, cutoff=cutoff)
    if result["files_removed"] and invalidate_disk_stats_cache is not None:
        invalidate_disk_stats_cache()
    return result


def sweep_missing_cache_entries(
    *,
    meta_lock,
    db_connect: Callable[[], object],
    remove_cache_entry_locked: Callable[[object, object], object],
    invalidate_disk_stats_cache: Callable[[], object] | None = None,
    batch_size: int = 500,
    max_batches: int | None = None,
    path_exists: Callable[[str], bool] = os.path.exists,
    stand_aside: Callable[[], None] | None = None,
) -> dict:
    """Delete cache_entries rows whose files are gone (phantom preview_ready).

    Cheap idle-time repair: batched, resumable via ``after_rowid``, once per
    process start. Does not delete real files — only rows already pointing at
    missing paths.

    ``stand_aside`` is called between batches so the repair waits while someone
    is actually browsing. On a library with tens of thousands of stale rows this
    is the difference between a chore and an outage.

    The file checks happen outside the lock on purpose. They are the slow part
    — one filesystem call per cached preview, and this laptop has 121,826 of
    them — and the grid needs that same lock to know whether a photo has a
    preview yet. Holding it across a batch of checks meant opening the app
    started a repair that browsing then queued behind: measured, the first page
    of photos never arrived at all. Now the lock is held only to read a batch of
    rows and to delete the ones that turned out to be missing.
    """

    removed = 0
    scanned = 0
    batches = 0
    after_rowid = 0
    batch = max(50, int(batch_size))
    while True:
        if max_batches is not None and batches >= max_batches:
            break
        # Before the first batch as well as between them: someone who opens
        # their library and starts browsing should not have a repair begin
        # underneath them.
        if stand_aside is not None:
            stand_aside()
        with meta_lock:
            conn = db_connect()
            try:
                rows = conn.execute(
                    "SELECT rowid, cache_root, size, image_id, path "
                    "FROM cache_entries WHERE rowid > ? "
                    "ORDER BY rowid ASC LIMIT ?",
                    (after_rowid, batch),
                ).fetchall()
            finally:
                conn.close()
        if not rows:
            break
        batches += 1

        missing = []
        for row in rows:
            after_rowid = int(row["rowid"])
            scanned += 1
            path = str(row["path"] or "")
            if path and path_exists(path):
                continue
            missing.append(row)

        if missing:
            with meta_lock:
                conn = db_connect()
                try:
                    for row in missing:
                        remove_cache_entry_locked(conn, row)
                        removed += 1
                    conn.commit()
                finally:
                    conn.close()
        if len(rows) < batch:
            break
    if removed and invalidate_disk_stats_cache is not None:
        invalidate_disk_stats_cache()
    return {
        "scanned": scanned,
        "removed": removed,
        "batches": batches,
        "after_rowid": after_rowid,
    }


def purge_image_cache(
    image_ids: list[int],
    *,
    memory_cache,
    clear_memory_image_ids: Callable[[set[int]], None],
    flush_write_queue: Callable[[], object],
    meta_lock,
    db_connect: Callable[[], object],
    remove_cache_entry_locked: Callable[[object, object], object],
    tier_byte_totals,
    invalidate_disk_stats_cache: Callable[[], object],
    source_stat_cache,
) -> dict:
    """Remove RAM/disk cache entries for catalog images that are being purged."""
    ids = {int(image_id) for image_id in image_ids or [] if int(image_id) > 0}
    if not ids:
        return {"memory_entries_removed": 0, "disk_entries_removed": 0, "disk_files_removed": 0}

    memory_before = len(memory_cache)
    clear_memory_image_ids(ids)
    memory_removed = max(0, memory_before - len(memory_cache))
    flush_write_queue()

    disk_entries_removed = 0
    disk_files_removed = 0
    with meta_lock:
        conn = db_connect()
        try:
            id_list = list(ids)
            for start in range(0, len(id_list), 500):
                chunk = id_list[start:start + 500]
                placeholders = ",".join("?" for _ in chunk)
                rows = conn.execute(
                    f"SELECT cache_root, size, image_id, path, size_bytes "
                    f"FROM cache_entries WHERE image_id IN ({placeholders})",
                    chunk,
                ).fetchall()
                for row in rows:
                    if os.path.exists(row["path"]):
                        disk_files_removed += 1
                    remove_cache_entry_locked(conn, row)
                    disk_entries_removed += 1
            conn.commit()
        finally:
            conn.close()
    tier_byte_totals.clear()
    invalidate_disk_stats_cache()
    source_stat_cache.clear()
    return {
        "memory_entries_removed": memory_removed,
        "disk_entries_removed": disk_entries_removed,
        "disk_files_removed": disk_files_removed,
    }


def clear_cache(
    *,
    cache_root: str,
    cache_marker: str,
    cache_dir_safe_to_clear: Callable[[], tuple[bool, str]],
    clear_memory_cache: Callable[[], dict],
    flush_write_queue: Callable[[], object],
    ensure_disk_cache_dirs: Callable[[], object],
    clear_disk_index: Callable[[], object],
    tier_byte_totals,
    invalidate_disk_stats_cache: Callable[[], object],
    source_stat_cache,
    reset_pregen_bulk_cursor: Callable[[], object],
    reset_pregen_full_cursor: Callable[[], object],
    meta_lock,
    db_connect: Callable[[], object],
    clear_cache_metadata_lock_backoff: Callable[[], object],
    is_sqlite_locked: Callable[[Exception], bool],
    note_cache_metadata_lock: Callable[[], object],
    set_replace_stale_thumbnails: Callable[[bool], object],
    invalidate_cached_image_ids_cache: Callable[..., object],
    print_fn: Callable[[str], object] = print,
) -> dict:
    safe_to_clear, unsafe_reason = cache_dir_safe_to_clear()
    if not safe_to_clear:
        return {
            "refused": True,
            "error": unsafe_reason,
            "ssd_cache_dir": cache_root,
        }

    memory = clear_memory_cache()
    flush_write_queue()

    disk_removed = 0
    if cache_root and os.path.isdir(cache_root):
        for _root, _dirs, files in os.walk(cache_root):
            disk_removed += sum(1 for filename in files if filename != cache_marker)
        shutil.rmtree(cache_root, ignore_errors=True)
    ensure_disk_cache_dirs()
    clear_disk_index()
    tier_byte_totals.clear()
    invalidate_disk_stats_cache()
    source_stat_cache.clear()
    reset_pregen_bulk_cursor()
    reset_pregen_full_cursor()

    with meta_lock:
        conn = None
        try:
            conn = db_connect()
            conn.execute("DELETE FROM cache_entries WHERE cache_root = ?", (cache_root,))
            conn.execute(
                "UPDATE cache_metadata SET replace_stale_thumbnails = 0 WHERE cache_root = ?",
                (cache_root,),
            )
            conn.commit()
            clear_cache_metadata_lock_backoff()
        except sqlite3.OperationalError as exc:
            if is_sqlite_locked(exc):
                note_cache_metadata_lock()
                print_fn(f"Cache metadata clear skipped: {exc}")
            else:
                raise
        finally:
            if conn is not None:
                conn.close()

    set_replace_stale_thumbnails(False)
    invalidate_cached_image_ids_cache(cache_root=cache_root)

    return {
        "memory_entries_cleared": memory["entries_cleared"],
        "memory_bytes_cleared": memory["bytes_cleared"],
        "memory_before": memory["counts"],
        "disk_files_removed": disk_removed,
        "ssd_cache_dir": cache_root,
    }
