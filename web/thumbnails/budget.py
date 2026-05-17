"""Runtime thumbnail cache budget adapters.

The pure allocation math lives in ``thumbnails.config``. This module owns the
small runtime adapter layer that samples the cache database and feeds those
numbers into the pure budget helpers.
"""

from . import config as thumbnail_config


def estimated_tier_bytes(size: str, *, thumb_quality: int) -> int:
    return thumbnail_config.estimated_tier_bytes(size, thumb_quality)


def cache_archive_estimates(
    *,
    all_tiers: tuple[str, ...],
    thumb_tiers: tuple[str, ...],
    full_tier: str,
    ssd_cache_dir: str,
    thumb_config_changed_at: float,
    meta_lock,
    db_connect,
    estimated_tier_bytes_for_size,
) -> dict:
    fallbacks = {tier: estimated_tier_bytes_for_size(tier) for tier in all_tiers}
    estimates = {
        "active_images": 0,
        "total_images": 0,
        "avg_bytes": dict(fallbacks),
        "sample_count": {tier: 0 for tier in all_tiers},
        "needed_bytes": {tier: 0 for tier in all_tiers},
    }
    try:
        with meta_lock:
            conn = db_connect()
            try:
                row = conn.execute(
                    "SELECT "
                    "SUM(CASE WHEN s.included = 1 AND s.online = 1 AND i.missing_at IS NULL THEN 1 ELSE 0 END) AS active_images, "
                    "SUM(CASE WHEN s.included = 1 AND i.missing_at IS NULL THEN 1 ELSE 0 END) AS total_images "
                    "FROM images i LEFT JOIN catalog_sources s ON s.id = i.source_id"
                ).fetchone()
                active_images = int(row["active_images"] or 0)
                total_images = int(row["total_images"] or 0)
                rows = conn.execute(
                    "SELECT size, COUNT(*) AS count, COALESCE(SUM(size_bytes), 0) AS bytes "
                    "FROM cache_entries WHERE cache_root = ? AND (size = ? OR created_at >= ?) GROUP BY size",
                    (ssd_cache_dir, full_tier, thumb_config_changed_at),
                ).fetchall()
            finally:
                conn.close()
    except Exception:
        active_images = 0
        total_images = 0
        rows = []

    for row in rows:
        size = row["size"]
        if size not in estimates["avg_bytes"]:
            continue
        count = int(row["count"] or 0)
        size_bytes = int(row["bytes"] or 0)
        if count > 0 and size_bytes > 0:
            estimates["avg_bytes"][size] = max(1, int(size_bytes / count))
            estimates["sample_count"][size] = count

    estimates["active_images"] = active_images
    estimates["total_images"] = total_images
    preview_target_images = active_images if active_images > 0 else total_images
    for tier in thumb_tiers:
        estimates["needed_bytes"][tier] = estimates["avg_bytes"][tier] * preview_target_images
    estimates["needed_bytes"][full_tier] = estimates["avg_bytes"][full_tier] * total_images
    return estimates


def allocate_disk_budget(
    total_bytes: int,
    *,
    needed_bytes: dict[str, int],
    profile: str,
) -> dict[str, int]:
    return thumbnail_config.allocate_disk_budget(
        total_bytes,
        needed_bytes=needed_bytes,
        profile=profile,
    )


def background_tier_budget(
    *,
    size: str,
    budget: int,
    needed_bytes: int,
    hot_lg_reserve_fraction: float,
    hot_lg_reserve_min_bytes: int,
    hot_lg_reserve_max_bytes: int,
) -> int:
    return thumbnail_config.background_tier_budget(
        size=size,
        budget=budget,
        needed_bytes=needed_bytes,
        hot_lg_reserve_fraction=hot_lg_reserve_fraction,
        hot_lg_reserve_min_bytes=hot_lg_reserve_min_bytes,
        hot_lg_reserve_max_bytes=hot_lg_reserve_max_bytes,
    )


def cache_budget_config(
    *,
    profile: str,
    memory_cache_bytes: int,
    disk_allocations: dict[str, int],
) -> dict:
    return thumbnail_config.cache_budget_config(
        profile=profile,
        memory_cache_bytes=memory_cache_bytes,
        disk_allocations=disk_allocations,
    )
