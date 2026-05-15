import aiosqlite
import asyncio
from collections import Counter
import os
import sqlite3
import time as _time

import settings

DB_PATH = os.path.join(os.path.dirname(__file__), "photoarchive.db")
_embedding_batch_listeners = []
_deep_search_query_embedding_listeners = []


def register_embedding_batch_listener(listener):
    if listener not in _embedding_batch_listeners:
        _embedding_batch_listeners.append(listener)


def register_deep_search_query_embedding_listener(listener):
    if listener not in _deep_search_query_embedding_listeners:
        _deep_search_query_embedding_listeners.append(listener)


def _notify_embedding_batch_stored(model_key: str, image_ids: list[int]):
    for listener in list(_embedding_batch_listeners):
        try:
            listener(model_key, image_ids)
        except Exception:
            pass


def _notify_deep_search_query_embedding_stored(model_key: str, query: str):
    for listener in list(_deep_search_query_embedding_listeners):
        try:
            listener(model_key, query)
        except Exception:
            pass


EXPECTED_EMBEDDING_DIM = 2048  # Qwen3-VL-Embedding-2B native dimension
SCHEMA_VERSION = 5

SCHEMA = """
CREATE TABLE IF NOT EXISTS catalog_sources (
    id INTEGER PRIMARY KEY,
    path TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    included INTEGER NOT NULL DEFAULT 1,
    online INTEGER NOT NULL DEFAULT 1,
    image_count INTEGER NOT NULL DEFAULT 0,
    active_image_count INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL DEFAULT (strftime('%s', 'now')),
    last_scan_at REAL DEFAULT NULL,
    last_seen_at REAL DEFAULT NULL,
    removed_at REAL DEFAULT NULL
);

CREATE TABLE IF NOT EXISTS images (
    id INTEGER PRIMARY KEY,
    source_id INTEGER REFERENCES catalog_sources(id),
    filename TEXT NOT NULL,
    filepath TEXT NOT NULL UNIQUE,
    elo REAL DEFAULT 1200.0,
    comparisons INTEGER DEFAULT 0,
    propagated_updates INTEGER DEFAULT 0,
    status TEXT DEFAULT 'kept',
    flag TEXT DEFAULT 'unflagged',
    orientation TEXT DEFAULT NULL,
    date_taken TEXT DEFAULT NULL,
    camera_make TEXT DEFAULT NULL,
    camera_model TEXT DEFAULT NULL,
    lens TEXT DEFAULT NULL,
    file_ext TEXT DEFAULT NULL,
    file_size INTEGER DEFAULT NULL,
    file_modified_at REAL DEFAULT NULL,
    width INTEGER DEFAULT NULL,
    height INTEGER DEFAULT NULL,
    latitude REAL DEFAULT NULL,
    longitude REAL DEFAULT NULL,
    metadata_scanned_at REAL DEFAULT NULL,
    metadata_version INTEGER DEFAULT NULL,
    missing_at REAL DEFAULT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE VIRTUAL TABLE IF NOT EXISTS images_metadata_fts
USING fts5(
    filename,
    filepath,
    date_taken,
    camera_make,
    camera_model,
    lens,
    file_ext,
    content='images',
    content_rowid='id',
    tokenize='trigram'
);

CREATE TRIGGER IF NOT EXISTS images_metadata_fts_ai AFTER INSERT ON images BEGIN
    INSERT INTO images_metadata_fts(
        rowid, filename, filepath, date_taken, camera_make, camera_model, lens, file_ext
    )
    VALUES (
        new.id, new.filename, new.filepath, new.date_taken, new.camera_make,
        new.camera_model, new.lens, new.file_ext
    );
END;

CREATE TRIGGER IF NOT EXISTS images_metadata_fts_ad AFTER DELETE ON images BEGIN
    INSERT INTO images_metadata_fts(
        images_metadata_fts, rowid, filename, filepath, date_taken, camera_make,
        camera_model, lens, file_ext
    )
    VALUES (
        'delete', old.id, old.filename, old.filepath, old.date_taken,
        old.camera_make, old.camera_model, old.lens, old.file_ext
    );
END;

CREATE TRIGGER IF NOT EXISTS images_metadata_fts_au
AFTER UPDATE OF filename, filepath, date_taken, camera_make, camera_model, lens, file_ext
ON images BEGIN
    INSERT INTO images_metadata_fts(
        images_metadata_fts, rowid, filename, filepath, date_taken, camera_make,
        camera_model, lens, file_ext
    )
    VALUES (
        'delete', old.id, old.filename, old.filepath, old.date_taken,
        old.camera_make, old.camera_model, old.lens, old.file_ext
    );
    INSERT INTO images_metadata_fts(
        rowid, filename, filepath, date_taken, camera_make, camera_model, lens, file_ext
    )
    VALUES (
        new.id, new.filename, new.filepath, new.date_taken, new.camera_make,
        new.camera_model, new.lens, new.file_ext
    );
END;

CREATE TABLE IF NOT EXISTS comparisons (
    id INTEGER PRIMARY KEY,
    winner_id INTEGER REFERENCES images(id),
    loser_id INTEGER REFERENCES images(id),
    mode TEXT,
    elo_before_winner REAL,
    elo_before_loser REAL,
    action_id TEXT DEFAULT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_images_status ON images(status);
CREATE INDEX IF NOT EXISTS idx_images_source_id ON images(source_id);
CREATE INDEX IF NOT EXISTS idx_catalog_sources_path ON catalog_sources(path);
CREATE INDEX IF NOT EXISTS idx_catalog_sources_active ON catalog_sources(included, online);
CREATE INDEX IF NOT EXISTS idx_images_elo ON images(elo DESC);
CREATE INDEX IF NOT EXISTS idx_images_comparisons ON images(comparisons);
CREATE INDEX IF NOT EXISTS idx_comparisons_pair ON comparisons(winner_id, loser_id);
CREATE INDEX IF NOT EXISTS idx_comparisons_loser ON comparisons(loser_id);
CREATE INDEX IF NOT EXISTS idx_comparisons_action_id ON comparisons(action_id);

CREATE TABLE IF NOT EXISTS propagation_updates (
    id INTEGER PRIMARY KEY,
    action_id TEXT NOT NULL,
    image_id INTEGER NOT NULL REFERENCES images(id),
    elo_before REAL NOT NULL,
    propagated_updates_before INTEGER NOT NULL,
    elo_after REAL NOT NULL,
    delta REAL NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_propagation_updates_action_id
ON propagation_updates(action_id);

-- Composite indexes for fast sorted queries with status filter
CREATE INDEX IF NOT EXISTS idx_images_status_elo ON images(status, elo DESC);
CREATE INDEX IF NOT EXISTS idx_images_status_elo_asc ON images(status, elo ASC);
CREATE INDEX IF NOT EXISTS idx_images_status_id ON images(status, id DESC);
CREATE INDEX IF NOT EXISTS idx_images_status_comparisons ON images(status, comparisons DESC);
CREATE INDEX IF NOT EXISTS idx_images_status_filename ON images(status, filename ASC);
CREATE INDEX IF NOT EXISTS idx_images_status_orient_elo ON images(status, orientation, elo DESC);
CREATE INDEX IF NOT EXISTS idx_images_status_comps_elo ON images(status, comparisons, elo DESC);

-- Hot-path partial indexes for the active Library/Compare working set.
-- These avoid temp B-tree sorts caused by status IN ('kept', 'maybe').
CREATE INDEX IF NOT EXISTS idx_images_active_elo
ON images(elo DESC) WHERE status IN ('kept', 'maybe');
CREATE INDEX IF NOT EXISTS idx_images_active_elo_asc
ON images(elo ASC) WHERE status IN ('kept', 'maybe');
CREATE INDEX IF NOT EXISTS idx_images_active_comparisons
ON images(comparisons DESC) WHERE status IN ('kept', 'maybe');
CREATE INDEX IF NOT EXISTS idx_images_active_comparisons_asc
ON images(comparisons ASC) WHERE status IN ('kept', 'maybe');
CREATE INDEX IF NOT EXISTS idx_images_visible_comparisons_elo
ON images(comparisons ASC, elo DESC)
WHERE status IN ('kept', 'maybe') AND missing_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_images_active_filename
ON images(filename ASC) WHERE status IN ('kept', 'maybe');
CREATE INDEX IF NOT EXISTS idx_images_active_id
ON images(id DESC) WHERE status IN ('kept', 'maybe');
CREATE INDEX IF NOT EXISTS idx_images_active_filepath
ON images(filepath ASC) WHERE status IN ('kept', 'maybe');
CREATE INDEX IF NOT EXISTS idx_images_active_orientation_elo
ON images(orientation, elo DESC) WHERE status IN ('kept', 'maybe');
CREATE INDEX IF NOT EXISTS idx_images_active_visible_orientation_elo
ON images(orientation, elo DESC) WHERE status IN ('kept', 'maybe') AND missing_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_images_active_camera
ON images(camera_make, camera_model) WHERE status IN ('kept', 'maybe');
CREATE INDEX IF NOT EXISTS idx_images_active_lens
ON images(lens) WHERE status IN ('kept', 'maybe');
CREATE INDEX IF NOT EXISTS idx_images_active_date_taken_sort_desc
ON images((date_taken IS NULL), date_taken DESC, id DESC)
WHERE status IN ('kept', 'maybe') AND missing_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_images_active_date_taken_sort_asc
ON images((date_taken IS NULL), date_taken ASC, id ASC)
WHERE status IN ('kept', 'maybe') AND missing_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_images_active_file_size_sort_desc
ON images((file_size IS NULL), file_size DESC, id DESC)
WHERE status IN ('kept', 'maybe') AND missing_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_images_active_file_size_sort_asc
ON images((file_size IS NULL), file_size ASC, id ASC)
WHERE status IN ('kept', 'maybe') AND missing_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_images_active_modified_sort_desc
ON images((file_modified_at IS NULL), file_modified_at DESC, id DESC)
WHERE status IN ('kept', 'maybe') AND missing_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_images_active_modified_sort_asc
ON images((file_modified_at IS NULL), file_modified_at ASC, id ASC)
WHERE status IN ('kept', 'maybe') AND missing_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_images_active_resolution_sort_desc
ON images(((width * height) IS NULL), (width * height) DESC, id DESC)
WHERE status IN ('kept', 'maybe') AND missing_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_images_active_resolution_sort_asc
ON images(((width * height) IS NULL), (width * height) ASC, id ASC)
WHERE status IN ('kept', 'maybe') AND missing_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_images_active_camera_sort_asc
ON images((camera_make IS NULL), camera_make ASC, camera_model ASC, id ASC)
WHERE status IN ('kept', 'maybe') AND missing_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_images_active_camera_sort_desc
ON images((camera_make IS NULL), camera_make DESC, camera_model DESC, id DESC)
WHERE status IN ('kept', 'maybe') AND missing_at IS NULL;

-- Source-aware indexes for the active working set. Source state now determines
-- whether an image is active; status is retained only for old DB compatibility.
CREATE INDEX IF NOT EXISTS idx_images_source_elo
ON images(source_id, elo DESC);
CREATE INDEX IF NOT EXISTS idx_images_source_elo_asc
ON images(source_id, elo ASC);
CREATE INDEX IF NOT EXISTS idx_images_source_comparisons
ON images(source_id, comparisons DESC);
CREATE INDEX IF NOT EXISTS idx_images_source_comparisons_asc
ON images(source_id, comparisons ASC);
CREATE INDEX IF NOT EXISTS idx_images_source_filename
ON images(source_id, filename ASC);
CREATE INDEX IF NOT EXISTS idx_images_source_id_desc
ON images(source_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_images_source_filepath
ON images(source_id, filepath ASC);
CREATE INDEX IF NOT EXISTS idx_images_source_orientation_elo
ON images(source_id, orientation, elo DESC);
CREATE INDEX IF NOT EXISTS idx_images_source_date_taken
ON images(source_id, date_taken DESC);
CREATE INDEX IF NOT EXISTS idx_images_source_file_size
ON images(source_id, file_size DESC);
CREATE INDEX IF NOT EXISTS idx_images_source_file_ext
ON images(source_id, file_ext);
CREATE INDEX IF NOT EXISTS idx_images_source_camera
ON images(source_id, camera_make, camera_model);
CREATE INDEX IF NOT EXISTS idx_images_source_lens
ON images(source_id, lens);
CREATE INDEX IF NOT EXISTS idx_images_source_missing
ON images(source_id, missing_at);
CREATE INDEX IF NOT EXISTS idx_images_source_missing_filepath
ON images(source_id, missing_at, filepath ASC);
CREATE INDEX IF NOT EXISTS idx_images_source_missing_filepath_filename
ON images(source_id, missing_at, filepath, filename);
CREATE INDEX IF NOT EXISTS idx_images_source_missing_id
ON images(source_id, missing_at, id);
CREATE INDEX IF NOT EXISTS idx_images_missing_source_filepath_id
ON images(missing_at, source_id, filepath ASC, id ASC);
CREATE INDEX IF NOT EXISTS idx_images_active_orientation_count
ON images(orientation, missing_at) WHERE status IN ('kept', 'maybe');
CREATE INDEX IF NOT EXISTS idx_images_missing_file_ext_source
ON images(missing_at, file_ext, source_id);
CREATE INDEX IF NOT EXISTS idx_images_missing_file_ext_source_size
ON images(missing_at, file_ext, source_id, file_size);
CREATE INDEX IF NOT EXISTS idx_images_missing_year_source
ON images(missing_at, substr(date_taken, 1, 4), source_id)
WHERE date_taken IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_images_missing_lower_file_ext_source
ON images(missing_at, LOWER(file_ext), source_id)
WHERE file_ext IS NOT NULL AND file_ext != '';
CREATE INDEX IF NOT EXISTS idx_images_missing_date_source
ON images(missing_at, date_taken, source_id);
CREATE INDEX IF NOT EXISTS idx_images_missing_camera_label_source
ON images(missing_at, TRIM(COALESCE(camera_make, '') || ' ' || COALESCE(camera_model, '')), source_id)
WHERE camera_make IS NOT NULL OR camera_model IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_images_missing_lens_source
ON images(missing_at, lens, source_id)
WHERE lens IS NOT NULL AND lens != '';
CREATE INDEX IF NOT EXISTS idx_images_active_gps_count
ON images(source_id, latitude, longitude)
WHERE status IN ('kept', 'maybe') AND missing_at IS NULL
AND latitude IS NOT NULL AND longitude IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_images_rating_signal_cover
ON images(comparisons, propagated_updates, elo);
CREATE INDEX IF NOT EXISTS idx_images_source_missing_rating_signal
ON images(source_id, missing_at, comparisons, propagated_updates, elo);
CREATE TABLE IF NOT EXISTS embeddings (
    image_id INTEGER PRIMARY KEY REFERENCES images(id),
    embedding BLOB NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS embedding_models (
    model_key TEXT PRIMARY KEY,
    model_id TEXT NOT NULL,
    revision TEXT NOT NULL,
    dimension INTEGER NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS embeddings_by_model (
    model_key TEXT NOT NULL REFERENCES embedding_models(model_key),
    image_id INTEGER NOT NULL REFERENCES images(id),
    embedding BLOB NOT NULL,
    dimension INTEGER NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (model_key, image_id)
);

CREATE INDEX IF NOT EXISTS idx_embeddings_by_model_image_id
ON embeddings_by_model(image_id);

CREATE TABLE IF NOT EXISTS deep_search_queries (
    query_key TEXT PRIMARY KEY,
    query TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'search',
    use_count INTEGER NOT NULL DEFAULT 0,
    pinned INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL DEFAULT (strftime('%s', 'now')),
    last_used_at REAL DEFAULT NULL,
    updated_at REAL NOT NULL DEFAULT (strftime('%s', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_deep_search_queries_pinned_updated
ON deep_search_queries(pinned DESC, updated_at DESC);

CREATE TABLE IF NOT EXISTS deep_search_query_embeddings (
    model_key TEXT NOT NULL REFERENCES embedding_models(model_key),
    query_key TEXT NOT NULL REFERENCES deep_search_queries(query_key),
    embedding BLOB NOT NULL,
    dimension INTEGER NOT NULL,
    created_at REAL NOT NULL DEFAULT (strftime('%s', 'now')),
    updated_at REAL NOT NULL DEFAULT (strftime('%s', 'now')),
    PRIMARY KEY (model_key, query_key)
);

CREATE INDEX IF NOT EXISTS idx_deep_search_query_embeddings_query
ON deep_search_query_embeddings(query_key);

CREATE TABLE IF NOT EXISTS cache_entries (
    cache_root TEXT NOT NULL,
    size TEXT NOT NULL,
    image_id INTEGER NOT NULL REFERENCES images(id),
    path TEXT NOT NULL,
    source_signature TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    last_accessed REAL NOT NULL,
    created_at REAL NOT NULL,
    PRIMARY KEY (cache_root, size, image_id)
);

CREATE INDEX IF NOT EXISTS idx_cache_entries_root_size_access
ON cache_entries(cache_root, size, last_accessed);
CREATE INDEX IF NOT EXISTS idx_cache_entries_root_size_bytes
ON cache_entries(cache_root, size, size_bytes);

-- For pregen candidate batch query (ORDER BY filepath ASC with status filter)
CREATE INDEX IF NOT EXISTS idx_images_status_filepath ON images(status, filepath ASC);

-- For LRU eviction ordering (avoids TEMP B-TREE sort during budget enforcement)
CREATE INDEX IF NOT EXISTS idx_cache_entries_root_size_accessed_id
ON cache_entries(cache_root, size, last_accessed, image_id);

CREATE TABLE IF NOT EXISTS cache_metadata (
    cache_root TEXT PRIMARY KEY,
    thumb_config_signature TEXT NOT NULL,
    thumb_config_changed_at REAL NOT NULL,
    replace_stale_thumbnails INTEGER NOT NULL DEFAULT 0
);
"""

_stats_cache = {"data": None, "expires": 0}
_stats_inflight_task = None
_catalog_image_counts_cache = {"data": None, "expires": 0}
_filter_options_cache = {"data": None, "expires": 0}
_filter_options_refreshing = False
_catalog_sources_cache = {"data": None, "expires": 0}
_catalog_summary_cache = {"data": None, "expires": 0}
_catalog_light_summary_cache = {"data": None, "expires": 0}
_date_groups_cache: dict[tuple, dict] = {}
_date_groups_refreshing: set[tuple] = set()
_map_markers_cache: dict[tuple, dict] = {}
_ranking_count_cache: dict[tuple, dict] = {}
_visible_pairing_pool_counts_cache: dict[tuple, dict] = {}
_cached_image_ids_cache: dict[tuple[str, str], dict] = {}
_cache_entry_count_cache: dict[tuple[str, str], dict] = {}
_rankable_image_ids_cache = {"ids": frozenset(), "expires": 0}
_embedding_count_cache = {"key": None, "value": None, "expires": 0}
_ensured_embedding_model_keys: set[str] = set()
_ai_status_counts_cache = {"data": None, "expires": 0}
_active_source_ids_cache = {"ids": frozenset(), "expires": 0}
_past_matchups_cache = {"data": None, "signature": None}
CACHED_IMAGE_IDS_TTL_SECONDS = 30.0
RANKING_COUNT_CACHE_TTL_SECONDS = 2.0
VISIBLE_PAIRING_POOL_COUNTS_TTL_SECONDS = 30.0
CACHE_ENTRY_COUNT_TTL_SECONDS = 30.0
RANKING_VISIBLE_ID_FILTER_LIMIT = 5000
RANKING_CACHE_FIRST_VISIBLE_LIMIT = 12000
STATS_CACHE_TTL_SECONDS = 30.0
EMBEDDING_COUNT_CACHE_TTL_SECONDS = 10.0
AI_STATUS_COUNTS_CACHE_TTL_SECONDS = 30.0
ACTIVE_SOURCE_IDS_TTL_SECONDS = 5.0
CATALOG_CACHE_TTL_SECONDS = 10.0
FACET_CACHE_TTL_SECONDS = 30.0
FILTER_OPTIONS_CACHE_TTL_SECONDS = 300.0


def normalize_source_path(path: str) -> str:
    """Return the canonical local path used as a catalog source key."""
    return os.path.realpath(os.path.abspath(os.path.expanduser(path or "")))


def active_embedding_config() -> dict:
    return settings.fast_search_embedding_config()


def active_embedding_model_key() -> str:
    return active_embedding_config()["model_key"]


def source_display_name(path: str) -> str:
    normalized = normalize_source_path(path)
    return os.path.basename(normalized.rstrip(os.sep)) or normalized


def active_source_join(image_alias: str = "i", source_alias: str = "s") -> str:
    return f"JOIN catalog_sources {source_alias} ON {source_alias}.id = {image_alias}.source_id"


def active_source_condition(source_alias: str = "s") -> str:
    return f"{source_alias}.included = 1 AND {source_alias}.online = 1"


def active_image_condition(image_alias: str = "i", source_alias: str = "s") -> str:
    return (
        f"{source_alias}.included = 1 "
        f"AND {source_alias}.online = 1 "
        f"AND {image_alias}.missing_at IS NULL"
    )


def _chunked(values: list[int], chunk_size: int = 500):
    for start in range(0, len(values), chunk_size):
        yield values[start:start + chunk_size]


def _invalidate_stats_cache():
    _stats_cache["data"] = None
    _stats_cache["expires"] = 0
    _invalidate_past_matchups_cache()
    _catalog_image_counts_cache["data"] = None
    _catalog_image_counts_cache["expires"] = 0
    _invalidate_catalog_cache()
    _invalidate_facet_caches()
    _invalidate_ranking_count_cache()
    _invalidate_rankable_image_ids_cache()
    _invalidate_embedding_count_cache()
    _invalidate_active_source_ids_cache()


def _invalidate_ai_status_counts_cache():
    _ai_status_counts_cache["data"] = None
    _ai_status_counts_cache["expires"] = 0


def _invalidate_catalog_summary_cache():
    _catalog_summary_cache["data"] = None
    _catalog_summary_cache["expires"] = 0
    _catalog_light_summary_cache["data"] = None
    _catalog_light_summary_cache["expires"] = 0


def _invalidate_rating_stats_cache():
    _stats_cache["data"] = None
    _stats_cache["expires"] = 0
    _invalidate_past_matchups_cache()
    _invalidate_catalog_summary_cache()
    _invalidate_rating_facet_caches()
    _invalidate_rating_ranking_count_cache()
    _invalidate_ai_status_counts_cache()


def _increment_cached_int(mapping: dict, key: str, delta: int, *, cap: int | None = None):
    if key not in mapping:
        return
    value = max(0, int(mapping.get(key) or 0) + int(delta))
    if cap is not None:
        value = min(value, cap)
    mapping[key] = value


def _patch_direct_rating_stats_cache(pair_delta: int, rated_image_delta: int):
    """Keep hot stats caches valid after direct Compare/Mosaic writes."""
    pair_delta = int(pair_delta or 0)
    rated_image_delta = int(rated_image_delta or 0)
    active_cap = None
    if _stats_cache["data"] and _time.time() < _stats_cache["expires"]:
        stats = _stats_cache["data"]
        active_cap = int(stats.get("active_images") or stats.get("total_images") or 0)
        for key in (
            "total_comparisons",
            "total_catalog_comparisons",
            "direct_comparison_rows",
            "direct_catalog_comparison_rows",
            "ranking_signal_count",
            "catalog_ranking_signal_count",
        ):
            _increment_cached_int(stats, key, pair_delta)
        _increment_cached_int(stats, "rated_images", rated_image_delta, cap=active_cap)
        _invalidate_catalog_summary_cache()
    else:
        _stats_cache["data"] = None
        _stats_cache["expires"] = 0
        _invalidate_catalog_summary_cache()

    if _ai_status_counts_cache["data"] and _time.time() < _ai_status_counts_cache["expires"]:
        ai_counts = _ai_status_counts_cache["data"]
        if active_cap is None:
            active_cap = int(ai_counts.get("total_images") or 0)
        for key in ("direct_comparison_rows", "ranking_signal_count"):
            _increment_cached_int(ai_counts, key, pair_delta)
        _increment_cached_int(ai_counts, "rated_images", rated_image_delta, cap=active_cap)
    else:
        _invalidate_ai_status_counts_cache()

    _invalidate_rating_facet_caches()
    _invalidate_rating_ranking_count_cache()


def _invalidate_filter_options_cache():
    # Keep the last payload available for stale-while-refresh reads. Metadata
    # scanning can invalidate this often, and a cold facet rebuild is visible.
    _filter_options_cache["expires"] = 0


def clear_filter_options_cache():
    global _filter_options_refreshing
    _filter_options_cache["data"] = None
    _filter_options_cache["expires"] = 0
    _filter_options_refreshing = False


def _invalidate_catalog_cache():
    _catalog_sources_cache["data"] = None
    _catalog_sources_cache["expires"] = 0
    _invalidate_catalog_summary_cache()


def _invalidate_facet_caches():
    _date_groups_cache.clear()
    _date_groups_refreshing.clear()
    _map_markers_cache.clear()


def _invalidate_visible_facet_caches(cache_root: str | None = None, size: str | None = None):
    if cache_root is None and size is None:
        _invalidate_facet_caches()
        return
    for cache in (_date_groups_cache, _map_markers_cache):
        for key in list(cache.keys()):
            key_size = key[9]
            key_root = key[10]
            if key_size and key_root and _cache_scope_matches(key_root, key_size, cache_root, size):
                cache.pop(key, None)
                _date_groups_refreshing.discard(key)


def _invalidate_rating_facet_caches():
    for cache in (_date_groups_cache, _map_markers_cache):
        for key in list(cache.keys()):
            _orientation, compared, min_stars, *_rest = key
            if compared or int(min_stars or 0) > 0:
                cache.pop(key, None)
                _date_groups_refreshing.discard(key)


def _invalidate_ranking_count_cache():
    _ranking_count_cache.clear()
    _visible_pairing_pool_counts_cache.clear()


def _cache_scope_matches(cache_root: str, size: str, target_root: str | None, target_size: str | None) -> bool:
    if target_root is not None and cache_root != target_root:
        return False
    if target_size is not None and size != target_size:
        return False
    return True


def _invalidate_visible_cache_dependent_counts(cache_root: str | None = None, size: str | None = None):
    for key in list(_ranking_count_cache.keys()):
        key_size = key[9]
        key_root = key[10]
        if key_size and key_root and _cache_scope_matches(key_root, key_size, cache_root, size):
            _ranking_count_cache.pop(key, None)
    for key in list(_visible_pairing_pool_counts_cache.keys()):
        key_root, key_size = key[:2]
        if _cache_scope_matches(key_root, key_size, cache_root, size):
            _visible_pairing_pool_counts_cache.pop(key, None)


def _invalidate_rating_ranking_count_cache():
    for key in list(_ranking_count_cache.keys()):
        _orientation, compared, min_stars, *_rest = key
        if compared or int(min_stars or 0) > 0:
            _ranking_count_cache.pop(key, None)


def invalidate_cached_image_ids_cache(cache_root: str | None = None, size: str | None = None):
    _invalidate_visible_cache_dependent_counts(cache_root, size)
    _invalidate_visible_facet_caches(cache_root, size)
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
    """Patch hot cached-ID sets after append-only cache writes."""
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


def _invalidate_rankable_image_ids_cache():
    _rankable_image_ids_cache["ids"] = frozenset()
    _rankable_image_ids_cache["expires"] = 0


def _invalidate_embedding_count_cache():
    _embedding_count_cache["key"] = None
    _embedding_count_cache["value"] = None
    _embedding_count_cache["expires"] = 0
    _invalidate_ai_status_counts_cache()


def _invalidate_active_source_ids_cache():
    _active_source_ids_cache["ids"] = frozenset()
    _active_source_ids_cache["expires"] = 0


def _invalidate_past_matchups_cache():
    _past_matchups_cache["data"] = None
    _past_matchups_cache["signature"] = None


def invalidate_stats_cache():
    _invalidate_stats_cache()


def invalidate_rating_stats_cache():
    _invalidate_rating_stats_cache()


async def get_db() -> aiosqlite.Connection:
    db = await aiosqlite.connect(DB_PATH, timeout=30)
    db.row_factory = aiosqlite.Row
    return db


async def _ensure_catalog_source(conn, path: str, *, included: bool = True, last_scan_at=None):
    normalized = normalize_source_path(path)
    display_name = source_display_name(normalized)
    online = 1 if os.path.isdir(normalized) else 0
    now = _time.time()
    await conn.execute(
        "INSERT INTO catalog_sources "
        "(path, display_name, included, online, created_at, last_scan_at, last_seen_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(path) DO UPDATE SET "
        "display_name = excluded.display_name, "
        "included = CASE WHEN excluded.included = 1 THEN 1 ELSE catalog_sources.included END, "
        "online = excluded.online, "
        "last_scan_at = COALESCE(excluded.last_scan_at, catalog_sources.last_scan_at), "
        "last_seen_at = excluded.last_seen_at, "
        "removed_at = CASE WHEN excluded.included = 1 THEN NULL ELSE catalog_sources.removed_at END",
        (normalized, display_name, 1 if included else 0, online, now, last_scan_at, now),
    )
    cursor = await conn.execute("SELECT * FROM catalog_sources WHERE path = ?", (normalized,))
    return await cursor.fetchone()


async def _update_source_counts(conn, source_id: int | None = None):
    params = []
    where = ""
    if source_id is not None:
        where = " WHERE id = ?"
        params.append(source_id)
    await conn.execute(
        "UPDATE catalog_sources SET image_count = ("
        "  SELECT COUNT(*) FROM images WHERE images.source_id = catalog_sources.id"
        f"){where}",
        params,
    )
    await conn.execute(
        "UPDATE catalog_sources SET active_image_count = CASE "
        "WHEN included = 1 THEN ("
        "  SELECT COUNT(*) FROM images "
        "  WHERE images.source_id = catalog_sources.id AND images.missing_at IS NULL"
        ") ELSE 0 END"
        f"{where}",
        params,
    )


async def _refresh_source_online_states_on_conn(conn) -> bool:
    cursor = await conn.execute("SELECT id, path, online FROM catalog_sources")
    rows = await cursor.fetchall()
    now = _time.time()
    updates = []
    for row in rows:
        online = 1 if os.path.isdir(row["path"]) else 0
        if int(row["online"] or 0) != online:
            updates.append((online, now, row["id"]))
    if not updates:
        return False
    await conn.executemany(
        "UPDATE catalog_sources SET online = ?, last_seen_at = ? WHERE id = ?",
        updates,
    )
    await _update_source_counts(conn)
    return True


async def _normalize_legacy_image_state(conn):
    cursor = await conn.execute(
        "UPDATE images SET flag = 'rejected' "
        "WHERE status = 'rejected' "
        "AND (flag IS NULL OR flag = '' OR flag = 'unflagged')"
    )
    if cursor.rowcount:
        _invalidate_filter_options_cache()

    cursor = await conn.execute(
        "UPDATE images SET flag = 'unflagged' WHERE flag IS NULL OR flag = ''"
    )
    if cursor.rowcount:
        _invalidate_filter_options_cache()

    # Status used to control membership in older versions. Sources now own
    # membership, so normalize old statuses after preserving rejection as a flag.
    cursor = await conn.execute(
        "UPDATE images SET status = 'kept' WHERE status IS NULL OR status != 'kept'"
    )
    if cursor.rowcount:
        _invalidate_filter_options_cache()


async def _migrate_catalog_sources(conn):
    await _normalize_legacy_image_state(conn)

    await conn.execute(
        "UPDATE images SET source_id = NULL "
        "WHERE source_id IS NOT NULL "
        "AND NOT EXISTS (SELECT 1 FROM catalog_sources WHERE catalog_sources.id = images.source_id)"
    )

    cursor = await conn.execute(
        "SELECT filepath FROM images WHERE source_id IS NULL ORDER BY filepath"
    )
    rows = await cursor.fetchall()
    if rows:
        dirs = [os.path.dirname(row["filepath"]) for row in rows if row["filepath"]]
        try:
            root = os.path.commonpath(dirs) if dirs else os.path.expanduser("~/Pictures")
        except ValueError:
            root = dirs[0] if dirs else os.path.expanduser("~/Pictures")
        source = await _ensure_catalog_source(conn, root, included=True)
        await conn.execute(
            "UPDATE images SET source_id = ? WHERE source_id IS NULL",
            (source["id"],),
        )

    await _update_source_counts(conn)


async def _table_columns(conn, table: str) -> set[str]:
    cursor = await conn.execute(f"PRAGMA table_info({table})")
    return {row["name"] for row in await cursor.fetchall()}


async def _schema_is_current(conn) -> bool:
    cursor = await conn.execute("PRAGMA user_version")
    row = await cursor.fetchone()
    schema_version = int(row[0] if row is not None else 0)

    cursor = await conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'"
    )
    tables = {row["name"] for row in await cursor.fetchall()}
    required_tables = {
        "catalog_sources",
        "images",
        "images_metadata_fts",
        "comparisons",
        "embeddings",
        "embedding_models",
        "embeddings_by_model",
        "deep_search_queries",
        "deep_search_query_embeddings",
        "cache_entries",
        "cache_metadata",
    }
    if not required_tables.issubset(tables):
        return False

    required_columns = {
        "images": {
            "source_id", "orientation", "flag", "propagated_updates", "predicted_elo",
            "uncertainty", "aspect_ratio", "date_taken", "camera_make", "camera_model",
            "lens", "file_ext", "file_size", "file_modified_at", "width", "height",
            "metadata_scanned_at", "latitude", "longitude", "metadata_version", "missing_at",
        },
        "catalog_sources": {
            "display_name", "included", "online", "image_count", "active_image_count",
            "created_at", "last_scan_at", "last_seen_at", "removed_at",
        },
        "comparisons": {"action_id"},
        "cache_metadata": {"replace_stale_thumbnails"},
    }
    for table, columns in required_columns.items():
        existing = await _table_columns(conn, table)
        if not columns.issubset(existing):
            return False

    cursor = await conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'index'"
    )
    indexes = {row["name"] for row in await cursor.fetchall()}
    required_indexes = {
        "idx_catalog_sources_active",
        "idx_images_missing_source_filepath_id",
        "idx_images_source_missing_id",
        "idx_images_source_missing_rating_signal",
        "idx_images_active_visible_orientation_elo",
        "idx_images_visible_comparisons_elo",
        "idx_images_missing_file_ext_source_size",
        "idx_images_active_camera_sort_desc",
        "idx_images_rating_signal_cover",
        "idx_embeddings_by_model_image_id",
        "idx_deep_search_queries_pinned_updated",
        "idx_deep_search_query_embeddings_query",
        "idx_cache_entries_root_size_bytes",
        "idx_cache_entries_root_size_accessed_id",
    }
    if not required_indexes.issubset(indexes):
        return False

    for sql in (
        "SELECT 1 FROM images WHERE source_id IS NULL LIMIT 1",
        "SELECT 1 FROM images WHERE COALESCE(status, '') != 'kept' LIMIT 1",
    ):
        cursor = await conn.execute(sql)
        if await cursor.fetchone():
            return False

    await conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    await conn.commit()
    return schema_version >= SCHEMA_VERSION


async def _check_embedding_dimension(conn):
    """Legacy no-op kept for callers from older code.

    Model upgrades now preserve old vectors by writing each model to
    embeddings_by_model instead of clearing the original embeddings table.
    """
    await _ensure_embedding_model_tables(conn)


async def _ensure_embedding_model_tables(conn):
    config = active_embedding_config()
    active_model_key = config["model_key"]
    legacy_model_id = settings.DEFAULT_SETTINGS["embed_model_id"]
    legacy_revision = settings.DEFAULT_SETTINGS["embed_model_revision"]
    legacy_dimension = int(settings.DEFAULT_SETTINGS["embed_model_dim"])
    legacy_model_key = settings.embedding_model_key(settings.DEFAULT_SETTINGS)
    if active_model_key in _ensured_embedding_model_keys:
        cursor = await conn.execute(
            "SELECT 1 FROM embeddings e "
            "WHERE NOT EXISTS ("
            "  SELECT 1 FROM embeddings_by_model bm "
            "  WHERE bm.model_key = ? AND bm.image_id = e.image_id"
            ") LIMIT 1",
            (legacy_model_key,),
        )
        if await cursor.fetchone() is None:
            return
    cursor = await conn.execute(
        "SELECT model_key FROM embedding_models WHERE model_key IN (?, ?)",
        (active_model_key, legacy_model_key),
    )
    existing_keys = {row["model_key"] for row in await cursor.fetchall()}
    if active_model_key in existing_keys and legacy_model_key in existing_keys:
        cursor = await conn.execute(
            "SELECT 1 FROM embeddings e "
            "WHERE NOT EXISTS ("
            "  SELECT 1 FROM embeddings_by_model bm "
            "  WHERE bm.model_key = ? AND bm.image_id = e.image_id"
            ") LIMIT 1",
            (legacy_model_key,),
        )
        if await cursor.fetchone() is None:
            _ensured_embedding_model_keys.add(active_model_key)
            return
    await conn.execute(
        "INSERT OR IGNORE INTO embedding_models "
        "(model_key, model_id, revision, dimension) VALUES (?, ?, ?, ?)",
        (
            active_model_key,
            config["model_id"],
            config["revision"],
            config["dimension"],
        ),
    )

    await conn.execute(
        "INSERT OR IGNORE INTO embedding_models "
        "(model_key, model_id, revision, dimension) VALUES (?, ?, ?, ?)",
        (legacy_model_key, legacy_model_id, legacy_revision, legacy_dimension),
    )
    await conn.execute(
        "INSERT OR IGNORE INTO embeddings_by_model "
        "(model_key, image_id, embedding, dimension, created_at) "
        "SELECT ?, image_id, embedding, ?, created_at FROM embeddings",
        (legacy_model_key, legacy_dimension),
    )
    await conn.commit()
    _ensured_embedding_model_keys.add(active_model_key)


async def _ensure_embedding_model_row(conn, config: dict):
    await conn.execute(
        "INSERT OR IGNORE INTO embedding_models "
        "(model_key, model_id, revision, dimension) VALUES (?, ?, ?, ?)",
        (
            config["model_key"],
            config["model_id"],
            config["revision"],
            int(config["dimension"]),
        ),
    )


def normalize_deep_search_query(query: str) -> str:
    return " ".join(str(query or "").split())


def deep_search_query_key(query: str) -> str:
    return normalize_deep_search_query(query).casefold()


async def record_deep_search_query(query: str, *, source: str = "search", pinned: bool = False) -> dict | None:
    normalized = normalize_deep_search_query(query)
    if not normalized:
        return None
    key = normalized.casefold()
    now = _time.time()
    source = source if source in {"search", "settings"} else "search"
    pinned_value = 1 if pinned else 0
    db = await get_db()
    try:
        await db.execute(
            "INSERT INTO deep_search_queries "
            "(query_key, query, source, use_count, pinned, created_at, last_used_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(query_key) DO UPDATE SET "
            "query = excluded.query, "
            "source = CASE WHEN excluded.pinned = 1 THEN excluded.source ELSE deep_search_queries.source END, "
            "use_count = deep_search_queries.use_count + excluded.use_count, "
            "pinned = CASE WHEN excluded.pinned = 1 THEN 1 ELSE deep_search_queries.pinned END, "
            "last_used_at = COALESCE(excluded.last_used_at, deep_search_queries.last_used_at), "
            "updated_at = excluded.updated_at",
            (
                key,
                normalized,
                source,
                0 if pinned else 1,
                pinned_value,
                now,
                None if pinned else now,
                now,
            ),
        )
        await db.commit()
        return {"query_key": key, "query": normalized}
    finally:
        await db.close()


async def sync_deep_search_terms(terms: list[str] | tuple[str, ...] | None):
    normalized_terms = settings.normalize_deep_search_terms(terms or [])
    db = await get_db()
    try:
        await db.execute("UPDATE deep_search_queries SET pinned = 0 WHERE pinned = 1")
        now = _time.time()
        for term in normalized_terms:
            key = term.casefold()
            await db.execute(
                "INSERT INTO deep_search_queries "
                "(query_key, query, source, use_count, pinned, created_at, last_used_at, updated_at) "
                "VALUES (?, ?, 'settings', 0, 1, ?, NULL, ?) "
                "ON CONFLICT(query_key) DO UPDATE SET "
                "query = excluded.query, "
                "source = 'settings', "
                "pinned = 1, "
                "updated_at = excluded.updated_at",
                (key, term, now, now),
            )
        await db.commit()
    finally:
        await db.close()


async def get_deep_search_query_embedding(query: str, model_key: str) -> bytes | None:
    key = deep_search_query_key(query)
    if not key:
        return None
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT embedding FROM deep_search_query_embeddings "
            "WHERE model_key = ? AND query_key = ?",
            (model_key, key),
        )
        row = await cursor.fetchone()
        return row["embedding"] if row else None
    finally:
        await db.close()


async def store_deep_search_query_embedding(config: dict, query: str, blob: bytes):
    normalized = normalize_deep_search_query(query)
    if not normalized:
        return
    key = normalized.casefold()
    now = _time.time()
    db = await get_db()
    try:
        await _ensure_embedding_model_row(db, config)
        await db.execute(
            "INSERT INTO deep_search_queries "
            "(query_key, query, source, use_count, pinned, created_at, updated_at) "
            "VALUES (?, ?, 'search', 0, 0, ?, ?) "
            "ON CONFLICT(query_key) DO UPDATE SET "
            "query = excluded.query, "
            "updated_at = excluded.updated_at",
            (key, normalized, now, now),
        )
        await db.execute(
            "INSERT OR REPLACE INTO deep_search_query_embeddings "
            "(model_key, query_key, embedding, dimension, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, COALESCE((SELECT created_at FROM deep_search_query_embeddings "
            "WHERE model_key = ? AND query_key = ?), ?), ?)",
            (
                config["model_key"],
                key,
                blob,
                int(config["dimension"]),
                config["model_key"],
                key,
                now,
                now,
            ),
        )
        await db.commit()
        _notify_deep_search_query_embedding_stored(config["model_key"], normalized)
    finally:
        await db.close()


async def get_pending_deep_search_queries(config: dict, terms=None, limit: int = 16) -> list[dict]:
    if terms is not None:
        await sync_deep_search_terms(terms)
    limit = max(1, min(int(limit or 16), 128))
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT q.query_key, q.query, q.source, q.use_count, q.pinned, q.last_used_at, q.updated_at "
            "FROM deep_search_queries q "
            "WHERE NOT EXISTS ("
            "  SELECT 1 FROM deep_search_query_embeddings e "
            "  WHERE e.model_key = ? AND e.query_key = q.query_key"
            ") "
            "ORDER BY q.pinned DESC, q.last_used_at IS NULL ASC, q.last_used_at DESC, q.updated_at DESC "
            "LIMIT ?",
            (config["model_key"], limit),
        )
        return [dict(row) for row in await cursor.fetchall()]
    finally:
        await db.close()


async def get_deep_search_cache_status(config: dict, terms=None) -> dict:
    if terms is not None:
        await sync_deep_search_terms(terms)
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT COUNT(*) AS total, "
            "SUM(CASE WHEN pinned = 1 THEN 1 ELSE 0 END) AS pinned, "
            "SUM(CASE WHEN use_count > 0 THEN 1 ELSE 0 END) AS used "
            "FROM deep_search_queries"
        )
        query_counts = await cursor.fetchone()
        cursor = await db.execute(
            "SELECT COUNT(*) AS embedded FROM deep_search_query_embeddings WHERE model_key = ?",
            (config["model_key"],),
        )
        embedded = int((await cursor.fetchone())["embedded"] or 0)
        total = int(query_counts["total"] or 0)
        return {
            "model_key": config["model_key"],
            "model_id": config["model_id"],
            "dimension": int(config["dimension"]),
            "total_queries": total,
            "pinned_queries": int(query_counts["pinned"] or 0),
            "used_queries": int(query_counts["used"] or 0),
            "embedded_queries": embedded,
            "pending_queries": max(0, total - embedded),
        }
    finally:
        await db.close()


async def list_deep_search_queries(config: dict, limit: int = 200) -> list[dict]:
    limit = max(1, min(int(limit or 200), 500))
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT q.query, q.source, q.use_count, q.pinned, q.last_used_at, q.updated_at, "
            "e.updated_at AS embedded_at "
            "FROM deep_search_queries q "
            "LEFT JOIN deep_search_query_embeddings e "
            "ON e.model_key = ? AND e.query_key = q.query_key "
            "ORDER BY q.pinned DESC, e.updated_at IS NULL DESC, q.last_used_at DESC, q.updated_at DESC "
            "LIMIT ?",
            (config["model_key"], limit),
        )
        rows = []
        for row in await cursor.fetchall():
            item = dict(row)
            item["cached"] = item.get("embedded_at") is not None
            rows.append(item)
        return rows
    finally:
        await db.close()


async def _ensure_metadata_fts(conn):
    try:
        cursor = await conn.execute("SELECT COUNT(*) AS count FROM images")
        image_count = int((await cursor.fetchone())["count"] or 0)
        cursor = await conn.execute("SELECT COUNT(*) AS count FROM images_metadata_fts_docsize")
        fts_count = int((await cursor.fetchone())["count"] or 0)
        if image_count != fts_count:
            await conn.execute("INSERT INTO images_metadata_fts(images_metadata_fts) VALUES('rebuild')")
    except Exception:
        # FTS is a speed path only; metadata search falls back to regular SQL.
        pass


async def init_db():
    db_exists = os.path.exists(DB_PATH)
    db = await get_db()
    try:
        if not db_exists:
            await db.execute("PRAGMA journal_mode=WAL")
        if db_exists and await _schema_is_current(db):
            await _normalize_legacy_image_state(db)
            await _refresh_source_online_states_on_conn(db)
            await _check_embedding_dimension(db)
            await _ensure_metadata_fts(db)
            await db.commit()
            return
        if db_exists:
            # Existing databases may predate columns now referenced by indexes in
            # SCHEMA. Add those columns first so CREATE INDEX IF NOT EXISTS is safe.
            await db.execute(
                "CREATE TABLE IF NOT EXISTS catalog_sources ("
                "id INTEGER PRIMARY KEY, "
                "path TEXT NOT NULL UNIQUE, "
                "display_name TEXT NOT NULL, "
                "included INTEGER NOT NULL DEFAULT 1, "
                "online INTEGER NOT NULL DEFAULT 1, "
                "image_count INTEGER NOT NULL DEFAULT 0, "
                "active_image_count INTEGER NOT NULL DEFAULT 0, "
                "created_at REAL NOT NULL DEFAULT (strftime('%s', 'now')), "
                "last_scan_at REAL DEFAULT NULL, "
                "last_seen_at REAL DEFAULT NULL, "
                "removed_at REAL DEFAULT NULL"
                ")"
            )
            cursor = await db.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'images'"
            )
            if await cursor.fetchone():
                for col, defn in [
                    ("source_id", "INTEGER REFERENCES catalog_sources(id)"),
                    ("orientation", "TEXT DEFAULT NULL"),
                    ("flag", "TEXT DEFAULT 'unflagged'"),
                    ("propagated_updates", "INTEGER DEFAULT 0"),
                    ("predicted_elo", "REAL DEFAULT NULL"),
                    ("uncertainty", "REAL DEFAULT NULL"),
                    ("aspect_ratio", "REAL DEFAULT NULL"),
                    ("date_taken", "TEXT DEFAULT NULL"),
                    ("camera_make", "TEXT DEFAULT NULL"),
                    ("camera_model", "TEXT DEFAULT NULL"),
                    ("lens", "TEXT DEFAULT NULL"),
                    ("file_ext", "TEXT DEFAULT NULL"),
                    ("file_size", "INTEGER DEFAULT NULL"),
                    ("file_modified_at", "REAL DEFAULT NULL"),
                    ("width", "INTEGER DEFAULT NULL"),
                    ("height", "INTEGER DEFAULT NULL"),
                    ("metadata_scanned_at", "REAL DEFAULT NULL"),
                    ("latitude", "REAL DEFAULT NULL"),
                    ("longitude", "REAL DEFAULT NULL"),
                    ("metadata_version", "INTEGER DEFAULT NULL"),
                    ("missing_at", "REAL DEFAULT NULL"),
                ]:
                    try:
                        await db.execute(f"ALTER TABLE images ADD COLUMN {col} {defn}")
                    except Exception:
                        pass
            cursor = await db.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'comparisons'"
            )
            if await cursor.fetchone():
                try:
                    await db.execute(
                        "ALTER TABLE comparisons "
                        "ADD COLUMN action_id TEXT DEFAULT NULL"
                    )
                except Exception:
                    pass
        await db.executescript(SCHEMA)
        # Migrations: add columns if missing
        for col, defn in [
            ("source_id", "INTEGER REFERENCES catalog_sources(id)"),
            ("orientation", "TEXT DEFAULT NULL"),
            ("flag", "TEXT DEFAULT 'unflagged'"),
            ("propagated_updates", "INTEGER DEFAULT 0"),
            ("predicted_elo", "REAL DEFAULT NULL"),
            ("uncertainty", "REAL DEFAULT NULL"),
            ("aspect_ratio", "REAL DEFAULT NULL"),
            ("date_taken", "TEXT DEFAULT NULL"),
            ("camera_make", "TEXT DEFAULT NULL"),
            ("camera_model", "TEXT DEFAULT NULL"),
            ("lens", "TEXT DEFAULT NULL"),
            ("file_ext", "TEXT DEFAULT NULL"),
            ("file_size", "INTEGER DEFAULT NULL"),
            ("file_modified_at", "REAL DEFAULT NULL"),
            ("width", "INTEGER DEFAULT NULL"),
            ("height", "INTEGER DEFAULT NULL"),
            ("metadata_scanned_at", "REAL DEFAULT NULL"),
            ("latitude", "REAL DEFAULT NULL"),
            ("longitude", "REAL DEFAULT NULL"),
            ("metadata_version", "INTEGER DEFAULT NULL"),
            ("missing_at", "REAL DEFAULT NULL"),
        ]:
            try:
                await db.execute(f"ALTER TABLE images ADD COLUMN {col} {defn}")
            except Exception:
                pass  # Column already exists
        try:
            await db.execute(
                "ALTER TABLE cache_metadata "
                "ADD COLUMN replace_stale_thumbnails INTEGER NOT NULL DEFAULT 0"
            )
        except Exception:
            pass  # Column already exists
        try:
            await db.execute(
                "ALTER TABLE comparisons "
                "ADD COLUMN action_id TEXT DEFAULT NULL"
            )
        except Exception:
            pass  # Column already exists
        for col, defn in [
            ("display_name", "TEXT DEFAULT ''"),
            ("included", "INTEGER NOT NULL DEFAULT 1"),
            ("online", "INTEGER NOT NULL DEFAULT 1"),
            ("image_count", "INTEGER NOT NULL DEFAULT 0"),
            ("active_image_count", "INTEGER NOT NULL DEFAULT 0"),
            ("created_at", "REAL DEFAULT NULL"),
            ("last_scan_at", "REAL DEFAULT NULL"),
            ("last_seen_at", "REAL DEFAULT NULL"),
            ("removed_at", "REAL DEFAULT NULL"),
        ]:
            try:
                await db.execute(f"ALTER TABLE catalog_sources ADD COLUMN {col} {defn}")
            except Exception:
                pass  # Column already exists
        await db.execute("CREATE INDEX IF NOT EXISTS idx_images_flag ON images(flag)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_comparisons_action_id ON comparisons(action_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_comparisons_loser ON comparisons(loser_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_images_source_id ON images(source_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_catalog_sources_path ON catalog_sources(path)")
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_cache_entries_root_size_bytes "
            "ON cache_entries(cache_root, size, size_bytes)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_catalog_sources_active "
            "ON catalog_sources(included, online)"
        )
        for name, sql in [
            ("idx_images_source_elo", "ON images(source_id, elo DESC)"),
            ("idx_images_source_elo_asc", "ON images(source_id, elo ASC)"),
            ("idx_images_source_comparisons", "ON images(source_id, comparisons DESC)"),
            ("idx_images_source_comparisons_asc", "ON images(source_id, comparisons ASC)"),
            ("idx_images_source_filename", "ON images(source_id, filename ASC)"),
            ("idx_images_source_id_desc", "ON images(source_id, id DESC)"),
            ("idx_images_source_filepath", "ON images(source_id, filepath ASC)"),
            ("idx_images_source_orientation_elo", "ON images(source_id, orientation, elo DESC)"),
            ("idx_images_source_date_taken", "ON images(source_id, date_taken DESC)"),
            ("idx_images_source_file_size", "ON images(source_id, file_size DESC)"),
            ("idx_images_source_file_ext", "ON images(source_id, file_ext)"),
            ("idx_images_source_camera", "ON images(source_id, camera_make, camera_model)"),
            ("idx_images_source_lens", "ON images(source_id, lens)"),
            ("idx_images_source_missing", "ON images(source_id, missing_at)"),
            ("idx_images_source_missing_filepath", "ON images(source_id, missing_at, filepath ASC)"),
            ("idx_images_source_missing_filepath_filename", "ON images(source_id, missing_at, filepath, filename)"),
            ("idx_images_source_missing_id", "ON images(source_id, missing_at, id)"),
            ("idx_images_missing_source_filepath_id", "ON images(missing_at, source_id, filepath ASC, id ASC)"),
            (
                "idx_images_active_orientation_count",
                "ON images(orientation, missing_at) WHERE status IN ('kept', 'maybe')",
            ),
            ("idx_images_missing_file_ext_source", "ON images(missing_at, file_ext, source_id)"),
            ("idx_images_missing_file_ext_source_size", "ON images(missing_at, file_ext, source_id, file_size)"),
            (
                "idx_images_missing_year_source",
                "ON images(missing_at, substr(date_taken, 1, 4), source_id) "
                "WHERE date_taken IS NOT NULL",
            ),
            (
                "idx_images_missing_lower_file_ext_source",
                "ON images(missing_at, LOWER(file_ext), source_id) "
                "WHERE file_ext IS NOT NULL AND file_ext != ''",
            ),
            ("idx_images_missing_date_source", "ON images(missing_at, date_taken, source_id)"),
            (
                "idx_images_missing_camera_label_source",
                "ON images(missing_at, TRIM(COALESCE(camera_make, '') || ' ' || COALESCE(camera_model, '')), source_id) "
                "WHERE camera_make IS NOT NULL OR camera_model IS NOT NULL",
            ),
            (
                "idx_images_missing_lens_source",
                "ON images(missing_at, lens, source_id) "
                "WHERE lens IS NOT NULL AND lens != ''",
            ),
            (
                "idx_images_active_gps_count",
                "ON images(source_id, latitude, longitude) "
                "WHERE status IN ('kept', 'maybe') AND missing_at IS NULL "
                "AND latitude IS NOT NULL AND longitude IS NOT NULL",
            ),
            (
                "idx_images_rating_signal_cover",
                "ON images(comparisons, propagated_updates, elo)",
            ),
            (
                "idx_images_source_missing_rating_signal",
                "ON images(source_id, missing_at, comparisons, propagated_updates, elo)",
            ),
        ]:
            await db.execute(f"CREATE INDEX IF NOT EXISTS {name} {sql}")
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_images_active_date_taken "
            "ON images(date_taken DESC) WHERE status IN ('kept', 'maybe')"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_images_active_file_size "
            "ON images(file_size DESC) WHERE status IN ('kept', 'maybe')"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_images_active_file_ext "
            "ON images(file_ext) WHERE status IN ('kept', 'maybe')"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_images_active_camera "
            "ON images(camera_make, camera_model) WHERE status IN ('kept', 'maybe')"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_images_active_lens "
            "ON images(lens) WHERE status IN ('kept', 'maybe')"
        )
        for name, sql in [
            (
                "idx_images_active_date_taken_sort_desc",
                "ON images((date_taken IS NULL), date_taken DESC, id DESC) "
                "WHERE status IN ('kept', 'maybe') AND missing_at IS NULL",
            ),
            (
                "idx_images_active_visible_orientation_elo",
                "ON images(orientation, elo DESC) "
                "WHERE status IN ('kept', 'maybe') AND missing_at IS NULL",
            ),
            (
                "idx_images_visible_comparisons_elo",
                "ON images(comparisons ASC, elo DESC) "
                "WHERE status IN ('kept', 'maybe') AND missing_at IS NULL",
            ),
            (
                "idx_images_active_date_taken_sort_asc",
                "ON images((date_taken IS NULL), date_taken ASC, id ASC) "
                "WHERE status IN ('kept', 'maybe') AND missing_at IS NULL",
            ),
            (
                "idx_images_active_file_size_sort_desc",
                "ON images((file_size IS NULL), file_size DESC, id DESC) "
                "WHERE status IN ('kept', 'maybe') AND missing_at IS NULL",
            ),
            (
                "idx_images_active_file_size_sort_asc",
                "ON images((file_size IS NULL), file_size ASC, id ASC) "
                "WHERE status IN ('kept', 'maybe') AND missing_at IS NULL",
            ),
            (
                "idx_images_active_modified_sort_desc",
                "ON images((file_modified_at IS NULL), file_modified_at DESC, id DESC) "
                "WHERE status IN ('kept', 'maybe') AND missing_at IS NULL",
            ),
            (
                "idx_images_active_modified_sort_asc",
                "ON images((file_modified_at IS NULL), file_modified_at ASC, id ASC) "
                "WHERE status IN ('kept', 'maybe') AND missing_at IS NULL",
            ),
            (
                "idx_images_active_resolution_sort_desc",
                "ON images(((width * height) IS NULL), (width * height) DESC, id DESC) "
                "WHERE status IN ('kept', 'maybe') AND missing_at IS NULL",
            ),
            (
                "idx_images_active_resolution_sort_asc",
                "ON images(((width * height) IS NULL), (width * height) ASC, id ASC) "
                "WHERE status IN ('kept', 'maybe') AND missing_at IS NULL",
            ),
            (
                "idx_images_active_camera_sort_asc",
                "ON images((camera_make IS NULL), camera_make ASC, camera_model ASC, id ASC) "
                "WHERE status IN ('kept', 'maybe') AND missing_at IS NULL",
            ),
            (
                "idx_images_active_camera_sort_desc",
                "ON images((camera_make IS NULL), camera_make DESC, camera_model DESC, id DESC) "
                "WHERE status IN ('kept', 'maybe') AND missing_at IS NULL",
            ),
        ]:
            await db.execute(f"CREATE INDEX IF NOT EXISTS {name} {sql}")
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_images_gps "
            "ON images(latitude, longitude) WHERE latitude IS NOT NULL"
        )
        # Backfill aspect_ratio from orientation for images that don't have it yet
        await db.execute(
            "UPDATE images SET aspect_ratio = 1.5 WHERE orientation = 'landscape' AND aspect_ratio IS NULL"
        )
        await db.execute(
            "UPDATE images SET aspect_ratio = 0.6667 WHERE orientation = 'portrait' AND aspect_ratio IS NULL"
        )
        await _migrate_catalog_sources(db)
        await _refresh_source_online_states_on_conn(db)
        await _ensure_embedding_model_tables(db)
        await _ensure_metadata_fts(db)
        await db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        await db.commit()
    finally:
        await db.close()


async def set_image_orientation(image_id: int, orientation: str):
    db = await get_db()
    try:
        await db.execute("UPDATE images SET orientation = ? WHERE id = ?", (orientation, image_id))
        await db.commit()
    finally:
        await db.close()


async def get_unclassified_images(limit: int = 200):
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT i.id, i.filepath FROM images i "
            "JOIN catalog_sources s ON s.id = i.source_id "
            "WHERE i.orientation IS NULL AND s.included = 1 "
            "AND i.missing_at IS NULL "
            "LIMIT ?",
            (limit,),
        )
        return await cursor.fetchall()
    finally:
        await db.close()


async def batch_set_orientations(updates: list[tuple[str, float, int]]):
    """Set orientation and aspect_ratio for multiple images. Each tuple: (orientation, aspect_ratio, image_id)."""
    db = await get_db()
    try:
        await db.executemany(
            "UPDATE images SET orientation = ?, aspect_ratio = ? WHERE id = ?",
            updates,
        )
        await db.commit()
        _invalidate_filter_options_cache()
    finally:
        await db.close()


async def get_images_needing_metadata(limit: int = 100, metadata_version: int = 1):
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT i.id, i.filepath FROM images i "
            "JOIN catalog_sources s ON s.id = i.source_id "
            "WHERE s.included = 1 AND ("
            "i.metadata_scanned_at IS NULL "
            "OR i.metadata_version IS NULL "
            "OR i.metadata_version < ?) "
            "AND i.missing_at IS NULL "
            "LIMIT ?",
            (metadata_version, limit),
        )
        return await cursor.fetchall()
    finally:
        await db.close()


async def batch_update_metadata(updates: list[tuple]):
    """Persist extracted metadata. Tuples are built in app._metadata_update_tuple."""
    if not updates:
        return
    db = await get_db()
    try:
        await db.executemany(
            "UPDATE images SET "
            "date_taken = COALESCE(?, date_taken), "
            "camera_make = COALESCE(?, camera_make), "
            "camera_model = COALESCE(?, camera_model), "
            "lens = COALESCE(?, lens), "
            "file_ext = COALESCE(?, file_ext), "
            "file_size = COALESCE(?, file_size), "
            "file_modified_at = COALESCE(?, file_modified_at), "
            "width = COALESCE(?, width), "
            "height = COALESCE(?, height), "
            "metadata_scanned_at = ?, metadata_version = ?, "
            "orientation = COALESCE(orientation, ?), "
            "aspect_ratio = COALESCE(aspect_ratio, ?), "
            "latitude = COALESCE(?, latitude), "
            "longitude = COALESCE(?, longitude) "
            "WHERE id = ?",
            updates,
        )
        await db.commit()
        _invalidate_filter_options_cache()
    finally:
        await db.close()


def _insert_row_with_file_metadata(row):
    if len(row) >= 5:
        return row[:5]
    filename, filepath = row[:2]
    file_ext = os.path.splitext(filename)[1].lower()
    file_size = None
    file_modified_at = None
    try:
        stat = os.stat(filepath)
        file_size = int(stat.st_size)
        file_modified_at = float(stat.st_mtime)
    except Exception:
        pass
    return filename, filepath, file_ext, file_size, file_modified_at


async def insert_images_batch(rows: list[tuple], source_id: int | None = None):
    """Insert image rows, ignoring duplicates."""
    if not rows:
        return
    db = await get_db()
    try:
        normalized_rows = [_insert_row_with_file_metadata(row) for row in rows]
        if source_id is not None:
            await db.executemany(
                "INSERT OR IGNORE INTO images "
                "(source_id, filename, filepath, status, file_ext, file_size, file_modified_at) "
                "VALUES (?, ?, ?, 'kept', ?, ?, ?)",
                [(source_id, *row) for row in normalized_rows],
            )
            await db.executemany(
                "UPDATE images SET "
                "source_id = CASE WHEN source_id IS NULL THEN ? ELSE source_id END, "
                "filename = ?, "
                "file_ext = COALESCE(?, file_ext), "
                "file_size = COALESCE(?, file_size), "
                "file_modified_at = COALESCE(?, file_modified_at), "
                "missing_at = NULL "
                "WHERE filepath = ? AND (source_id = ? OR source_id IS NULL)",
                [
                    (source_id, row[0], row[2], row[3], row[4], row[1], source_id)
                    for row in normalized_rows
                ],
            )
            await _update_source_counts(db, source_id)
        else:
            await db.executemany(
                "INSERT OR IGNORE INTO images "
                "(filename, filepath, status, file_ext, file_size, file_modified_at) "
                "VALUES (?, ?, 'kept', ?, ?, ?)",
                normalized_rows,
            )
        await db.commit()
        _invalidate_stats_cache()
        _invalidate_filter_options_cache()
    finally:
        await db.close()


async def _mark_source_missing_files(conn, source_id: int, seen_filepaths: list[str], missing_at: float):
    await conn.execute(
        "CREATE TEMP TABLE IF NOT EXISTS source_scan_seen (filepath TEXT PRIMARY KEY)"
    )
    await conn.execute("DELETE FROM source_scan_seen")
    unique_seen = list(dict.fromkeys(seen_filepaths))
    if unique_seen:
        await conn.executemany(
            "INSERT OR IGNORE INTO source_scan_seen(filepath) VALUES (?)",
            [(filepath,) for filepath in unique_seen],
        )
    await conn.execute(
        "UPDATE images SET missing_at = NULL "
        "WHERE source_id = ? AND filepath IN (SELECT filepath FROM source_scan_seen)",
        (source_id,),
    )
    await conn.execute(
        "UPDATE images SET missing_at = ? "
        "WHERE source_id = ? AND missing_at IS NULL "
        "AND filepath NOT IN (SELECT filepath FROM source_scan_seen)",
        (missing_at, source_id),
    )
    await conn.execute("DELETE FROM source_scan_seen")


async def refresh_source_online_states():
    db = await get_db()
    try:
        if await _refresh_source_online_states_on_conn(db):
            await db.commit()
            _invalidate_stats_cache()
            _invalidate_filter_options_cache()
    finally:
        await db.close()


async def add_or_restore_source(path: str):
    db = await get_db()
    try:
        source = await _ensure_catalog_source(db, path, included=True)
        await _update_source_counts(db, source["id"])
        await db.commit()
        _invalidate_stats_cache()
        _invalidate_filter_options_cache()
        cursor = await db.execute("SELECT * FROM catalog_sources WHERE id = ?", (source["id"],))
        return await cursor.fetchone()
    finally:
        await db.close()


async def mark_source_scan_started(source_id: int):
    db = await get_db()
    try:
        now = _time.time()
        cursor = await db.execute(
            "UPDATE catalog_sources SET included = 1, online = 1, removed_at = NULL, last_seen_at = ? "
            "WHERE id = ?",
            (now, source_id),
        )
        await db.commit()
        if cursor.rowcount:
            _invalidate_stats_cache()
            _invalidate_filter_options_cache()
    finally:
        await db.close()


async def mark_source_scan_finished(source_id: int, seen_filepaths: list[str] | None = None):
    db = await get_db()
    try:
        now = _time.time()
        await db.execute(
            "UPDATE catalog_sources SET last_scan_at = ?, last_seen_at = ?, online = ? WHERE id = ?",
            (now, now, 1, source_id),
        )
        if seen_filepaths is not None:
            await _mark_source_missing_files(db, source_id, seen_filepaths, now)
        await _update_source_counts(db, source_id)
        await db.commit()
        _invalidate_stats_cache()
        _invalidate_filter_options_cache()
        invalidate_cached_image_ids_cache()
    finally:
        await db.close()


def mark_image_missing_sync(image_id: int, missing_at: float | None = None) -> bool:
    """Mark one image missing from sync thumbnail/worker code."""
    when = _time.time() if missing_at is None else float(missing_at)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    try:
        cursor = conn.execute(
            "UPDATE images SET missing_at = COALESCE(missing_at, ?) WHERE id = ?",
            (when, int(image_id)),
        )
        conn.commit()
        changed = cursor.rowcount > 0
    finally:
        conn.close()
    if changed:
        _invalidate_stats_cache()
        _invalidate_filter_options_cache()
        invalidate_cached_image_ids_cache()
    return changed


async def get_source(source_id: int):
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM catalog_sources WHERE id = ?", (source_id,))
        return await cursor.fetchone()
    finally:
        await db.close()


async def get_source_by_path(path: str):
    normalized = normalize_source_path(path)
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM catalog_sources WHERE path = ?", (normalized,))
        return await cursor.fetchone()
    finally:
        await db.close()


async def get_catalog_sources():
    if _catalog_sources_cache["data"] and _time.time() < _catalog_sources_cache["expires"]:
        return _catalog_sources_cache["data"]
    await refresh_source_online_states()
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT id, path, display_name, included, online, image_count, active_image_count, "
            "created_at, last_scan_at, last_seen_at, removed_at "
            "FROM catalog_sources ORDER BY included DESC, display_name COLLATE NOCASE ASC, path ASC"
        )
        rows = await cursor.fetchall()
        _catalog_sources_cache["data"] = rows
        _catalog_sources_cache["expires"] = _time.time() + CATALOG_CACHE_TTL_SECONDS
        return rows
    finally:
        await db.close()


async def get_catalog_summary():
    if _catalog_summary_cache["data"] and _time.time() < _catalog_summary_cache["expires"]:
        return _catalog_summary_cache["data"]
    sources = [dict(row) for row in await get_catalog_sources()]
    stats = await get_stats()
    result = {"sources": sources, "stats": stats}
    _catalog_summary_cache["data"] = result
    _catalog_summary_cache["expires"] = _time.time() + CATALOG_CACHE_TTL_SECONDS
    return result


async def get_catalog_light_summary():
    if _catalog_light_summary_cache["data"] and _time.time() < _catalog_light_summary_cache["expires"]:
        return _catalog_light_summary_cache["data"]
    sources, counts = await asyncio.gather(
        get_catalog_sources(),
        get_catalog_image_counts(),
    )
    active = int(counts.get("active_images") or 0)
    total = int(counts.get("total_catalog_images") or 0)
    stats = {
        **counts,
        "total_images": active,
        "active_images": active,
        "kept": active,
        "maybe": 0,
        "removed_images": int(counts.get("removed_images") or 0),
        "offline_images": int(counts.get("offline_images") or 0),
        "total_catalog_images": total,
    }
    result = {"sources": [dict(row) for row in sources], "stats": stats}
    _catalog_light_summary_cache["data"] = result
    _catalog_light_summary_cache["expires"] = _time.time() + CATALOG_CACHE_TTL_SECONDS
    return result


async def remove_source_keep_data(source_id: int):
    db = await get_db()
    try:
        now = _time.time()
        await db.execute(
            "UPDATE catalog_sources SET included = 0, removed_at = ?, last_seen_at = ? WHERE id = ?",
            (now, now, source_id),
        )
        await _update_source_counts(db, source_id)
        await db.commit()
        _invalidate_stats_cache()
        _invalidate_filter_options_cache()
    finally:
        await db.close()


async def get_source_image_ids(source_id: int) -> list[int]:
    db = await get_db()
    try:
        cursor = await db.execute("SELECT id FROM images WHERE source_id = ?", (source_id,))
        return [int(row["id"]) for row in await cursor.fetchall()]
    finally:
        await db.close()


async def purge_source_catalog_data(source_id: int) -> dict:
    image_ids = await get_source_image_ids(source_id)
    db = await get_db()
    try:
        comparison_count = 0
        comparison_decrements: dict[int, int] = {}
        if image_ids:
            source_id_set = set(image_ids)
            for chunk in _chunked(image_ids):
                placeholders = ",".join("?" for _ in chunk)
                cursor = await db.execute(
                    f"DELETE FROM embeddings WHERE image_id IN ({placeholders})",
                    chunk,
                )
                await db.execute(
                    f"DELETE FROM embeddings_by_model WHERE image_id IN ({placeholders})",
                    chunk,
                )
                _invalidate_embedding_count_cache()
                cursor = await db.execute(
                    f"SELECT winner_id, loser_id FROM comparisons "
                    f"WHERE winner_id IN ({placeholders}) OR loser_id IN ({placeholders})",
                    chunk + chunk,
                )
                for row in await cursor.fetchall():
                    winner_id = int(row["winner_id"])
                    loser_id = int(row["loser_id"])
                    if winner_id in source_id_set and loser_id not in source_id_set:
                        comparison_decrements[loser_id] = comparison_decrements.get(loser_id, 0) + 1
                    elif loser_id in source_id_set and winner_id not in source_id_set:
                        comparison_decrements[winner_id] = comparison_decrements.get(winner_id, 0) + 1
                cursor = await db.execute(
                    f"DELETE FROM comparisons "
                    f"WHERE winner_id IN ({placeholders}) OR loser_id IN ({placeholders})",
                    chunk + chunk,
                )
                comparison_count += max(0, cursor.rowcount or 0)
                await db.execute(
                    f"DELETE FROM cache_entries WHERE image_id IN ({placeholders})",
                    chunk,
                )
                await db.execute(
                    f"DELETE FROM images WHERE id IN ({placeholders})",
                    chunk,
                )
            if comparison_decrements:
                await db.executemany(
                    "UPDATE images SET comparisons = MAX(COALESCE(comparisons, 0) - ?, 0) WHERE id = ?",
                    [(count, image_id) for image_id, count in comparison_decrements.items()],
                )
        await db.execute("DELETE FROM catalog_sources WHERE id = ?", (source_id,))
        await _update_source_counts(db)
        await db.commit()
        _invalidate_stats_cache()
        _invalidate_filter_options_cache()
        invalidate_cached_image_ids_cache()
        return {
            "images_deleted": len(image_ids),
            "comparisons_deleted": comparison_count,
        }
    finally:
        await db.close()


async def get_recent_active_images(limit: int = 10):
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT i.id, i.filename, i.filepath FROM images i "
            "JOIN catalog_sources s ON s.id = i.source_id "
            "WHERE s.included = 1 AND i.missing_at IS NULL "
            "ORDER BY i.id DESC LIMIT ?",
            (limit,),
        )
        return await cursor.fetchall()
    finally:
        await db.close()


async def set_image_status(image_id: int, status: str):
    db = await get_db()
    try:
        await db.execute(
            "UPDATE images SET status = ? WHERE id = ?", (status, image_id)
        )
        await db.commit()
        _invalidate_past_matchups_cache()
        _invalidate_rating_stats_cache()
        _invalidate_filter_options_cache()
    finally:
        await db.close()


async def set_image_flag(image_id: int, flag: str):
    db = await get_db()
    try:
        await db.execute(
            "UPDATE images SET flag = ? WHERE id = ?", (flag, image_id)
        )
        await db.commit()
        _invalidate_ranking_count_cache()
        _invalidate_filter_options_cache()
    finally:
        await db.close()


async def batch_set_image_flags(image_ids: list[int], flag: str, chunk_size: int = 500) -> int:
    if not image_ids:
        return 0
    db = await get_db()
    updated = 0
    try:
        for start in range(0, len(image_ids), chunk_size):
            chunk = image_ids[start:start + chunk_size]
            placeholders = ",".join("?" for _ in chunk)
            await db.execute(
                f"UPDATE images SET flag = ? WHERE id IN ({placeholders})",
                [flag] + chunk,
            )
            updated += len(chunk)
        await db.commit()
        _invalidate_ranking_count_cache()
        _invalidate_filter_options_cache()
        return updated
    finally:
        await db.close()


async def get_active_images_for_pairing():
    """Get active images sorted by Elo for Swiss-system pairing."""
    counts = await get_catalog_image_counts()
    active_images = int(counts.get("active_images") or 0)
    if active_images <= 0:
        return []
    all_catalog_images_active = (
        active_images == int(counts.get("total_catalog_images") or 0)
        and int(counts.get("removed_images") or 0) == 0
    )
    source_filter = (
        "AND i.source_id IN ("
        "SELECT id FROM catalog_sources WHERE included = 1"
        ") "
        if not all_catalog_images_active
        else ""
    )
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT i.id, i.filename, i.filepath, i.elo, i.comparisons, "
            "i.propagated_updates, i.status, i.flag, i.orientation, "
            "i.aspect_ratio, i.date_taken, i.camera_make, i.camera_model, "
            "i.lens, i.file_ext FROM images i "
            "WHERE i.status IN ('kept', 'maybe') "
            f"{source_filter}"
            "AND i.missing_at IS NULL"
        )
        return await cursor.fetchall()
    finally:
        await db.close()


async def get_visible_images_for_pairing(
    size: str,
    cache_root: str,
    *,
    include_card_metadata: bool = True,
    limit: int | None = None,
    order: str = "elo",
):
    """Return visible active pairing rows for one thumbnail tier, sorted by Elo."""
    if not size or not cache_root:
        return []
    metadata_columns = (
        "i.file_size, i.file_modified_at, i.width, i.height, "
        "i.latitude, i.longitude, i.created_at "
        if include_card_metadata
        else ""
    )
    db = await get_db()
    try:
        limit_sql = " LIMIT ?" if limit and limit > 0 else ""
        params = [cache_root, size]
        if limit_sql:
            params.append(int(limit))
        if order == "least_compared":
            order_sql = " ORDER BY i.comparisons ASC, i.elo DESC"
        elif order == "cache":
            order_sql = ""
        else:
            order_sql = " ORDER BY i.elo DESC"
        if order in {"elo", "least_compared"} and limit and limit > 0:
            image_source = (
                "images i INDEXED BY idx_images_visible_comparisons_elo"
                if order == "least_compared"
                else "images i INDEXED BY idx_images_active_elo"
            )
            from_sql = (
                f"FROM {image_source} "
                "JOIN catalog_sources s ON s.id = i.source_id "
            )
            where_sql = (
                "WHERE i.status IN ('kept', 'maybe') "
                "AND s.included = 1 AND s.online = 1 AND i.missing_at IS NULL "
                "AND EXISTS ("
                "  SELECT 1 FROM cache_entries c "
                "  WHERE c.cache_root = ? AND c.size = ? AND c.image_id = i.id"
                ") "
            )
        else:
            from_sql = (
                "FROM cache_entries c "
                "JOIN images i ON i.id = c.image_id "
                "JOIN catalog_sources s ON s.id = i.source_id "
            )
            where_sql = (
                "WHERE c.cache_root = ? AND c.size = ? "
                "AND s.included = 1 AND s.online = 1 AND i.missing_at IS NULL "
            )
        cursor = await db.execute(
            "SELECT i.id, i.filename, i.filepath, i.elo, i.comparisons, "
            "i.propagated_updates, i.status, i.flag, i.orientation, "
            "i.aspect_ratio, i.date_taken, i.camera_make, i.camera_model, "
            f"i.lens, i.file_ext{', ' if metadata_columns else ' '}{metadata_columns}"
            f"{from_sql}"
            f"{where_sql}"
            f"{order_sql}{limit_sql}",
            params,
        )
        return await cursor.fetchall()
    finally:
        await db.close()


async def get_visible_pairing_pool_counts(size: str, cache_root: str) -> dict:
    """Return active and visible counts for the default Compare/Mosaic pool."""
    if not size or not cache_root:
        counts = await get_catalog_image_counts()
        return {"active_images": int(counts.get("active_images") or 0), "visible_images": 0}
    cache_key = (cache_root, size)
    now = _time.time()
    cached = _visible_pairing_pool_counts_cache.get(cache_key)
    if cached and cached["expires"] > now:
        return dict(cached["data"])
    counts = await get_catalog_image_counts()
    active_images = int(counts.get("active_images") or 0)
    all_catalog_images_active = (
        active_images > 0
        and active_images == int(counts.get("total_catalog_images") or 0)
        and int(counts.get("removed_images") or 0) == 0
    )
    all_sources_available = (
        int(counts.get("removed_images") or 0) == 0
    )
    db = await get_db()
    try:
        if all_catalog_images_active or all_sources_available:
            cursor = await db.execute(
                "SELECT COUNT(*) AS count FROM cache_entries "
                "WHERE cache_root = ? AND size = ?",
                (cache_root, size),
            )
        else:
            cursor = await db.execute(
                "SELECT COUNT(*) AS count FROM cache_entries c "
                "JOIN images i ON i.id = c.image_id "
                "JOIN catalog_sources s ON s.id = i.source_id "
                "WHERE c.cache_root = ? AND c.size = ? "
                "AND s.included = 1 "
                "AND i.status IN ('kept', 'maybe') AND i.missing_at IS NULL",
                (cache_root, size),
            )
        visible_images = min(active_images, int((await cursor.fetchone())["count"] or 0))
        result = {"active_images": active_images, "visible_images": visible_images}
        _visible_pairing_pool_counts_cache[cache_key] = {
            "data": result,
            "expires": _time.time() + VISIBLE_PAIRING_POOL_COUNTS_TTL_SECONDS,
        }
        return dict(result)
    finally:
        await db.close()


async def get_visible_orientation_pairing_pool_counts(
    size: str,
    cache_root: str,
    orientation: str,
) -> dict:
    """Return active and visible counts for a simple orientation-filtered pool."""
    orientation = (orientation or "").strip()
    if not orientation:
        return await get_visible_pairing_pool_counts(size, cache_root)
    if not size or not cache_root:
        active = await count_rankings(orientation=orientation)
        return {"active_images": int(active), "visible_images": 0}
    cache_key = (cache_root, size, "orientation", orientation)
    now = _time.time()
    cached = _visible_pairing_pool_counts_cache.get(cache_key)
    if cached and cached["expires"] > now:
        return dict(cached["data"])
    counts = await get_catalog_image_counts()
    active_images = int(counts.get("active_images") or 0)
    all_catalog_images_active = (
        active_images > 0
        and active_images == int(counts.get("total_catalog_images") or 0)
        and int(counts.get("removed_images") or 0) == 0
    )
    all_sources_available = (
        int(counts.get("removed_images") or 0) == 0
    )
    db = await get_db()
    try:
        if all_catalog_images_active or all_sources_available:
            active_cursor = await db.execute(
                "SELECT COUNT(*) AS count FROM images INDEXED BY idx_images_active_orientation_count "
                "WHERE orientation = ? AND status IN ('kept', 'maybe') AND missing_at IS NULL",
                (orientation,),
            )
            visible_cursor = await db.execute(
                "SELECT COUNT(*) AS count FROM cache_entries c INDEXED BY sqlite_autoindex_cache_entries_1 "
                "CROSS JOIN images i "
                "WHERE c.cache_root = ? AND c.size = ? "
                "AND i.id = c.image_id "
                "AND i.orientation = ? AND i.status IN ('kept', 'maybe') AND i.missing_at IS NULL",
                (cache_root, size, orientation),
            )
        else:
            active_cursor = await db.execute(
                "SELECT COUNT(*) AS count FROM images i "
                "JOIN catalog_sources s ON s.id = i.source_id "
                "WHERE s.included = 1 "
                "AND i.orientation = ? AND i.status IN ('kept', 'maybe') AND i.missing_at IS NULL",
                (orientation,),
            )
            visible_cursor = await db.execute(
                "SELECT COUNT(*) AS count FROM cache_entries c INDEXED BY sqlite_autoindex_cache_entries_1 "
                "CROSS JOIN images i "
                "JOIN catalog_sources s ON s.id = i.source_id "
                "WHERE c.cache_root = ? AND c.size = ? "
                "AND i.id = c.image_id "
                "AND s.included = 1 "
                "AND i.orientation = ? AND i.status IN ('kept', 'maybe') AND i.missing_at IS NULL",
                (cache_root, size, orientation),
            )
        result = {
            "active_images": int((await active_cursor.fetchone())["count"] or 0),
            "visible_images": int((await visible_cursor.fetchone())["count"] or 0),
        }
        _visible_pairing_pool_counts_cache[cache_key] = {
            "data": result,
            "expires": _time.time() + VISIBLE_PAIRING_POOL_COUNTS_TTL_SECONDS,
        }
        return dict(result)
    finally:
        await db.close()


async def get_past_matchups() -> set[tuple[int, int]]:
    """Return set of (min_id, max_id) tuples for all past matchups."""
    if not await get_active_source_id_set():
        return set()

    def _load_matchups(path: str) -> tuple[tuple[int, int | None], set[tuple[int, int]] | None]:
        conn = sqlite3.connect(path, timeout=30)
        try:
            conn.execute("PRAGMA busy_timeout=30000")
            signature = conn.execute(
                "SELECT COUNT(*) AS count, MAX(id) AS max_id FROM comparisons"
            ).fetchone()
            signature_key = (int(signature[0] or 0), int(signature[1]) if signature[1] is not None else None)
            if _past_matchups_cache["signature"] == signature_key and _past_matchups_cache["data"] is not None:
                return signature_key, None
            rows = conn.execute("SELECT winner_id, loser_id FROM comparisons").fetchall()
            return signature_key, {
                (min(winner_id, loser_id), max(winner_id, loser_id))
                for winner_id, loser_id in rows
            }
        finally:
            conn.close()

    signature, loaded = await asyncio.to_thread(_load_matchups, DB_PATH)
    if loaded is None:
        return set(_past_matchups_cache["data"])
    _past_matchups_cache["signature"] = signature
    _past_matchups_cache["data"] = loaded
    return set(loaded)


async def get_visible_past_matchups(size: str, cache_root: str) -> set[tuple[int, int]]:
    """Return past matchup pairs where both images are visible in one cache tier."""
    if not size or not cache_root:
        return set()

    def _load_matchups(path: str, tier: str, root: str) -> set[tuple[int, int]]:
        conn = sqlite3.connect(path, timeout=30)
        try:
            conn.execute("PRAGMA busy_timeout=30000")
            rows = conn.execute(
                "SELECT c.winner_id, c.loser_id FROM comparisons c "
                "JOIN cache_entries cw ON cw.image_id = c.winner_id "
                "AND cw.cache_root = ? AND cw.size = ? "
                "JOIN cache_entries cl ON cl.image_id = c.loser_id "
                "AND cl.cache_root = ? AND cl.size = ?",
                (root, tier, root, tier),
            ).fetchall()
            return {(min(winner_id, loser_id), max(winner_id, loser_id)) for winner_id, loser_id in rows}
        finally:
            conn.close()

    return await asyncio.to_thread(_load_matchups, DB_PATH, size, cache_root)


async def get_past_matchups_for_image_ids(image_ids: list[int]) -> set[tuple[int, int]]:
    """Return past matchup pairs where both images are in a bounded candidate set."""
    ids = list(dict.fromkeys(int(image_id) for image_id in image_ids or [] if int(image_id) > 0))
    if len(ids) < 2:
        return set()

    if len(ids) <= 2000:
        def _load_bounded_matchups(path: str, candidate_ids: list[int]) -> set[tuple[int, int]]:
            candidate_set = set(candidate_ids)
            matchups = set()
            conn = sqlite3.connect(path, timeout=30)
            try:
                conn.execute("PRAGMA busy_timeout=30000")
                for chunk in _chunked(candidate_ids, 900):
                    placeholders = ",".join("?" for _ in chunk)
                    for winner_id, loser_id in conn.execute(
                        f"SELECT winner_id, loser_id FROM comparisons WHERE winner_id IN ({placeholders})",
                        chunk,
                    ):
                        if loser_id in candidate_set:
                            matchups.add((min(winner_id, loser_id), max(winner_id, loser_id)))
                    for winner_id, loser_id in conn.execute(
                        f"SELECT winner_id, loser_id FROM comparisons WHERE loser_id IN ({placeholders})",
                        chunk,
                    ):
                        if winner_id in candidate_set:
                            matchups.add((min(winner_id, loser_id), max(winner_id, loser_id)))
                return matchups
            finally:
                conn.close()

        return await asyncio.to_thread(_load_bounded_matchups, DB_PATH, ids)

    def _load_matchups(path: str, candidate_ids: list[int]) -> set[tuple[int, int]]:
        conn = sqlite3.connect(path, timeout=30)
        try:
            conn.execute("PRAGMA busy_timeout=30000")
            conn.execute("CREATE TEMP TABLE candidate_ids(id INTEGER PRIMARY KEY)")
            conn.executemany("INSERT INTO candidate_ids(id) VALUES (?)", [(image_id,) for image_id in candidate_ids])
            rows = conn.execute(
                "SELECT c.winner_id, c.loser_id FROM comparisons c "
                "JOIN candidate_ids w ON w.id = c.winner_id "
                "JOIN candidate_ids l ON l.id = c.loser_id"
            ).fetchall()
            return {(min(winner_id, loser_id), max(winner_id, loser_id)) for winner_id, loser_id in rows}
        finally:
            conn.close()

    return await asyncio.to_thread(_load_matchups, DB_PATH, ids)


async def record_comparison(
    winner_id: int,
    loser_id: int,
    mode: str,
    elo_before_winner: float,
    elo_before_loser: float,
    new_winner_elo: float,
    new_loser_elo: float,
    action_id: str | None = None,
):
    db = await get_db()
    try:
        await db.execute(
            "INSERT INTO comparisons "
            "(winner_id, loser_id, mode, elo_before_winner, elo_before_loser, action_id) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (winner_id, loser_id, mode, elo_before_winner, elo_before_loser, action_id),
        )
        await db.execute(
            "UPDATE images SET elo = ?, comparisons = COALESCE(comparisons, 0) + 1 WHERE id = ?",
            (new_winner_elo, winner_id),
        )
        await db.execute(
            "UPDATE images SET elo = ?, comparisons = COALESCE(comparisons, 0) + 1 WHERE id = ?",
            (new_loser_elo, loser_id),
        )
        await db.commit()
        _invalidate_rating_stats_cache()
    finally:
        await db.close()


async def record_active_comparison(
    winner_id: int,
    loser_id: int,
    mode: str,
    action_id: str | None = None,
) -> dict | None:
    """Validate active images and record a comparison in one DB round trip."""
    if winner_id == loser_id:
        return None

    import pairing

    def was_rated(row: dict) -> bool:
        return (
            int(row.get("comparisons") or 0) > 0
            or int(row.get("propagated_updates") or 0) > 0
            or abs(float(row.get("elo") or 1200.0) - 1200.0) > 0.0001
        )

    counts = await get_catalog_image_counts()
    all_catalog_images_active = (
        int(counts.get("active_images") or 0) > 0
        and int(counts.get("active_images") or 0) == int(counts.get("total_catalog_images") or 0)
        and int(counts.get("removed_images") or 0) == 0
    )
    db = await get_db()
    try:
        if all_catalog_images_active:
            cursor = await db.execute(
                "SELECT i.id, i.elo, COALESCE(i.comparisons, 0) AS comparisons, "
                "COALESCE(i.propagated_updates, 0) AS propagated_updates "
                "FROM images i NOT INDEXED "
                "WHERE i.missing_at IS NULL AND i.id IN (?, ?)",
                (winner_id, loser_id),
            )
        else:
            cursor = await db.execute(
                "SELECT i.id, i.elo, COALESCE(i.comparisons, 0) AS comparisons, "
                "COALESCE(i.propagated_updates, 0) AS propagated_updates "
                "FROM images i NOT INDEXED "
                "JOIN catalog_sources s ON s.id = i.source_id "
                "WHERE s.included = 1 AND i.missing_at IS NULL "
                "AND i.id IN (?, ?)",
                (winner_id, loser_id),
            )
        rows = {row["id"]: dict(row) for row in await cursor.fetchall()}
        winner = rows.get(winner_id)
        loser = rows.get(loser_id)
        if not winner or not loser:
            return None

        k = pairing.get_k_factor(min(winner["comparisons"], loser["comparisons"]), mode)
        new_winner_elo, new_loser_elo = pairing.update_elo(winner["elo"], loser["elo"], k)
        await db.execute(
            "INSERT INTO comparisons "
            "(winner_id, loser_id, mode, elo_before_winner, elo_before_loser, action_id) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (winner_id, loser_id, mode, winner["elo"], loser["elo"], action_id),
        )
        await db.execute(
            "UPDATE images SET "
            "elo = CASE id WHEN ? THEN ? WHEN ? THEN ? ELSE elo END, "
            "comparisons = COALESCE(comparisons, 0) + 1 "
            "WHERE id IN (?, ?)",
            (winner_id, new_winner_elo, loser_id, new_loser_elo, winner_id, loser_id),
        )
        await db.commit()
        _invalidate_past_matchups_cache()
        rated_delta = (0 if was_rated(winner) else 1) + (0 if was_rated(loser) else 1)
        _patch_direct_rating_stats_cache(1, rated_delta)
        return {
            "winner_elo_before": winner["elo"],
            "loser_elo_before": loser["elo"],
            "winner_elo": new_winner_elo,
            "loser_elo": new_loser_elo,
            "k": k,
        }
    finally:
        await db.close()


async def record_active_mosaic_pick(
    picked_id: int,
    other_ids: list[int],
    action_id: str,
) -> dict:
    """Validate active mosaic images and record the full pick action."""
    if not other_ids:
        return {"ok": False, "missing_ids": []}
    all_ids = [picked_id] + list(other_ids)
    unique_ids = list(dict.fromkeys(int(image_id) for image_id in all_ids))

    import pairing

    def was_rated(row: dict) -> bool:
        return (
            int(row.get("comparisons") or 0) > 0
            or int(row.get("propagated_updates") or 0) > 0
            or abs(float(row.get("elo") or 1200.0) - 1200.0) > 0.0001
        )

    counts = await get_catalog_image_counts()
    all_catalog_images_active = (
        int(counts.get("active_images") or 0) > 0
        and int(counts.get("active_images") or 0) == int(counts.get("total_catalog_images") or 0)
        and int(counts.get("removed_images") or 0) == 0
    )
    db = await get_db()
    try:
        placeholders = ",".join("?" for _ in unique_ids)
        if all_catalog_images_active:
            cursor = await db.execute(
                "SELECT i.id, i.elo, COALESCE(i.comparisons, 0) AS comparisons, "
                "COALESCE(i.propagated_updates, 0) AS propagated_updates "
                "FROM images i NOT INDEXED "
                "WHERE i.missing_at IS NULL "
                f"AND i.id IN ({placeholders})",
                unique_ids,
            )
        else:
            cursor = await db.execute(
                "SELECT i.id, i.elo, COALESCE(i.comparisons, 0) AS comparisons, "
                "COALESCE(i.propagated_updates, 0) AS propagated_updates "
                "FROM images i NOT INDEXED "
                "JOIN catalog_sources s ON s.id = i.source_id "
                "WHERE s.included = 1 AND i.missing_at IS NULL "
                f"AND i.id IN ({placeholders})",
                unique_ids,
            )
        images = {row["id"]: dict(row) for row in await cursor.fetchall()}
        missing_ids = [image_id for image_id in all_ids if image_id not in images]
        if missing_ids:
            return {"ok": False, "missing_ids": missing_ids}

        picked_elo = images[picked_id]["elo"]
        comparison_rows = []
        loser_updates = []
        for other_id in other_ids:
            other = images[other_id]
            new_picked, new_other = pairing.update_elo(picked_elo, other["elo"], k=12.0)
            comparison_rows.append((picked_id, other_id, picked_elo, other["elo"], action_id))
            loser_updates.append((other_id, new_other))
            picked_elo = new_picked

        if comparison_rows:
            row_placeholders = ",".join(
                "(?, ?, 'mosaic', ?, ?, ?)" for _row in comparison_rows
            )
            comparison_params = [
                value
                for row in comparison_rows
                for value in row
            ]
            await db.execute(
                "INSERT INTO comparisons "
                "(winner_id, loser_id, mode, elo_before_winner, elo_before_loser, action_id) "
                f"VALUES {row_placeholders}",
                comparison_params,
            )
            loser_case_parts = []
            loser_params = []
            loser_ids = []
            for image_id, new_elo in loser_updates:
                loser_case_parts.append("WHEN ? THEN ?")
                loser_params.extend([image_id, new_elo])
                loser_ids.append(image_id)
            all_update_ids = [picked_id] + loser_ids
            update_placeholders = ",".join("?" for _ in all_update_ids)
            await db.execute(
                "UPDATE images SET "
                f"elo = CASE id WHEN ? THEN ? {' '.join(loser_case_parts)} ELSE elo END, "
                "comparisons = COALESCE(comparisons, 0) + CASE id WHEN ? THEN ? ELSE 1 END "
                f"WHERE id IN ({update_placeholders})",
                [picked_id, picked_elo] + loser_params
                + [picked_id, len(comparison_rows)]
                + all_update_ids,
            )
        await db.commit()
        _invalidate_past_matchups_cache()
        rated_delta = sum(0 if was_rated(images[image_id]) else 1 for image_id in unique_ids)
        _patch_direct_rating_stats_cache(len(comparison_rows), rated_delta)
        return {
            "ok": True,
            "new_elo": picked_elo,
            "pairs_recorded": len(comparison_rows),
            "loser_updates": loser_updates,
        }
    finally:
        await db.close()


async def undo_last_comparison():
    """Undo the last comparison/action, restoring Elo ratings."""
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT id, action_id FROM comparisons ORDER BY id DESC LIMIT 1"
        )
        latest = await cursor.fetchone()
        if latest:
            action_id = latest["action_id"]
            if action_id:
                cursor = await db.execute(
                    "SELECT id, winner_id, loser_id, elo_before_winner, elo_before_loser, action_id "
                    "FROM comparisons WHERE action_id = ? ORDER BY id ASC",
                    (action_id,),
                )
                rows = await cursor.fetchall()
            else:
                cursor = await db.execute(
                    "SELECT id, winner_id, loser_id, elo_before_winner, elo_before_loser, action_id "
                    "FROM comparisons WHERE id = ?",
                    (latest["id"],),
                )
                rows = await cursor.fetchall()

            if not rows:
                return None

            propagation_rows = []
            if action_id:
                cursor = await db.execute(
                    "SELECT image_id, elo_before, propagated_updates_before "
                    "FROM propagation_updates WHERE action_id = ? ORDER BY id ASC",
                    (action_id,),
                )
                propagation_rows = await cursor.fetchall()

            for row in propagation_rows:
                await db.execute(
                    "UPDATE images SET elo = ?, propagated_updates = ? WHERE id = ?",
                    (
                        float(row["elo_before"]),
                        int(row["propagated_updates_before"]),
                        int(row["image_id"]),
                    ),
                )

            restore_elo: dict[int, float] = {}
            comparison_decrements: dict[int, int] = {}
            for row in rows:
                winner_id = int(row["winner_id"])
                loser_id = int(row["loser_id"])
                restore_elo.setdefault(winner_id, float(row["elo_before_winner"]))
                restore_elo.setdefault(loser_id, float(row["elo_before_loser"]))
                comparison_decrements[winner_id] = comparison_decrements.get(winner_id, 0) + 1
                comparison_decrements[loser_id] = comparison_decrements.get(loser_id, 0) + 1

            for image_id, elo in restore_elo.items():
                await db.execute(
                    "UPDATE images SET elo = ?, comparisons = MAX(COALESCE(comparisons, 0) - ?, 0) WHERE id = ?",
                    (elo, comparison_decrements.get(image_id, 0), image_id),
                )

            if action_id:
                await db.execute("DELETE FROM comparisons WHERE action_id = ?", (action_id,))
                await db.execute("DELETE FROM propagation_updates WHERE action_id = ?", (action_id,))
            else:
                await db.execute("DELETE FROM comparisons WHERE id = ?", (latest["id"],))
            await db.commit()
            _invalidate_rating_stats_cache()
            last_row = rows[-1]
            return {
                "winner_id": last_row["winner_id"],
                "loser_id": last_row["loser_id"],
                "comparisons_undone": len(rows),
                "propagations_undone": len(propagation_rows),
                "action_id": action_id,
            }
        return None
    finally:
        await db.close()


RANKING_SORTS = {
    "elo": "i.elo DESC",
    "elo_asc": "i.elo ASC",
    "comparisons": "i.comparisons DESC",
    "least_compared": "i.comparisons ASC",
    "filename": "i.filename ASC",
    "filename_desc": "i.filename DESC",
    "newest": "i.id DESC",
    "oldest": "i.id ASC",
    "date_taken": "i.date_taken IS NULL ASC, i.date_taken DESC, i.id DESC",
    "date_taken_asc": "i.date_taken IS NULL ASC, i.date_taken ASC, i.id ASC",
    "file_size": "i.file_size IS NULL ASC, i.file_size DESC, i.id DESC",
    "file_size_asc": "i.file_size IS NULL ASC, i.file_size ASC, i.id ASC",
    "date_modified": "i.file_modified_at IS NULL ASC, i.file_modified_at DESC, i.id DESC",
    "date_modified_asc": "i.file_modified_at IS NULL ASC, i.file_modified_at ASC, i.id ASC",
    "camera": "i.camera_make IS NULL ASC, i.camera_make ASC, i.camera_model ASC, i.id ASC",
    "camera_desc": "i.camera_make IS NULL ASC, i.camera_make DESC, i.camera_model DESC, i.id DESC",
    "resolution": "(i.width * i.height) IS NULL ASC, (i.width * i.height) DESC, i.id DESC",
    "resolution_asc": "(i.width * i.height) IS NULL ASC, (i.width * i.height) ASC, i.id ASC",
}
RANKING_INDEXES = {
    "elo": "idx_images_active_elo",
    "elo_asc": "idx_images_active_elo_asc",
    "comparisons": "idx_images_active_comparisons",
    "least_compared": "idx_images_active_comparisons_asc",
    "filename": "idx_images_active_filename",
    "filename_desc": "idx_images_active_filename",
    "newest": "idx_images_active_id",
    "oldest": "idx_images_active_id",
    "date_taken": "idx_images_active_date_taken_sort_desc",
    "date_taken_asc": "idx_images_active_date_taken_sort_asc",
    "file_size": "idx_images_active_file_size_sort_desc",
    "file_size_asc": "idx_images_active_file_size_sort_asc",
    "date_modified": "idx_images_active_modified_sort_desc",
    "date_modified_asc": "idx_images_active_modified_sort_asc",
    "camera": "idx_images_active_camera_sort_asc",
    "camera_desc": "idx_images_active_camera_sort_desc",
    "resolution": "idx_images_active_resolution_sort_desc",
    "resolution_asc": "idx_images_active_resolution_sort_asc",
}
SPARSE_VISIBLE_ID_FILTER_SORTS = {
    "elo",
    "elo_asc",
    "comparisons",
    "least_compared",
    "filename",
    "filename_desc",
    "date_taken",
    "date_taken_asc",
    "date_modified",
    "date_modified_asc",
    "camera",
    "camera_desc",
    "resolution",
    "resolution_asc",
}
VISIBLE_CACHE_FIRST_SORTS = {
    "comparisons",
    "least_compared",
    "filename",
    "filename_desc",
    "date_taken",
    "date_taken_asc",
    "date_modified",
    "date_modified_asc",
    "file_size",
    "file_size_asc",
    "camera",
    "camera_desc",
    "resolution",
    "resolution_asc",
}

STAR_THRESHOLDS = {5: 1500, 4: 1350, 3: 1250, 2: 1150, 1: 0}
IMAGE_EXTENSION_SEARCH_TERMS = {
    "avif", "bmp", "gif", "jpeg", "jpg", "png", "webp",
    "arw", "cr2", "cr3", "dng", "nef", "orf", "raf", "rw2",
}


def _ranking_filter_parts(
    orientation: str = "", compared: str = "", min_stars: int = 0,
    folder: str = "", flag: str = "", date_taken: str = "",
    file_type: str = "", camera: str = "", lens: str = "",
    visible_thumb_size: str = "", cache_root: str = "",
    text_query: str = "",
    include_source: bool = True,
) -> tuple[list[str], list]:
    conditions = [
        "i.status IN ('kept', 'maybe')",
        "i.missing_at IS NULL",
    ]
    if include_source:
        conditions[:0] = ["s.included = 1"]
    params = []

    if orientation in ("landscape", "portrait"):
        conditions.append("i.orientation = ?")
        params.append(orientation)

    if compared == "compared":
        conditions.append(
            "(i.comparisons > 0 OR i.propagated_updates > 0 OR ABS(COALESCE(i.elo, 1200.0) - 1200.0) > 0.0001)"
        )
    elif compared == "uncompared":
        conditions.append(
            "i.comparisons = 0 AND COALESCE(i.propagated_updates, 0) = 0 "
            "AND ABS(COALESCE(i.elo, 1200.0) - 1200.0) <= 0.0001"
        )
    elif compared == "confident":
        conditions.append("i.comparisons >= 10")

    if min_stars > 0 and min_stars in STAR_THRESHOLDS:
        conditions.append("i.elo >= ?")
        params.append(STAR_THRESHOLDS[min_stars])

    if folder:
        conditions.append("i.filepath LIKE ?")
        params.append(f"%/{folder}/%")

    if flag in ("picked", "unflagged", "rejected"):
        conditions.append("i.flag = ?")
        params.append(flag)

    if date_taken == "undated":
        conditions.append("i.date_taken IS NULL")
    elif date_taken.isdigit() and len(date_taken) == 4:
        start = f"{date_taken}-01-01 00:00:00"
        end = f"{int(date_taken) + 1}-01-01 00:00:00"
        conditions.append("i.date_taken >= ? AND i.date_taken < ?")
        params.extend([start, end])

    if file_type:
        normalized_type = file_type.lower().lstrip(".")
        conditions.append("i.file_ext IS NOT NULL")
        conditions.append("i.file_ext != ''")
        conditions.append("LOWER(i.file_ext) IN (?, ?)")
        params.extend([normalized_type, f".{normalized_type}"])

    if camera:
        conditions.append(
            "TRIM(COALESCE(i.camera_make, '') || ' ' || COALESCE(i.camera_model, '')) = ?"
        )
        params.append(camera)

    if lens:
        conditions.append("i.lens = ?")
        params.append(lens)

    if text_query:
        escaped = (
            text_query.strip()
            .replace("\\", "\\\\")
            .replace("%", "\\%")
            .replace("_", "\\_")
        )
        if escaped:
            extension_query = escaped.lower().lstrip(".")
            if extension_query in IMAGE_EXTENSION_SEARCH_TERMS:
                conditions.append("i.file_ext IS NOT NULL")
                conditions.append("i.file_ext != ''")
                conditions.append("LOWER(i.file_ext) IN (?, ?)")
                params.extend([extension_query, f".{extension_query}"])
            else:
                pattern = f"%{escaped}%"
                fields = (
                    "i.filename",
                    "i.filepath",
                    "i.date_taken",
                    "i.camera_make",
                    "i.camera_model",
                    "i.lens",
                    "i.file_ext",
                )
                conditions.append(
                    "("
                    + " OR ".join(f"{field} LIKE ? COLLATE NOCASE ESCAPE '\\'" for field in fields)
                    + ")"
                )
                params.extend([pattern] * len(fields))

    if visible_thumb_size and cache_root:
        conditions.append(
            "EXISTS ("
            "  SELECT 1 FROM cache_entries c "
            "  WHERE c.cache_root = ? AND c.size = ? AND c.image_id = i.id"
            ")"
        )
        params.extend([cache_root, visible_thumb_size])

    return conditions, params


def _ranking_index_for_query(
    sort: str,
    *,
    orientation: str = "",
    id_filter: set | None,
    text_query: str,
) -> str | None:
    if id_filter is not None or text_query:
        return None
    if sort == "elo" and orientation in ("landscape", "portrait"):
        return "idx_images_active_visible_orientation_elo"
    return RANKING_INDEXES.get(sort)


def _ranking_image_source(
    sort: str,
    *,
    orientation: str = "",
    id_filter: set | None,
    text_query: str,
) -> str:
    index_name = _ranking_index_for_query(
        sort,
        orientation=orientation,
        id_filter=id_filter,
        text_query=text_query,
    )
    if not index_name:
        return "images i"
    return f"images i INDEXED BY {index_name}"


def _ranking_count_image_source(
    *,
    file_type: str = "",
    id_filter: set | None,
    text_query: str = "",
) -> str:
    if file_type and id_filter is None and not text_query:
        return "images i INDEXED BY idx_images_missing_lower_file_ext_source"
    return "images i"


async def get_cached_image_ids(
    image_ids: list[int],
    size: str,
    cache_root: str,
    chunk_size: int = 900,
) -> set[int]:
    """Return IDs with a cache_entries row for the exact cache root/tier."""
    if not image_ids or not size or not cache_root:
        return set()
    unique_ids = list(dict.fromkeys(int(image_id) for image_id in image_ids))
    cached_all = await get_cached_image_id_set(size, cache_root)
    if cached_all:
        return {image_id for image_id in unique_ids if image_id in cached_all}
    return set()


async def get_cached_image_id_set(size: str, cache_root: str) -> frozenset[int]:
    """Return cached image IDs for one cache root/tier with a short TTL."""
    if not size or not cache_root:
        return frozenset()
    key = (cache_root, size)
    now = _time.time()
    cached_entry = _cached_image_ids_cache.get(key)
    if cached_entry and now < cached_entry["expires"]:
        return cached_entry["ids"]

    cached: set[int] = set()
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT image_id FROM cache_entries WHERE cache_root = ? AND size = ?",
            (cache_root, size),
        )
        cached.update(int(row["image_id"]) for row in await cursor.fetchall())
    finally:
        await db.close()
    frozen = frozenset(cached)
    now = _time.time()
    _cached_image_ids_cache[key] = {
        "ids": frozen,
        "expires": now + CACHED_IMAGE_IDS_TTL_SECONDS,
    }
    return frozen


async def get_active_source_id_set() -> frozenset[int]:
    now = _time.time()
    if now < _active_source_ids_cache["expires"]:
        return _active_source_ids_cache["ids"]

    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT id FROM catalog_sources WHERE included = 1 AND online = 1"
        )
        frozen = frozenset(int(row["id"]) for row in await cursor.fetchall())
    finally:
        await db.close()
    _active_source_ids_cache["ids"] = frozen
    _active_source_ids_cache["expires"] = now + ACTIVE_SOURCE_IDS_TTL_SECONDS
    return frozen


def _metadata_fts_query(text_query: str) -> str:
    return '"' + (text_query or "").replace('"', '""') + '"'


async def metadata_search_image_ids(text_query: str, *, max_results: int = 5000) -> set[int] | None:
    """Return a bounded metadata-search ID set using the trigram FTS index.

    None means "fall back to regular metadata LIKE filtering"; an empty set is
    a real no-match result and lets callers skip expensive count queries.
    """
    query = (text_query or "").strip()
    if len(query) < 3:
        return None
    active_source_ids = sorted(await get_active_source_id_set())
    if not active_source_ids:
        return set()
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT rowid AS id FROM images_metadata_fts "
            "WHERE images_metadata_fts MATCH ? LIMIT ?",
            [_metadata_fts_query(query), int(max_results) + 1],
        )
        candidate_ids = [int(row["id"]) for row in await cursor.fetchall()]
        if len(candidate_ids) > int(max_results):
            return None
        if not candidate_ids:
            return set()
        active_ids: set[int] = set()
        source_placeholders = ",".join("?" for _ in active_source_ids)
        for chunk in _chunked(candidate_ids, 900):
            id_placeholders = ",".join("?" for _ in chunk)
            cursor = await db.execute(
                "SELECT i.id FROM images i "
                f"WHERE i.id IN ({id_placeholders}) "
                f"AND i.source_id IN ({source_placeholders}) "
                "AND i.status IN ('kept', 'maybe') "
                "AND i.missing_at IS NULL",
                [*chunk, *active_source_ids],
            )
            active_ids.update(int(row["id"]) for row in await cursor.fetchall())
        return active_ids
    except Exception:
        return None
    finally:
        await db.close()


async def _cache_entry_count(size: str, cache_root: str) -> int:
    key = (cache_root, size)
    now = _time.time()
    cached = _cache_entry_count_cache.get(key)
    if cached and cached["expires"] > now:
        return int(cached["count"])
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT COUNT(*) AS count FROM cache_entries WHERE cache_root = ? AND size = ?",
            (cache_root, size),
        )
        count = int((await cursor.fetchone())["count"] or 0)
        _cache_entry_count_cache[key] = {
            "count": count,
            "expires": now + CACHE_ENTRY_COUNT_TTL_SECONDS,
        }
        return count
    finally:
        await db.close()


async def get_rankable_image_id_set() -> frozenset[int]:
    """Return active ranking image IDs with a short TTL for hot count paths."""
    now = _time.time()
    if now < _rankable_image_ids_cache["expires"]:
        return _rankable_image_ids_cache["ids"]
    if not await get_active_source_id_set():
        _rankable_image_ids_cache["ids"] = frozenset()
        _rankable_image_ids_cache["expires"] = now + CACHED_IMAGE_IDS_TTL_SECONDS
        return frozenset()

    ids: set[int] = set()
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT i.id FROM images i "
            "JOIN catalog_sources s ON s.id = i.source_id "
            "WHERE s.included = 1 "
            "AND i.status IN ('kept', 'maybe') AND i.missing_at IS NULL"
        )
        ids.update(int(row["id"]) for row in await cursor.fetchall())
    finally:
        await db.close()
    frozen = frozenset(ids)
    _rankable_image_ids_cache["ids"] = frozen
    _rankable_image_ids_cache["expires"] = now + CACHED_IMAGE_IDS_TTL_SECONDS
    return frozen


def _has_ranking_count_filters(
    orientation: str,
    compared: str,
    min_stars: int,
    folder: str,
    flag: str,
    date_taken: str,
    file_type: str,
    camera: str,
    lens: str,
    id_filter: set | None,
    text_query: str,
) -> bool:
    return bool(
        orientation or compared or min_stars > 0 or folder or flag or date_taken
        or file_type or camera or lens or id_filter is not None or text_query
    )


async def _count_rankings_with_id_filter(
    conn,
    conditions: list[str],
    params: list,
    id_values,
) -> int:
    ids = list(dict.fromkeys(int(image_id) for image_id in id_values))
    if not ids:
        return 0
    total = 0
    for chunk in _chunked(ids, 900):
        placeholders = ",".join("?" for _ in chunk)
        cursor = await conn.execute(
            f"SELECT COUNT(*) AS count FROM images i "
            f"JOIN catalog_sources s ON s.id = i.source_id "
            f"WHERE {' AND '.join(conditions + [f'i.id IN ({placeholders})'])}",
            list(params) + chunk,
        )
        row = await cursor.fetchone()
        total += int(row["count"] or 0)
    return total


async def get_rankings(limit: int = 100, offset: int = 0, sort: str = "elo",
                       orientation: str = "", compared: str = "", min_stars: int = 0,
                       folder: str = "", flag: str = "", date_taken: str = "",
                       file_type: str = "", camera: str = "", lens: str = "",
                       id_filter: set = None,
                       visible_thumb_size: str = "", cache_root: str = "",
                       text_query: str = ""):
    counts = await get_catalog_image_counts()
    active_images = int(counts.get("active_images") or 0)
    if active_images <= 0:
        return []
    all_sources_available_for_visible = (
        int(counts.get("removed_images") or 0) == 0
    )
    use_cache_first_visible = (
        visible_thumb_size
        and cache_root
        and id_filter is None
        and (sort in VISIBLE_CACHE_FIRST_SORTS or bool(text_query))
        and all_sources_available_for_visible
        and await _cache_entry_count(visible_thumb_size, cache_root) <= RANKING_CACHE_FIRST_VISIBLE_LIMIT
    )
    use_sparse_visible_id_filter = (
        visible_thumb_size
        and cache_root
        and id_filter is not None
        and sort in SPARSE_VISIBLE_ID_FILTER_SORTS
        and not use_cache_first_visible
    )
    if visible_thumb_size and cache_root and (id_filter is not None or use_sparse_visible_id_filter):
        cached_ids = set(await get_cached_image_id_set(visible_thumb_size, cache_root))
        if id_filter is not None:
            cached_ids.intersection_update(int(image_id) for image_id in id_filter)
        if not cached_ids:
            return []
        if len(cached_ids) <= RANKING_VISIBLE_ID_FILTER_LIMIT:
            id_filter = cached_ids
            visible_thumb_size = ""
            cache_root = ""

    db = await get_db()
    try:
        all_catalog_images_active = (
            active_images > 0
            and active_images == int(counts.get("total_catalog_images") or 0)
            and int(counts.get("removed_images") or 0) == 0
        )
        all_sources_available = (
            int(counts.get("removed_images") or 0) == 0
        )
        order = RANKING_SORTS.get(sort, "elo DESC")
        conditions, params = _ranking_filter_parts(
            orientation=orientation, compared=compared, min_stars=min_stars,
            folder=folder, flag=flag, date_taken=date_taken,
            file_type=file_type, camera=camera, lens=lens,
            visible_thumb_size=visible_thumb_size, cache_root=cache_root,
            text_query=text_query,
            include_source=not all_catalog_images_active,
        )

        if id_filter is not None:
            if not id_filter:
                return []
            placeholders = ",".join("?" * len(id_filter))
            conditions.append(f"i.id IN ({placeholders})")
            params.extend(id_filter)

        where = " AND ".join(conditions)
        if (
            visible_thumb_size
            and cache_root
            and id_filter is None
            and all_sources_available
            and use_cache_first_visible
        ):
            conditions_no_source, params_no_source = _ranking_filter_parts(
                orientation=orientation, compared=compared, min_stars=min_stars,
                folder=folder, flag=flag, date_taken=date_taken,
                file_type=file_type, camera=camera, lens=lens,
                text_query=text_query,
                include_source=False,
            )
            cursor = await db.execute(
                f"SELECT i.id, i.source_id, i.filename, i.filepath, i.elo, i.comparisons, "
                f"i.propagated_updates, "
                f"i.status, i.flag, i.aspect_ratio, "
                f"i.date_taken, i.camera_make, i.camera_model, i.lens, i.file_ext, i.file_size, "
                f"i.file_modified_at, i.width, i.height, i.latitude, i.longitude, i.created_at "
                f"FROM cache_entries c INDEXED BY sqlite_autoindex_cache_entries_1 "
                f"CROSS JOIN images i ON i.id = c.image_id "
                f"WHERE c.cache_root = ? AND c.size = ? AND {' AND '.join(conditions_no_source)} "
                f"ORDER BY {order} LIMIT ? OFFSET ?",
                [cache_root, visible_thumb_size] + params_no_source + [limit, offset],
            )
            return await cursor.fetchall()

        params.extend([limit, offset])
        image_source = _ranking_image_source(
            sort,
            orientation=orientation,
            id_filter=id_filter,
            text_query=text_query,
        )
        source_join = (
            "JOIN catalog_sources s ON s.id = i.source_id "
            if not all_catalog_images_active
            else ""
        )

        cursor = await db.execute(
            f"SELECT i.id, i.source_id, i.filename, i.filepath, i.elo, i.comparisons, "
            f"i.propagated_updates, "
            f"i.status, i.flag, i.aspect_ratio, "
            f"i.date_taken, i.camera_make, i.camera_model, i.lens, i.file_ext, i.file_size, "
            f"i.file_modified_at, i.width, i.height, i.latitude, i.longitude, i.created_at "
            f"FROM {image_source} {source_join}"
            f"WHERE {where} ORDER BY {order} LIMIT ? OFFSET ?",
            params,
        )
        return await cursor.fetchall()
    finally:
        await db.close()


def _ranking_count_cache_key(
    orientation: str = "",
    compared: str = "",
    min_stars: int = 0,
    folder: str = "",
    flag: str = "",
    date_taken: str = "",
    file_type: str = "",
    camera: str = "",
    lens: str = "",
    id_filter: set = None,
    visible_thumb_size: str = "",
    cache_root: str = "",
    text_query: str = "",
):
    if id_filter is not None:
        return None
    return (
        orientation or "",
        compared or "",
        int(min_stars or 0),
        folder or "",
        flag or "",
        date_taken or "",
        file_type or "",
        camera or "",
        lens or "",
        visible_thumb_size or "",
        cache_root or "",
        text_query or "",
    )


def _facet_cache_key(
    orientation: str = "", compared: str = "", min_stars: int = 0,
    folder: str = "", flag: str = "", date_taken: str = "",
    file_type: str = "", camera: str = "", lens: str = "",
    visible_thumb_size: str = "", cache_root: str = "",
) -> tuple:
    return (
        orientation or "",
        compared or "",
        int(min_stars or 0),
        folder or "",
        flag or "",
        date_taken or "",
        file_type or "",
        camera or "",
        lens or "",
        visible_thumb_size or "",
        cache_root or "",
    )


async def _count_rankings_uncached(orientation: str = "", compared: str = "", min_stars: int = 0,
                                  folder: str = "", flag: str = "", date_taken: str = "",
                                  file_type: str = "", camera: str = "", lens: str = "",
                                  id_filter: set = None,
                                  visible_thumb_size: str = "", cache_root: str = "",
                                  text_query: str = "") -> int:
    if not _has_ranking_count_filters(
        orientation, compared, min_stars, folder, flag, date_taken,
        file_type, camera, lens, id_filter, text_query,
    ):
        if not visible_thumb_size or not cache_root:
            counts = await get_catalog_image_counts()
            return int(counts.get("active_images") or 0)
        counts = await get_catalog_image_counts()
        if (
            int(counts.get("active_images") or 0) > 0
            and (
                int(counts.get("active_images") or 0) == int(counts.get("total_catalog_images") or 0)
                or (
                    int(counts.get("removed_images") or 0) == 0
                )
            )
        ):
            db = await get_db()
            try:
                cursor = await db.execute(
                    "SELECT COUNT(*) AS count FROM cache_entries "
                    "WHERE cache_root = ? AND size = ?",
                    (cache_root, visible_thumb_size),
                )
                return min(
                    int(counts.get("active_images") or 0),
                    int((await cursor.fetchone())["count"] or 0),
                )
            finally:
                await db.close()
        db = await get_db()
        try:
            cursor = await db.execute(
                "SELECT COUNT(*) AS count FROM cache_entries c "
                "JOIN images i ON i.id = c.image_id "
                "JOIN catalog_sources s ON s.id = i.source_id "
                "WHERE c.cache_root = ? AND c.size = ? "
                "AND s.included = 1 "
                "AND i.status IN ('kept', 'maybe') AND i.missing_at IS NULL",
                (cache_root, visible_thumb_size),
            )
            return int((await cursor.fetchone())["count"] or 0)
        finally:
            await db.close()

    db = await get_db()
    try:
        counts = await get_catalog_image_counts()
        all_catalog_images_active = (
            int(counts.get("active_images") or 0) > 0
            and int(counts.get("active_images") or 0) == int(counts.get("total_catalog_images") or 0)
            and int(counts.get("removed_images") or 0) == 0
        )
        all_sources_available = (
            int(counts.get("removed_images") or 0) == 0
        )
        if visible_thumb_size and cache_root and id_filter is None:
            conditions, params = _ranking_filter_parts(
                orientation=orientation, compared=compared, min_stars=min_stars,
                folder=folder, flag=flag, date_taken=date_taken,
                file_type=file_type, camera=camera, lens=lens,
                text_query=text_query,
                include_source=not all_sources_available,
            )
            source_join = (
                "JOIN catalog_sources s ON s.id = i.source_id "
                if not all_sources_available
                else ""
            )
            cursor = await db.execute(
                "SELECT COUNT(*) AS count FROM cache_entries c INDEXED BY sqlite_autoindex_cache_entries_1 "
                "CROSS JOIN images i "
                f"{source_join}"
                "WHERE c.cache_root = ? AND c.size = ? "
                "AND i.id = c.image_id "
                f"AND {' AND '.join(conditions)}",
                [cache_root, visible_thumb_size] + params,
            )
            row = await cursor.fetchone()
            return int(row["count"] or 0)

        cached_id_filter = None
        if visible_thumb_size and cache_root:
            cached_id_filter = set(await get_cached_image_id_set(visible_thumb_size, cache_root))
            visible_thumb_size = ""
            cache_root = ""

        conditions, params = _ranking_filter_parts(
            orientation=orientation, compared=compared, min_stars=min_stars,
            folder=folder, flag=flag, date_taken=date_taken,
            file_type=file_type, camera=camera, lens=lens,
            visible_thumb_size=visible_thumb_size, cache_root=cache_root,
            text_query=text_query,
            include_source=not all_sources_available,
        )

        if cached_id_filter is not None:
            if id_filter is not None:
                cached_id_filter.intersection_update(int(image_id) for image_id in id_filter)
            return await _count_rankings_with_id_filter(db, conditions, params, cached_id_filter)

        if id_filter is not None:
            if not id_filter:
                return 0
            return await _count_rankings_with_id_filter(db, conditions, params, id_filter)
        source_join = (
            "JOIN catalog_sources s ON s.id = i.source_id "
            if not all_sources_available
            else ""
        )
        image_source = _ranking_count_image_source(
            file_type=file_type,
            id_filter=id_filter,
            text_query=text_query,
        )
        cursor = await db.execute(
            f"SELECT COUNT(*) AS count FROM {image_source} "
            f"{source_join}"
            f"WHERE {' AND '.join(conditions)}",
            params,
        )
        row = await cursor.fetchone()
        return int(row["count"] or 0)
    finally:
        await db.close()


async def count_rankings(orientation: str = "", compared: str = "", min_stars: int = 0,
                         folder: str = "", flag: str = "", date_taken: str = "",
                         file_type: str = "", camera: str = "", lens: str = "",
                         id_filter: set = None,
                         visible_thumb_size: str = "", cache_root: str = "",
                         text_query: str = "") -> int:
    cache_key = _ranking_count_cache_key(
        orientation=orientation, compared=compared, min_stars=min_stars,
        folder=folder, flag=flag, date_taken=date_taken,
        file_type=file_type, camera=camera, lens=lens, id_filter=id_filter,
        visible_thumb_size=visible_thumb_size, cache_root=cache_root,
        text_query=text_query,
    )
    if cache_key is not None:
        now = _time.time()
        cached = _ranking_count_cache.get(cache_key)
        if cached and cached["expires"] > now:
            return int(cached["value"])

    value = await _count_rankings_uncached(
        orientation=orientation, compared=compared, min_stars=min_stars,
        folder=folder, flag=flag, date_taken=date_taken,
        file_type=file_type, camera=camera, lens=lens, id_filter=id_filter,
        visible_thumb_size=visible_thumb_size, cache_root=cache_root,
        text_query=text_query,
    )
    if cache_key is not None:
        _ranking_count_cache[cache_key] = {
            "value": int(value),
            "expires": _time.time() + RANKING_COUNT_CACHE_TTL_SECONDS,
        }
    return value


async def get_date_groups(orientation: str = "", compared: str = "", min_stars: int = 0,
                          folder: str = "", flag: str = "", date_taken: str = "",
                          file_type: str = "", camera: str = "", lens: str = "",
                          visible_thumb_size: str = "", cache_root: str = "",
                          _force_refresh: bool = False):
    cache_key = _facet_cache_key(
        orientation=orientation, compared=compared, min_stars=min_stars,
        folder=folder, flag=flag, date_taken=date_taken,
        file_type=file_type, camera=camera, lens=lens,
        visible_thumb_size=visible_thumb_size, cache_root=cache_root,
    )
    now = _time.time()
    cached = _date_groups_cache.get(cache_key)
    if cached and not _force_refresh:
        if cached["expires"] > now:
            return cached["data"]
        if cache_key not in _date_groups_refreshing:
            _date_groups_refreshing.add(cache_key)

            async def _refresh_date_groups():
                try:
                    await get_date_groups(
                        orientation=orientation, compared=compared, min_stars=min_stars,
                        folder=folder, flag=flag, date_taken=date_taken,
                        file_type=file_type, camera=camera, lens=lens,
                        visible_thumb_size=visible_thumb_size, cache_root=cache_root,
                        _force_refresh=True,
                    )
                finally:
                    _date_groups_refreshing.discard(cache_key)

            asyncio.create_task(_refresh_date_groups())
        return cached["data"]

    counts = await get_catalog_image_counts()
    active_images = int(counts.get("active_images") or 0)
    if active_images <= 0:
        return []
    db = await get_db()
    try:
        has_filters = _has_ranking_count_filters(
            orientation, compared, min_stars, folder, flag, date_taken,
            file_type, camera, lens, None, "",
        )
        all_catalog_images_active = (
            not has_filters
            and active_images == int(counts.get("total_catalog_images") or 0)
        )
        all_sources_available = (
            int(counts.get("removed_images") or 0) == 0
        )
        conditions, params = _ranking_filter_parts(
            orientation=orientation, compared=compared, min_stars=min_stars,
            folder=folder, flag=flag, date_taken=date_taken,
            file_type=file_type, camera=camera, lens=lens,
            include_source=not all_sources_available,
        )
        select_sql = (
            "SELECT "
            "CASE WHEN i.date_taken IS NOT NULL AND length(i.date_taken) >= 7 "
            "THEN substr(i.date_taken, 1, 7) ELSE '' END AS date_group, "
            "COUNT(*) AS count "
        )
        if visible_thumb_size and cache_root:
            if all_sources_available:
                cursor = await db.execute(
                    select_sql
                    + "FROM cache_entries c INDEXED BY sqlite_autoindex_cache_entries_1 "
                    "CROSS JOIN images i ON i.id = c.image_id "
                    "WHERE c.cache_root = ? AND c.size = ? "
                    f"AND {' AND '.join(conditions)} "
                    "GROUP BY date_group ORDER BY date_group DESC",
                    [cache_root, visible_thumb_size] + params,
                )
            else:
                cursor = await db.execute(
                    select_sql
                    + "FROM cache_entries c "
                    "JOIN images i ON i.id = c.image_id "
                    "JOIN catalog_sources s ON s.id = i.source_id "
                    f"WHERE c.cache_root = ? AND c.size = ? AND {' AND '.join(conditions)} "
                    "GROUP BY date_group ORDER BY date_group DESC",
                    [cache_root, visible_thumb_size] + params,
                )
        else:
            if all_sources_available:
                cursor = await db.execute(
                    select_sql
                    + "FROM images i "
                    f"WHERE {' AND '.join(conditions)} "
                    "GROUP BY date_group ORDER BY date_group DESC",
                    params,
                )
            else:
                cursor = await db.execute(
                    select_sql
                    + "FROM images i JOIN catalog_sources s ON s.id = i.source_id "
                    f"WHERE {' AND '.join(conditions)} "
                    "GROUP BY date_group ORDER BY date_group DESC",
                    params,
                )
        groups = []
        for row in await cursor.fetchall():
            date_group = row["date_group"] or ""
            if date_group:
                try:
                    from datetime import datetime
                    label = datetime.strptime(date_group, "%Y-%m").strftime("%B %Y")
                except ValueError:
                    label = date_group
            else:
                label = "No Date"
            groups.append({"date": date_group, "label": label, "count": row["count"]})
        _date_groups_cache[cache_key] = {
            "data": groups,
            "expires": _time.time() + FACET_CACHE_TTL_SECONDS,
        }
        return groups
    finally:
        await db.close()


async def get_map_markers(orientation: str = "", compared: str = "", min_stars: int = 0,
                          folder: str = "", flag: str = "", date_taken: str = "",
                          file_type: str = "", camera: str = "", lens: str = "",
                          visible_thumb_size: str = "", cache_root: str = ""):
    cache_key = _facet_cache_key(
        orientation=orientation, compared=compared, min_stars=min_stars,
        folder=folder, flag=flag, date_taken=date_taken,
        file_type=file_type, camera=camera, lens=lens,
        visible_thumb_size=visible_thumb_size, cache_root=cache_root,
    )
    now = _time.time()
    cached = _map_markers_cache.get(cache_key)
    if cached and cached["expires"] > now:
        return cached["data"]

    catalog_counts = await get_catalog_image_counts()
    active_images = int(catalog_counts.get("active_images") or 0)
    if active_images <= 0:
        return {
            "markers": [],
            "total_count": 0,
            "visible_count": 0,
            "gps_count": 0,
            "gps_total_count": 0,
            "hidden_pending_thumbnails": 0,
        }
    all_sources_available = (
        int(catalog_counts.get("removed_images") or 0) == 0
    )
    db = await get_db()
    try:
        conditions, params = _ranking_filter_parts(
            orientation=orientation, compared=compared, min_stars=min_stars,
            folder=folder, flag=flag, date_taken=date_taken,
            file_type=file_type, camera=camera, lens=lens,
            include_source=not all_sources_available,
        )
        has_filters = _has_ranking_count_filters(
            orientation, compared, min_stars, folder, flag, date_taken,
            file_type, camera, lens, None, "",
        )
        if not has_filters:
            total_count = active_images
            all_catalog_images_active = (
                total_count > 0
                and total_count == int(catalog_counts.get("total_catalog_images") or 0)
            )
        else:
            all_catalog_images_active = False
            total_count = await count_rankings(
                orientation=orientation, compared=compared, min_stars=min_stars,
                folder=folder, flag=flag, date_taken=date_taken,
                file_type=file_type, camera=camera, lens=lens,
            )
        gps_conditions = conditions + ["i.latitude IS NOT NULL", "i.longitude IS NOT NULL"]
        if all_sources_available:
            gps_total_cursor = await db.execute(
                "SELECT COUNT(*) AS count FROM images i "
                f"WHERE {' AND '.join(gps_conditions)}",
                params,
            )
        else:
            gps_total_cursor = await db.execute(
                f"SELECT COUNT(*) AS count FROM images i INDEXED BY idx_images_active_gps_count "
                f"JOIN catalog_sources s ON s.id = i.source_id "
                f"WHERE {' AND '.join(gps_conditions)}",
                params,
            )
        gps_total_count = int((await gps_total_cursor.fetchone())["count"] or 0)

        marker_conditions = list(gps_conditions)
        marker_params = list(params)
        visible_total_count = total_count
        if visible_thumb_size and cache_root:
            if all_sources_available and not has_filters:
                visible_counts = await get_visible_pairing_pool_counts(visible_thumb_size, cache_root)
                visible_total_count = int(visible_counts.get("visible_images") or 0)
            else:
                visible_total_count = await count_rankings(
                    orientation=orientation, compared=compared, min_stars=min_stars,
                    folder=folder, flag=flag, date_taken=date_taken,
                    file_type=file_type, camera=camera, lens=lens,
                    visible_thumb_size=visible_thumb_size, cache_root=cache_root,
                )

        if gps_total_count <= 0:
            result = {
                "markers": [],
                "total_count": total_count,
                "visible_count": visible_total_count,
                "gps_count": 0,
                "gps_total_count": 0,
                "hidden_pending_thumbnails": 0,
            }
            _map_markers_cache[cache_key] = {
                "data": result,
                "expires": _time.time() + FACET_CACHE_TTL_SECONDS,
            }
            return result

        if visible_thumb_size and cache_root:

            if all_sources_available:
                cursor = await db.execute(
                    "SELECT i.id, i.filename, i.latitude, i.longitude "
                    "FROM cache_entries c INDEXED BY sqlite_autoindex_cache_entries_1 "
                    "CROSS JOIN images i ON i.id = c.image_id "
                    "WHERE c.cache_root = ? AND c.size = ? "
                    f"AND {' AND '.join(marker_conditions)}",
                    [cache_root, visible_thumb_size] + marker_params,
                )
            else:
                cursor = await db.execute(
                    "SELECT i.id, i.filename, i.latitude, i.longitude FROM cache_entries c "
                    "JOIN images i ON i.id = c.image_id "
                    "JOIN catalog_sources s ON s.id = i.source_id "
                    f"WHERE c.cache_root = ? AND c.size = ? AND {' AND '.join(marker_conditions)}",
                    [cache_root, visible_thumb_size] + marker_params,
                )
        else:
            if all_sources_available:
                cursor = await db.execute(
                    "SELECT i.id, i.filename, i.latitude, i.longitude FROM images i "
                    f"WHERE {' AND '.join(marker_conditions)}",
                    marker_params,
                )
            else:
                cursor = await db.execute(
                    "SELECT i.id, i.filename, i.latitude, i.longitude FROM images i "
                    "JOIN catalog_sources s ON s.id = i.source_id "
                    f"WHERE {' AND '.join(marker_conditions)}",
                    marker_params,
                )
        markers = [
            {
                "id": row["id"],
                "filename": row["filename"],
                "lat": row["latitude"],
                "lng": row["longitude"],
                "thumb_url": f"/api/thumb/sm/{row['id']}",
            }
            for row in await cursor.fetchall()
        ]
        result = {
            "markers": markers,
            "total_count": total_count,
            "visible_count": visible_total_count,
            "gps_count": len(markers),
            "gps_total_count": gps_total_count,
            "hidden_pending_thumbnails": max(gps_total_count - len(markers), 0),
        }
        _map_markers_cache[cache_key] = {
            "data": result,
            "expires": _time.time() + FACET_CACHE_TTL_SECONDS,
        }
        return result
    finally:
        await db.close()


async def get_filter_options():
    global _filter_options_refreshing
    data = _filter_options_cache["data"]
    if data and _time.time() < _filter_options_cache["expires"]:
        return data
    if data:
        if not _filter_options_refreshing:
            _filter_options_refreshing = True

            async def _refresh_filter_options():
                global _filter_options_refreshing
                try:
                    await _load_filter_options_uncached()
                finally:
                    _filter_options_refreshing = False

            asyncio.create_task(_refresh_filter_options())
        return data
    return await _load_filter_options_uncached()


async def _load_filter_options_uncached():
    if _filter_options_cache["data"] and _time.time() < _filter_options_cache["expires"]:
        return _filter_options_cache["data"]
    counts = await get_catalog_image_counts()
    active_images = int(counts.get("active_images") or 0)
    if active_images <= 0:
        result = {
            "years": [],
            "file_types": [],
            "undated": 0,
            "cameras": [],
            "lenses": [],
        }
        _filter_options_cache["data"] = result
        _filter_options_cache["expires"] = _time.time() + FILTER_OPTIONS_CACHE_TTL_SECONDS
        return result

    all_catalog_images_active = (
        active_images == int(counts.get("total_catalog_images") or 0)
        and int(counts.get("removed_images") or 0) == 0
    )
    all_sources_available = (
        int(counts.get("removed_images") or 0) == 0
    )
    if all_catalog_images_active or all_sources_available:
        image_source_clause = ""
        bare_source_clause = ""
        source_params = ()
    else:
        active_source_ids = sorted(await get_active_source_id_set())
        if not active_source_ids:
            result = {
                "years": [],
                "file_types": [],
                "undated": 0,
                "cameras": [],
                "lenses": [],
            }
            _filter_options_cache["data"] = result
            _filter_options_cache["expires"] = _time.time() + FILTER_OPTIONS_CACHE_TTL_SECONDS
            return result
        source_placeholders = ",".join("?" for _ in active_source_ids)
        image_source_clause = f"i.source_id IN ({source_placeholders}) AND "
        bare_source_clause = f"source_id IN ({source_placeholders}) AND "
        source_params = tuple(active_source_ids)

    def _filter_rows(sql: str, params=()):
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()

    base_where = f"{bare_source_clause}missing_at IS NULL"
    (
        year_rows,
        undated_rows,
        file_type_rows,
        camera_rows,
        lens_rows,
    ) = await asyncio.gather(
        asyncio.to_thread(
            _filter_rows,
            "SELECT SUBSTR(date_taken, 1, 4) AS value, COUNT(*) AS count "
            f"FROM images WHERE {base_where} "
            "AND date_taken IS NOT NULL AND LENGTH(date_taken) >= 4 "
            "GROUP BY value",
            source_params,
        ),
        asyncio.to_thread(
            _filter_rows,
            "SELECT COUNT(*) AS count "
            f"FROM images WHERE {base_where} "
            "AND (date_taken IS NULL OR LENGTH(date_taken) < 4)",
            source_params,
        ),
        asyncio.to_thread(
            _filter_rows,
            "SELECT file_ext AS value, COUNT(*) AS count "
            f"FROM images WHERE {base_where} "
            "AND file_ext IS NOT NULL AND file_ext != '' "
            "GROUP BY value",
            source_params,
        ),
        asyncio.to_thread(
            _filter_rows,
            "SELECT TRIM(COALESCE(camera_make, '') || ' ' || COALESCE(camera_model, '')) AS value, "
            "COUNT(*) AS count "
            f"FROM images WHERE {base_where} "
            "AND (camera_make IS NOT NULL OR camera_model IS NOT NULL) "
            "GROUP BY value HAVING value != ''",
            source_params,
        ),
        asyncio.to_thread(
            _filter_rows,
            "SELECT lens AS value, COUNT(*) AS count "
            f"FROM images WHERE {base_where} "
            "AND lens IS NOT NULL AND lens != '' "
            "GROUP BY value",
            source_params,
        ),
    )

    file_type_counts: Counter[str] = Counter()
    for row in file_type_rows:
        ext = str(row["value"] or "").strip().lower().lstrip(".")
        if ext:
            file_type_counts[ext] += int(row["count"] or 0)

    result = {
        "years": [
            {"year": row["value"], "count": int(row["count"] or 0)}
            for row in sorted(year_rows, key=lambda item: item["value"], reverse=True)
        ],
        "file_types": [
            {"ext": ext, "count": count}
            for ext, count in sorted(
                file_type_counts.items(),
                key=lambda item: (-int(item[1] or 0), item[0]),
            )
        ],
        "undated": int(undated_rows[0]["count"] or 0) if undated_rows else 0,
        "cameras": [
            {"camera": row["value"], "count": int(row["count"] or 0)}
            for row in sorted(
                camera_rows,
                key=lambda item: (-int(item["count"] or 0), item["value"]),
            )[:200]
        ],
        "lenses": [
            {"lens": row["value"], "count": int(row["count"] or 0)}
            for row in sorted(
                lens_rows,
                key=lambda item: (-int(item["count"] or 0), item["value"]),
            )[:200]
        ],
    }
    _filter_options_cache["data"] = result
    _filter_options_cache["expires"] = _time.time() + FILTER_OPTIONS_CACHE_TTL_SECONDS
    return result


async def get_stats():
    global _stats_inflight_task
    cached_data = _stats_cache["data"]
    if cached_data and _time.time() < _stats_cache["expires"]:
        return cached_data
    loop = asyncio.get_running_loop()
    task = _stats_inflight_task
    if cached_data:
        if task is None or task.done() or task.get_loop() is not loop:
            _stats_inflight_task = loop.create_task(_get_stats_uncached())
        return cached_data
    if task is not None and not task.done() and task.get_loop() is loop:
        return await task
    task = loop.create_task(_get_stats_uncached())
    _stats_inflight_task = task
    try:
        return await task
    finally:
        if _stats_inflight_task is task:
            _stats_inflight_task = None


async def _get_stats_uncached():
    if _stats_cache["data"] and _time.time() < _stats_cache["expires"]:
        return _stats_cache["data"]
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT "
            "COALESCE(SUM(image_count), 0) AS catalog_images, "
            "COALESCE(SUM(active_image_count), 0) AS active_images, "
            "COALESCE(SUM(CASE WHEN included = 0 THEN image_count ELSE 0 END), 0) AS removed_images, "
            "COALESCE(SUM(CASE WHEN included = 1 AND online = 0 THEN image_count ELSE 0 END), 0) AS offline_images "
            "FROM catalog_sources"
        )
        source_counts = await cursor.fetchone()

        active = int(source_counts["active_images"] or 0)
        catalog_images = int(source_counts["catalog_images"] or 0)
        all_catalog_images_active = (
            active == catalog_images
            and int(source_counts["removed_images"] or 0) == 0
        )

        if active <= 0:
            flag_counts = {}
        elif all_catalog_images_active:
            cursor = await db.execute(
                "SELECT flag, COUNT(*) AS count FROM images "
                "WHERE flag IN ('picked', 'rejected') GROUP BY flag"
            )
            flag_counts = {
                row["flag"]: int(row["count"] or 0)
                for row in await cursor.fetchall()
            }
        else:
            cursor = await db.execute(
                "SELECT i.flag, COUNT(*) AS count "
                "FROM images i JOIN catalog_sources s ON s.id = i.source_id "
                "WHERE s.included = 1 AND i.missing_at IS NULL "
                "AND i.flag IN ('picked', 'rejected') "
                "GROUP BY i.flag"
            )
            flag_counts = {
                row["flag"]: int(row["count"] or 0)
                for row in await cursor.fetchall()
            }

        cursor = await db.execute("SELECT COUNT(*) as c FROM comparisons")
        total_catalog_comparison_rows = int((await cursor.fetchone())["c"] or 0)

        invalid_comparison_rows = 0
        invalid_comparison_endpoints = 0
        if all_catalog_images_active:
            direct_comparison_rows = total_catalog_comparison_rows
        elif active <= 0:
            direct_comparison_rows = 0
        else:
            cursor = await db.execute(
                "WITH invalid_endpoints AS ("
                "  SELECT c.rowid AS comparison_rowid "
                "  FROM images i JOIN catalog_sources s ON s.id = i.source_id "
                "  JOIN comparisons c INDEXED BY idx_comparisons_pair ON c.winner_id = i.id "
                "  WHERE NOT (s.included = 1 AND i.missing_at IS NULL) "
                "  UNION ALL "
                "  SELECT c.rowid AS comparison_rowid "
                "  FROM images i JOIN catalog_sources s ON s.id = i.source_id "
                "  JOIN comparisons c INDEXED BY idx_comparisons_loser ON c.loser_id = i.id "
                "  WHERE NOT (s.included = 1 AND i.missing_at IS NULL)"
                ") "
                "SELECT COUNT(DISTINCT comparison_rowid) AS invalid_rows, "
                "COUNT(*) AS invalid_endpoints FROM invalid_endpoints"
            )
            invalid_comparisons = await cursor.fetchone()
            invalid_comparison_rows = int(invalid_comparisons["invalid_rows"] or 0)
            invalid_comparison_endpoints = int(invalid_comparisons["invalid_endpoints"] or 0)
            direct_comparison_rows = max(0, total_catalog_comparison_rows - invalid_comparison_rows)

        if active <= 0:
            ranking_counts = {
                "image_comparison_count": 0,
                "propagated_update_count": 0,
                "rated_images": 0,
            }
        elif all_catalog_images_active:
            cursor = await db.execute(
                "SELECT "
                "COALESCE(SUM(COALESCE(comparisons, 0)), 0) AS image_comparison_count, "
                "COALESCE(SUM(COALESCE(propagated_updates, 0)), 0) AS propagated_update_count, "
                "SUM(CASE WHEN COALESCE(comparisons, 0) > 0 "
                "      OR COALESCE(propagated_updates, 0) > 0 "
                "      OR ABS(COALESCE(elo, 1200.0) - 1200.0) > 0.0001 "
                "    THEN 1 ELSE 0 END) AS rated_images "
                "FROM images"
            )
            ranking_counts = await cursor.fetchone()
        else:
            cursor = await db.execute(
                "SELECT "
                "COALESCE(SUM(COALESCE(i.comparisons, 0)), 0) AS image_comparison_count, "
                "COALESCE(SUM(COALESCE(i.propagated_updates, 0)), 0) AS propagated_update_count, "
                "SUM(CASE WHEN COALESCE(i.comparisons, 0) > 0 "
                "      OR COALESCE(i.propagated_updates, 0) > 0 "
                "      OR ABS(COALESCE(i.elo, 1200.0) - 1200.0) > 0.0001 "
                "    THEN 1 ELSE 0 END) AS rated_images "
                "FROM images i INDEXED BY idx_images_source_missing_rating_signal "
                "LEFT JOIN catalog_sources s ON s.id = i.source_id "
                "WHERE s.included = 1 AND i.missing_at IS NULL"
            )
            ranking_counts = await cursor.fetchone()

        if all_catalog_images_active:
            direct_image_history_count = total_catalog_comparison_rows * 2
        elif active <= 0:
            direct_image_history_count = 0
        else:
            direct_image_history_count = max(
                0,
                (total_catalog_comparison_rows * 2) - invalid_comparison_endpoints,
            )

        image_comparison_count = int(ranking_counts["image_comparison_count"] or 0)
        propagated_update_count = int(ranking_counts["propagated_update_count"] or 0)
        imported_ranking_without_history = max(0, image_comparison_count - direct_image_history_count)
        ranking_signal_count = direct_comparison_rows + imported_ranking_without_history + propagated_update_count

        if all_catalog_images_active:
            catalog_image_comparison_count = image_comparison_count
            catalog_propagated_update_count = propagated_update_count
        else:
            cursor = await db.execute(
                "SELECT "
                "COALESCE(SUM(COALESCE(comparisons, 0)), 0) AS image_comparison_count, "
                "COALESCE(SUM(COALESCE(propagated_updates, 0)), 0) AS propagated_update_count "
                "FROM images"
            )
            catalog_ranking_counts = await cursor.fetchone()
            catalog_image_comparison_count = int(catalog_ranking_counts["image_comparison_count"] or 0)
            catalog_propagated_update_count = int(catalog_ranking_counts["propagated_update_count"] or 0)
        catalog_imported_without_history = max(
            0,
            catalog_image_comparison_count - (total_catalog_comparison_rows * 2),
        )
        catalog_ranking_signal_count = (
            total_catalog_comparison_rows
            + catalog_imported_without_history
            + catalog_propagated_update_count
        )

        result = {
            "total_images": active,
            "active_images": active,
            "total_catalog_images": catalog_images,
            "removed_images": int(source_counts["removed_images"] or 0),
            "offline_images": int(source_counts["offline_images"] or 0),
            "kept": active,
            "maybe": 0,
            "picked": int(flag_counts.get("picked") or 0),
            "rejected": int(flag_counts.get("rejected") or 0),
            "total_comparisons": ranking_signal_count,
            "total_catalog_comparisons": catalog_ranking_signal_count,
            "direct_comparison_rows": direct_comparison_rows,
            "direct_catalog_comparison_rows": total_catalog_comparison_rows,
            "rated_images": int(ranking_counts["rated_images"] or 0),
            "ranking_signal_count": ranking_signal_count,
            "catalog_ranking_signal_count": catalog_ranking_signal_count,
            "propagated_update_count": propagated_update_count,
            "imported_ranking_without_history": imported_ranking_without_history,
        }
        _stats_cache["data"] = result
        _stats_cache["expires"] = _time.time() + STATS_CACHE_TTL_SECONDS
        return result
    finally:
        await db.close()


async def get_catalog_image_counts() -> dict:
    now = _time.time()
    if _catalog_image_counts_cache["data"] and now < _catalog_image_counts_cache["expires"]:
        return dict(_catalog_image_counts_cache["data"])
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT "
            "COALESCE(SUM(image_count), 0) AS catalog_images, "
            "COALESCE(SUM(active_image_count), 0) AS active_images, "
            "COALESCE(SUM(CASE WHEN included = 0 THEN image_count ELSE 0 END), 0) AS removed_images, "
            "COALESCE(SUM(CASE WHEN included = 1 AND online = 0 THEN image_count ELSE 0 END), 0) AS offline_images "
            "FROM catalog_sources"
        )
        row = await cursor.fetchone()
        result = {
            "total_catalog_images": int(row["catalog_images"] or 0),
            "active_images": int(row["active_images"] or 0),
            "removed_images": int(row["removed_images"] or 0),
            "offline_images": int(row["offline_images"] or 0),
        }
        _catalog_image_counts_cache["data"] = result
        _catalog_image_counts_cache["expires"] = _time.time() + CATALOG_CACHE_TTL_SECONDS
        return dict(result)
    finally:
        await db.close()


async def get_image_by_id(image_id: int):
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM images WHERE id = ?", (image_id,))
        return await cursor.fetchone()
    finally:
        await db.close()


async def get_images_by_ids(image_ids: list[int]) -> dict[int, dict]:
    """Fetch multiple images by ID in a single query. Returns {id: row_dict}."""
    if not image_ids:
        return {}
    db = await get_db()
    try:
        placeholders = ",".join("?" for _ in image_ids)
        cursor = await db.execute(
            f"SELECT * FROM images WHERE id IN ({placeholders})", image_ids
        )
        rows = await cursor.fetchall()
        return {row["id"]: dict(row) for row in rows}
    finally:
        await db.close()


async def get_active_images_by_ids(image_ids: list[int]) -> dict[int, dict]:
    """Fetch active/online images by ID. Returns {id: row_dict}."""
    if not image_ids:
        return {}
    unique_ids = list(dict.fromkeys(int(image_id) for image_id in image_ids))
    db = await get_db()
    try:
        placeholders = ",".join("?" for _ in unique_ids)
        cursor = await db.execute(
            f"SELECT i.* FROM images i NOT INDEXED "
            f"JOIN catalog_sources s ON s.id = i.source_id "
            f"WHERE s.included = 1 AND i.missing_at IS NULL "
            f"AND i.id IN ({placeholders})",
            unique_ids,
        )
        rows = await cursor.fetchall()
        return {row["id"]: dict(row) for row in rows}
    finally:
        await db.close()


async def get_top_images(limit: int = 50):
    """Get top N images by Elo for top-tier refinement."""
    counts = await get_catalog_image_counts()
    active_images = int(counts.get("active_images") or 0)
    if active_images <= 0:
        return []
    all_catalog_images_active = (
        active_images == int(counts.get("total_catalog_images") or 0)
        and int(counts.get("removed_images") or 0) == 0
    )
    source_filter = (
        "AND i.source_id IN ("
        "SELECT id FROM catalog_sources WHERE included = 1"
        ") "
        if not all_catalog_images_active
        else ""
    )
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT i.id, i.filename, i.filepath, i.elo, i.comparisons, "
            "i.propagated_updates, i.status, i.flag, i.orientation, i.aspect_ratio, i.date_taken, "
            "i.camera_make, i.camera_model, i.lens, i.file_ext, i.file_size, "
            "i.width, i.height, i.file_modified_at, i.latitude, i.longitude, i.created_at "
            "FROM images i INDEXED BY idx_images_active_elo "
            "WHERE i.status IN ('kept', 'maybe') "
            f"{source_filter}"
            "AND i.missing_at IS NULL "
            "ORDER BY i.elo DESC LIMIT ?",
            (limit,),
        )
        return await cursor.fetchall()
    finally:
        await db.close()


async def get_scan_folder():
    """Get a representative active source folder."""
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT path FROM catalog_sources WHERE included = 1 "
            "ORDER BY last_scan_at IS NULL ASC, last_scan_at DESC, id DESC LIMIT 1"
        )
        row = await cursor.fetchone()
        if row:
            return row["path"]
        cursor = await db.execute(
            "SELECT filepath FROM images WHERE missing_at IS NULL ORDER BY RANDOM() LIMIT 50"
        )
        rows = await cursor.fetchall()
        if not rows:
            return None
        dirs = [os.path.dirname(row["filepath"]) for row in rows]
        return os.path.commonpath(dirs)
    finally:
        await db.close()


# --- Embedding / Active Learning ---

async def get_unembedded_images(
    limit: int = 64,
    md_cache_root: str = "",
    cache_size: str = "md",
    embedding_config: dict | None = None,
):
    """Get kept/maybe images that don't have CLIP embeddings yet."""
    embedding_config = embedding_config or active_embedding_config()
    model_key = embedding_config["model_key"]
    db = await get_db()
    try:
        await _ensure_embedding_model_tables(db)
        await _ensure_embedding_model_row(db, embedding_config)
        cache_size = cache_size if cache_size in {"sm", "md"} else "md"
        if md_cache_root:
            cursor = await db.execute(
                "SELECT i.id, c.path AS filepath FROM images i "
                "JOIN catalog_sources s ON s.id = i.source_id "
                "JOIN cache_entries c "
                "  ON c.cache_root = ? AND c.size = ? AND c.image_id = i.id "
                "WHERE s.included = 1 "
                "AND i.missing_at IS NULL "
                "AND NOT EXISTS ("
                "  SELECT 1 FROM embeddings_by_model e "
                "  WHERE e.model_key = ? AND e.image_id = i.id"
                ") "
                "ORDER BY i.id ASC "
                "LIMIT ?",
                (md_cache_root, cache_size, model_key, limit),
            )
        else:
            cursor = await db.execute(
                "SELECT i.id, i.filepath FROM images i "
                "JOIN catalog_sources s ON s.id = i.source_id "
                "WHERE s.included = 1 "
                "AND i.missing_at IS NULL "
                "AND NOT EXISTS ("
                "  SELECT 1 FROM embeddings_by_model e "
                "  WHERE e.model_key = ? AND e.image_id = i.id"
                ") "
                "ORDER BY i.id ASC "
                "LIMIT ?",
                (model_key, limit),
            )
        return await cursor.fetchall()
    finally:
        await db.close()


async def store_embeddings_batch(rows: list[tuple[int, bytes]], embedding_config: dict | None = None):
    """Store CLIP embedding blobs. Each row: (image_id, embedding_bytes)."""
    if not rows:
        return
    embedding_config = embedding_config or active_embedding_config()
    model_key = embedding_config["model_key"]
    dimension = int(embedding_config["dimension"])
    db = await get_db()
    try:
        await _ensure_embedding_model_tables(db)
        await _ensure_embedding_model_row(db, embedding_config)
        await db.executemany(
            "INSERT OR REPLACE INTO embeddings_by_model "
            "(model_key, image_id, embedding, dimension) VALUES (?, ?, ?, ?)",
            [(model_key, image_id, blob, dimension) for image_id, blob in rows],
        )
        if model_key == settings.embedding_model_key(settings.DEFAULT_SETTINGS):
            await db.executemany(
                "INSERT OR REPLACE INTO embeddings (image_id, embedding) VALUES (?, ?)",
                rows,
            )
        await db.commit()
        _invalidate_embedding_count_cache()
        _notify_embedding_batch_stored(model_key, [int(image_id) for image_id, _blob in rows])
    finally:
        await db.close()


async def count_embeddings_for_model(embedding_config: dict, *, online_only: bool = False) -> int:
    model_key = embedding_config["model_key"]
    if online_only:
        db = await get_db()
        try:
            await _ensure_embedding_model_tables(db)
            await _ensure_embedding_model_row(db, embedding_config)
            cursor = await db.execute(
                "SELECT COUNT(*) AS c FROM embeddings_by_model e "
                "JOIN images i ON e.image_id = i.id "
                "JOIN catalog_sources s ON s.id = i.source_id "
                "WHERE e.model_key = ? AND s.included = 1 AND s.online = 1 "
                "AND i.missing_at IS NULL",
                (model_key,),
            )
            return int((await cursor.fetchone())["c"] or 0)
        finally:
            await db.close()
    counts = await get_catalog_image_counts()
    active = int(counts.get("active_images") or 0)
    if active <= 0:
        return 0
    all_catalog_images_active = (
        active == int(counts.get("total_catalog_images") or 0)
        and int(counts.get("removed_images") or 0) == 0
    )
    db = await get_db()
    try:
        await _ensure_embedding_model_row(db, embedding_config)
        if all_catalog_images_active:
            cursor = await db.execute(
                "SELECT COUNT(*) AS c FROM embeddings_by_model WHERE model_key = ?",
                (model_key,),
            )
        else:
            active_source_ids = sorted(await get_active_source_id_set())
            if not active_source_ids:
                return 0
            placeholders = ",".join("?" for _ in active_source_ids)
            cursor = await db.execute(
                "SELECT COUNT(*) AS c FROM embeddings_by_model e "
                "JOIN images i ON e.image_id = i.id "
                f"WHERE e.model_key = ? AND i.source_id IN ({placeholders}) AND i.missing_at IS NULL",
                [model_key] + active_source_ids,
            )
        return int((await cursor.fetchone())["c"] or 0)
    finally:
        await db.close()


async def get_all_embeddings():
    """Get all embeddings for prediction pass."""
    model_key = active_embedding_model_key()
    db = await get_db()
    try:
        await _ensure_embedding_model_tables(db)
        cursor = await db.execute(
            "SELECT e.image_id, e.embedding FROM embeddings_by_model e "
            "JOIN images i ON e.image_id = i.id "
            "JOIN catalog_sources s ON s.id = i.source_id "
            "WHERE e.model_key = ? "
            "AND s.included = 1 AND i.missing_at IS NULL",
            (model_key,),
        )
        return await cursor.fetchall()
    finally:
        await db.close()


async def get_embedding_count() -> int:
    now = _time.time()
    model_key = active_embedding_model_key()
    if (
        _embedding_count_cache["key"] in (model_key, None)
        and _embedding_count_cache["value"] is not None
        and now < _embedding_count_cache["expires"]
    ):
        return int(_embedding_count_cache["value"])
    count = await count_embeddings_for_model(active_embedding_config(), online_only=True)
    _embedding_count_cache["key"] = model_key
    _embedding_count_cache["value"] = count
    _embedding_count_cache["expires"] = _time.time() + EMBEDDING_COUNT_CACHE_TTL_SECONDS
    return count


async def get_ai_status_counts() -> dict:
    """Return the small stats subset needed by the AI status poller."""
    global _stats_inflight_task
    now = _time.time()
    if _ai_status_counts_cache["data"] and now < _ai_status_counts_cache["expires"]:
        return dict(_ai_status_counts_cache["data"])
    if _stats_cache["data"]:
        stats = _stats_cache["data"]
        if now >= _stats_cache["expires"]:
            try:
                loop = asyncio.get_running_loop()
                task = _stats_inflight_task
                if task is None or task.done() or task.get_loop() is not loop:
                    _stats_inflight_task = loop.create_task(_get_stats_uncached())
            except RuntimeError:
                pass
        result = {
            "embedded": await get_embedding_count(),
            "total_images": int(stats.get("active_images") or stats.get("total_images") or 0),
            "rated_images": int(stats.get("rated_images") or 0),
            "direct_comparison_rows": int(stats.get("direct_comparison_rows") or 0),
            "ranking_signal_count": int(stats.get("ranking_signal_count") or stats.get("total_comparisons") or 0),
            "imported_ranking_without_history": int(stats.get("imported_ranking_without_history") or 0),
        }
        _ai_status_counts_cache["data"] = result
        _ai_status_counts_cache["expires"] = now + AI_STATUS_COUNTS_CACHE_TTL_SECONDS
        return dict(result)
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT "
            "COALESCE(SUM(image_count), 0) AS catalog_images, "
            "COALESCE(SUM(CASE WHEN included = 1 AND online = 1 THEN active_image_count ELSE 0 END), 0) AS active_images, "
            "COALESCE(SUM(CASE WHEN included = 0 THEN image_count ELSE 0 END), 0) AS removed_images, "
            "COALESCE(SUM(CASE WHEN included = 1 AND online = 0 THEN image_count ELSE 0 END), 0) AS offline_images "
            "FROM catalog_sources"
        )
        source_counts = await cursor.fetchone()
        active = int(source_counts["active_images"] or 0)
        catalog_images = int(source_counts["catalog_images"] or 0)
        all_catalog_images_active = (
            active == catalog_images
            and int(source_counts["removed_images"] or 0) == 0
        )

        active_source_ids = sorted(await get_active_source_id_set()) if active > 0 else []
        source_placeholders = ",".join("?" for _ in active_source_ids)

        embedded = 0 if active <= 0 else await count_embeddings_for_model(active_embedding_config(), online_only=True)

        cursor = await db.execute("SELECT COUNT(*) AS c FROM comparisons")
        total_catalog_comparison_rows = int((await cursor.fetchone())["c"] or 0)

        if active <= 0:
            direct_comparison_rows = 0
            image_comparison_count = 0
            propagated_update_count = 0
            rated_images = 0
            direct_image_history_count = 0
        elif all_catalog_images_active:
            direct_comparison_rows = total_catalog_comparison_rows
            cursor = await db.execute(
                "SELECT "
                "COALESCE(SUM(COALESCE(comparisons, 0)), 0) AS image_comparison_count, "
                "COALESCE(SUM(COALESCE(propagated_updates, 0)), 0) AS propagated_update_count, "
                "SUM(CASE WHEN COALESCE(comparisons, 0) > 0 "
                "      OR COALESCE(propagated_updates, 0) > 0 "
                "      OR ABS(COALESCE(elo, 1200.0) - 1200.0) > 0.0001 "
                "    THEN 1 ELSE 0 END) AS rated_images "
                "FROM images"
            )
            ranking_counts = await cursor.fetchone()
            image_comparison_count = int(ranking_counts["image_comparison_count"] or 0)
            propagated_update_count = int(ranking_counts["propagated_update_count"] or 0)
            rated_images = int(ranking_counts["rated_images"] or 0)
            direct_image_history_count = total_catalog_comparison_rows * 2
        else:
            cursor = await db.execute(
                "WITH invalid_endpoints AS ("
                "  SELECT c.rowid AS comparison_rowid "
                "  FROM images i JOIN comparisons c INDEXED BY idx_comparisons_pair ON c.winner_id = i.id "
                f"  WHERE NOT (i.source_id IN ({source_placeholders}) AND i.missing_at IS NULL) "
                "  UNION ALL "
                "  SELECT c.rowid AS comparison_rowid "
                "  FROM images i JOIN comparisons c INDEXED BY idx_comparisons_loser ON c.loser_id = i.id "
                f"  WHERE NOT (i.source_id IN ({source_placeholders}) AND i.missing_at IS NULL)"
                ") "
                "SELECT COUNT(DISTINCT comparison_rowid) AS invalid_rows, "
                "COUNT(*) AS invalid_endpoints FROM invalid_endpoints",
                active_source_ids + active_source_ids,
            )
            comparison_counts = await cursor.fetchone()
            direct_comparison_rows = max(
                0,
                total_catalog_comparison_rows - int(comparison_counts["invalid_rows"] or 0),
            )
            direct_image_history_count = max(
                0,
                (total_catalog_comparison_rows * 2)
                - int(comparison_counts["invalid_endpoints"] or 0),
            )
            cursor = await db.execute(
                "SELECT "
                "COALESCE(SUM(COALESCE(i.comparisons, 0)), 0) AS image_comparison_count, "
                "COALESCE(SUM(COALESCE(i.propagated_updates, 0)), 0) AS propagated_update_count, "
                "SUM(CASE WHEN COALESCE(i.comparisons, 0) > 0 "
                "      OR COALESCE(i.propagated_updates, 0) > 0 "
                "      OR ABS(COALESCE(i.elo, 1200.0) - 1200.0) > 0.0001 "
                "    THEN 1 ELSE 0 END) AS rated_images "
                "FROM images i INDEXED BY idx_images_source_missing_rating_signal "
                f"WHERE i.source_id IN ({source_placeholders}) AND i.missing_at IS NULL",
                active_source_ids,
            )
            ranking_counts = await cursor.fetchone()
            image_comparison_count = int(ranking_counts["image_comparison_count"] or 0)
            propagated_update_count = int(ranking_counts["propagated_update_count"] or 0)
            rated_images = int(ranking_counts["rated_images"] or 0)

        imported_ranking_without_history = max(0, image_comparison_count - direct_image_history_count)
        ranking_signal_count = (
            direct_comparison_rows
            + imported_ranking_without_history
            + propagated_update_count
        )
        result = {
            "embedded": embedded,
            "total_images": active,
            "rated_images": rated_images,
            "direct_comparison_rows": direct_comparison_rows,
            "ranking_signal_count": ranking_signal_count,
            "imported_ranking_without_history": imported_ranking_without_history,
        }
        _ai_status_counts_cache["data"] = result
        _ai_status_counts_cache["expires"] = _time.time() + AI_STATUS_COUNTS_CACHE_TTL_SECONDS
        return dict(result)
    finally:
        await db.close()
