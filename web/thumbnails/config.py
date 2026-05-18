"""Thumbnail cache configuration defaults and budget math."""

import os

THUMB_TIERS = ("sm", "md", "lg")
FULL_TIER = "full"
ALL_TIERS = THUMB_TIERS + (FULL_TIER,)
SIZES = {
    "sm": 400,
    "md": 1920,
    "lg": 3840,
}
THUMB_QUALITY = 92
CACHE_VERSION = "v3"
CACHE_MARKER = ".photoarchive-cache"
CACHE_PROFILE = "original_heavy"
SSD_CACHE_DIR = os.getenv(
    "PHOTOARCHIVE_THUMB_CACHE_DIR",
    os.path.join(os.path.dirname(os.path.dirname(__file__)), ".thumbcache"),
)
SSD_CACHE_BYTES = 10 * 1024 * 1024 * 1024
MEMORY_CACHE_BYTES = 512 * 1024 * 1024
PREGENERATE_ON_IDLE = True
PREGENERATE_IDLE_SECONDS = 1.0
PREGENERATE_SCAN_BATCH = 1024
PREGENERATE_GENERATE_BATCH = 16
PREGENERATE_NO_PROGRESS_SCAN_LIMIT = 12
PREGENERATE_BATCH_PAUSE_SECONDS = 0.25
MANUAL_PREGEN_FOREGROUND_SETTLE_SECONDS = 5.0
THUMBNAIL_RETRY_SECONDS = 6 * 60 * 60
BROWSER_CACHE_MAX_AGE = 86400
BROWSER_CACHE_STALE_WHILE_REVALIDATE = 604800
HOT_LG_RESERVE_FRACTION = 0.35
HOT_LG_RESERVE_MIN_BYTES = 2 * 1024 * 1024 * 1024
HOT_LG_RESERVE_MAX_BYTES = 64 * 1024 * 1024 * 1024
COLD_CACHE_ACCESS_OFFSET_SECONDS = 45 * 24 * 60 * 60
JPEG_EXTENSIONS = {".jpg", ".jpeg"}
BROWSER_ORIGINAL_EXTENSIONS = {".avif", ".bmp", ".gif", ".jpeg", ".jpg", ".png", ".webp"}
RAW_EXTENSIONS = {
    ".arw",
    ".cr2",
    ".cr3",
    ".dng",
    ".nef",
    ".orf",
    ".raf",
    ".rw2",
}

DEFAULT_EXPORT_NAMES = (
    "THUMB_TIERS",
    "FULL_TIER",
    "ALL_TIERS",
    "SIZES",
    "THUMB_QUALITY",
    "CACHE_VERSION",
    "CACHE_MARKER",
    "CACHE_PROFILE",
    "SSD_CACHE_DIR",
    "SSD_CACHE_BYTES",
    "MEMORY_CACHE_BYTES",
    "PREGENERATE_ON_IDLE",
    "PREGENERATE_IDLE_SECONDS",
    "PREGENERATE_SCAN_BATCH",
    "PREGENERATE_GENERATE_BATCH",
    "PREGENERATE_NO_PROGRESS_SCAN_LIMIT",
    "PREGENERATE_BATCH_PAUSE_SECONDS",
    "MANUAL_PREGEN_FOREGROUND_SETTLE_SECONDS",
    "THUMBNAIL_RETRY_SECONDS",
    "BROWSER_CACHE_MAX_AGE",
    "BROWSER_CACHE_STALE_WHILE_REVALIDATE",
    "HOT_LG_RESERVE_FRACTION",
    "HOT_LG_RESERVE_MIN_BYTES",
    "HOT_LG_RESERVE_MAX_BYTES",
    "COLD_CACHE_ACCESS_OFFSET_SECONDS",
    "JPEG_EXTENSIONS",
    "BROWSER_ORIGINAL_EXTENSIONS",
    "RAW_EXTENSIONS",
)

SSD_REMAINDER_PROFILES = {
    "browse_fast": {"lg": 0.80, FULL_TIER: 0.20},
    "balanced": {"lg": 0.55, FULL_TIER: 0.45},
    "original_heavy": {"lg": 0.35, FULL_TIER: 0.65},
}

MEMORY_CACHE_PROFILES = {
    "browse_fast": {"sm": 0.30, "md": 0.50, "lg": 0.20},
    "balanced": {"sm": 0.20, "md": 0.45, "lg": 0.35},
    "original_heavy": {"sm": 0.15, "md": 0.45, "lg": 0.40},
}
VALID_CACHE_PROFILES = frozenset(SSD_REMAINDER_PROFILES)


def normalize_ratios(values: dict[str, float], tiers: tuple[str, ...]) -> dict[str, float]:
    cleaned = {tier: max(0.0, float(values.get(tier, 0.0) or 0.0)) for tier in tiers}
    total = sum(cleaned.values())
    if total <= 0:
        return {tier: 0.0 for tier in tiers}
    return {tier: cleaned[tier] / total for tier in tiers}


def active_memory_ratios(profile: str) -> dict[str, float]:
    return normalize_ratios(
        MEMORY_CACHE_PROFILES.get(profile, MEMORY_CACHE_PROFILES["original_heavy"]),
        THUMB_TIERS,
    )


def allocate_by_ratios(total_bytes: int, ratios: dict[str, float], tiers: tuple[str, ...]) -> dict[str, int]:
    total = max(0, int(total_bytes))
    allocations = {tier: 0 for tier in tiers}
    if total <= 0:
        return allocations

    assigned = 0
    active_tiers = [tier for tier in tiers if ratios.get(tier, 0.0) > 0]
    for tier in active_tiers[:-1]:
        amount = int(total * ratios[tier])
        allocations[tier] = amount
        assigned += amount
    if active_tiers:
        allocations[active_tiers[-1]] = max(0, total - assigned)
    return allocations


def quality_size_factor(quality_value: int) -> float:
    quality = max(40, min(100, int(quality_value)))
    if quality >= 92:
        return 1.0 + (quality - 92) * 0.08
    if quality >= 80:
        return 0.45 + ((quality - 80) / 12.0) * 0.55
    if quality >= 60:
        return 0.28 + ((quality - 60) / 20.0) * 0.17
    return 0.18 + ((quality - 40) / 20.0) * 0.10


def estimated_tier_bytes(size: str, quality_value: int) -> int:
    base = {
        "sm": 33 * 1024,
        "md": 450 * 1024,
        "lg": 1750 * 1024,
        FULL_TIER: 20 * 1024 * 1024,
    }
    if size == FULL_TIER:
        return base[FULL_TIER]
    return max(1, int(base.get(size, base["md"]) * quality_size_factor(quality_value)))


def allocate_weighted_capped(
    total_bytes: int,
    weights: dict[str, float],
    caps: dict[str, int],
) -> dict[str, int]:
    tiers = tuple(caps.keys())
    ratios = normalize_ratios(weights, tiers)
    allocations = {tier: 0 for tier in tiers}
    remaining = max(0, int(total_bytes))
    if remaining <= 0:
        return allocations

    rough = allocate_by_ratios(remaining, ratios, tiers)
    for tier in tiers:
        allocations[tier] = min(max(0, int(caps.get(tier, 0))), rough.get(tier, 0))
    remaining -= sum(allocations.values())

    for tier in sorted(tiers, key=lambda item: ratios.get(item, 0.0), reverse=True):
        if remaining <= 0:
            break
        room = max(0, int(caps.get(tier, 0)) - allocations[tier])
        take = min(room, remaining)
        allocations[tier] += take
        remaining -= take
    return allocations


def allocate_disk_budget(
    total_bytes: int,
    *,
    needed_bytes: dict[str, int],
    profile: str,
) -> dict[str, int]:
    total = max(0, int(total_bytes))
    allocations = {tier: 0 for tier in ALL_TIERS}
    if total <= 0:
        return allocations

    remaining = total
    for tier in ("sm", "md"):
        amount = min(remaining, int(needed_bytes.get(tier, 0) or 0))
        allocations[tier] = amount
        remaining -= amount
        if remaining <= 0:
            return allocations

    remainder_weights = SSD_REMAINDER_PROFILES.get(
        profile,
        SSD_REMAINDER_PROFILES["original_heavy"],
    )
    remainder = allocate_weighted_capped(
        remaining,
        remainder_weights,
        {
            "lg": int(needed_bytes.get("lg", 0) or 0),
            FULL_TIER: int(needed_bytes.get(FULL_TIER, 0) or 0),
        },
    )
    allocations["lg"] = remainder["lg"]
    allocations[FULL_TIER] = remainder[FULL_TIER]

    leftover = remaining - allocations["lg"] - allocations[FULL_TIER]
    if leftover > 0:
        allocations["md"] += leftover
    return allocations


def background_tier_budget(
    *,
    size: str,
    budget: int,
    needed_bytes: int,
    hot_lg_reserve_fraction: float,
    hot_lg_reserve_min_bytes: int,
    hot_lg_reserve_max_bytes: int,
) -> int:
    budget = int(budget or 0)
    if size != "lg" or budget <= 0:
        return budget
    needed = int(needed_bytes or 0)
    if needed <= 0 or budget >= int(needed * 0.95):
        return budget

    reserve = min(
        budget,
        max(
            hot_lg_reserve_min_bytes,
            min(hot_lg_reserve_max_bytes, int(budget * hot_lg_reserve_fraction)),
        ),
    )
    return max(0, budget - reserve)


def cache_budget_config(
    *,
    profile: str,
    memory_cache_bytes: int,
    disk_allocations: dict[str, int],
) -> dict:
    memory_ratios = active_memory_ratios(profile)
    return {
        "profile": profile,
        "ssd_ratios": normalize_ratios(disk_allocations, ALL_TIERS),
        "memory_ratios": memory_ratios,
        "ssd_allocations": dict(disk_allocations),
        "memory_allocations": allocate_by_ratios(memory_cache_bytes, memory_ratios, THUMB_TIERS),
    }


def runtime_config_values(
    config: dict,
    *,
    current_sizes: dict[str, int],
    current_thumb_quality: int,
    current_browser_cache_max_age: int,
    current_browser_cache_stale_while_revalidate: int,
    current_cache_profile: str,
    current_pregenerate_on_idle: bool,
    current_generate_batch: int,
    current_ssd_cache_dir: str,
    current_executor_workers: int,
    current_prefetch_workers: int,
    as_bool,
) -> dict:
    memory_cache_gb = config.get("memory_cache_gb")
    if memory_cache_gb is None:
        try:
            memory_cache_gb = float(config.get("memory_cache_mb", 512)) / 1024.0
        except (TypeError, ValueError):
            memory_cache_gb = 0.5
    try:
        memory_cache_bytes = max(0, int(float(memory_cache_gb) * 1024 * 1024 * 1024))
    except (TypeError, ValueError):
        memory_cache_bytes = int(0.5 * 1024 * 1024 * 1024)

    profile = str(config.get("cache_profile", current_cache_profile)).strip().lower()
    disk_cache_dir = (
        config.get("ssd_cache_dir")
        or config.get("disk_cache_dir")
        or current_ssd_cache_dir
    )
    return {
        "replace_thumbnail_cache": as_bool(config.get("_replace_thumbnail_cache"), False),
        "sizes": {
            "sm": int(config.get("thumb_size_sm", current_sizes["sm"])),
            "md": int(config.get("thumb_size_md", current_sizes["md"])),
            "lg": int(config.get("thumb_size_lg", current_sizes["lg"])),
        },
        "thumb_quality": int(
            config.get("thumb_quality", config.get("jpeg_quality", current_thumb_quality))
        ),
        "browser_cache_max_age": int(
            config.get("browser_cache_max_age", current_browser_cache_max_age)
        ),
        "browser_cache_stale_while_revalidate": int(
            config.get(
                "browser_cache_stale_while_revalidate",
                current_browser_cache_stale_while_revalidate,
            )
        ),
        "memory_cache_bytes": memory_cache_bytes,
        "ssd_cache_bytes": max(0, int(config.get("ssd_cache_gb", 10))) * 1024 * 1024 * 1024,
        "cache_profile": profile if profile in VALID_CACHE_PROFILES else "original_heavy",
        "pregenerate_on_idle": as_bool(
            config.get("pregenerate_on_idle"),
            current_pregenerate_on_idle,
        ),
        "pregenerate_generate_batch": max(
            4,
            min(64, int(config.get("pregen_generate_batch", current_generate_batch))),
        ),
        "pregenerate_batch_pause_seconds": max(
            0.0,
            min(5.0, float(config.get("pregen_batch_pause_ms", 250)) / 1000.0),
        ),
        "ssd_cache_dir": os.path.abspath(str(disk_cache_dir).strip() or current_ssd_cache_dir),
        "user_workers": int(config.get("user_workers", current_executor_workers)),
        "prefetch_workers": int(config.get("prefetch_workers", current_prefetch_workers)),
    }
