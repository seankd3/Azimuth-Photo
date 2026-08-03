"""Cache-status builder owned by the cache feature."""

from core.catalog_path import catalog_path
import asyncio
import os
import shutil
import time
from collections.abc import Awaitable, Callable

import db
import thumbnails
from core import requests as request_helpers
from core import responses as response_helpers
from data.repositories import stats as stats_repository
from features.settings import status as settings_status


AsyncDictBuilder = Callable[[], Awaitable[dict]]
CacheRootProvider = Callable[[], str]
DbPathProvider = Callable[[], str]
ExpireSettingsResponseCache = Callable[[], None]


_cache_status_cache: dict[tuple[int], dict] = {}
_cache_status_refreshing: set[tuple[int]] = set()
_cache_status_cache_ttl_seconds = 30.0
_cache_status_ahead_limit = 5000
_browser_original_count_cache = {"value": None, "bytes": 0, "expires": 0.0}
_browser_original_count_cache_ttl_seconds = 30.0



def _cache_root() -> str:
    return thumbnails.SSD_CACHE_DIR



async def _catalog_image_counts() -> dict:
    return await db.get_catalog_image_counts()


def _expire_settings_cache() -> None:
    settings_status.expire_settings_response_cache()


def invalidate_cache_status_cache() -> None:
    _cache_status_cache.clear()
    _cache_status_refreshing.clear()
    _browser_original_count_cache["value"] = None
    _browser_original_count_cache["bytes"] = 0
    _browser_original_count_cache["expires"] = 0.0
    _expire_settings_cache()


async def _browser_original_summary() -> dict:
    now = time.monotonic()
    cached = _browser_original_count_cache.get("value")
    if cached is not None and float(_browser_original_count_cache.get("expires") or 0) > now:
        return {
            "count": int(cached),
            "bytes": int(_browser_original_count_cache.get("bytes") or 0),
        }

    summary = await stats_repository.browser_original_summary(
        catalog_path(),
        catalog_counts=await _catalog_image_counts(),
        browser_extensions=tuple(sorted(thumbnails.BROWSER_ORIGINAL_EXTENSIONS)),
        is_browser_displayable_original=thumbnails.is_browser_displayable_original,
    )
    _browser_original_count_cache["value"] = int(summary.get("count") or 0)
    _browser_original_count_cache["bytes"] = int(summary.get("bytes") or 0)
    _browser_original_count_cache["expires"] = time.monotonic() + _browser_original_count_cache_ttl_seconds
    return summary


def _cache_recommendations(
    cache: dict,
    eligible_images: int,
    total_images: int,
    browser_original_images: int,
    estimates: dict | None = None,
) -> dict:
    estimates = estimates or thumbnails.cache_archive_estimates()
    tiers = {}
    for tier_name in thumbnails.ALL_TIERS:
        avg_bytes = int(estimates.get("avg_bytes", {}).get(tier_name) or thumbnails.estimated_tier_bytes(tier_name))
        target_count = browser_original_images if tier_name == thumbnails.FULL_TIER else eligible_images
        full_archive_bytes = int(estimates.get("needed_bytes", {}).get(tier_name) or 0)
        if full_archive_bytes <= 0:
            full_archive_bytes = avg_bytes * max(0, int(target_count))
        budget_bytes = int(cache.get("disk", {}).get("tiers", {}).get(tier_name, {}).get("budget_bytes") or 0)
        estimated_cached = int(budget_bytes / avg_bytes) if avg_bytes > 0 else 0
        tiers[tier_name] = {
            "avg_bytes": avg_bytes,
            "sample_count": int(estimates.get("sample_count", {}).get(tier_name) or 0),
            "full_archive_bytes": full_archive_bytes,
            "budget_bytes": budget_bytes,
            "estimated_cached": min(target_count, estimated_cached),
            "coverage_pct": round((budget_bytes / full_archive_bytes) * 100, 1) if full_archive_bytes > 0 else 0.0,
        }

    return {
        "eligible_images": eligible_images,
        "total_images": total_images,
        "browser_original_images": browser_original_images,
        "budget": thumbnails.cache_budget_config(),
        "tiers": tiers,
    }


def _cache_archive_estimates_from_status(
    cache: dict,
    active_images: int,
    total_images: int,
    browser_original_images: int = 0,
    browser_original_bytes: int = 0,
) -> dict:
    avg_bytes = {}
    sample_count = {}
    for tier_name in thumbnails.ALL_TIERS:
        tier = cache.get("disk", {}).get("tiers", {}).get(tier_name, {})
        count = int(tier.get("current_count") or tier.get("count") or 0)
        bytes_used = int(tier.get("current_bytes") or tier.get("bytes") or 0)
        avg_bytes[tier_name] = (
            max(1, int(bytes_used / count))
            if count > 0 and bytes_used > 0
            else thumbnails.estimated_tier_bytes(tier_name)
        )
        sample_count[tier_name] = count

    needed_bytes = {
        tier_name: avg_bytes[tier_name] * max(0, int(active_images))
        for tier_name in thumbnails.THUMB_TIERS
    }
    if browser_original_bytes > 0:
        avg_bytes[thumbnails.FULL_TIER] = max(
            1,
            int(browser_original_bytes / max(1, int(browser_original_images or 0))),
        )
        needed_bytes[thumbnails.FULL_TIER] = int(browser_original_bytes)
    else:
        needed_bytes[thumbnails.FULL_TIER] = (
            avg_bytes[thumbnails.FULL_TIER] * max(0, int(total_images))
        )
    return {
        "active_images": max(0, int(active_images)),
        "total_images": max(0, int(total_images)),
        "avg_bytes": avg_bytes,
        "sample_count": sample_count,
        "needed_bytes": needed_bytes,
    }


def _system_resource_status(cache_root: str) -> dict:
    disk_path = cache_root or os.getcwd()
    try:
        os.makedirs(disk_path, exist_ok=True)
    except OSError:
        disk_path = os.path.dirname(disk_path) or os.getcwd()
    try:
        disk_usage = shutil.disk_usage(disk_path)
        disk = {
            "path": disk_path,
            "total_bytes": int(disk_usage.total),
            "used_bytes": int(disk_usage.used),
            "free_bytes": int(disk_usage.free),
            "free_pct": round((disk_usage.free / disk_usage.total) * 100, 1) if disk_usage.total > 0 else 0.0,
        }
    except OSError:
        disk = {
            "path": disk_path,
            "total_bytes": 0,
            "used_bytes": 0,
            "free_bytes": 0,
            "free_pct": 0.0,
        }

    meminfo = {}
    try:
        with open("/proc/meminfo", "r", encoding="utf-8") as f:
            for line in f:
                key, raw_value = line.split(":", 1)
                parts = raw_value.strip().split()
                if parts:
                    meminfo[key] = int(parts[0]) * 1024
    except OSError:
        pass
    total = int(meminfo.get("MemTotal") or 0)
    available = int(meminfo.get("MemAvailable") or 0)
    swap_total = int(meminfo.get("SwapTotal") or 0)
    swap_free = int(meminfo.get("SwapFree") or 0)
    memory = {
        "total_bytes": total,
        "available_bytes": available,
        "used_bytes": max(0, total - available),
        "available_pct": round((available / total) * 100, 1) if total > 0 else 0.0,
        "swap_total_bytes": swap_total,
        "swap_used_bytes": max(0, swap_total - swap_free),
        "swap_used_pct": round(((swap_total - swap_free) / swap_total) * 100, 1) if swap_total > 0 else 0.0,
    }
    return {"disk": disk, "memory": memory}


def _copy_cache_status_response(status: dict) -> dict:
    return response_helpers.copy_cache_status_response(status)


def cached_cache_status(ahead: int = 0, *, stale_reason: str = "refresh_deferred") -> dict | None:
    ahead = request_helpers.clamp_int(ahead, 0, 0, _cache_status_ahead_limit)
    cached = _cache_status_cache.get((ahead,))
    if not cached or cached.get("data") is None:
        return None
    response = _copy_cache_status_response(cached["data"])
    response["counts_stale"] = True
    response["status_stale"] = True
    response["stale_reason"] = stale_reason
    return response


def deferred_cache_status(ahead: int = 0, *, reason: str = "refresh_deferred") -> dict:
    ahead = request_helpers.clamp_int(ahead, 0, 0, _cache_status_ahead_limit)
    cache_root = _cache_root()
    return {
        "memory": {"limit_bytes": 0, "used_bytes": 0, "tiers": {}, "utilization_pct": 0.0},
        "disk": {
            "root": cache_root,
            "limit_bytes": 0,
            "used_bytes": 0,
            "tiers": {},
            "utilization_pct": 0.0,
        },
        "eligible_images": 0,
        "browser_original_images": 0,
        "recommendations": {
            "eligible_images": 0,
            "total_images": 0,
            "browser_original_images": 0,
            "budget": thumbnails.cache_budget_config(),
            "tiers": {},
        },
        "pregen": {
            "enabled": False,
            "manual_mode": False,
            "manual_pause": False,
            "state": "stale",
            "message": "Cache status refresh is still catching up.",
            "preview": {"count": 0, "total": 0, "remaining": 0, "image_remaining": 0, "progress_pct": 0.0},
            "originals": {"count": 0, "total": 0, "remaining": 0, "progress_pct": 0.0},
        },
        "system_resources": _system_resource_status(cache_root),
        "total": 0,
        "cached": 0,
        "window": ahead,
        "counts_stale": True,
        "status_stale": True,
        "stale_reason": reason,
    }


def _cache_status_ttl(result: dict) -> float:
    pregen = result.get("pregen") or {}
    if pregen.get("state") == "running":
        return 2.0

    preview_remaining = int((pregen.get("preview") or {}).get("remaining") or 0)
    original_remaining = int((pregen.get("originals") or {}).get("remaining") or 0)
    warming_enabled = bool(pregen.get("enabled")) and not bool(pregen.get("manual_pause"))
    if warming_enabled and (preview_remaining > 0 or original_remaining > 0):
        return 1.0

    return _cache_status_cache_ttl_seconds


async def build_cache_status(
    ahead: int = 100,
    *,
    force: bool = False,
    cache_recommendations: Callable[..., dict] | None = None,
):
    ahead = request_helpers.clamp_int(ahead, 0, 0, _cache_status_ahead_limit)
    cache_key = (ahead,)
    now = time.monotonic()
    if not force:
        cached = _cache_status_cache.get(cache_key)
        if cached and cached["expires"] > now:
            return _copy_cache_status_response(cached["data"])
        if cached and cached.get("data") is not None:
            if _cache_status_ttl(cached["data"]) > 1.0:
                if cache_key not in _cache_status_refreshing:
                    _cache_status_refreshing.add(cache_key)

                    async def _refresh_cache_status():
                        try:
                            await build_cache_status(
                                ahead=ahead,
                                force=True,
                                cache_recommendations=cache_recommendations,
                            )
                        except Exception:
                            pass
                        finally:
                            _cache_status_refreshing.discard(cache_key)

                    asyncio.create_task(_refresh_cache_status())
                return _copy_cache_status_response(cached["data"])

    counts = await _catalog_image_counts()

    if int(counts.get("active_images") or 0) > 0:
        browser_original_summary, cache = await asyncio.gather(
            _browser_original_summary(),
            asyncio.to_thread(thumbnails.cache_stats),
        )
        browser_original_total = int(browser_original_summary["count"])
        browser_original_bytes = int(browser_original_summary["bytes"])
    else:
        cache = await asyncio.to_thread(thumbnails.cache_stats)
        browser_original_total = 0
        browser_original_bytes = 0
    active_total = int(counts.get("active_images") or 0)
    total_images = int(counts.get("total_catalog_images") or 0)
    archive_estimates = _cache_archive_estimates_from_status(
        cache,
        active_total,
        total_images,
        browser_original_total,
        browser_original_bytes,
    )

    memory = cache["memory"]
    disk = cache["disk"]
    memory["utilization_pct"] = round(
        (memory["used_bytes"] / memory["limit_bytes"]) * 100,
        1,
    ) if memory["limit_bytes"] > 0 else 0.0
    disk["utilization_pct"] = round(
        (disk["used_bytes"] / disk["limit_bytes"]) * 100,
        1,
    ) if disk["limit_bytes"] > 0 else 0.0

    for tier_name, tier in disk["tiers"].items():
        progress_total = active_total if tier_name in thumbnails.THUMB_TIERS else browser_original_total
        progress_count = (
            tier.get("current_count", 0)
            if tier.get("replacement_mode")
            else tier.get("count", 0)
        )
        tier["progress_total"] = progress_total
        tier["progress_count"] = progress_count
        tier["progress_pct"] = round((progress_count / progress_total) * 100, 1) if progress_total > 0 else 0.0
        tier["utilization_pct"] = round(
            (tier["bytes"] / tier["budget_bytes"]) * 100,
            1,
        ) if tier["budget_bytes"] > 0 else 0.0

    recommendations_builder = cache_recommendations or _cache_recommendations
    result = {
        **cache,
        "eligible_images": active_total,
        "browser_original_images": browser_original_total,
        "recommendations": recommendations_builder(
            cache,
            active_total,
            total_images,
            browser_original_total,
            archive_estimates,
        ),
        "pregen": thumbnails.get_pregen_status(
            active_total,
            cache,
            browser_original_total,
            archive_estimates,
        ),
        "system_resources": _system_resource_status(_cache_root()),
    }

    if ahead > 0:
        ahead_counts = await stats_repository.cache_ahead_counts(
            catalog_path(),
            ahead=ahead,
            cache_root=_cache_root(),
            size="lg",
        )
        total = int(ahead_counts.get("total") or 0)
        cached = int(ahead_counts.get("cached") or 0)

        result["total"] = total
        result["cached"] = cached
    else:
        result["total"] = 0
        result["cached"] = 0
    result["window"] = ahead
    cache_ttl = _cache_status_ttl(result)
    _cache_status_cache[cache_key] = {
        "data": _copy_cache_status_response(result),
        "expires": time.monotonic() + cache_ttl,
    }
    return result
