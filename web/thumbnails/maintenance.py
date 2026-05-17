"""Thumbnail cache-root maintenance helpers."""

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
