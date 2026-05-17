from collections.abc import Callable


def copy_disk_stats(disk: dict) -> dict:
    copied = dict(disk)
    copied["tiers"] = {
        size: dict(info)
        for size, info in (disk.get("tiers") or {}).items()
    }
    return copied


def cache_stats(
    *,
    memory_stats: Callable[[], dict],
    current_time: Callable[[], float],
    disk_stats_cache: dict,
    disk_stats_cache_ttl_seconds: float,
    disk_stats_cache_max_stale_seconds: float,
    meta_lock,
    db_connect: Callable[[], object],
    cache_root: str,
    cache_limit_bytes: int,
    disk_allocations: dict[str, int],
    all_tiers: tuple[str, ...],
    thumb_tiers: tuple[str, ...],
    full_tier: str,
    replace_stale_thumbnails: bool,
    thumb_config_changed_at: float,
) -> dict:
    memory = memory_stats()
    now = current_time()
    cached_disk = disk_stats_cache["data"]
    if cached_disk is not None and now < disk_stats_cache["expires"]:
        disk = copy_disk_stats(cached_disk)
    else:
        disk_tiers = {
            size: {
                "count": 0,
                "bytes": 0,
                "current_count": 0,
                "current_bytes": 0,
                "stale_count": 0,
                "replacement_mode": False,
                "budget_bytes": disk_allocations.get(size, 0),
            }
            for size in all_tiers
        }

        lock_acquired = meta_lock.acquire(blocking=False)
        if not lock_acquired and cached_disk is not None:
            disk = copy_disk_stats(cached_disk)
            return {
                "memory": memory,
                "disk": disk,
                "thumbnail_config": {
                    "changed_at": thumb_config_changed_at,
                    "replace_stale_thumbnails": replace_stale_thumbnails,
                },
            }
        if not lock_acquired:
            meta_lock.acquire()
            lock_acquired = True
        try:
            conn = db_connect()
            rows = conn.execute(
                "SELECT size, COUNT(*) AS count, COALESCE(SUM(size_bytes), 0) AS bytes "
                "FROM cache_entries WHERE cache_root = ? GROUP BY size",
                (cache_root,),
            ).fetchall()
            if replace_stale_thumbnails and thumb_config_changed_at > 0:
                current_rows = conn.execute(
                    "SELECT size, COUNT(*) AS count, COALESCE(SUM(size_bytes), 0) AS bytes "
                    "FROM cache_entries "
                    "WHERE cache_root = ? AND (size = ? OR created_at >= ?) "
                    "GROUP BY size",
                    (cache_root, full_tier, thumb_config_changed_at),
                ).fetchall()
            else:
                current_rows = rows
        finally:
            if lock_acquired:
                meta_lock.release()

        for row in rows:
            if row["size"] in disk_tiers:
                disk_tiers[row["size"]]["count"] = int(row["count"])
                disk_tiers[row["size"]]["bytes"] = int(row["bytes"])
        for row in current_rows:
            if row["size"] in disk_tiers:
                disk_tiers[row["size"]]["current_count"] = int(row["count"])
                disk_tiers[row["size"]]["current_bytes"] = int(row["bytes"])
        for size, info in disk_tiers.items():
            if not replace_stale_thumbnails or size == full_tier:
                info["current_count"] = info["count"]
                info["current_bytes"] = info["bytes"]
            info["stale_count"] = max(0, info["count"] - info["current_count"])
            info["replacement_mode"] = bool(
                replace_stale_thumbnails
                and size in thumb_tiers
                and info["stale_count"] > 0
            )

        disk = {
            "root": cache_root,
            "limit_bytes": cache_limit_bytes,
            "used_bytes": sum(info["bytes"] for info in disk_tiers.values()),
            "tiers": disk_tiers,
        }
        disk_stats_cache["data"] = copy_disk_stats(disk)
        disk_stats_cache["expires"] = now + disk_stats_cache_ttl_seconds
        disk_stats_cache["stale_until"] = now + disk_stats_cache_max_stale_seconds
    return {
        "memory": memory,
        "disk": disk,
        "thumbnail_config": {
            "changed_at": thumb_config_changed_at,
            "replace_stale_thumbnails": replace_stale_thumbnails,
        },
    }


def original_cache_status(
    stats: dict,
    *,
    full_tier: str,
    original_total: int = 0,
    archive_estimates: dict | None = None,
    estimated_full_tier_bytes: int,
) -> dict:
    tier_stats = stats["disk"]["tiers"][full_tier]
    count = int(tier_stats.get("count", 0) or 0)
    bytes_used = int(tier_stats.get("bytes", 0) or 0)
    budget = int(tier_stats.get("budget_bytes", 0) or 0)
    estimated_original_bytes = int(
        (archive_estimates or {}).get("needed_bytes", {}).get(full_tier) or 0
    )
    avg_bytes = (
        max(1, int(estimated_original_bytes / original_total))
        if original_total > 0 and estimated_original_bytes > 0
        else int(bytes_used / count)
        if count > 0 and bytes_used > 0
        else estimated_full_tier_bytes
    )
    estimated_capacity = int(budget / avg_bytes) if avg_bytes > 0 and budget > 0 else 0
    if original_total > 0:
        target = min(original_total, max(count, estimated_capacity))
        remaining = min(max(0, original_total - count), max(0, estimated_capacity - count))
    else:
        target = max(count, estimated_capacity)
        remaining = max(0, estimated_capacity - count)

    utilization_pct = round((bytes_used / budget) * 100, 1) if budget > 0 else 0.0
    progress_pct = round((count / target) * 100, 1) if target > 0 else 0.0
    return {
        "count": count,
        "total": target,
        "eligible_total": int(original_total or 0),
        "remaining": remaining,
        "bytes": bytes_used,
        "budget_bytes": budget,
        "avg_bytes": avg_bytes,
        "estimated_capacity": estimated_capacity,
        "progress_pct": progress_pct,
        "utilization_pct": utilization_pct,
    }


def pregen_status(
    *,
    pregen_state: dict,
    stats: dict,
    target_total: int,
    original_total: int,
    archive_estimates: dict | None,
    thumb_tiers: tuple[str, ...],
    background_tier_budget: Callable[[str, dict | None], int],
    estimated_tier_bytes: Callable[[str], int],
    original_status: Callable[[dict, int, dict | None], dict],
    pregen_rates: Callable[[], tuple[float, float, dict]],
    pregen_background_decision: Callable[[], object],
    pregen_generate_batch_for_decision: Callable[[object], int],
    idle_seconds: float,
) -> dict:
    phases = {}
    remaining = 0
    background_budgets = {
        size: background_tier_budget(size, archive_estimates)
        for size in thumb_tiers
    }
    for size in thumb_tiers:
        tier_stats = stats["disk"]["tiers"][size]
        count = int(
            tier_stats.get("progress_count")
            if tier_stats.get("progress_count") is not None
            else (
                tier_stats.get("current_count", 0)
                if tier_stats.get("replacement_mode")
                else tier_stats.get("count", 0)
            )
        )
        available_count = int(tier_stats.get("count", 0))
        current_count = int(tier_stats.get("current_count", count))
        target = target_total
        progress_pct = round((count / target_total) * 100, 1) if target_total > 0 else 0.0
        if target_total > 0:
            background_budget = background_budgets[size]
            avg_bytes = (
                int(tier_stats.get("current_bytes", 0) / current_count)
                if current_count > 0 and tier_stats.get("current_bytes", 0) > 0
                else int(tier_stats["bytes"] / available_count)
                if available_count > 0 and tier_stats["bytes"] > 0
                else estimated_tier_bytes(size)
            )
            if avg_bytes > 0 and background_budget > 0:
                target = min(target_total, max(count, int(background_budget / avg_bytes)))
            remaining += max(0, target - count)
        phases[size] = {
            "count": count,
            "available_count": available_count,
            "current_count": current_count,
            "stale_count": max(0, available_count - current_count),
            "replacement_mode": bool(tier_stats.get("replacement_mode")),
            "total": target,
            "progress_pct": round((count / target) * 100, 1) if target > 0 else progress_pct,
            "budget_bytes": tier_stats["budget_bytes"],
            "background_budget_bytes": background_budgets[size],
            "remaining": max(0, target - count),
        }

    preview_total = sum(int(phase.get("total", 0) or 0) for phase in phases.values())
    preview_count = sum(
        min(int(phase.get("count", 0) or 0), int(phase.get("total", 0) or 0))
        for phase in phases.values()
    )
    preview_images_remaining = max(
        (int(phase.get("remaining", 0) or 0) for phase in phases.values()),
        default=0,
    )
    preview = {
        "count": preview_count,
        "total": preview_total,
        "remaining": remaining,
        "image_remaining": preview_images_remaining,
        "progress_pct": round((preview_count / preview_total) * 100, 1) if preview_total > 0 else 0.0,
    }
    originals = original_status(stats, original_total, archive_estimates)

    recent_rate, overall_rate, diagnostics = pregen_rates()
    thumbnail_rate = diagnostics.get("recent_thumbnails_written_per_min", 0.0)
    preview_rate = max(recent_rate, overall_rate, thumbnail_rate / max(1, len(thumb_tiers)))
    eta_seconds = (
        int((preview_images_remaining / preview_rate) * 60)
        if preview_images_remaining > 0 and preview_rate > 0
        else None
    )
    effective_rate = max(recent_rate, overall_rate)
    original_eta_seconds = (
        int((originals["remaining"] / effective_rate) * 60)
        if remaining <= 0 and originals["remaining"] > 0 and effective_rate > 0
        else None
    )
    replacement_mode = any(phase["replacement_mode"] for phase in phases.values())
    decision = pregen_background_decision()
    governor_status = decision.to_dict()
    governor_status["effective_thumbnail_batch_size"] = pregen_generate_batch_for_decision(decision)

    return {
        **dict(pregen_state),
        "governor": governor_status,
        "idle_seconds": round(max(0.0, idle_seconds), 2),
        "phases": phases,
        "preview": preview,
        "originals": originals,
        "remaining": remaining,
        "preview_remaining": remaining,
        "originals_remaining": originals["remaining"],
        "recent_images_per_min": round(recent_rate, 2),
        "overall_images_per_min": round(overall_rate, 2),
        "recent_source_reads_per_min": round(diagnostics["recent_source_reads_per_min"], 2),
        "recent_thumbnails_written_per_min": round(diagnostics["recent_thumbnails_written_per_min"], 2),
        "recent_read_mbps": round(diagnostics["recent_read_mbps"], 2),
        "avg_source_read_seconds": round(diagnostics["avg_source_read_seconds"], 4),
        "avg_decode_encode_seconds": round(diagnostics["avg_decode_encode_seconds"], 4),
        "recent_source_read_failures": int(diagnostics["recent_source_read_failures"]),
        "source_read_failures": int(diagnostics["source_read_failures"]),
        "eta_seconds": eta_seconds,
        "original_eta_seconds": original_eta_seconds,
        "replacement_mode": replacement_mode,
    }
