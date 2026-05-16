import asyncio
import hashlib
import io
import os
import shutil
import sqlite3
import threading
import time
from collections import OrderedDict, deque
from concurrent.futures import ThreadPoolExecutor
from functools import partial

import db
import resource_governor
from PIL import Image, ImageOps

Image.MAX_IMAGE_PIXELS = None

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
    os.path.join(os.path.dirname(__file__), ".thumbcache"),
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
_executor_workers = 4
_prefetch_workers_count = 6

_disk_allocations = {tier: 0 for tier in ALL_TIERS}
_executor = ThreadPoolExecutor(max_workers=_executor_workers, thread_name_prefix="thumb")
_prefetch_executor = ThreadPoolExecutor(
    max_workers=_prefetch_workers_count,
    thread_name_prefix="thumb-prefetch",
)

# In-memory thumbnail LRU: (size, image_id) -> (source_signature, jpeg_bytes)
_memory_cache: OrderedDict[tuple[str, int], tuple[str, bytes]] = OrderedDict()
_memory_cache_bytes = 0
_memory_tier_bytes = {size: 0 for size in THUMB_TIERS}
_cache_lock = threading.Lock()
_meta_lock = threading.Lock()

# Shared in-flight work so a burst of requests only performs one source read.
_inflight: dict[tuple[str, int, str], asyncio.Task[object]] = {}
_thumbnail_retry_after: dict[tuple[str, int, str], float] = {}

# Write-behind queue for cache DB entries — reduces _meta_lock contention.
_write_queue: list[tuple[str, int, str, str, int, float]] = []  # (size, image_id, sig, path, bytes, access)
_write_queue_lock = threading.Lock()

# Orientation detections pending DB write: image_id -> (orientation, aspect_ratio)
_orientation_queue: dict[int, tuple[str, float]] = {}
_orientation_lock = threading.Lock()

_last_user_activity = time.monotonic()
_prefetching = False
_pregen_manual_mode = False
_pregen_manual_pause = False
_pregen_scan_offsets = {tier: 0 for tier in THUMB_TIERS}
_pregen_bulk_cursor = {"source_id": 0, "filepath": "", "id": 0}
_pregen_full_cursor = {"source_id": 0, "filepath": "", "id": 0}
_last_thumb_config_signature = ""
_thumb_config_changed_at = 0.0
_replace_stale_thumbnails = False
_pregen_status = {
    "enabled": True,
    "manual_mode": False,
    "manual_pause": False,
    "state": "idle",
    "message": "",
    "active_phase": None,
    "started_at": None,
    "last_generated_at": None,
    "generated_this_session": 0,
    "last_error": "",
}
_pregen_history = deque()
_pregen_session_started_at: float | None = None
_pregen_session_generated = 0
_pregen_source_read_failures = 0

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


_persistent_conn: sqlite3.Connection | None = None
_cache_metadata_retry_after = 0.0
_cache_metadata_lock_failures = 0


def _db_connect() -> sqlite3.Connection:
    """Return persistent connection (under _meta_lock, so safe to share)."""
    global _persistent_conn
    if _persistent_conn is not None:
        try:
            _persistent_conn.execute("SELECT 1")
            return _persistent_conn
        except sqlite3.ProgrammingError:
            _persistent_conn = None

    if _persistent_conn is None:
        conn = sqlite3.connect(db.DB_PATH, timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
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


def _as_bool(value, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() not in ("", "0", "false", "no", "off")
    return bool(value)


def _current_time() -> float:
    return time.time()


def _is_sqlite_locked(exc: Exception) -> bool:
    return isinstance(exc, sqlite3.OperationalError) and "locked" in str(exc).lower()


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


def _cache_access_time(*, hot: bool) -> float:
    now = _current_time()
    return now if hot else now - COLD_CACHE_ACCESS_OFFSET_SECONDS


def _cache_marker_path() -> str:
    return os.path.join(SSD_CACHE_DIR, CACHE_MARKER)


def _cache_dir_has_marker() -> bool:
    return bool(SSD_CACHE_DIR and os.path.isfile(_cache_marker_path()))


def _cache_dir_is_legacy_cache_layout() -> bool:
    """True for empty or old unmarked cache roots containing only cache tiers."""
    if not SSD_CACHE_DIR or not os.path.isdir(SSD_CACHE_DIR):
        return True
    allowed = set(ALL_TIERS) | {CACHE_MARKER}
    try:
        entries = os.listdir(SSD_CACHE_DIR)
    except OSError:
        return False
    for entry in entries:
        if entry not in allowed:
            return False
        path = os.path.join(SSD_CACHE_DIR, entry)
        if entry == CACHE_MARKER:
            if not os.path.isfile(path):
                return False
        elif not os.path.isdir(path):
            return False
    return True


def _write_cache_marker():
    if not SSD_CACHE_DIR:
        return
    try:
        with open(_cache_marker_path(), "w", encoding="utf-8") as marker:
            marker.write("photoArchive thumbnail cache\n")
    except OSError as exc:
        print(f"Could not write cache marker for {SSD_CACHE_DIR}: {exc}")


def _cache_dir_safe_to_clear() -> tuple[bool, str]:
    if not SSD_CACHE_DIR:
        return True, ""
    if not os.path.exists(SSD_CACHE_DIR):
        return True, ""
    if not os.path.isdir(SSD_CACHE_DIR):
        return False, f"Cache path is not a directory: {SSD_CACHE_DIR}"
    if _cache_dir_has_marker():
        return True, ""
    if _cache_dir_is_legacy_cache_layout():
        _write_cache_marker()
        return True, ""
    return (
        False,
        "Refusing to clear an unmarked cache directory that contains non-cache files",
    )


def note_user_activity():
    global _last_user_activity
    _last_user_activity = time.monotonic()


def get_idle_seconds() -> float:
    return max(0.0, time.monotonic() - _last_user_activity)


def _ensure_disk_cache_dirs():
    if not SSD_CACHE_DIR:
        return
    should_mark = not os.path.exists(SSD_CACHE_DIR) or _cache_dir_has_marker() or _cache_dir_is_legacy_cache_layout()
    os.makedirs(SSD_CACHE_DIR, exist_ok=True)
    for size in THUMB_TIERS:
        os.makedirs(os.path.join(SSD_CACHE_DIR, size), exist_ok=True)
    os.makedirs(os.path.join(SSD_CACHE_DIR, FULL_TIER), exist_ok=True)
    if should_mark:
        _write_cache_marker()


def _cleanup_stale_cache_temps(max_age_seconds: float = 30 * 60) -> dict:
    if not SSD_CACHE_DIR or not os.path.isdir(SSD_CACHE_DIR):
        return {"files_removed": 0, "bytes_removed": 0}
    cutoff = time.time() - max(60.0, float(max_age_seconds))
    files_removed = 0
    bytes_removed = 0
    for tier in ALL_TIERS:
        tier_dir = os.path.join(SSD_CACHE_DIR, tier)
        if not os.path.isdir(tier_dir):
            continue
        for root, _dirs, files in os.walk(tier_dir):
            for filename in files:
                if not filename.endswith(".tmp"):
                    continue
                path = os.path.join(root, filename)
                try:
                    stat = os.stat(path)
                    if stat.st_mtime > cutoff:
                        continue
                    os.remove(path)
                    files_removed += 1
                    bytes_removed += int(stat.st_size)
                except FileNotFoundError:
                    continue
                except OSError:
                    continue
    if files_removed:
        _invalidate_disk_stats_cache()
    return {"files_removed": files_removed, "bytes_removed": bytes_removed}


def cleanup_stale_cache_temps(max_age_seconds: float = 30 * 60) -> dict:
    return _cleanup_stale_cache_temps(max_age_seconds=max_age_seconds)


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


def _normalize_ratios(values: dict[str, float], tiers: tuple[str, ...]) -> dict[str, float]:
    cleaned = {tier: max(0.0, float(values.get(tier, 0.0) or 0.0)) for tier in tiers}
    total = sum(cleaned.values())
    if total <= 0:
        return {tier: 0.0 for tier in tiers}
    return {tier: cleaned[tier] / total for tier in tiers}


def _active_memory_ratios() -> dict[str, float]:
    return _normalize_ratios(
        MEMORY_CACHE_PROFILES.get(CACHE_PROFILE, MEMORY_CACHE_PROFILES["original_heavy"]),
        THUMB_TIERS,
    )


def _allocate_by_ratios(total_bytes: int, ratios: dict[str, float], tiers: tuple[str, ...]) -> dict[str, int]:
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


def _quality_size_factor() -> float:
    quality = max(40, min(100, int(THUMB_QUALITY)))
    if quality >= 92:
        return 1.0 + (quality - 92) * 0.08
    if quality >= 80:
        return 0.45 + ((quality - 80) / 12.0) * 0.55
    if quality >= 60:
        return 0.28 + ((quality - 60) / 20.0) * 0.17
    return 0.18 + ((quality - 40) / 20.0) * 0.10


def estimated_tier_bytes(size: str) -> int:
    base = {
        "sm": 33 * 1024,
        "md": 450 * 1024,
        "lg": 1750 * 1024,
        FULL_TIER: 20 * 1024 * 1024,
    }
    if size == FULL_TIER:
        return base[FULL_TIER]
    return max(1, int(base.get(size, base["md"]) * _quality_size_factor()))


def _cache_archive_estimates() -> dict:
    fallbacks = {tier: estimated_tier_bytes(tier) for tier in ALL_TIERS}
    estimates = {
        "active_images": 0,
        "total_images": 0,
        "avg_bytes": dict(fallbacks),
        "sample_count": {tier: 0 for tier in ALL_TIERS},
        "needed_bytes": {tier: 0 for tier in ALL_TIERS},
    }
    try:
        with _meta_lock:
            conn = _db_connect()
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
                    (SSD_CACHE_DIR, FULL_TIER, _thumb_config_changed_at),
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
    for tier in THUMB_TIERS:
        estimates["needed_bytes"][tier] = estimates["avg_bytes"][tier] * preview_target_images
    estimates["needed_bytes"][FULL_TIER] = estimates["avg_bytes"][FULL_TIER] * total_images
    return estimates


def cache_archive_estimates() -> dict:
    return _cache_archive_estimates()


def _allocate_weighted_capped(total_bytes: int, weights: dict[str, float], caps: dict[str, int]) -> dict[str, int]:
    tiers = tuple(caps.keys())
    ratios = _normalize_ratios(weights, tiers)
    allocations = {tier: 0 for tier in tiers}
    remaining = max(0, int(total_bytes))
    if remaining <= 0:
        return allocations

    rough = _allocate_by_ratios(remaining, ratios, tiers)
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


def _allocate_disk_budget(total_bytes: int) -> dict[str, int]:
    total = max(0, int(total_bytes))
    allocations = {tier: 0 for tier in ALL_TIERS}
    if total <= 0:
        return allocations

    estimates = _cache_archive_estimates()
    needed = estimates["needed_bytes"]
    remaining = total

    for tier in ("sm", "md"):
        amount = min(remaining, int(needed.get(tier, 0) or 0))
        allocations[tier] = amount
        remaining -= amount
        if remaining <= 0:
            return allocations

    remainder_weights = SSD_REMAINDER_PROFILES.get(
        CACHE_PROFILE,
        SSD_REMAINDER_PROFILES["original_heavy"],
    )
    remainder = _allocate_weighted_capped(
        remaining,
        remainder_weights,
        {
            "lg": int(needed.get("lg", 0) or 0),
            FULL_TIER: int(needed.get(FULL_TIER, 0) or 0),
        },
    )
    allocations["lg"] = remainder["lg"]
    allocations[FULL_TIER] = remainder[FULL_TIER]

    leftover = remaining - allocations["lg"] - allocations[FULL_TIER]
    if leftover > 0:
        allocations["md"] += leftover
    return allocations


def _background_tier_budget(size: str, archive_estimates: dict | None = None) -> int:
    budget = int(_disk_allocations.get(size, 0) or 0)
    if size != "lg" or budget <= 0:
        return budget

    estimates = archive_estimates or _cache_archive_estimates()
    needed = int(estimates["needed_bytes"].get("lg", 0) or 0)
    if needed <= 0 or budget >= int(needed * 0.95):
        return budget

    reserve = min(
        budget,
        max(
            HOT_LG_RESERVE_MIN_BYTES,
            min(HOT_LG_RESERVE_MAX_BYTES, int(budget * HOT_LG_RESERVE_FRACTION)),
        ),
    )
    return max(0, budget - reserve)


def cache_budget_config() -> dict:
    memory_allocations = _allocate_by_ratios(MEMORY_CACHE_BYTES, _active_memory_ratios(), THUMB_TIERS)
    ssd_ratios = _normalize_ratios(_disk_allocations, ALL_TIERS)
    return {
        "profile": CACHE_PROFILE,
        "ssd_ratios": ssd_ratios,
        "memory_ratios": _active_memory_ratios(),
        "ssd_allocations": dict(_disk_allocations),
        "memory_allocations": memory_allocations,
    }


_SOURCE_STAT_CACHE_MAX = 25000
_SOURCE_STAT_CACHE_TTL_SECONDS = 60.0
_source_stat_cache: OrderedDict[str, tuple[float, str]] = OrderedDict()

def _get_source_bits(filepath: str) -> str:
    """Cache os.stat results per filepath to avoid repeated HDD stat calls."""
    now = time.monotonic()
    cached = _source_stat_cache.get(filepath)
    if cached is not None and now - cached[0] <= _SOURCE_STAT_CACHE_TTL_SECONDS:
        _source_stat_cache.move_to_end(filepath)
        return cached[1]
    try:
        stat = os.stat(filepath)
        bits = f"{stat.st_size}|{stat.st_mtime_ns}|{filepath}"
    except OSError:
        bits = f"missing|{filepath}"
    _source_stat_cache[filepath] = (now, bits)
    if len(_source_stat_cache) > _SOURCE_STAT_CACHE_MAX:
        _source_stat_cache.popitem(last=False)
    return bits


def _source_bits_from_catalog_metadata(
    filepath: str,
    file_size,
    file_modified_at,
) -> tuple[str, int | None, bool]:
    """Build source identity from scan-time metadata, falling back to stat only if needed."""
    if file_size is not None and file_modified_at is not None:
        try:
            source_size = int(file_size)
            source_mtime = float(file_modified_at)
            return f"catalog|{source_size}|{source_mtime:.9f}|{filepath}", source_size, False
        except (TypeError, ValueError):
            pass

    bits = _get_source_bits(filepath)
    if bits.startswith("missing|"):
        return bits, None, True
    try:
        source_size = int(bits.split("|", 1)[0])
    except (TypeError, ValueError):
        source_size = None
    return bits, source_size, False


def _build_source_signature_from_bits(source_bits: str, size: str, image_id: int) -> str:
    if size == FULL_TIER:
        signature = f"{CACHE_VERSION}|full|{image_id}|{source_bits}"
    else:
        signature = (
            f"{CACHE_VERSION}|thumb|{size}|{image_id}|{SIZES[size]}|"
            f"{THUMB_QUALITY}|{source_bits}"
        )
    return hashlib.sha1(signature.encode("utf-8", "surrogateescape")).hexdigest()


def _build_source_signature(filepath: str, size: str, image_id: int) -> str:
    return _build_source_signature_from_bits(_get_source_bits(filepath), size, image_id)


def _build_catalog_source_signature(
    filepath: str,
    size: str,
    image_id: int,
    file_size,
    file_modified_at,
) -> tuple[str, int | None, bool]:
    source_bits, source_size, source_missing = _source_bits_from_catalog_metadata(
        filepath,
        file_size,
        file_modified_at,
    )
    return _build_source_signature_from_bits(source_bits, size, image_id), source_size, source_missing


def _source_missing(filepath: str) -> bool:
    return _get_source_bits(filepath).startswith("missing|")


def _source_missing_error(filepath: str, exc: Exception) -> bool:
    if isinstance(exc, FileNotFoundError):
        return True
    try:
        return not os.path.exists(filepath)
    except OSError:
        return False


def _mark_source_missing_from_error(filepath: str, image_id: int, exc: Exception) -> bool:
    if not _source_missing_error(filepath, exc):
        return False
    try:
        db.mark_image_missing_sync(image_id)
    except Exception as mark_error:
        print(f"Failed to mark missing image {image_id} for {filepath}: {mark_error}")
        return False
    return True


def get_etag(filepath: str, size: str, image_id: int) -> str:
    return f"\"{_build_source_signature(filepath, size, image_id)}\""


def response_headers(filepath: str, size: str, image_id: int) -> dict[str, str]:
    return {
        "Cache-Control": (
            f"public, max-age={BROWSER_CACHE_MAX_AGE}, "
            f"stale-while-revalidate={BROWSER_CACHE_STALE_WHILE_REVALIDATE}"
        ),
        "ETag": get_etag(filepath, size, image_id),
    }


def _thumbnail_disk_path(size: str, image_id: int) -> str:
    return os.path.join(SSD_CACHE_DIR, size, f"{image_id}.jpg")


def _full_disk_path(image_id: int, filepath: str) -> str:
    ext = os.path.splitext(filepath)[1].lower() or ".bin"
    return os.path.join(SSD_CACHE_DIR, FULL_TIER, f"{image_id}{ext}")


def is_browser_displayable_original(filepath: str) -> bool:
    return os.path.splitext(filepath or "")[1].lower() in BROWSER_ORIGINAL_EXTENSIONS


def _replace_executor(
    current: ThreadPoolExecutor,
    workers: int,
    prefix: str,
) -> ThreadPoolExecutor:
    replacement = ThreadPoolExecutor(max_workers=workers, thread_name_prefix=prefix)
    try:
        current.shutdown(wait=False, cancel_futures=False)
    except TypeError:
        current.shutdown(wait=False)
    return replacement


def _memory_get_fast(size: str, image_id: int) -> bytes | None:
    """Fast memory check — no signature validation."""
    entry = _memory_get_entry_fast(size, image_id)
    return entry[1] if entry is not None else None


def _memory_get_entry_fast(size: str, image_id: int) -> tuple[str, bytes] | None:
    """Fast memory check that also returns the cached signature."""
    key = (size, image_id)
    with _cache_lock:
        entry = _memory_cache.get(key)
        if entry is None:
            return None
        signature, data = entry
        _memory_cache.move_to_end(key)
        return signature, data


def _memory_get(size: str, image_id: int, source_signature: str) -> bytes | None:
    key = (size, image_id)

    with _cache_lock:
        entry = _memory_cache.get(key)
        if entry is None:
            return None
        cached_signature, data = entry
        if cached_signature != source_signature:
            _memory_remove_locked(key)
            return None
        _memory_cache.move_to_end(key)
        return data


def _memory_tier_budget(size: str) -> int:
    if MEMORY_CACHE_BYTES <= 0:
        return 0
    ratios = _active_memory_ratios()
    return int(MEMORY_CACHE_BYTES * ratios.get(size, 0.0))


def _memory_remove_locked(key: tuple[str, int]) -> bool:
    global _memory_cache_bytes
    entry = _memory_cache.pop(key, None)
    if entry is None:
        return False
    size = key[0]
    data_len = len(entry[1])
    _memory_cache_bytes -= data_len
    _memory_tier_bytes[size] = max(0, _memory_tier_bytes.get(size, 0) - data_len)
    return True


def _evict_memory_oldest_locked(size: str | None = None) -> bool:
    for key in list(_memory_cache.keys()):
        if size is None or key[0] == size:
            return _memory_remove_locked(key)
    return False


def _enforce_memory_budget_locked():
    for size in THUMB_TIERS:
        budget = _memory_tier_budget(size)
        while _memory_tier_bytes.get(size, 0) > budget:
            if not _evict_memory_oldest_locked(size):
                break

    while _memory_cache and _memory_cache_bytes > MEMORY_CACHE_BYTES:
        if not _evict_memory_oldest_locked():
            break


def _memory_put(size: str, image_id: int, source_signature: str, data: bytes):
    if not data or MEMORY_CACHE_BYTES <= 0:
        return
    tier_budget = _memory_tier_budget(size)
    if tier_budget <= 0 or len(data) > tier_budget:
        return

    key = (size, image_id)
    global _memory_cache_bytes

    with _cache_lock:
        _memory_remove_locked(key)

        _memory_cache[key] = (source_signature, data)
        _memory_cache.move_to_end(key)
        data_len = len(data)
        _memory_cache_bytes += data_len
        _memory_tier_bytes[size] = _memory_tier_bytes.get(size, 0) + data_len
        _enforce_memory_budget_locked()


def _clear_memory_cache() -> dict:
    global _memory_cache_bytes
    with _cache_lock:
        counts = {size: 0 for size in THUMB_TIERS}
        for size, _image_id in _memory_cache.keys():
            counts[size] = counts.get(size, 0) + 1
        entries_cleared = len(_memory_cache)
        bytes_cleared = _memory_cache_bytes
        _memory_cache.clear()
        _memory_cache_bytes = 0
        for size in THUMB_TIERS:
            _memory_tier_bytes[size] = 0
    return {
        "entries_cleared": entries_cleared,
        "bytes_cleared": bytes_cleared,
        "counts": counts,
    }


def _clear_memory_tiers(tiers: tuple[str, ...]):
    with _cache_lock:
        for key in list(_memory_cache.keys()):
            if key[0] not in tiers:
                continue
            _memory_remove_locked(key)


def _clear_memory_image_ids(image_ids: set[int]):
    if not image_ids:
        return
    with _cache_lock:
        for key in list(_memory_cache.keys()):
            if key[1] in image_ids:
                _memory_remove_locked(key)


def _memory_stats() -> dict:
    with _cache_lock:
        tiers = {size: {"count": 0, "bytes": 0} for size in THUMB_TIERS}
        for (size, _image_id), (_signature, data) in _memory_cache.items():
            tiers[size]["count"] += 1
            tiers[size]["bytes"] += len(data)
        for size in THUMB_TIERS:
            tiers[size]["budget_bytes"] = _memory_tier_budget(size)
        return {
            "limit_bytes": MEMORY_CACHE_BYTES,
            "used_bytes": _memory_cache_bytes,
            "tiers": tiers,
        }


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


_tier_byte_totals: dict[str, int] = {}  # running totals, populated lazily
_disk_stats_cache = {"data": None, "expires": 0.0, "stale_until": 0.0}
_disk_stats_cache_ttl_seconds = 5.0
_disk_stats_cache_max_stale_seconds = 5.0


def _invalidate_disk_stats_cache(*, soft: bool = False):
    if soft and _disk_stats_cache["data"] is not None:
        now = _current_time()
        if now < float(_disk_stats_cache.get("stale_until") or 0.0):
            _disk_stats_cache["expires"] = max(
                float(_disk_stats_cache.get("expires") or 0.0),
                now + 1.0,
            )
            return
    _disk_stats_cache["expires"] = 0.0


def _tier_bytes(conn: sqlite3.Connection, size: str) -> int:
    """Get running total for a tier, initializing from DB if needed."""
    total = _tier_byte_totals.get(size)
    if total is None:
        row = conn.execute(
            "SELECT COALESCE(SUM(size_bytes), 0) AS t FROM cache_entries "
            "WHERE cache_root = ? AND size = ?",
            (SSD_CACHE_DIR, size),
        ).fetchone()
        total = int(row["t"])
        _tier_byte_totals[size] = total
    return total


def _enforce_tier_budget_locked(conn: sqlite3.Connection, size: str) -> list[int]:
    budget = _disk_allocations.get(size, 0)
    total = _tier_bytes(conn, size)
    removed_ids: list[int] = []

    if budget <= 0:
        rows = conn.execute(
            "SELECT cache_root, size, image_id, path, size_bytes FROM cache_entries "
            "WHERE cache_root = ? AND size = ?",
            (SSD_CACHE_DIR, size),
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

    # Evict cold background entries before recently viewed/warmed images.
    # Elo is a secondary tie-breaker once access recency has separated hot
    # session cache from overnight cache building.
    evict_rows = conn.execute(
        "SELECT c.cache_root, c.size, c.image_id, c.path, c.size_bytes "
        "FROM cache_entries c "
        "LEFT JOIN images i ON c.image_id = i.id "
        "WHERE c.cache_root = ? AND c.size = ? "
        "ORDER BY c.last_accessed ASC, COALESCE(i.elo, 1200) ASC",
        (SSD_CACHE_DIR, size),
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
    _tier_byte_totals.clear()  # force re-read from DB
    with _meta_lock:
        conn = _db_connect()
        try:
            for size in ALL_TIERS:
                _enforce_tier_budget_locked(conn, size)
        finally:
            conn.close()


def _get_disk_entry(
    size: str,
    image_id: int,
    source_signature: str,
    touch: bool = True,
) -> sqlite3.Row | None:
    if not SSD_CACHE_DIR or _disk_allocations.get(size, 0) <= 0:
        return None

    with _meta_lock:
        conn = None
        try:
            conn = _db_connect()
            row = conn.execute(
                "SELECT cache_root, size, image_id, path, source_signature, size_bytes "
                "FROM cache_entries WHERE cache_root = ? AND size = ? AND image_id = ?",
                (SSD_CACHE_DIR, size, image_id),
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
                        (SSD_CACHE_DIR, size, image_id),
                    ).fetchone()
                    if stale_row is not None:
                        _remove_cache_entry_locked(conn, stale_row)
                        conn.commit()
                except sqlite3.OperationalError as exc:
                    try:
                        conn.rollback()
                    except Exception:
                        pass
                    if _is_sqlite_locked(exc):
                        _note_cache_metadata_lock()
                    else:
                        raise
                return None
            if touch:
                try:
                    conn.execute(
                        "UPDATE cache_entries SET last_accessed = ? "
                        "WHERE cache_root = ? AND size = ? AND image_id = ?",
                        (_current_time(), SSD_CACHE_DIR, size, image_id),
                    )
                    conn.commit()
                except sqlite3.OperationalError as exc:
                    try:
                        conn.rollback()
                    except Exception:
                        pass
                    if not _is_sqlite_locked(exc):
                        raise
                    _note_cache_metadata_lock()
            _index_disk_entry(size, image_id, row["path"], row["source_signature"])
            return row
        except sqlite3.OperationalError as exc:
            if _is_sqlite_locked(exc):
                _note_cache_metadata_lock()
                return None
            raise
        finally:
            close = getattr(conn, "close", None) if conn is not None else None
            if close is not None:
                close()


def touch_cached(size: str, filepath: str, image_id: int) -> bool:
    if size not in ALL_TIERS:
        return False
    source_signature = _build_source_signature(filepath, size, image_id)
    return touch_cached_signature(size, image_id, source_signature)


def touch_cached_signature(size: str, image_id: int, source_signature: str | None = None) -> bool:
    if not SSD_CACHE_DIR or _disk_allocations.get(size, 0) <= 0:
        return False
    with _meta_lock:
        conn = None
        try:
            conn = _db_connect()
            if source_signature:
                cursor = conn.execute(
                    "UPDATE cache_entries SET last_accessed = ? "
                    "WHERE cache_root = ? AND size = ? AND image_id = ? AND source_signature = ?",
                    (_current_time(), SSD_CACHE_DIR, size, image_id, source_signature),
                )
            else:
                cursor = conn.execute(
                    "UPDATE cache_entries SET last_accessed = ? "
                    "WHERE cache_root = ? AND size = ? AND image_id = ?",
                    (_current_time(), SSD_CACHE_DIR, size, image_id),
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
            if _is_sqlite_locked(exc):
                _note_cache_metadata_lock()
                return False
            raise
        finally:
            close = getattr(conn, "close", None) if conn is not None else None
            if close is not None:
                close()


# In-memory index: (size, image_id) -> (disk path, source signature).
# Built on startup and updated on writes/evictions so hot thumbnail requests
# can skip SQLite without believing stale evicted files still exist.
_disk_path_index: dict[tuple[str, int], tuple[str, str]] = {}
_disk_index_lock = threading.Lock()
_disk_index_built = False


def _build_disk_path_index() -> bool:
    """Load all cache entry paths into memory for fast lookup."""
    global _disk_index_built
    if not SSD_CACHE_DIR:
        _clear_disk_index()
        _disk_index_built = True
        return True
    if _cache_metadata_backoff_active():
        return False
    try:
        with _meta_lock:
            conn = _db_connect()
            try:
                rows = conn.execute(
                    "SELECT size, image_id, path, source_signature FROM cache_entries WHERE cache_root = ?",
                    (SSD_CACHE_DIR,),
                ).fetchall()
            finally:
                conn.close()
    except sqlite3.OperationalError as exc:
        if _is_sqlite_locked(exc):
            _note_cache_metadata_lock()
            return False
        raise
    new_index = {}
    for row in rows:
        new_index[(row["size"], row["image_id"])] = (row["path"], row["source_signature"])
    with _disk_index_lock:
        _disk_path_index.clear()
        _disk_path_index.update(new_index)
    _disk_index_built = True
    _clear_cache_metadata_lock_backoff()
    return True


def _index_disk_entry(size: str, image_id: int, path: str, source_signature: str):
    """Update the in-memory index when a new cache entry is written."""
    with _disk_index_lock:
        _disk_path_index[(size, image_id)] = (path, source_signature)


def _unindex_disk_entry(size: str, image_id: int):
    with _disk_index_lock:
        _disk_path_index.pop((size, image_id), None)


def _clear_disk_index(tiers: tuple[str, ...] | None = None):
    global _disk_index_built
    with _disk_index_lock:
        if tiers is None:
            _disk_path_index.clear()
            _disk_index_built = False
            return
        for key in list(_disk_path_index.keys()):
            if key[0] in tiers:
                _disk_path_index.pop(key, None)


def fast_disk_has(size: str, image_id: int, source_signature: str | None = None) -> bool:
    if not _disk_index_built:
        if not _build_disk_path_index():
            return False
    with _disk_index_lock:
        entry = _disk_path_index.get((size, image_id))
    if entry is None:
        return False
    path, cached_signature = entry
    if source_signature is not None and cached_signature != source_signature:
        return False
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
    with _disk_index_lock:
        entry = _disk_path_index.get((size, image_id))
    if entry is None:
        return None
    path, cached_signature = entry
    if source_signature is not None and cached_signature != source_signature:
        return None
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
    with _disk_index_lock:
        entry = _disk_path_index.get((size, image_id))
    if entry is None:
        return None
    path, cached_signature = entry
    if source_signature is not None and cached_signature != source_signature:
        return None
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        _unindex_disk_entry(size, image_id)
        return None
    if populate_memory and size in THUMB_TIERS:
        _memory_put(size, image_id, cached_signature, data)
    return cached_signature, data


def fast_disk_read(size: str, image_id: int) -> bytes | None:
    """Fast path: read thumbnail from SSD via in-memory index. No SQLite, no locks, no HDD stat."""
    entry = fast_disk_read_entry(size, image_id)
    return entry[1] if entry is not None else None


def _read_disk_thumbnail(size: str, image_id: int, source_signature: str) -> bytes | None:
    row = _get_disk_entry(size, image_id, source_signature)
    if row is None:
        return None

    try:
        with open(row["path"], "rb") as f:
            data = f.read()
    except OSError:
        with _meta_lock:
            conn = None
            try:
                conn = _db_connect()
                stale_row = conn.execute(
                    "SELECT cache_root, size, image_id, path FROM cache_entries "
                    "WHERE cache_root = ? AND size = ? AND image_id = ?",
                    (SSD_CACHE_DIR, size, image_id),
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
                if _is_sqlite_locked(exc):
                    _note_cache_metadata_lock()
                else:
                    raise
            finally:
                if conn is not None:
                    conn.close()
        return None

    _memory_put(size, image_id, source_signature, data)
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
    now = _current_time()
    access_time = _cache_access_time(hot=hot)
    with _meta_lock:
        conn = _db_connect()
        try:
            previous = conn.execute(
                "SELECT cache_root, size, image_id, path, size_bytes FROM cache_entries "
                "WHERE cache_root = ? AND size = ? AND image_id = ?",
                (SSD_CACHE_DIR, size, image_id),
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
                    SSD_CACHE_DIR,
                    size,
                    image_id,
                    path,
                    source_signature,
                    int(size_bytes),
                    access_time,
                    now,
                ),
            )
            # Update running total: add new, subtract old (if replacing)
            if size in _tier_byte_totals:
                _tier_byte_totals[size] += int(size_bytes) - old_bytes
            removed_cache_ids.extend(_enforce_tier_budget_locked(conn, size))
            conn.commit()
            _invalidate_disk_stats_cache(soft=True)
        finally:
            conn.close()
        _index_disk_entry(size, image_id, path, source_signature)
        if removed_cache_ids:
            db.invalidate_cached_image_ids_cache(cache_root=SSD_CACHE_DIR, size=size)
        else:
            db.note_cached_image_ids_added(SSD_CACHE_DIR, size, [image_id])


def _write_thumbnail_to_disk(size: str, image_id: int, source_signature: str, data: bytes, *, hot: bool) -> bool:
    budget = _disk_allocations.get(size, 0)
    if not SSD_CACHE_DIR or budget <= 0 or len(data) > budget:
        return False

    path = _thumbnail_disk_path(size, image_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temp_path = f"{path}.{threading.get_ident()}.tmp"
    with open(temp_path, "wb") as f:
        f.write(data)
    os.replace(temp_path, path)
    # Update in-memory index immediately so has_cached sees it.
    _index_disk_entry(size, image_id, path, source_signature)
    # Queue DB write for bulk flush instead of acquiring _meta_lock per thumbnail.
    with _write_queue_lock:
        _write_queue.append((size, image_id, source_signature, path, len(data), _cache_access_time(hot=hot)))
    _maybe_flush_write_queue()
    return True


_WRITE_FLUSH_SIZE = 96  # large enough for batching, small enough to avoid long foreground waits


def _flush_write_queue() -> bool:
    """Flush pending cache DB writes in a single transaction."""
    if _cache_metadata_backoff_active():
        return False
    with _write_queue_lock:
        if not _write_queue:
            return True
        # Keep only the newest write per cache key. Pregeneration can queue the
        # same image/tier more than once while older work is still flushing.
        latest = {}
        for entry in _write_queue:
            latest[(entry[0], entry[1])] = entry
        batch = list(latest.values())
        _write_queue.clear()

    now = _current_time()
    with _meta_lock:
        conn = None
        try:
            conn = _db_connect()
            now = _current_time()
            removed_by_size: dict[str, list[int]] = {}
            added_by_size: dict[str, list[int]] = {}
            for size, image_id, source_signature, path, size_bytes, access_time in batch:
                previous = conn.execute(
                    "SELECT cache_root, size, image_id, path, size_bytes FROM cache_entries "
                    "WHERE cache_root = ? AND size = ? AND image_id = ?",
                    (SSD_CACHE_DIR, size, image_id),
                ).fetchone()
                old_bytes = int(previous["size_bytes"]) if previous is not None else 0
                if previous is not None and previous["path"] != path:
                    removed_by_size.setdefault(size, []).append(int(previous["image_id"]))
                    _remove_cache_entry_locked(conn, previous)

                conn.execute(
                    "INSERT OR REPLACE INTO cache_entries "
                    "(cache_root, size, image_id, path, source_signature, size_bytes, last_accessed, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (SSD_CACHE_DIR, size, image_id, path, source_signature, int(size_bytes), access_time, now),
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
                    db.invalidate_cached_image_ids_cache(cache_root=SSD_CACHE_DIR, size=size)
                else:
                    db.note_cached_image_ids_added(
                        SSD_CACHE_DIR,
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
            if _is_sqlite_locked(exc):
                _note_cache_metadata_lock()
                return False
            raise
        finally:
            close = getattr(conn, "close", None) if conn is not None else None
            if close is not None:
                close()


def _maybe_flush_write_queue():
    """Flush if enough writes have accumulated — called from worker threads."""
    with _write_queue_lock:
        should_flush = len(_write_queue) >= _WRITE_FLUSH_SIZE
    if should_flush:
        _flush_write_queue()


def _full_cache_has_room(image_id: int, source_size: int, budget: int) -> bool:
    if budget <= 0 or source_size > budget:
        return False
    if _cache_metadata_backoff_active():
        return False
    with _meta_lock:
        conn = None
        try:
            conn = _db_connect()
            total = _tier_bytes(conn, FULL_TIER)
            previous = conn.execute(
                "SELECT size_bytes FROM cache_entries "
                "WHERE cache_root = ? AND size = ? AND image_id = ?",
                (SSD_CACHE_DIR, FULL_TIER, image_id),
            ).fetchone()
            previous_bytes = int(previous["size_bytes"]) if previous is not None else 0
            _clear_cache_metadata_lock_backoff()
            return max(0, total - previous_bytes) + source_size <= budget
        except sqlite3.OperationalError as exc:
            if _is_sqlite_locked(exc):
                _note_cache_metadata_lock()
                return False
            raise
        finally:
            if conn is not None:
                conn.close()


def _cache_full_image_sync(
    filepath: str,
    image_id: int,
    source_signature: str,
    hot: bool = True,
    *,
    room_prechecked: bool = False,
) -> str:
    if not os.path.exists(filepath):
        return filepath

    budget = _disk_allocations.get(FULL_TIER, 0)
    if not SSD_CACHE_DIR or budget <= 0:
        return filepath

    try:
        source_size = os.path.getsize(filepath)
    except OSError:
        return filepath

    if source_size > budget:
        return filepath

    row = _get_disk_entry(FULL_TIER, image_id, source_signature)
    if row is not None:
        return row["path"]

    if not hot and not room_prechecked and not _full_cache_has_room(image_id, source_size, budget):
        return filepath

    path = _full_disk_path(image_id, filepath)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temp_path = f"{path}.{threading.get_ident()}.tmp"
    shutil.copyfile(filepath, temp_path)
    if not hot and not room_prechecked and not _full_cache_has_room(image_id, source_size, budget):
        try:
            os.remove(temp_path)
        except OSError:
            pass
        return filepath
    os.replace(temp_path, path)
    _store_disk_entry(FULL_TIER, image_id, source_signature, path, source_size, hot=hot)
    return path


def _cache_full_image_bytes_sync(
    filepath: str,
    image_id: int,
    source_signature: str,
    data: bytes,
    *,
    hot: bool = True,
    room_prechecked: bool = False,
) -> str:
    if not data:
        return filepath

    budget = _disk_allocations.get(FULL_TIER, 0)
    source_size = len(data)
    if not SSD_CACHE_DIR or budget <= 0 or source_size > budget:
        return filepath

    row = _get_disk_entry(FULL_TIER, image_id, source_signature)
    if row is not None:
        return row["path"]

    if not hot and not room_prechecked and not _full_cache_has_room(image_id, source_size, budget):
        return filepath

    path = _full_disk_path(image_id, filepath)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temp_path = f"{path}.{threading.get_ident()}.tmp"
    with open(temp_path, "wb") as f:
        f.write(data)
    if not hot and not room_prechecked and not _full_cache_has_room(image_id, source_size, budget):
        try:
            os.remove(temp_path)
        except OSError:
            pass
        return filepath
    os.replace(temp_path, path)
    _store_disk_entry(FULL_TIER, image_id, source_signature, path, source_size, hot=hot)
    return path


def _load_raw_preview(filepath: str, max_target: int) -> Image.Image | None:
    import rawpy

    try:
        with rawpy.imread(filepath) as raw:
            thumb = raw.extract_thumb()
        if thumb.format == rawpy.ThumbFormat.JPEG:
            with Image.open(io.BytesIO(thumb.data)) as source:
                source.load()
                img = ImageOps.exif_transpose(source)
                if img is source:
                    img = source.copy()
        elif thumb.format == rawpy.ThumbFormat.BITMAP:
            img = Image.fromarray(thumb.data)
        else:
            return None

        if max(img.width, img.height) >= max_target:
            return img
        img.close()
    except Exception:
        return None
    return None


def _load_source_image(filepath: str, max_target: int, prefer_draft: bool) -> Image.Image:
    ext = os.path.splitext(filepath)[1].lower()
    if ext in RAW_EXTENSIONS:
        preview = _load_raw_preview(filepath, max_target)
        if preview is not None:
            return preview

        import rawpy

        with rawpy.imread(filepath) as raw:
            rgb = raw.postprocess(use_camera_wb=True, no_auto_bright=True)
        return Image.fromarray(rgb)

    with Image.open(filepath) as source:
        if ext in JPEG_EXTENSIONS:
            # Always use draft mode for JPEGs — decodes at reduced resolution
            # via libjpeg DCT scaling, cutting both I/O and decode time.
            source.draft("RGB", (max_target * 2, max_target * 2))
        source.load()
        img = ImageOps.exif_transpose(source)
        if img is source:
            img = source.copy()
        return img


def _load_source_image_from_bytes(
    filepath: str,
    data: bytes,
    max_target: int,
    prefer_draft: bool,
) -> Image.Image:
    ext = os.path.splitext(filepath)[1].lower()
    if ext in RAW_EXTENSIONS:
        return _load_source_image(filepath, max_target, prefer_draft)

    with Image.open(io.BytesIO(data)) as source:
        if ext in JPEG_EXTENSIONS:
            source.draft("RGB", (max_target * 2, max_target * 2))
        source.load()
        img = ImageOps.exif_transpose(source)
        if img is source:
            img = source.copy()
        return img


def _resize_to_long_side(img: Image.Image, target_long_side: int) -> Image.Image:
    long_side = max(img.width, img.height)
    if long_side <= target_long_side:
        return img.copy()

    # Fast integer pre-downscale with reduce() when source is much larger,
    # then final filter for quality.
    factor = max(1, long_side // (target_long_side * 2))
    if factor > 1:
        img = img.reduce(factor)
        long_side = max(img.width, img.height)

    scale = target_long_side / long_side
    new_size = (
        max(1, int(round(img.width * scale))),
        max(1, int(round(img.height * scale))),
    )
    # Use BILINEAR for large targets (≥1920px) where the downscale ratio is small
    # and quality difference is imperceptible. LANCZOS for smaller sizes.
    resample = Image.BILINEAR if target_long_side >= 1920 else Image.LANCZOS
    return img.resize(new_size, resample)


def _queue_orientation(image_id: int, img: Image.Image):
    orientation = "landscape" if img.width >= img.height else "portrait"
    aspect_ratio = round(img.width / img.height, 4) if img.height > 0 else 1.5
    with _orientation_lock:
        _orientation_queue[image_id] = (orientation, aspect_ratio)


def _thumbnail_jpeg_bytes(variant: Image.Image, size: str) -> bytes:
    buf = io.BytesIO()
    variant.save(
        buf,
        "JPEG",
        quality=THUMB_QUALITY,
        progressive=(size != "sm"),
    )
    return buf.getvalue()


def _encode_and_cache_thumbnail(
    size: str,
    image_id: int,
    source_signature: str,
    variant: Image.Image,
    *,
    hot: bool,
) -> tuple[Image.Image, bytes, bool]:
    if variant.mode != "RGB":
        converted = variant.convert("RGB")
        variant.close()
        variant = converted

    data = _thumbnail_jpeg_bytes(variant, size)
    _memory_put(size, image_id, source_signature, data)
    written = _write_thumbnail_to_disk(size, image_id, source_signature, data, hot=hot)
    _thumbnail_retry_after.pop((size, image_id, source_signature), None)
    return variant, data, written


def _planned_thumbnail_sizes(
    filepath: str,
    image_id: int,
    requested_size: str,
    *,
    include_smaller_tiers: bool = False,
    allow_stale_fallback: bool = True,
) -> list[str]:
    if _source_missing(filepath):
        return []

    needed = []
    now = time.time()
    if include_smaller_tiers:
        requested_long_side = SIZES.get(requested_size, 0)
        candidate_sizes = tuple(
            size for size in THUMB_TIERS
            if SIZES[size] <= requested_long_side and (size == requested_size or _disk_allocations.get(size, 0) > 0)
        )
    else:
        candidate_sizes = (requested_size,)
    for size in candidate_sizes:
        if size not in THUMB_TIERS:
            continue
        source_signature = _build_source_signature(filepath, size, image_id)
        if _thumbnail_retry_after.get((size, image_id, source_signature), 0) > now:
            continue
        if _memory_get(size, image_id, source_signature) is not None:
            continue
        # Fast check via in-memory index before expensive DB query.
        if fast_disk_has(size, image_id, source_signature):
            continue
        if allow_stale_fallback and fast_disk_has(size, image_id):
            continue
        if _get_disk_entry(size, image_id, source_signature, touch=False) is not None:
            continue
        needed.append(size)
    if include_smaller_tiers:
        return sorted(needed, key=lambda tier: SIZES[tier], reverse=True)
    return needed


def _generate_missing_thumbnails_sync(
    filepath: str,
    requested_size: str,
    image_id: int,
    *,
    include_smaller_tiers: bool = False,
    hot: bool = False,
    allow_stale_fallback: bool = True,
):
    needed_sizes = _planned_thumbnail_sizes(
        filepath,
        image_id,
        requested_size,
        include_smaller_tiers=include_smaller_tiers,
        allow_stale_fallback=allow_stale_fallback,
    )
    if not needed_sizes:
        return None

    img = None
    current = None
    requested_data = None

    try:
        max_target = max(SIZES[size] for size in needed_sizes)
        prefer_draft = max_target <= SIZES["sm"]
        img = _load_source_image(filepath, max_target, prefer_draft=prefer_draft)
        _queue_orientation(image_id, img)

        current = img
        for size in needed_sizes:
            variant = _resize_to_long_side(current, SIZES[size])
            source_signature = _build_source_signature(filepath, size, image_id)
            variant, data, _written = _encode_and_cache_thumbnail(
                size,
                image_id,
                source_signature,
                variant,
                hot=hot,
            )
            if size == requested_size:
                requested_data = data

            if current is not img:
                current.close()
            current = variant
    except Exception as e:
        source_missing = _mark_source_missing_from_error(filepath, image_id, e)
        if not source_missing:
            retry_until = time.time() + THUMBNAIL_RETRY_SECONDS
            for size in needed_sizes:
                source_signature = _build_source_signature(filepath, size, image_id)
                _thumbnail_retry_after[(size, image_id, source_signature)] = retry_until
            print(f"Thumbnail error for {filepath}: {e}")
        return None
    finally:
        if current is not None and current is not img:
            try:
                current.close()
            except Exception:
                pass
        if img is not None:
            try:
                img.close()
            except Exception:
                pass
    return requested_data


def _generate_thumbnail_set_sync(
    filepath: str,
    image_id: int,
    size_signatures: dict[str, str],
    *,
    source_bytes: int | None = None,
    full_item: dict | None = None,
    hot: bool = False,
) -> dict:
    """Generate cache tiers for one image during a single warm-up pass."""
    needed_sizes = [
        size for size in sorted(size_signatures, key=lambda tier: SIZES[tier], reverse=True)
        if size in THUMB_TIERS
    ]
    metrics = {
        "source_reads": 0,
        "thumbnails_written": 0,
        "source_bytes": 0,
        "read_seconds": 0.0,
        "decode_encode_seconds": 0.0,
        "source_read_failures": 0,
        "originals_written": 0,
    }
    if not needed_sizes and not full_item:
        return metrics

    img = None
    current = None
    source_data = None
    try:
        if needed_sizes:
            max_target = max(SIZES[size] for size in needed_sizes)
            prefer_draft = max_target <= SIZES["sm"]
            read_started = time.monotonic()
            if (
                full_item
                and full_item.get("filepath") == filepath
                and is_browser_displayable_original(filepath)
            ):
                with open(filepath, "rb") as f:
                    source_data = f.read()
                img = _load_source_image_from_bytes(
                    filepath,
                    source_data,
                    max_target,
                    prefer_draft=prefer_draft,
                )
                metrics["source_bytes"] = len(source_data)
            else:
                img = _load_source_image(filepath, max_target, prefer_draft=prefer_draft)
                metrics["source_bytes"] = int(source_bytes or 0)
            metrics["read_seconds"] = max(0.0, time.monotonic() - read_started)
            metrics["source_reads"] = 1
            _queue_orientation(image_id, img)

            process_started = time.monotonic()
            current = img
            for size in needed_sizes:
                source_signature = size_signatures[size]
                if fast_disk_has(size, image_id, source_signature):
                    continue

                variant = _resize_to_long_side(current, SIZES[size])
                variant, _data, written = _encode_and_cache_thumbnail(
                    size,
                    image_id,
                    source_signature,
                    variant,
                    hot=hot,
                )
                if written:
                    metrics["thumbnails_written"] += 1

                if current is not img:
                    current.close()
                current = variant
            metrics["decode_encode_seconds"] = max(0.0, time.monotonic() - process_started)

        if full_item:
            full_id = int(full_item["id"])
            if source_data is not None:
                result = _cache_full_image_bytes_sync(
                    full_item["filepath"],
                    full_id,
                    full_item["signature"],
                    source_data,
                    hot=False,
                    room_prechecked=True,
                )
            else:
                full_started = time.monotonic()
                result = _cache_full_image_sync(
                    full_item["filepath"],
                    full_id,
                    full_item["signature"],
                    hot=False,
                    room_prechecked=True,
                )
                full_seconds = max(0.0, time.monotonic() - full_started)
                if result != full_item["filepath"]:
                    metrics["read_seconds"] += full_seconds
                    metrics["source_bytes"] += int(full_item.get("source_size") or 0)
                    if metrics["source_reads"] <= 0:
                        metrics["source_reads"] = 1
            if result != full_item["filepath"] and fast_disk_has(
                FULL_TIER,
                full_id,
                full_item["signature"],
            ):
                metrics["originals_written"] = 1
    except Exception as e:
        source_missing = _mark_source_missing_from_error(filepath, image_id, e)
        if not source_missing:
            retry_until = time.time() + THUMBNAIL_RETRY_SECONDS
            for size in needed_sizes:
                source_signature = size_signatures[size]
                _thumbnail_retry_after[(size, image_id, source_signature)] = retry_until
            print(f"Thumbnail bulk error for {filepath}: {e}")
        metrics["source_read_failures"] = 1
    finally:
        if current is not None and current is not img:
            try:
                current.close()
            except Exception:
                pass
        if img is not None:
            try:
                img.close()
            except Exception:
                pass
    return metrics


def has_cached(size: str, filepath: str, image_id: int) -> bool:
    source_signature = _build_source_signature(filepath, size, image_id)
    if size in THUMB_TIERS and _memory_get(size, image_id, source_signature) is not None:
        return True
    # Fast check via in-memory index — avoids SQLite while still rejecting
    # stale signatures and evicted files.
    if fast_disk_has(size, image_id, source_signature):
        return True
    return _get_disk_entry(size, image_id, source_signature, touch=False) is not None


def has_cached_fast(size: str, image_id: int) -> bool:
    return _memory_get_entry_fast(size, image_id) is not None or fast_disk_has(size, image_id)


async def _run_thumbnail_job(
    filepath: str,
    size: str,
    image_id: int,
    executor: ThreadPoolExecutor,
    include_smaller_tiers: bool,
    hot: bool,
    allow_stale_fallback: bool,
):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        executor,
        partial(
            _generate_missing_thumbnails_sync,
            filepath,
            size,
            image_id,
            include_smaller_tiers=include_smaller_tiers,
            hot=hot,
            allow_stale_fallback=allow_stale_fallback,
        ),
    )


async def _ensure_thumbnail_with_executor(
    filepath: str,
    size: str,
    image_id: int,
    executor: ThreadPoolExecutor,
    *,
    note_activity: bool,
    include_smaller_tiers: bool = False,
    allow_stale_fallback: bool = True,
) -> bytes:
    if note_activity:
        note_user_activity()

    source_signature = _build_source_signature(filepath, size, image_id)
    cached = _memory_get(size, image_id, source_signature)
    if cached is not None:
        return cached

    # Try lock-free fast path via in-memory index before DB round-trip
    disk_entry = fast_disk_read_entry(
        size,
        image_id,
        None if allow_stale_fallback else source_signature,
    )
    if disk_entry is not None:
        return disk_entry[1]

    cached = _read_disk_thumbnail(size, image_id, source_signature)
    if cached is not None:
        return cached
    if _source_missing(filepath):
        return b""

    inflight_key = ("thumb", image_id, source_signature)
    task = _inflight.get(inflight_key)
    if task is None:
        task = asyncio.create_task(
            _run_thumbnail_job(
                filepath,
                size,
                image_id,
                executor,
                include_smaller_tiers,
                note_activity,
                allow_stale_fallback,
            )
        )
        _inflight[inflight_key] = task

    try:
        generated = await task
    finally:
        if _inflight.get(inflight_key) is task and task.done():
            _inflight.pop(inflight_key, None)
    if generated:
        return generated

    cached = _memory_get(size, image_id, source_signature)
    if cached is not None:
        return cached
    disk_entry = fast_disk_read_entry(
        size,
        image_id,
        None if allow_stale_fallback else source_signature,
    )
    if disk_entry is not None:
        return disk_entry[1]
    return _read_disk_thumbnail(size, image_id, source_signature) or b""


async def get_thumbnail(filepath: str, size: str, image_id: int) -> bytes:
    return await _ensure_thumbnail_with_executor(
        filepath,
        size,
        image_id,
        _executor,
        note_activity=True,
        include_smaller_tiers=False,
    )


async def prefetch_images(
    images: list[dict],
    size: str,
    limit: int | None = None,
    *,
    hot: bool = False,
) -> int:
    if size not in SIZES or not images:
        return 0

    scheduled = 0
    for img in images:
        if limit is not None and scheduled >= limit:
            break
        image_id = img.get("id")
        filepath = img.get("filepath")
        if image_id is None or not filepath:
            continue
        require_current = _replace_stale_thumbnails
        if not require_current and _memory_get_entry_fast(size, image_id) is not None:
            continue
        if not require_current and fast_disk_has(size, image_id):
            if hot:
                touch_cached_signature(size, image_id, None)
            continue

        if not require_current:
            asyncio.create_task(
                _ensure_thumbnail_with_executor(
                    filepath,
                    size,
                    image_id,
                    _prefetch_executor,
                    note_activity=hot,
                    include_smaller_tiers=True,
                    allow_stale_fallback=True,
                )
            )
            scheduled += 1
            continue

        source_signature = _build_source_signature(filepath, size, image_id)
        if require_current:
            if _memory_get(size, image_id, source_signature) is not None:
                continue
            if fast_disk_has(size, image_id, source_signature):
                if hot:
                    touch_cached_signature(size, image_id, source_signature)
                continue
        if has_cached(size, filepath, image_id):
            if hot:
                touch_cached(size, filepath, image_id)
            continue
        asyncio.create_task(
            _ensure_thumbnail_with_executor(
                filepath,
                size,
                image_id,
                _prefetch_executor,
                note_activity=hot,
                include_smaller_tiers=True,
                allow_stale_fallback=not require_current,
            )
        )
        scheduled += 1
    return scheduled


async def _run_full_image_job(filepath: str, image_id: int, hot: bool):
    loop = asyncio.get_running_loop()
    source_signature = _build_source_signature(filepath, FULL_TIER, image_id)
    return await loop.run_in_executor(
        _executor,
        _cache_full_image_sync,
        filepath,
        image_id,
        source_signature,
        hot,
    )


def get_cached_full_image_path(filepath: str, image_id: int) -> str | None:
    source_signature = _build_source_signature(filepath, FULL_TIER, image_id)
    row = _get_disk_entry(FULL_TIER, image_id, source_signature)
    return row["path"] if row is not None else None


async def schedule_full_image_cache(filepath: str, image_id: int, *, hot: bool = True):
    if not SSD_CACHE_DIR or _disk_allocations.get(FULL_TIER, 0) <= 0:
        return
    if not os.path.exists(filepath):
        return

    source_signature = _build_source_signature(filepath, FULL_TIER, image_id)
    if touch_cached_signature(FULL_TIER, image_id, source_signature):
        return
    inflight_key = ("full", image_id, source_signature)
    task = _inflight.get(inflight_key)
    if task is not None:
        return

    task = asyncio.create_task(_run_full_image_job(filepath, image_id, hot))
    _inflight[inflight_key] = task

    async def _release_when_done():
        try:
            await task
        except Exception:
            pass
        finally:
            if _inflight.get(inflight_key) is task:
                _inflight.pop(inflight_key, None)

    asyncio.create_task(_release_when_done())


async def get_full_image_path(filepath: str, image_id: int) -> str:
    note_user_activity()
    source_signature = _build_source_signature(filepath, FULL_TIER, image_id)
    row = _get_disk_entry(FULL_TIER, image_id, source_signature)
    if row is not None:
        return row["path"]

    inflight_key = ("full", image_id, source_signature)
    task = _inflight.get(inflight_key)
    if task is None:
        task = asyncio.create_task(_run_full_image_job(filepath, image_id, True))
        _inflight[inflight_key] = task

    try:
        result = await task
    finally:
        if _inflight.get(inflight_key) is task and task.done():
            _inflight.pop(inflight_key, None)

    return str(result)


def load_embedding_image(filepath: str, image_id: int, *, require_cached: bool = False) -> Image.Image | None:
    """Load an image for embedding. Prefers SSD-cached md thumbnails for speed,
    but falls back to reading the original file from HDD if no cache exists."""
    # Try fast path first (no HDD stat)
    data = _memory_get_fast("md", image_id)
    if data is None:
        data = fast_disk_read("md", image_id)

    # Fallback to signature-validated read
    if data is None:
        source_signature = _build_source_signature(filepath, "md", image_id)
        data = _memory_get("md", image_id, source_signature)
        if data is None:
            data = _read_disk_thumbnail("md", image_id, source_signature)

    if data is not None:
        with Image.open(io.BytesIO(data)) as source:
            source.load()
            img = source.copy()
        if img.mode != "RGB":
            converted = img.convert("RGB")
            img.close()
            img = converted
        return img

    if require_cached:
        return None

    # No cached thumbnail — load original from HDD and resize to md
    try:
        md_size = SIZES["md"]
        img = _load_source_image(filepath, md_size, prefer_draft=False)
        resized = _resize_to_long_side(img, md_size)
        if resized is not img:
            img.close()
        img = resized
        if img.mode != "RGB":
            converted = img.convert("RGB")
            img.close()
            img = converted
        return img
    except Exception:
        return None


async def flush_orientation_updates():
    with _orientation_lock:
        pending = dict(_orientation_queue)
        _orientation_queue.clear()

    if not pending:
        return

    await db.batch_set_orientations(
        [(orientation, aspect_ratio, image_id) for image_id, (orientation, aspect_ratio) in pending.items()]
    )


def _set_pregen_state(state: str, message: str = "", phase: str | None = None, error: str = ""):
    _pregen_status["enabled"] = PREGENERATE_ON_IDLE
    _pregen_status["manual_mode"] = _pregen_manual_mode
    _pregen_status["manual_pause"] = _pregen_manual_pause
    _pregen_status["state"] = state
    _pregen_status["message"] = message
    _pregen_status["active_phase"] = phase
    _pregen_status["last_error"] = error
    if _pregen_status["started_at"] is None and state == "running":
        _pregen_status["started_at"] = _current_time()


def _record_pregen_batch(
    count: int,
    *,
    thumbnails_written: int | None = None,
    source_bytes: int = 0,
    read_seconds: float = 0.0,
    decode_encode_seconds: float = 0.0,
    source_read_failures: int = 0,
):
    global _pregen_session_generated, _pregen_session_started_at, _pregen_source_read_failures
    if count <= 0 and not thumbnails_written and source_read_failures <= 0:
        return
    now = _current_time()
    if _pregen_session_started_at is None:
        _pregen_session_started_at = now
    _pregen_session_generated += count
    _pregen_source_read_failures += max(0, int(source_read_failures))
    _pregen_history.append({
        "ended_at": now,
        "count": int(count),
        "thumbnails_written": int(thumbnails_written if thumbnails_written is not None else count),
        "source_bytes": int(source_bytes or 0),
        "read_seconds": float(read_seconds or 0.0),
        "decode_encode_seconds": float(decode_encode_seconds or 0.0),
        "source_read_failures": int(source_read_failures or 0),
    })
    cutoff = now - 30 * 60
    while _pregen_history and _pregen_history[0]["ended_at"] < cutoff:
        _pregen_history.popleft()


def _pregen_rates() -> tuple[float, float, dict]:
    now = _current_time()
    cutoff = now - 30 * 60
    while _pregen_history and _pregen_history[0]["ended_at"] < cutoff:
        _pregen_history.popleft()
    recent_count = sum(item["count"] for item in _pregen_history)
    recent_thumbnails = sum(item.get("thumbnails_written", item["count"]) for item in _pregen_history)
    recent_bytes = sum(item.get("source_bytes", 0) for item in _pregen_history)
    recent_read_seconds = sum(item.get("read_seconds", 0.0) for item in _pregen_history)
    recent_decode_encode_seconds = sum(item.get("decode_encode_seconds", 0.0) for item in _pregen_history)
    recent_failures = sum(item.get("source_read_failures", 0) for item in _pregen_history)
    recent_window = max(1.0, min(30 * 60.0, now - _pregen_history[0]["ended_at"])) if _pregen_history else 0.0
    recent_rate = (recent_count / recent_window) * 60.0 if recent_window > 0 else 0.0
    recent_thumbnail_rate = (recent_thumbnails / recent_window) * 60.0 if recent_window > 0 else 0.0
    session_window = max(1.0, now - _pregen_session_started_at) if _pregen_session_started_at else 0.0
    overall_rate = (_pregen_session_generated / session_window) * 60.0 if session_window > 0 else 0.0
    diagnostics = {
        "recent_source_reads_per_min": recent_rate,
        "recent_thumbnails_written_per_min": recent_thumbnail_rate,
        "recent_read_mbps": (
            (recent_bytes / (1024 * 1024)) / recent_read_seconds
            if recent_read_seconds > 0
            else 0.0
        ),
        "avg_source_read_seconds": (
            recent_read_seconds / recent_count
            if recent_count > 0
            else 0.0
        ),
        "avg_decode_encode_seconds": (
            recent_decode_encode_seconds / recent_count
            if recent_count > 0
            else 0.0
        ),
        "recent_source_read_failures": recent_failures,
        "source_read_failures": _pregen_source_read_failures,
    }
    return recent_rate, overall_rate, diagnostics


async def _cache_target_total() -> int:
    conn = await db.get_db()
    try:
        cursor = await conn.execute(
            "SELECT COUNT(*) AS c FROM images i "
            "JOIN catalog_sources s ON s.id = i.source_id "
            "WHERE s.included = 1 AND s.online = 1 AND i.missing_at IS NULL"
        )
        row = await cursor.fetchone()
        return int(row["c"] if row else 0)
    finally:
        await conn.close()


def _reset_pregen_bulk_cursor():
    _pregen_bulk_cursor["source_id"] = 0
    _pregen_bulk_cursor["filepath"] = ""
    _pregen_bulk_cursor["id"] = 0


def _reset_pregen_full_cursor():
    _pregen_full_cursor["source_id"] = 0
    _pregen_full_cursor["filepath"] = ""
    _pregen_full_cursor["id"] = 0


async def _pregen_bulk_candidate_batch(limit: int):
    conn = await db.get_db()
    try:
        cursor = await conn.execute(
            "SELECT i.id, i.source_id, i.filepath, i.file_size, i.file_modified_at "
            "FROM images i "
            "JOIN catalog_sources s ON s.id = i.source_id "
            "WHERE s.included = 1 AND s.online = 1 "
            "AND i.missing_at IS NULL "
            "AND ("
            "  i.source_id > ? "
            "  OR (i.source_id = ? AND (i.filepath > ? OR (i.filepath = ? AND i.id > ?)))"
            ") "
            "ORDER BY i.source_id ASC, i.filepath ASC, i.id ASC "
            "LIMIT ?",
            (
                int(_pregen_bulk_cursor.get("source_id") or 0),
                int(_pregen_bulk_cursor.get("source_id") or 0),
                str(_pregen_bulk_cursor.get("filepath") or ""),
                str(_pregen_bulk_cursor.get("filepath") or ""),
                int(_pregen_bulk_cursor.get("id") or 0),
                limit,
            ),
        )
        rows = await cursor.fetchall()
        if rows:
            last = rows[-1]
            _pregen_bulk_cursor["source_id"] = int(last["source_id"] or 0)
            _pregen_bulk_cursor["filepath"] = str(last["filepath"] or "")
            _pregen_bulk_cursor["id"] = int(last["id"] or 0)
        return rows
    finally:
        await conn.close()


async def _pregen_full_candidate_batch(limit: int):
    conn = await db.get_db()
    try:
        cursor = await conn.execute(
            "SELECT i.id, i.source_id, i.filepath, i.file_size, i.file_modified_at "
            "FROM images i "
            "JOIN catalog_sources s ON s.id = i.source_id "
            "WHERE s.included = 1 AND s.online = 1 "
            "AND i.missing_at IS NULL "
            "AND ("
            "  i.source_id > ? "
            "  OR (i.source_id = ? AND (i.filepath > ? OR (i.filepath = ? AND i.id > ?)))"
            ") "
            "ORDER BY i.source_id ASC, i.filepath ASC, i.id ASC "
            "LIMIT ?",
            (
                int(_pregen_full_cursor.get("source_id") or 0),
                int(_pregen_full_cursor.get("source_id") or 0),
                str(_pregen_full_cursor.get("filepath") or ""),
                str(_pregen_full_cursor.get("filepath") or ""),
                int(_pregen_full_cursor.get("id") or 0),
                limit,
            ),
        )
        rows = await cursor.fetchall()
        if rows:
            last = rows[-1]
            _pregen_full_cursor["source_id"] = int(last["source_id"] or 0)
            _pregen_full_cursor["filepath"] = str(last["filepath"] or "")
            _pregen_full_cursor["id"] = int(last["id"] or 0)
        return rows
    finally:
        await conn.close()


def _bulk_tier_budgets() -> dict[str, int]:
    return {size: _background_tier_budget(size) for size in THUMB_TIERS}


def _full_tier_room(budget: int) -> int:
    if budget <= 0 or _cache_metadata_backoff_active():
        return 0
    with _meta_lock:
        conn = None
        try:
            conn = _db_connect()
            room = max(0, int(budget) - _tier_bytes(conn, FULL_TIER))
            _clear_cache_metadata_lock_backoff()
            return room
        except sqlite3.OperationalError as exc:
            if _is_sqlite_locked(exc):
                _note_cache_metadata_lock()
                return 0
            raise
        finally:
            if conn is not None:
                conn.close()


def _bulk_tier_room(tier_budgets: dict[str, int]) -> dict[str, int]:
    if _cache_metadata_backoff_active():
        return {size: 0 for size in THUMB_TIERS}
    with _meta_lock:
        conn = None
        try:
            conn = _db_connect()
            room = {
                size: max(0, int(tier_budgets.get(size, 0) or 0) - _tier_bytes(conn, size))
                for size in THUMB_TIERS
            }
            _clear_cache_metadata_lock_backoff()
            return room
        except sqlite3.OperationalError as exc:
            if _is_sqlite_locked(exc):
                _note_cache_metadata_lock()
                return {size: 0 for size in THUMB_TIERS}
            raise
        finally:
            if conn is not None:
                conn.close()


def _bulk_candidate_signatures(
    row,
    tier_room: dict[str, int],
    tier_budgets: dict[str, int],
) -> tuple[dict[str, str], int | None]:
    image_id = int(row["id"])
    filepath = row["filepath"]
    source_size = None
    source_missing = False
    signatures = {}
    active_tiers = [size for size in THUMB_TIERS if tier_budgets.get(size, 0) > 0]

    if (
        not _replace_stale_thumbnails
        and active_tiers
        and all(fast_disk_has(size, image_id) for size in active_tiers)
    ):
        try:
            return {}, int(row["file_size"]) if row["file_size"] is not None else None
        except (TypeError, ValueError):
            return {}, None

    for size in active_tiers:
        signature, row_source_size, row_source_missing = _build_catalog_source_signature(
            filepath,
            size,
            image_id,
            row["file_size"],
            row["file_modified_at"],
        )
        signatures[size] = signature
        if source_size is None and row_source_size is not None:
            source_size = row_source_size
        source_missing = source_missing or row_source_missing

    if source_missing:
        return {}, source_size

    needed = {}
    now = time.time()
    for size in active_tiers:
        source_signature = signatures[size]
        if _thumbnail_retry_after.get((size, image_id, source_signature), 0) > now:
            continue
        if fast_disk_has(size, image_id, source_signature):
            continue

        existing_entry = fast_disk_has(size, image_id)
        if not existing_entry:
            estimated_bytes = estimated_tier_bytes(size)
            if tier_room.get(size, 0) < estimated_bytes:
                continue
            tier_room[size] = max(0, tier_room.get(size, 0) - estimated_bytes)

        needed[size] = source_signature

    return needed, source_size


def _full_candidate_signature(row, full_room: dict[str, int], full_budget: int) -> dict | None:
    image_id = int(row["id"])
    filepath = row["filepath"]
    if not is_browser_displayable_original(filepath):
        return None
    if fast_disk_path_entry(FULL_TIER, image_id) is not None:
        return None

    source_bits, source_size, source_missing = _source_bits_from_catalog_metadata(
        filepath,
        row["file_size"],
        row["file_modified_at"],
    )
    if source_missing:
        return None
    try:
        source_size = int(source_size)
    except (TypeError, ValueError):
        return None
    source_signature = _build_source_signature_from_bits(source_bits, FULL_TIER, image_id)
    if source_size > full_budget:
        return None
    if full_room.get("bytes", 0) < source_size and not _full_cache_has_room(image_id, source_size, full_budget):
        return None

    full_room["bytes"] = max(0, int(full_room.get("bytes", 0)) - int(source_size))
    return {
        "id": image_id,
        "filepath": filepath,
        "signature": source_signature,
        "source_size": int(source_size),
    }


async def _run_pregen_phase(size: str, generate_batch: int | None = None) -> int:
    """Compatibility wrapper: bulk warm-up now handles all thumbnail tiers together."""
    return await _run_pregen_bulk_batch(generate_batch=generate_batch)


def _record_pregen_result(result: dict) -> int:
    source_reads = int(result.get("source_reads", 0))
    thumbnails_written = int(result.get("thumbnails_written", 0))
    originals_written = int(result.get("originals_written", 0))
    source_bytes = int(result.get("source_bytes", 0))
    read_seconds = float(result.get("read_seconds", 0.0))
    decode_encode_seconds = float(result.get("decode_encode_seconds", 0.0))
    source_read_failures = int(result.get("source_read_failures", 0))
    completed = max(source_reads, originals_written)
    useful_work = thumbnails_written + originals_written

    if completed or thumbnails_written or source_read_failures:
        if useful_work:
            _pregen_status["last_generated_at"] = _current_time()
            _pregen_status["generated_this_session"] += completed
        _record_pregen_batch(
            completed,
            thumbnails_written=thumbnails_written,
            source_bytes=source_bytes,
            read_seconds=read_seconds,
            decode_encode_seconds=decode_encode_seconds,
            source_read_failures=source_read_failures,
        )

    return useful_work


async def _run_pregen_bulk_batch(generate_batch: int | None = None) -> int:
    generate_batch = generate_batch or PREGENERATE_GENERATE_BATCH
    tier_budgets = _bulk_tier_budgets()
    if all(tier_budgets.get(size, 0) <= 0 for size in THUMB_TIERS):
        return 0

    if not await asyncio.to_thread(_flush_write_queue) and _cache_metadata_backoff_active():
        return 0
    tier_room = _bulk_tier_room(tier_budgets)
    if all(room <= 0 for room in tier_room.values()) and _cache_metadata_backoff_active():
        return 0
    full_budget = int(_disk_allocations.get(FULL_TIER, 0) or 0)
    full_room = {"bytes": _full_tier_room(full_budget)} if full_budget > 0 else {"bytes": 0}
    pending = []
    scanned_batches = 0
    max_scan_batches = 4
    reached_end = False

    while len(pending) < generate_batch and scanned_batches < max_scan_batches:
        if not _prefetching or _pregen_manual_pause:
            break
        if _pregen_should_yield_to_foreground():
            break

        rows = await _pregen_bulk_candidate_batch(PREGENERATE_SCAN_BATCH)
        if not rows:
            _reset_pregen_bulk_cursor()
            reached_end = True
            if pending:
                break
            rows = await _pregen_bulk_candidate_batch(PREGENERATE_SCAN_BATCH)
            if not rows:
                return 0

        for row in rows:
            if not _prefetching or _pregen_manual_pause:
                break
            if _pregen_should_yield_to_foreground():
                break
            size_signatures, source_size = _bulk_candidate_signatures(row, tier_room, tier_budgets)
            full_item = (
                _full_candidate_signature(row, full_room, full_budget)
                if full_room["bytes"] > 0
                else None
            )
            if not size_signatures and full_item is None:
                continue
            pending.append({
                "id": int(row["id"]),
                "filepath": row["filepath"],
                "signatures": size_signatures,
                "full": full_item,
                "source_size": source_size,
            })
            if len(pending) >= generate_batch:
                break

        scanned_batches += 1
        if len(rows) < PREGENERATE_SCAN_BATCH:
            _reset_pregen_bulk_cursor()
            reached_end = True
            break
        if reached_end:
            break

    if not pending:
        if scanned_batches >= max_scan_batches and not reached_end:
            return -1
        return 0

    loop = asyncio.get_running_loop()
    completed = 0
    idx = 0
    wave_size = 1
    for start in range(0, len(pending), wave_size):
        wave = pending[start:start + wave_size]
        tasks = [
            loop.run_in_executor(
                _prefetch_executor,
                partial(
                    _generate_thumbnail_set_sync,
                    item["filepath"],
                    item["id"],
                    item["signatures"],
                    source_bytes=item["source_size"],
                    full_item=item.get("full"),
                    hot=False,
                ),
            )
            for item in wave
        ]
        for task in asyncio.as_completed(tasks):
            idx += 1
            completed += _record_pregen_result(await task)
            if idx % 8 == 0 and not _pregen_should_yield_to_foreground():
                await asyncio.to_thread(_flush_write_queue)
        if _pregen_should_yield_to_foreground():
            break
    if not _pregen_should_yield_to_foreground():
        await asyncio.to_thread(_flush_write_queue)
    if completed <= 0:
        return -1
    return completed


async def _run_full_warm_batch(generate_batch: int | None = None) -> int:
    generate_batch = generate_batch or PREGENERATE_GENERATE_BATCH
    full_budget = int(_disk_allocations.get(FULL_TIER, 0) or 0)
    if full_budget <= 0 or not SSD_CACHE_DIR:
        return 0

    if not await asyncio.to_thread(_flush_write_queue) and _cache_metadata_backoff_active():
        return 0
    full_room = {"bytes": _full_tier_room(full_budget)}
    if full_room["bytes"] <= 0:
        return 0

    pending = []
    scanned_batches = 0
    max_scan_batches = 4
    reached_end = False

    while len(pending) < generate_batch and scanned_batches < max_scan_batches:
        if not _prefetching or _pregen_manual_pause:
            break
        if _pregen_should_yield_to_foreground():
            break

        rows = await _pregen_full_candidate_batch(PREGENERATE_SCAN_BATCH)
        if not rows:
            _reset_pregen_full_cursor()
            reached_end = True
            if pending:
                break
            rows = await _pregen_full_candidate_batch(PREGENERATE_SCAN_BATCH)
            if not rows:
                return 0

        for row in rows:
            if not _prefetching or _pregen_manual_pause:
                break
            if _pregen_should_yield_to_foreground():
                break
            item = _full_candidate_signature(row, full_room, full_budget)
            if item is None:
                continue
            pending.append(item)
            if len(pending) >= generate_batch or full_room["bytes"] <= 0:
                break

        scanned_batches += 1
        if len(rows) < PREGENERATE_SCAN_BATCH:
            _reset_pregen_full_cursor()
            reached_end = True
            break
        if reached_end or full_room["bytes"] <= 0:
            break

    if not pending:
        if scanned_batches >= max_scan_batches and not reached_end:
            return -1
        return 0

    loop = asyncio.get_running_loop()

    originals_written = 0
    wave_size = 1

    async def cache_full_item(item: dict) -> tuple[dict, str]:
        result = await loop.run_in_executor(
            _prefetch_executor,
            _cache_full_image_sync,
            item["filepath"],
            item["id"],
            item["signature"],
            False,
        )
        return item, result

    for start in range(0, len(pending), wave_size):
        wave = pending[start:start + wave_size]
        tasks = [asyncio.create_task(cache_full_item(item)) for item in wave]
        for task in asyncio.as_completed(tasks):
            item, result = await task
            if result != item["filepath"] and fast_disk_has(FULL_TIER, item["id"], item["signature"]):
                originals_written += 1
                item_bytes = int(item.get("source_size") or 0)
                _pregen_status["last_generated_at"] = _current_time()
                _pregen_status["generated_this_session"] += 1
                _record_pregen_batch(1, thumbnails_written=0, source_bytes=item_bytes)
        if _pregen_should_yield_to_foreground():
            break
    return originals_written


def _copy_disk_stats(disk: dict) -> dict:
    copied = dict(disk)
    copied["tiers"] = {
        size: dict(info)
        for size, info in (disk.get("tiers") or {}).items()
    }
    return copied


def cache_stats() -> dict:
    memory = _memory_stats()
    now = _current_time()
    cached_disk = _disk_stats_cache["data"]
    if cached_disk is not None and now < _disk_stats_cache["expires"]:
        disk = _copy_disk_stats(cached_disk)
    else:
        disk_tiers = {
            size: {
                "count": 0,
                "bytes": 0,
                "current_count": 0,
                "current_bytes": 0,
                "stale_count": 0,
                "replacement_mode": False,
                "budget_bytes": _disk_allocations.get(size, 0),
            }
            for size in ALL_TIERS
        }

        lock_acquired = _meta_lock.acquire(blocking=False)
        if not lock_acquired and cached_disk is not None:
            disk = _copy_disk_stats(cached_disk)
            return {
                "memory": memory,
                "disk": disk,
                "thumbnail_config": {
                    "changed_at": _thumb_config_changed_at,
                    "replace_stale_thumbnails": _replace_stale_thumbnails,
                },
            }
        if not lock_acquired:
            _meta_lock.acquire()
            lock_acquired = True
        try:
            conn = _db_connect()
            rows = conn.execute(
                "SELECT size, COUNT(*) AS count, COALESCE(SUM(size_bytes), 0) AS bytes "
                "FROM cache_entries WHERE cache_root = ? GROUP BY size",
                (SSD_CACHE_DIR,),
            ).fetchall()
            if _replace_stale_thumbnails and _thumb_config_changed_at > 0:
                current_rows = conn.execute(
                    "SELECT size, COUNT(*) AS count, COALESCE(SUM(size_bytes), 0) AS bytes "
                    "FROM cache_entries "
                    "WHERE cache_root = ? AND (size = ? OR created_at >= ?) "
                    "GROUP BY size",
                    (SSD_CACHE_DIR, FULL_TIER, _thumb_config_changed_at),
                ).fetchall()
            else:
                current_rows = rows
        finally:
            if lock_acquired:
                _meta_lock.release()

        for row in rows:
            if row["size"] in disk_tiers:
                disk_tiers[row["size"]]["count"] = int(row["count"])
                disk_tiers[row["size"]]["bytes"] = int(row["bytes"])
        for row in current_rows:
            if row["size"] in disk_tiers:
                disk_tiers[row["size"]]["current_count"] = int(row["count"])
                disk_tiers[row["size"]]["current_bytes"] = int(row["bytes"])
        for size, info in disk_tiers.items():
            if not _replace_stale_thumbnails or size == FULL_TIER:
                info["current_count"] = info["count"]
                info["current_bytes"] = info["bytes"]
            info["stale_count"] = max(0, info["count"] - info["current_count"])
            info["replacement_mode"] = bool(
                _replace_stale_thumbnails
                and size in THUMB_TIERS
                and info["stale_count"] > 0
            )

        disk = {
            "root": SSD_CACHE_DIR,
            "limit_bytes": SSD_CACHE_BYTES,
            "used_bytes": sum(info["bytes"] for info in disk_tiers.values()),
            "tiers": disk_tiers,
        }
        _disk_stats_cache["data"] = _copy_disk_stats(disk)
        _disk_stats_cache["expires"] = now + _disk_stats_cache_ttl_seconds
        _disk_stats_cache["stale_until"] = now + _disk_stats_cache_max_stale_seconds
    return {
        "memory": memory,
        "disk": disk,
        "thumbnail_config": {
            "changed_at": _thumb_config_changed_at,
            "replace_stale_thumbnails": _replace_stale_thumbnails,
        },
    }


def _thumb_config_signature() -> str:
    return f"{CACHE_VERSION}|{SIZES['sm']}|{SIZES['md']}|{SIZES['lg']}|{THUMB_QUALITY}"


def _sync_thumb_config_metadata(new_signature: str, *, replace_thumbnail_cache: bool):
    global _last_thumb_config_signature, _thumb_config_changed_at, _replace_stale_thumbnails
    now = _current_time()

    with _meta_lock:
        conn = _db_connect()
        try:
            row = conn.execute(
                "SELECT thumb_config_signature, thumb_config_changed_at, replace_stale_thumbnails "
                "FROM cache_metadata WHERE cache_root = ?",
                (SSD_CACHE_DIR,),
            ).fetchone()
            previous_signature = row["thumb_config_signature"] if row else _last_thumb_config_signature
            previous_changed_at = float(row["thumb_config_changed_at"]) if row else _thumb_config_changed_at
            previous_replace_stale = bool(row["replace_stale_thumbnails"]) if row else _replace_stale_thumbnails
        finally:
            conn.close()

    changed = bool(previous_signature and previous_signature != new_signature)
    if changed:
        _clear_memory_tiers(THUMB_TIERS)
        _thumb_config_changed_at = now
        _replace_stale_thumbnails = bool(replace_thumbnail_cache)
        for tier in THUMB_TIERS:
            _pregen_scan_offsets[tier] = 0
        _reset_pregen_bulk_cursor()
        _reset_pregen_full_cursor()
    else:
        _thumb_config_changed_at = previous_changed_at or 0.0
        _replace_stale_thumbnails = previous_replace_stale

    with _meta_lock:
        conn = _db_connect()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO cache_metadata "
                "(cache_root, thumb_config_signature, thumb_config_changed_at, replace_stale_thumbnails) "
                "VALUES (?, ?, ?, ?)",
                (
                    SSD_CACHE_DIR,
                    new_signature,
                    _thumb_config_changed_at,
                    1 if _replace_stale_thumbnails else 0,
                ),
            )
            conn.commit()
        finally:
            conn.close()

    _last_thumb_config_signature = new_signature


def _original_cache_status(
    stats: dict,
    original_total: int = 0,
    archive_estimates: dict | None = None,
) -> dict:
    tier_stats = stats["disk"]["tiers"][FULL_TIER]
    count = int(tier_stats.get("count", 0) or 0)
    bytes_used = int(tier_stats.get("bytes", 0) or 0)
    budget = int(tier_stats.get("budget_bytes", 0) or 0)
    estimated_original_bytes = int(
        (archive_estimates or {}).get("needed_bytes", {}).get(FULL_TIER) or 0
    )
    avg_bytes = (
        max(1, int(estimated_original_bytes / original_total))
        if original_total > 0 and estimated_original_bytes > 0
        else int(bytes_used / count)
        if count > 0 and bytes_used > 0
        else estimated_tier_bytes(FULL_TIER)
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


def get_pregen_status(
    target_total: int = 0,
    stats: dict | None = None,
    original_total: int = 0,
    archive_estimates: dict | None = None,
) -> dict:
    stats = stats or cache_stats()
    phases = {}
    remaining = 0
    background_budgets = {
        size: _background_tier_budget(size, archive_estimates)
        for size in THUMB_TIERS
    }
    for size in THUMB_TIERS:
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
    preview_count = sum(min(int(phase.get("count", 0) or 0), int(phase.get("total", 0) or 0)) for phase in phases.values())
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
    originals = _original_cache_status(stats, original_total, archive_estimates)

    recent_rate, overall_rate, diagnostics = _pregen_rates()
    thumbnail_rate = diagnostics.get("recent_thumbnails_written_per_min", 0.0)
    preview_rate = max(recent_rate, overall_rate, thumbnail_rate / max(1, len(THUMB_TIERS)))
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
    decision = _pregen_background_decision()
    governor_status = decision.to_dict()
    governor_status["effective_thumbnail_batch_size"] = _pregen_generate_batch_for_decision(decision)

    return {
        **dict(_pregen_status),
        "governor": governor_status,
        "idle_seconds": round(max(0.0, time.monotonic() - _last_user_activity), 2),
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


def purge_image_cache(image_ids: list[int]) -> dict:
    """Remove RAM/disk cache entries for catalog images that are being purged."""
    ids = {int(image_id) for image_id in image_ids or [] if int(image_id) > 0}
    if not ids:
        return {"memory_entries_removed": 0, "disk_entries_removed": 0, "disk_files_removed": 0}

    memory_before = len(_memory_cache)
    _clear_memory_image_ids(ids)
    memory_removed = max(0, memory_before - len(_memory_cache))
    _flush_write_queue()

    disk_entries_removed = 0
    disk_files_removed = 0
    with _meta_lock:
        conn = _db_connect()
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
                    _remove_cache_entry_locked(conn, row)
                    disk_entries_removed += 1
            conn.commit()
        finally:
            conn.close()
    _tier_byte_totals.clear()
    _invalidate_disk_stats_cache()
    _source_stat_cache.clear()
    return {
        "memory_entries_removed": memory_removed,
        "disk_entries_removed": disk_entries_removed,
        "disk_files_removed": disk_files_removed,
    }


def clear_cache() -> dict:
    global _replace_stale_thumbnails
    safe_to_clear, unsafe_reason = _cache_dir_safe_to_clear()
    if not safe_to_clear:
        return {
            "refused": True,
            "error": unsafe_reason,
            "ssd_cache_dir": SSD_CACHE_DIR,
        }

    memory = _clear_memory_cache()
    _flush_write_queue()

    disk_removed = 0
    if SSD_CACHE_DIR and os.path.isdir(SSD_CACHE_DIR):
        for _root, _dirs, files in os.walk(SSD_CACHE_DIR):
            disk_removed += sum(1 for filename in files if filename != CACHE_MARKER)
        shutil.rmtree(SSD_CACHE_DIR, ignore_errors=True)
    _ensure_disk_cache_dirs()
    _clear_disk_index()
    _tier_byte_totals.clear()
    _invalidate_disk_stats_cache()
    _source_stat_cache.clear()
    _reset_pregen_bulk_cursor()
    _reset_pregen_full_cursor()

    with _meta_lock:
        conn = None
        try:
            conn = _db_connect()
            conn.execute("DELETE FROM cache_entries WHERE cache_root = ?", (SSD_CACHE_DIR,))
            conn.execute(
                "UPDATE cache_metadata SET replace_stale_thumbnails = 0 WHERE cache_root = ?",
                (SSD_CACHE_DIR,),
            )
            conn.commit()
            _clear_cache_metadata_lock_backoff()
        except sqlite3.OperationalError as exc:
            if _is_sqlite_locked(exc):
                _note_cache_metadata_lock()
                print(f"Cache metadata clear skipped: {exc}")
            else:
                raise
        finally:
            if conn is not None:
                conn.close()

    _replace_stale_thumbnails = False
    db.invalidate_cached_image_ids_cache(cache_root=SSD_CACHE_DIR)

    return {
        "memory_entries_cleared": memory["entries_cleared"],
        "memory_bytes_cleared": memory["bytes_cleared"],
        "memory_before": memory["counts"],
        "disk_files_removed": disk_removed,
        "ssd_cache_dir": SSD_CACHE_DIR,
    }


def configure(config: dict):
    global THUMB_QUALITY, SSD_CACHE_DIR, SSD_CACHE_BYTES, MEMORY_CACHE_BYTES
    global CACHE_PROFILE
    global PREGENERATE_ON_IDLE, PREGENERATE_GENERATE_BATCH, PREGENERATE_BATCH_PAUSE_SECONDS
    global _memory_cache_bytes
    global BROWSER_CACHE_MAX_AGE, BROWSER_CACHE_STALE_WHILE_REVALIDATE
    global _executor_workers, _prefetch_workers_count, _executor, _prefetch_executor
    global _disk_allocations, _last_thumb_config_signature, _pregen_manual_pause, _thumb_config_changed_at
    global _replace_stale_thumbnails

    _flush_write_queue()
    old_cache_dir = SSD_CACHE_DIR
    replace_thumbnail_cache = _as_bool(config.get("_replace_thumbnail_cache"), False)

    SIZES["sm"] = int(config.get("thumb_size_sm", SIZES["sm"]))
    SIZES["md"] = int(config.get("thumb_size_md", SIZES["md"]))
    SIZES["lg"] = int(config.get("thumb_size_lg", SIZES["lg"]))
    THUMB_QUALITY = int(config.get("thumb_quality", config.get("jpeg_quality", THUMB_QUALITY)))
    BROWSER_CACHE_MAX_AGE = int(config.get("browser_cache_max_age", BROWSER_CACHE_MAX_AGE))
    BROWSER_CACHE_STALE_WHILE_REVALIDATE = int(
        config.get(
            "browser_cache_stale_while_revalidate",
            BROWSER_CACHE_STALE_WHILE_REVALIDATE,
        )
    )
    memory_cache_gb = config.get("memory_cache_gb")
    if memory_cache_gb is None:
        try:
            memory_cache_gb = float(config.get("memory_cache_mb", 512)) / 1024.0
        except (TypeError, ValueError):
            memory_cache_gb = 0.5
    try:
        MEMORY_CACHE_BYTES = max(0, int(float(memory_cache_gb) * 1024 * 1024 * 1024))
    except (TypeError, ValueError):
        MEMORY_CACHE_BYTES = int(0.5 * 1024 * 1024 * 1024)
    SSD_CACHE_BYTES = max(0, int(config.get("ssd_cache_gb", 10))) * 1024 * 1024 * 1024
    profile = str(config.get("cache_profile", CACHE_PROFILE)).strip().lower()
    CACHE_PROFILE = profile if profile in {"browse_fast", "balanced", "original_heavy"} else "original_heavy"
    PREGENERATE_ON_IDLE = _as_bool(config.get("pregenerate_on_idle"), PREGENERATE_ON_IDLE)
    PREGENERATE_GENERATE_BATCH = max(
        4,
        min(64, int(config.get("pregen_generate_batch", PREGENERATE_GENERATE_BATCH))),
    )
    PREGENERATE_BATCH_PAUSE_SECONDS = max(
        0.0,
        min(5.0, float(config.get("pregen_batch_pause_ms", 250)) / 1000.0),
    )

    disk_cache_dir = (
        config.get("ssd_cache_dir")
        or config.get("disk_cache_dir")
        or SSD_CACHE_DIR
    )
    SSD_CACHE_DIR = os.path.abspath(str(disk_cache_dir).strip() or SSD_CACHE_DIR)
    if SSD_CACHE_DIR != old_cache_dir:
        _clear_disk_index()
        _tier_byte_totals.clear()
        _invalidate_disk_stats_cache()
        _reset_pregen_bulk_cursor()
        _reset_pregen_full_cursor()
        db.invalidate_cached_image_ids_cache()
    _ensure_disk_cache_dirs()

    _sync_thumb_config_metadata(
        _thumb_config_signature(),
        replace_thumbnail_cache=replace_thumbnail_cache,
    )

    _disk_allocations = _allocate_disk_budget(SSD_CACHE_BYTES)
    _invalidate_disk_stats_cache()

    if not PREGENERATE_ON_IDLE and not _pregen_manual_mode:
        _pregen_manual_pause = True
    elif PREGENERATE_ON_IDLE and not _pregen_manual_mode:
        _pregen_manual_pause = False

    with _cache_lock:
        _enforce_memory_budget_locked()

    user_workers = int(config.get("user_workers", _executor_workers))
    if user_workers != _executor_workers:
        _executor = _replace_executor(_executor, user_workers, "thumb")
        _executor_workers = user_workers

    prefetch_workers = int(config.get("prefetch_workers", _prefetch_workers_count))
    if prefetch_workers != _prefetch_workers_count:
        _prefetch_executor = _replace_executor(
            _prefetch_executor,
            prefetch_workers,
            "thumb-prefetch",
        )
        _prefetch_workers_count = prefetch_workers

    _enforce_all_disk_budgets()
    _build_disk_path_index()
    db.invalidate_cached_image_ids_cache()


def start_pregeneration() -> dict:
    global _pregen_manual_mode, _pregen_manual_pause
    _pregen_manual_mode = True
    _pregen_manual_pause = False
    _reset_pregen_bulk_cursor()
    _reset_pregen_full_cursor()
    _pregen_status["started_at"] = _current_time()
    _set_pregen_state("running", "Pre-generating cache on demand.")
    return dict(_pregen_status)


def stop_pregeneration() -> dict:
    global _pregen_manual_mode, _pregen_manual_pause
    _pregen_manual_mode = False
    _pregen_manual_pause = True
    _set_pregen_state("paused", "Pre-generation paused by user.")
    return dict(_pregen_status)


def _pregen_generate_batch_for_decision(decision) -> int:
    if getattr(decision, "pause", False):
        return 0
    return max(
        1,
        min(
            int(getattr(decision, "thumbnail_batch_size", 1) or 1),
            int(PREGENERATE_GENERATE_BATCH or 1),
        ),
    )


def _pregen_background_decision():
    decision = resource_governor.get_background_decision(get_idle_seconds())
    if (
        _pregen_manual_mode
        and not _pregen_manual_pause
        and decision.reason in {"user active", "recent user activity"}
        and get_idle_seconds() >= MANUAL_PREGEN_FOREGROUND_SETTLE_SECONDS
    ):
        return resource_governor.BackgroundDecision(
            work_mode=decision.work_mode,
            mode="manual",
            intensity=0.45,
            pause=False,
            sleep_seconds=0.0,
            thumbnail_batch_size=max(4, min(8, int(PREGENERATE_GENERATE_BATCH or 8))),
            thumbnail_pause_seconds=max(0.05, min(0.5, PREGENERATE_BATCH_PAUSE_SECONDS)),
            embedding_pause_seconds=decision.embedding_pause_seconds,
            reason="manual cache build",
            load_1m=decision.load_1m,
            cpu_count=decision.cpu_count,
            available_memory_gb=decision.available_memory_gb,
            swap_used_pct=decision.swap_used_pct,
            idle_seconds=decision.idle_seconds,
            checked_at=decision.checked_at,
        )
    return decision


def _pregen_should_yield_to_foreground() -> bool:
    settle_seconds = MANUAL_PREGEN_FOREGROUND_SETTLE_SECONDS if _pregen_manual_mode else 15.0
    return get_idle_seconds() < settle_seconds


async def run_prefetch_worker():
    global _prefetching
    _prefetching = True
    _target_total_cache = 0
    _target_total_at = 0.0
    no_progress_scan_passes = 0

    while _prefetching:
        try:
            foreground_active = _pregen_should_yield_to_foreground()
            if not foreground_active:
                await asyncio.to_thread(_flush_write_queue)
                await flush_orientation_updates()

            if _pregen_manual_pause:
                _set_pregen_state("paused", "Pre-generation paused by user.")
                no_progress_scan_passes = 0
                await asyncio.sleep(1)
                continue

            auto_enabled = PREGENERATE_ON_IDLE
            idle_seconds = time.monotonic() - _last_user_activity
            if not _pregen_manual_mode and not auto_enabled:
                _set_pregen_state("disabled", "Idle pre-generation is disabled in Settings.")
                no_progress_scan_passes = 0
                await asyncio.sleep(2)
                continue

            if not _pregen_manual_mode and idle_seconds < PREGENERATE_IDLE_SECONDS:
                _set_pregen_state(
                    "waiting_for_idle",
                    f"Waiting for {PREGENERATE_IDLE_SECONDS:.0f}s of user idle time.",
                )
                no_progress_scan_passes = 0
                await asyncio.sleep(1)
                continue

            now = time.monotonic()
            if now - _target_total_at > 30:
                _target_total_cache = await _cache_target_total()
                _target_total_at = now
            target_total = _target_total_cache
            if target_total <= 0:
                _set_pregen_state("idle", "No images available to warm.")
                no_progress_scan_passes = 0
                await asyncio.sleep(5)
                continue

            if foreground_active or _pregen_should_yield_to_foreground():
                _set_pregen_state(
                    "waiting_for_idle",
                    "Waiting for foreground activity to settle.",
                )
                no_progress_scan_passes = 0
                await asyncio.sleep(1)
                continue

            decision = _pregen_background_decision()
            generate_batch = _pregen_generate_batch_for_decision(decision)
            governor_status = decision.to_dict()
            governor_status["effective_thumbnail_batch_size"] = generate_batch
            _pregen_status["governor"] = governor_status
            if decision.pause:
                _set_pregen_state(
                    "throttled",
                    f"Background work paused: {decision.reason}.",
                )
                no_progress_scan_passes = 0
                await asyncio.sleep(decision.sleep_seconds)
                continue

            phase_order = ("sm", "md", "lg")
            phases = [size for size in phase_order if _background_tier_budget(size) > 0]
            full_budget = int(_disk_allocations.get(FULL_TIER, 0) or 0)
            if not phases and full_budget <= 0:
                _set_pregen_state("idle", "No SSD cache budget is available.")
                no_progress_scan_passes = 0
                await asyncio.sleep(5)
                continue

            generated = 0
            if phases:
                _set_pregen_state(
                    "running",
                    f"Bulk warming preview cache ({decision.mode}: {decision.reason})...",
                    phase="previews",
                )
                generated = await _run_pregen_bulk_batch(
                    generate_batch=generate_batch,
                )

                await flush_orientation_updates()

            if generated <= 0 and full_budget > 0:
                _set_pregen_state(
                    "running",
                    f"Warming original SSD cache ({decision.mode}: {decision.reason})...",
                    phase=FULL_TIER,
                )
                full_generated = await _run_full_warm_batch(
                    generate_batch=max(1, min(8, generate_batch)),
                )
                generated = full_generated if full_generated != 0 else generated

            if generated == 0:
                no_progress_scan_passes = 0
                status = get_pregen_status(target_total)
                phase_parts = []
                for phase in phases:
                    phase_parts.append(
                        f"{phase}: {status['phases'][phase]['count']}/{target_total}"
                    )
                if full_budget > 0:
                    originals = status["originals"]
                    phase_parts.append(
                        f"full: {originals['count']} cached, {originals['utilization_pct']:.1f}% of budget"
                    )
                _set_pregen_state(
                    "complete",
                    "Cache is warm for current budget. " + " · ".join(phase_parts),
                )
                await asyncio.sleep(5)
            elif generated < 0:
                no_progress_scan_passes += 1
                if no_progress_scan_passes >= PREGENERATE_NO_PROGRESS_SCAN_LIMIT:
                    status = get_pregen_status(target_total)
                    phase_parts = []
                    for phase in phases:
                        phase_status = status["phases"][phase]
                        phase_parts.append(
                            f"{phase}: {phase_status['count']}/{phase_status['total']}"
                        )
                    if full_budget > 0:
                        originals = status["originals"]
                        phase_parts.append(
                            f"full: {originals['count']} cached, {originals['utilization_pct']:.1f}% of budget"
                        )
                    _set_pregen_state(
                        "complete",
                        "Cache scan found no more warmable images. " + " · ".join(phase_parts),
                    )
                    no_progress_scan_passes = 0
                    await asyncio.sleep(5)
                else:
                    _set_pregen_state(
                        "running",
                        f"Scanning for remaining cache work ({no_progress_scan_passes}/{PREGENERATE_NO_PROGRESS_SCAN_LIMIT}).",
                        phase="previews",
                    )
                    await asyncio.sleep(max(0.25, PREGENERATE_BATCH_PAUSE_SECONDS, decision.thumbnail_pause_seconds))
            else:
                no_progress_scan_passes = 0
                await asyncio.sleep(max(PREGENERATE_BATCH_PAUSE_SECONDS, decision.thumbnail_pause_seconds))

        except Exception as e:
            _set_pregen_state("error", "Pre-generation worker hit an error.", error=str(e))
            print(f"Prefetch worker error: {e}")
            await asyncio.sleep(5)


def stop_prefetch():
    global _prefetching, _persistent_conn
    _prefetching = False
    _flush_write_queue()
    with _meta_lock:
        if _persistent_conn is not None:
            try:
                _persistent_conn.close()
            finally:
                _persistent_conn = None
