import asyncio
import os
import shutil
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from functools import partial

import resource_governor
from data import connection as data_connection
from PIL import Image
from . import budget as thumbnail_budget
from . import cache_entries as thumbnail_cache_entries
from . import config as thumbnail_config
from . import data_providers
from . import disk_store
from . import full_cache
from . import generation
from . import maintenance as thumbnail_maintenance
from . import pregen
from . import pregen_candidates
from . import runtime
from . import source_identity
from . import status as thumbnail_status
from .memory_store import MemoryThumbnailStore
from .runtime import as_bool as _as_bool
from .runtime import current_time as _current_time
from .runtime import is_sqlite_locked as _is_sqlite_locked
from .runtime import replace_executor as _replace_executor

configure_data_providers = data_providers.configure

Image.MAX_IMAGE_PIXELS = None

for _name in thumbnail_config.DEFAULT_EXPORT_NAMES:
    globals()[_name] = getattr(thumbnail_config, _name)
del _name

_executor_workers = 4
_prefetch_workers_count = 6

_disk_allocations = {tier: 0 for tier in ALL_TIERS}
_executor = ThreadPoolExecutor(max_workers=_executor_workers, thread_name_prefix="thumb")
_prefetch_executor = ThreadPoolExecutor(
    max_workers=_prefetch_workers_count,
    thread_name_prefix="thumb-prefetch",
)

_memory_store = MemoryThumbnailStore(THUMB_TIERS)
# In-memory thumbnail LRU: (size, image_id) -> (source_signature, jpeg_bytes)
_memory_cache = _memory_store.cache
_memory_cache_bytes = 0
_memory_tier_bytes = _memory_store.tier_bytes
_cache_lock = threading.Lock()
_meta_lock = threading.Lock()

# Shared in-flight work so a burst of requests only performs one source read.
_inflight: dict[tuple[str, int, str], asyncio.Task[object]] = {}
_thumbnail_retry_after: dict[tuple[str, int, str], float] = {}

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
_pregen_bookkeeping = pregen.SessionBookkeeping()
_pregen_history = _pregen_bookkeeping.history
_pregen_session_started_at: float | None = _pregen_bookkeeping.session_started_at
_pregen_session_generated = _pregen_bookkeeping.session_generated
_pregen_source_read_failures = _pregen_bookkeeping.source_read_failures


_write_queue = thumbnail_cache_entries._write_queue
_write_queue_lock = thumbnail_cache_entries._write_queue_lock
_tier_byte_totals = thumbnail_cache_entries._tier_byte_totals
_disk_stats_cache = thumbnail_cache_entries._disk_stats_cache
_disk_stats_cache_ttl_seconds = thumbnail_cache_entries._disk_stats_cache_ttl_seconds
_disk_stats_cache_max_stale_seconds = thumbnail_cache_entries._disk_stats_cache_max_stale_seconds
_disk_path_index = thumbnail_cache_entries._disk_path_index
_disk_index_lock = thumbnail_cache_entries._disk_index_lock


def _sync_memory_cache_bytes() -> None:
    global _memory_cache_bytes
    _memory_cache_bytes = _memory_store.cache_bytes


def _db_connect() -> sqlite3.Connection:
    return thumbnail_cache_entries._db_connect()


def _note_cache_metadata_lock():
    return thumbnail_cache_entries._note_cache_metadata_lock()


def _clear_cache_metadata_lock_backoff():
    return thumbnail_cache_entries._clear_cache_metadata_lock_backoff()


def _cache_metadata_backoff_active() -> bool:
    return thumbnail_cache_entries._cache_metadata_backoff_active()


def _cache_access_time(*, hot: bool) -> float:
    now = _current_time()
    return now if hot else now - COLD_CACHE_ACCESS_OFFSET_SECONDS


def _cache_marker_path() -> str:
    return thumbnail_maintenance.cache_marker_path(SSD_CACHE_DIR, CACHE_MARKER)


def _cache_dir_has_marker() -> bool:
    return thumbnail_maintenance.cache_dir_has_marker(SSD_CACHE_DIR, CACHE_MARKER)


def _cache_dir_is_legacy_cache_layout() -> bool:
    """True for empty or old unmarked cache roots containing only cache tiers."""
    return thumbnail_maintenance.cache_dir_is_legacy_cache_layout(SSD_CACHE_DIR, ALL_TIERS, CACHE_MARKER)


def _write_cache_marker():
    thumbnail_maintenance.write_cache_marker(SSD_CACHE_DIR, CACHE_MARKER)


def _cache_dir_safe_to_clear() -> tuple[bool, str]:
    return thumbnail_maintenance.cache_dir_safe_to_clear(
        SSD_CACHE_DIR,
        ALL_TIERS,
        CACHE_MARKER,
        write_marker=lambda _root, _marker: _write_cache_marker(),
    )


def note_user_activity():
    global _last_user_activity
    _last_user_activity = time.monotonic()


def get_idle_seconds() -> float:
    return max(0.0, time.monotonic() - _last_user_activity)


def _ensure_disk_cache_dirs():
    thumbnail_maintenance.ensure_disk_cache_dirs(
        SSD_CACHE_DIR,
        THUMB_TIERS,
        FULL_TIER,
        CACHE_MARKER,
        write_marker=lambda _root, _marker: _write_cache_marker(),
    )


def _cleanup_stale_cache_temps(max_age_seconds: float = 30 * 60) -> dict:
    return thumbnail_maintenance.cleanup_stale_cache_temps(
        SSD_CACHE_DIR,
        ALL_TIERS,
        max_age_seconds=max_age_seconds,
        invalidate_disk_stats_cache=_invalidate_disk_stats_cache,
    )


def cleanup_stale_cache_temps(max_age_seconds: float = 30 * 60) -> dict:
    return _cleanup_stale_cache_temps(max_age_seconds=max_age_seconds)


SSD_REMAINDER_PROFILES = thumbnail_config.SSD_REMAINDER_PROFILES
MEMORY_CACHE_PROFILES = thumbnail_config.MEMORY_CACHE_PROFILES


_normalize_ratios = thumbnail_config.normalize_ratios


def _active_memory_ratios() -> dict[str, float]:
    return thumbnail_config.active_memory_ratios(CACHE_PROFILE)


_allocate_by_ratios = thumbnail_config.allocate_by_ratios


def _quality_size_factor() -> float:
    return thumbnail_config.quality_size_factor(THUMB_QUALITY)


def estimated_tier_bytes(size: str) -> int:
    return thumbnail_budget.estimated_tier_bytes(size, thumb_quality=THUMB_QUALITY)


def _cache_archive_estimates() -> dict:
    return thumbnail_budget.cache_archive_estimates(
        all_tiers=ALL_TIERS,
        thumb_tiers=THUMB_TIERS,
        full_tier=FULL_TIER,
        ssd_cache_dir=SSD_CACHE_DIR,
        thumb_config_changed_at=_thumb_config_changed_at,
        meta_lock=_meta_lock,
        db_connect=_db_connect,
        estimated_tier_bytes_for_size=estimated_tier_bytes,
    )


def cache_archive_estimates() -> dict:
    return _cache_archive_estimates()


_allocate_weighted_capped = thumbnail_config.allocate_weighted_capped


def _allocate_disk_budget(total_bytes: int) -> dict[str, int]:
    estimates = _cache_archive_estimates()
    return thumbnail_budget.allocate_disk_budget(
        total_bytes,
        needed_bytes=estimates["needed_bytes"],
        profile=CACHE_PROFILE,
    )


def _background_tier_budget(size: str, archive_estimates: dict | None = None) -> int:
    budget = int(_disk_allocations.get(size, 0) or 0)
    estimates = archive_estimates or _cache_archive_estimates()
    return thumbnail_budget.background_tier_budget(
        size=size,
        budget=budget,
        needed_bytes=int(estimates["needed_bytes"].get("lg", 0) or 0),
        hot_lg_reserve_fraction=HOT_LG_RESERVE_FRACTION,
        hot_lg_reserve_min_bytes=HOT_LG_RESERVE_MIN_BYTES,
        hot_lg_reserve_max_bytes=HOT_LG_RESERVE_MAX_BYTES,
    )


def cache_budget_config() -> dict:
    return thumbnail_budget.cache_budget_config(
        profile=CACHE_PROFILE,
        memory_cache_bytes=MEMORY_CACHE_BYTES,
        disk_allocations=_disk_allocations,
    )


_SOURCE_STAT_CACHE_MAX = source_identity.SOURCE_STAT_CACHE_MAX
_SOURCE_STAT_CACHE_TTL_SECONDS = source_identity.SOURCE_STAT_CACHE_TTL_SECONDS
_source_stat_cache = source_identity.source_stat_cache


def _get_source_bits(filepath: str) -> str:
    return source_identity.get_source_bits(
        filepath,
        stat_cache=_source_stat_cache,
        ttl_seconds=_SOURCE_STAT_CACHE_TTL_SECONDS,
        max_entries=_SOURCE_STAT_CACHE_MAX,
    )


def _source_bits_from_catalog_metadata(
    filepath: str,
    file_size,
    file_modified_at,
) -> tuple[str, int | None, bool]:
    return source_identity.source_bits_from_catalog_metadata(
        filepath,
        file_size,
        file_modified_at,
        get_source_bits_fn=_get_source_bits,
    )


def _build_source_signature_from_bits(source_bits: str, size: str, image_id: int) -> str:
    return source_identity.build_source_signature_from_bits(
        source_bits,
        size,
        image_id,
        cache_version=CACHE_VERSION,
        full_tier=FULL_TIER,
        sizes=SIZES,
        thumb_quality=THUMB_QUALITY,
    )


def _build_source_signature(filepath: str, size: str, image_id: int) -> str:
    return _build_source_signature_from_bits(_get_source_bits(filepath), size, image_id)


def _build_catalog_source_signature(
    filepath: str,
    size: str,
    image_id: int,
    file_size,
    file_modified_at,
) -> tuple[str, int | None, bool]:
    return source_identity.build_catalog_source_signature(
        filepath,
        size,
        image_id,
        file_size,
        file_modified_at,
        get_source_bits_fn=_get_source_bits,
        cache_version=CACHE_VERSION,
        full_tier=FULL_TIER,
        sizes=SIZES,
        thumb_quality=THUMB_QUALITY,
    )


def _source_missing(filepath: str) -> bool:
    return source_identity.source_missing(filepath, get_source_bits_fn=_get_source_bits)


_source_missing_error = source_identity.source_missing_error


def _mark_source_missing_from_error(filepath: str, image_id: int, exc: Exception) -> bool:
    return source_identity.mark_source_missing_from_error(
        filepath,
        image_id,
        exc,
        source_missing_error_fn=_source_missing_error,
        mark_missing_sync=data_providers.mark_image_missing_sync,
    )


def get_etag(filepath: str, size: str, image_id: int) -> str:
    return f"\"{_build_source_signature(filepath, size, image_id)}\""


def response_headers(filepath: str, size: str, image_id: int) -> dict[str, str]:
    return source_identity.response_headers(
        etag=get_etag(filepath, size, image_id),
        browser_cache_max_age=BROWSER_CACHE_MAX_AGE,
        browser_cache_stale_while_revalidate=BROWSER_CACHE_STALE_WHILE_REVALIDATE,
    )


def _thumbnail_disk_path(size: str, image_id: int) -> str:
    return disk_store.thumbnail_disk_path(SSD_CACHE_DIR, size, image_id)


def _full_disk_path(image_id: int, filepath: str) -> str:
    return disk_store.full_disk_path(SSD_CACHE_DIR, FULL_TIER, image_id, filepath)


def is_browser_displayable_original(filepath: str) -> bool:
    return disk_store.is_browser_displayable_original(filepath, BROWSER_ORIGINAL_EXTENSIONS)


def _memory_get_fast(size: str, image_id: int) -> bytes | None:
    """Fast memory check — no signature validation."""
    with _cache_lock:
        return _memory_store.get_fast(size, image_id)


def _memory_get_entry_fast(size: str, image_id: int) -> tuple[str, bytes] | None:
    """Fast memory check that also returns the cached signature."""
    with _cache_lock:
        return _memory_store.get_entry_fast(size, image_id)


def _memory_get(size: str, image_id: int, source_signature: str) -> bytes | None:
    with _cache_lock:
        data = _memory_store.get(size, image_id, source_signature)
        _sync_memory_cache_bytes()
        return data


def _memory_tier_budget(size: str) -> int:
    if MEMORY_CACHE_BYTES <= 0:
        return 0
    ratios = _active_memory_ratios()
    return int(MEMORY_CACHE_BYTES * ratios.get(size, 0.0))


def _memory_remove_locked(key: tuple[str, int]) -> bool:
    removed = _memory_store.remove(key)
    _sync_memory_cache_bytes()
    return removed


def _evict_memory_oldest_locked(size: str | None = None) -> bool:
    removed = _memory_store.evict_oldest(size)
    _sync_memory_cache_bytes()
    return removed


def _enforce_memory_budget_locked():
    _memory_store.enforce_budget(THUMB_TIERS, MEMORY_CACHE_BYTES, _memory_tier_budget)
    _sync_memory_cache_bytes()


def _memory_put(size: str, image_id: int, source_signature: str, data: bytes):
    with _cache_lock:
        _memory_store.put(
            size,
            image_id,
            source_signature,
            data,
            THUMB_TIERS,
            MEMORY_CACHE_BYTES,
            _memory_tier_budget,
        )
        _sync_memory_cache_bytes()


def _clear_memory_cache() -> dict:
    with _cache_lock:
        result = _memory_store.clear(THUMB_TIERS)
        _sync_memory_cache_bytes()
        return result


def _clear_memory_tiers(tiers: tuple[str, ...]):
    with _cache_lock:
        _memory_store.clear_tiers(tiers)
        _sync_memory_cache_bytes()


def _clear_memory_image_ids(image_ids: set[int]):
    with _cache_lock:
        _memory_store.clear_image_ids(image_ids)
        _sync_memory_cache_bytes()


def _memory_stats() -> dict:
    with _cache_lock:
        _sync_memory_cache_bytes()
        return _memory_store.stats(THUMB_TIERS, MEMORY_CACHE_BYTES, _memory_tier_budget)


def _configure_cache_entries():
    thumbnail_cache_entries.configure(
        meta_lock=_meta_lock,
        cache_root=lambda: SSD_CACHE_DIR,
        disk_allocations=lambda: _disk_allocations,
        all_tiers=lambda: ALL_TIERS,
        thumb_tiers=lambda: THUMB_TIERS,
        db_path=data_providers.db_path,
        is_ephemeral_db_path=data_connection.is_ephemeral_db_path,
        current_time=_current_time,
        sqlite_locked=_is_sqlite_locked,
        thumbnail_disk_path=_thumbnail_disk_path,
        cache_access_time=_cache_access_time,
        memory_put=_memory_put,
        invalidate_cached_image_ids_cache=data_providers.invalidate_cached_image_ids_cache,
        note_cached_image_ids_added=data_providers.note_cached_image_ids_added,
    )


_configure_cache_entries()


def _remove_cache_entry_locked(conn: sqlite3.Connection, row: sqlite3.Row):
    return thumbnail_cache_entries._remove_cache_entry_locked(conn, row)


def _invalidate_disk_stats_cache(*, soft: bool = False):
    return thumbnail_cache_entries._invalidate_disk_stats_cache(soft=soft)


def _tier_bytes(conn: sqlite3.Connection, size: str) -> int:
    return thumbnail_cache_entries._tier_bytes(conn, size)


def _enforce_tier_budget_locked(conn: sqlite3.Connection, size: str) -> list[int]:
    return thumbnail_cache_entries._enforce_tier_budget_locked(conn, size)


def _enforce_all_disk_budgets():
    return thumbnail_cache_entries._enforce_all_disk_budgets()


def _get_disk_entry(
    size: str,
    image_id: int,
    source_signature: str,
    touch: bool = True,
) -> sqlite3.Row | None:
    return thumbnail_cache_entries._get_disk_entry(size, image_id, source_signature, touch=touch)


def touch_cached(size: str, filepath: str, image_id: int) -> bool:
    if size not in ALL_TIERS:
        return False
    source_signature = _build_source_signature(filepath, size, image_id)
    return touch_cached_signature(size, image_id, source_signature)


def touch_cached_signature(size: str, image_id: int, source_signature: str | None = None) -> bool:
    return thumbnail_cache_entries.touch_cached_signature(size, image_id, source_signature)


def _build_disk_path_index() -> bool:
    return thumbnail_cache_entries._build_disk_path_index()


def _index_disk_entry(size: str, image_id: int, path: str, source_signature: str):
    return thumbnail_cache_entries._index_disk_entry(size, image_id, path, source_signature)


def _unindex_disk_entry(size: str, image_id: int):
    return thumbnail_cache_entries._unindex_disk_entry(size, image_id)


def _clear_disk_index(tiers: tuple[str, ...] | None = None):
    return thumbnail_cache_entries._clear_disk_index(tiers)


def fast_disk_has(size: str, image_id: int, source_signature: str | None = None) -> bool:
    return thumbnail_cache_entries.fast_disk_has(size, image_id, source_signature)


def fast_disk_path_entry(
    size: str,
    image_id: int,
    source_signature: str | None = None,
) -> tuple[str, str] | None:
    return thumbnail_cache_entries.fast_disk_path_entry(size, image_id, source_signature)


def fast_disk_read_entry(
    size: str,
    image_id: int,
    source_signature: str | None = None,
    *,
    populate_memory: bool = False,
) -> tuple[str, bytes] | None:
    return thumbnail_cache_entries.fast_disk_read_entry(
        size,
        image_id,
        source_signature,
        populate_memory=populate_memory,
    )


def fast_disk_read(size: str, image_id: int) -> bytes | None:
    return thumbnail_cache_entries.fast_disk_read(size, image_id)


def _read_disk_thumbnail(size: str, image_id: int, source_signature: str) -> bytes | None:
    return thumbnail_cache_entries._read_disk_thumbnail(size, image_id, source_signature)


def _store_disk_entry(
    size: str,
    image_id: int,
    source_signature: str,
    path: str,
    size_bytes: int,
    *,
    hot: bool = True,
):
    return thumbnail_cache_entries._store_disk_entry(
        size,
        image_id,
        source_signature,
        path,
        size_bytes,
        hot=hot,
    )


def _write_thumbnail_to_disk(size: str, image_id: int, source_signature: str, data: bytes, *, hot: bool) -> bool:
    return thumbnail_cache_entries._write_thumbnail_to_disk(size, image_id, source_signature, data, hot=hot)


def _flush_write_queue() -> bool:
    return thumbnail_cache_entries._flush_write_queue()


def _maybe_flush_write_queue():
    return thumbnail_cache_entries._maybe_flush_write_queue()


def _full_cache_has_room(image_id: int, source_size: int, budget: int) -> bool:
    return full_cache.has_room(
        image_id,
        source_size,
        budget,
        cache_root=SSD_CACHE_DIR,
        full_tier=FULL_TIER,
        metadata_backoff_active=_cache_metadata_backoff_active,
        meta_lock=_meta_lock,
        db_connect=_db_connect,
        tier_bytes=_tier_bytes,
        clear_metadata_backoff=_clear_cache_metadata_lock_backoff,
        note_metadata_lock=_note_cache_metadata_lock,
        is_sqlite_locked=_is_sqlite_locked,
    )


def _cache_full_image_sync(
    filepath: str,
    image_id: int,
    source_signature: str,
    hot: bool = True,
    *,
    room_prechecked: bool = False,
) -> str:
    return full_cache.cache_full_image(
        filepath,
        image_id,
        source_signature,
        cache_root=SSD_CACHE_DIR,
        budget=_disk_allocations.get(FULL_TIER, 0),
        read_disk_entry=lambda signature: _get_disk_entry(FULL_TIER, image_id, signature),
        has_cache_room=_full_cache_has_room,
        full_disk_path=_full_disk_path,
        store_disk_entry=lambda entry_image_id, signature, path, size_bytes, *, hot: _store_disk_entry(
            FULL_TIER,
            entry_image_id,
            signature,
            path,
            size_bytes,
            hot=hot,
        ),
        hot=hot,
        room_prechecked=room_prechecked,
    )


def _cache_full_image_bytes_sync(
    filepath: str,
    image_id: int,
    source_signature: str,
    data: bytes,
    *,
    hot: bool = True,
    room_prechecked: bool = False,
) -> str:
    return full_cache.cache_full_image_bytes(
        filepath,
        image_id,
        source_signature,
        data,
        cache_root=SSD_CACHE_DIR,
        budget=_disk_allocations.get(FULL_TIER, 0),
        read_disk_entry=lambda signature: _get_disk_entry(FULL_TIER, image_id, signature),
        has_cache_room=_full_cache_has_room,
        full_disk_path=_full_disk_path,
        store_disk_entry=lambda entry_image_id, signature, path, size_bytes, *, hot: _store_disk_entry(
            FULL_TIER,
            entry_image_id,
            signature,
            path,
            size_bytes,
            hot=hot,
        ),
        hot=hot,
        room_prechecked=room_prechecked,
    )


_load_raw_preview = generation.load_raw_preview


def _load_source_image(filepath: str, max_target: int, prefer_draft: bool) -> Image.Image:
    return generation.load_source_image(
        filepath,
        max_target,
        prefer_draft,
        jpeg_extensions=JPEG_EXTENSIONS,
        raw_extensions=RAW_EXTENSIONS,
    )


def _load_source_image_from_bytes(
    filepath: str,
    data: bytes,
    max_target: int,
    prefer_draft: bool,
) -> Image.Image:
    return generation.load_source_image_from_bytes(
        filepath,
        data,
        max_target,
        prefer_draft,
        jpeg_extensions=JPEG_EXTENSIONS,
        raw_extensions=RAW_EXTENSIONS,
    )


_resize_to_long_side = generation.resize_to_long_side


def _queue_orientation(image_id: int, img: Image.Image):
    return generation.queue_orientation(
        image_id,
        img,
        orientation_lock=_orientation_lock,
        orientation_queue=_orientation_queue,
    )


def _thumbnail_jpeg_bytes(variant: Image.Image, size: str) -> bytes:
    return generation.thumbnail_jpeg_bytes(variant, size, THUMB_QUALITY)


def _encode_and_cache_thumbnail(
    size: str,
    image_id: int,
    source_signature: str,
    variant: Image.Image,
    *,
    hot: bool,
) -> tuple[Image.Image, bytes, bool]:
    return generation.encode_and_cache_thumbnail(
        size,
        image_id,
        source_signature,
        variant,
        hot=hot,
        thumb_quality=THUMB_QUALITY,
        memory_put=_memory_put,
        write_thumbnail_to_disk=_write_thumbnail_to_disk,
        thumbnail_retry_after=_thumbnail_retry_after,
    )


def _planned_thumbnail_sizes(
    filepath: str,
    image_id: int,
    requested_size: str,
    *,
    include_smaller_tiers: bool = False,
    allow_stale_fallback: bool = True,
) -> list[str]:
    return generation.planned_thumbnail_sizes(
        filepath,
        image_id,
        requested_size,
        include_smaller_tiers=include_smaller_tiers,
        allow_stale_fallback=allow_stale_fallback,
        source_missing=_source_missing,
        sizes=SIZES,
        thumb_tiers=THUMB_TIERS,
        disk_allocations=_disk_allocations,
        build_source_signature=_build_source_signature,
        thumbnail_retry_after=_thumbnail_retry_after,
        memory_get=_memory_get,
        fast_disk_has=fast_disk_has,
        get_disk_entry=_get_disk_entry,
    )


def _generate_missing_thumbnails_sync(
    filepath: str,
    requested_size: str,
    image_id: int,
    *,
    include_smaller_tiers: bool = False,
    hot: bool = False,
    allow_stale_fallback: bool = True,
):
    return generation.generate_missing_thumbnails(
        filepath,
        requested_size,
        image_id,
        include_smaller_tiers=include_smaller_tiers,
        hot=hot,
        allow_stale_fallback=allow_stale_fallback,
        planned_thumbnail_sizes=_planned_thumbnail_sizes,
        sizes=SIZES,
        load_source_image=_load_source_image,
        queue_orientation=_queue_orientation,
        resize_to_long_side=_resize_to_long_side,
        build_source_signature=_build_source_signature,
        encode_and_cache_thumbnail=_encode_and_cache_thumbnail,
        mark_source_missing_from_error=_mark_source_missing_from_error,
        thumbnail_retry_after=_thumbnail_retry_after,
        thumbnail_retry_seconds=THUMBNAIL_RETRY_SECONDS,
    )


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
    return await full_cache.run_full_image_job(
        filepath,
        image_id,
        hot,
        executor=_executor,
        full_tier=FULL_TIER,
        build_source_signature=_build_source_signature,
        cache_full_image_sync=_cache_full_image_sync,
    )


def get_cached_full_image_path(filepath: str, image_id: int) -> str | None:
    return full_cache.get_cached_full_image_path(
        filepath,
        image_id,
        full_tier=FULL_TIER,
        build_source_signature=_build_source_signature,
        get_disk_entry=_get_disk_entry,
    )


async def schedule_full_image_cache(filepath: str, image_id: int, *, hot: bool = True):
    return await full_cache.schedule_full_image_cache(
        filepath,
        image_id,
        hot=hot,
        cache_root=SSD_CACHE_DIR,
        budget=_disk_allocations.get(FULL_TIER, 0),
        path_exists=os.path.exists,
        full_tier=FULL_TIER,
        build_source_signature=_build_source_signature,
        touch_cached_signature=touch_cached_signature,
        inflight=_inflight,
        run_full_image_job=_run_full_image_job,
    )


async def get_full_image_path(filepath: str, image_id: int) -> str:
    return await full_cache.get_full_image_path(
        filepath,
        image_id,
        note_user_activity=note_user_activity,
        full_tier=FULL_TIER,
        build_source_signature=_build_source_signature,
        get_disk_entry=_get_disk_entry,
        inflight=_inflight,
        run_full_image_job=_run_full_image_job,
    )


def load_embedding_image(filepath: str, image_id: int, *, require_cached: bool = False) -> Image.Image | None:
    return generation.load_embedding_image(
        filepath,
        image_id,
        require_cached=require_cached,
        sizes=SIZES,
        memory_get_fast=_memory_get_fast,
        fast_disk_read=fast_disk_read,
        build_source_signature=_build_source_signature,
        memory_get=_memory_get,
        read_disk_thumbnail=_read_disk_thumbnail,
        load_source_image=_load_source_image,
        resize_to_long_side=_resize_to_long_side,
    )


async def flush_orientation_updates():
    with _orientation_lock:
        pending = dict(_orientation_queue)
        _orientation_queue.clear()

    if not pending:
        return

    await data_providers.batch_set_orientations(
        [(orientation, aspect_ratio, image_id) for image_id, (orientation, aspect_ratio) in pending.items()]
    )


def _set_pregen_state(state: str, message: str = "", phase: str | None = None, error: str = ""):
    pregen.set_state(
        _pregen_status,
        state,
        message=message,
        phase=phase,
        error=error,
        enabled=PREGENERATE_ON_IDLE,
        manual_mode=_pregen_manual_mode,
        manual_pause=_pregen_manual_pause,
        now_provider=_current_time,
    )


def _sync_pregen_bookkeeping_from_facade() -> None:
    _pregen_bookkeeping.history = _pregen_history
    _pregen_bookkeeping.session_started_at = _pregen_session_started_at
    _pregen_bookkeeping.session_generated = _pregen_session_generated
    _pregen_bookkeeping.source_read_failures = _pregen_source_read_failures


def _sync_pregen_facade_from_bookkeeping() -> None:
    global _pregen_history, _pregen_session_started_at, _pregen_session_generated, _pregen_source_read_failures
    _pregen_history = _pregen_bookkeeping.history
    _pregen_session_started_at = _pregen_bookkeeping.session_started_at
    _pregen_session_generated = _pregen_bookkeeping.session_generated
    _pregen_source_read_failures = _pregen_bookkeeping.source_read_failures


def _record_pregen_batch(
    count: int,
    *,
    thumbnails_written: int | None = None,
    source_bytes: int = 0,
    read_seconds: float = 0.0,
    decode_encode_seconds: float = 0.0,
    source_read_failures: int = 0,
):
    _sync_pregen_bookkeeping_from_facade()
    pregen.record_batch(
        _pregen_bookkeeping,
        count,
        now=_current_time(),
        thumbnails_written=thumbnails_written,
        source_bytes=source_bytes,
        read_seconds=read_seconds,
        decode_encode_seconds=decode_encode_seconds,
        source_read_failures=source_read_failures,
    )
    _sync_pregen_facade_from_bookkeeping()


def _pregen_rates() -> tuple[float, float, dict]:
    _sync_pregen_bookkeeping_from_facade()
    result = pregen.session_rates(_pregen_bookkeeping, now=_current_time())
    _sync_pregen_facade_from_bookkeeping()
    return result


async def _cache_target_total() -> int:
    return await pregen.cache_target_total(data_providers.get_db)


def _reset_pregen_bulk_cursor():
    pregen.reset_cursor(_pregen_bulk_cursor)


def _reset_pregen_full_cursor():
    pregen.reset_cursor(_pregen_full_cursor)


async def _pregen_bulk_candidate_batch(limit: int):
    return await pregen.candidate_batch(
        data_providers.get_db,
        _pregen_bulk_cursor,
        limit,
    )


async def _pregen_full_candidate_batch(limit: int):
    return await pregen.candidate_batch(
        data_providers.get_db,
        _pregen_full_cursor,
        limit,
    )


def _bulk_tier_budgets() -> dict[str, int]:
    return pregen.bulk_tier_budgets(THUMB_TIERS, _background_tier_budget)


def _full_tier_room(budget: int) -> int:
    return pregen.full_tier_room(
        budget,
        full_tier=FULL_TIER,
        meta_lock=_meta_lock,
        db_connect=_db_connect,
        tier_bytes=_tier_bytes,
        cache_metadata_backoff_active=_cache_metadata_backoff_active,
        clear_cache_metadata_lock_backoff=_clear_cache_metadata_lock_backoff,
        note_cache_metadata_lock=_note_cache_metadata_lock,
        is_sqlite_locked=_is_sqlite_locked,
    )


def _bulk_tier_room(tier_budgets: dict[str, int]) -> dict[str, int]:
    return pregen.bulk_tier_room(
        tier_budgets,
        thumb_tiers=THUMB_TIERS,
        meta_lock=_meta_lock,
        db_connect=_db_connect,
        tier_bytes=_tier_bytes,
        cache_metadata_backoff_active=_cache_metadata_backoff_active,
        clear_cache_metadata_lock_backoff=_clear_cache_metadata_lock_backoff,
        note_cache_metadata_lock=_note_cache_metadata_lock,
        is_sqlite_locked=_is_sqlite_locked,
    )


def _bulk_candidate_signatures(
    row,
    tier_room: dict[str, int],
    tier_budgets: dict[str, int],
) -> tuple[dict[str, str], int | None]:
    return pregen_candidates.bulk_candidate_signatures(
        row,
        tier_room,
        tier_budgets,
        thumb_tiers=THUMB_TIERS,
        replace_stale_thumbnails=_replace_stale_thumbnails,
        fast_disk_has=fast_disk_has,
        build_catalog_source_signature=_build_catalog_source_signature,
        estimated_tier_bytes=estimated_tier_bytes,
        retry_after=_thumbnail_retry_after,
        now=time.time(),
    )


def _full_candidate_signature(row, full_room: dict[str, int], full_budget: int) -> dict | None:
    return pregen_candidates.full_candidate_signature(
        row,
        full_room,
        full_budget,
        full_tier=FULL_TIER,
        is_browser_displayable_original=is_browser_displayable_original,
        fast_disk_path_entry=fast_disk_path_entry,
        source_bits_from_catalog_metadata=_source_bits_from_catalog_metadata,
        build_source_signature_from_bits=_build_source_signature_from_bits,
        full_cache_has_room=_full_cache_has_room,
    )


async def _run_pregen_phase(size: str, generate_batch: int | None = None) -> int:
    """Compatibility wrapper: bulk warm-up now handles all thumbnail tiers together."""
    return await _run_pregen_bulk_batch(generate_batch=generate_batch)


def _record_pregen_result(result: dict) -> int:
    return pregen.record_result(
        result,
        _pregen_status,
        record_batch=_record_pregen_batch,
        now_provider=_current_time,
    )


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


_copy_disk_stats = thumbnail_status.copy_disk_stats


def cache_stats() -> dict:
    return thumbnail_status.cache_stats(
        memory_stats=_memory_stats,
        current_time=_current_time,
        disk_stats_cache=_disk_stats_cache,
        disk_stats_cache_ttl_seconds=_disk_stats_cache_ttl_seconds,
        disk_stats_cache_max_stale_seconds=_disk_stats_cache_max_stale_seconds,
        meta_lock=_meta_lock,
        db_connect=_db_connect,
        cache_root=SSD_CACHE_DIR,
        cache_limit_bytes=SSD_CACHE_BYTES,
        disk_allocations=_disk_allocations,
        all_tiers=ALL_TIERS,
        thumb_tiers=THUMB_TIERS,
        full_tier=FULL_TIER,
        replace_stale_thumbnails=_replace_stale_thumbnails,
        thumb_config_changed_at=_thumb_config_changed_at,
    )


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
    return thumbnail_status.original_cache_status(
        stats,
        full_tier=FULL_TIER,
        original_total=original_total,
        archive_estimates=archive_estimates,
        estimated_full_tier_bytes=estimated_tier_bytes(FULL_TIER),
    )


def get_pregen_status(
    target_total: int = 0,
    stats: dict | None = None,
    original_total: int = 0,
    archive_estimates: dict | None = None,
) -> dict:
    stats = stats or cache_stats()
    return thumbnail_status.pregen_status(
        pregen_state=_pregen_status,
        stats=stats,
        target_total=target_total,
        original_total=original_total,
        archive_estimates=archive_estimates,
        thumb_tiers=THUMB_TIERS,
        background_tier_budget=_background_tier_budget,
        estimated_tier_bytes=estimated_tier_bytes,
        original_status=_original_cache_status,
        pregen_rates=_pregen_rates,
        pregen_background_decision=_pregen_background_decision,
        pregen_generate_batch_for_decision=_pregen_generate_batch_for_decision,
        idle_seconds=get_idle_seconds(),
    )


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
    data_providers.invalidate_cached_image_ids_cache(cache_root=SSD_CACHE_DIR)

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
        data_providers.invalidate_cached_image_ids_cache()
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
    data_providers.invalidate_cached_image_ids_cache()


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
    return pregen.generate_batch_for_decision(decision, PREGENERATE_GENERATE_BATCH)


def _pregen_background_decision():
    return pregen.background_decision(
        get_idle_seconds(),
        work_mode_provider=_background_work_mode,
        decision_provider=resource_governor.get_background_decision,
    )


def _background_work_mode() -> str:
    return pregen.background_work_mode()


def _pregen_should_yield_to_foreground() -> bool:
    return pregen.should_yield_to_foreground(_background_work_mode)


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
            if not _pregen_manual_mode and not auto_enabled:
                _set_pregen_state("disabled", "Background cache warming is disabled in Settings.")
                no_progress_scan_passes = 0
                await asyncio.sleep(2)
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
                    "throttled",
                    "Background cache warming is paused in Browse mode.",
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
    global _prefetching
    _prefetching = False
    _flush_write_queue()
    thumbnail_cache_entries.close_persistent_conn()
