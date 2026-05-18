import aiosqlite
import os
import time as _time

from data import connection as data_connection
from data import schema as data_schema
from data.repositories import cache_entries as cache_entry_repository
from data.repositories import catalog as catalog_repository
from data.repositories import embeddings as embedding_repository
from data.repositories import filter_options as filter_options_repository
from data.repositories import images as image_repository
from data.repositories import metadata_search as metadata_search_repository
from data.repositories import people as people_repository
from data.repositories import ratings as rating_repository
from data.repositories import rankings as ranking_repository
from data.repositories import stats as stats_repository
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


EXPECTED_EMBEDDING_DIM = data_schema.EXPECTED_EMBEDDING_DIM
SCHEMA_VERSION = data_schema.SCHEMA_VERSION

SCHEMA = data_schema.SCHEMA

_stats_cache = stats_repository._stats_cache
_stats_inflight_task = stats_repository._stats_inflight_task
_catalog_image_counts_cache = stats_repository._catalog_image_counts_cache
_filter_options_cache = filter_options_repository._filter_options_cache
_filter_options_refreshing = filter_options_repository._filter_options_refreshing
_catalog_sources_cache = catalog_repository._catalog_sources_cache
_catalog_summary_cache = catalog_repository._catalog_summary_cache
_catalog_light_summary_cache = catalog_repository._catalog_light_summary_cache
_date_groups_cache = ranking_repository._date_groups_cache
_date_groups_refreshing = ranking_repository._date_groups_refreshing
_map_markers_cache = ranking_repository._map_markers_cache
_ranking_count_cache = ranking_repository._ranking_count_cache
_visible_pairing_pool_counts_cache = rating_repository._visible_pairing_pool_counts_cache
_past_matchups_cache = rating_repository._past_matchups_cache
_cached_image_ids_cache = cache_entry_repository._cached_image_ids_cache
_cache_entry_count_cache = cache_entry_repository._cache_entry_count_cache
_rankable_image_ids_cache = ranking_repository._rankable_image_ids_cache
_embedding_count_cache = embedding_repository._embedding_count_cache
_ensured_embedding_model_keys: set[str] = set()
_ai_status_counts_cache = stats_repository._ai_status_counts_cache
_active_source_ids_cache = catalog_repository._active_source_ids_cache
CACHED_IMAGE_IDS_TTL_SECONDS = cache_entry_repository.CACHED_IMAGE_IDS_TTL_SECONDS
RANKING_COUNT_CACHE_TTL_SECONDS = ranking_repository.RANKING_COUNT_CACHE_TTL_SECONDS
VISIBLE_PAIRING_POOL_COUNTS_TTL_SECONDS = rating_repository.VISIBLE_PAIRING_POOL_COUNTS_TTL_SECONDS
CACHE_ENTRY_COUNT_TTL_SECONDS = cache_entry_repository.CACHE_ENTRY_COUNT_TTL_SECONDS
RANKABLE_IMAGE_IDS_TTL_SECONDS = ranking_repository.RANKABLE_IMAGE_IDS_TTL_SECONDS
RANKING_VISIBLE_ID_FILTER_LIMIT = ranking_repository.RANKING_VISIBLE_ID_FILTER_LIMIT
RANKING_CACHE_FIRST_VISIBLE_LIMIT = ranking_repository.RANKING_CACHE_FIRST_VISIBLE_LIMIT
STATS_CACHE_TTL_SECONDS = stats_repository.FULL_STATS_CACHE_TTL_SECONDS
EMBEDDING_COUNT_CACHE_TTL_SECONDS = embedding_repository.EMBEDDING_COUNT_CACHE_TTL_SECONDS
AI_STATUS_COUNTS_CACHE_TTL_SECONDS = stats_repository.AI_STATUS_COUNTS_CACHE_TTL_SECONDS
ACTIVE_SOURCE_IDS_TTL_SECONDS = catalog_repository.ACTIVE_SOURCE_IDS_TTL_SECONDS
CATALOG_CACHE_TTL_SECONDS = catalog_repository.CATALOG_CACHE_TTL_SECONDS
FACET_CACHE_TTL_SECONDS = ranking_repository.FACET_CACHE_TTL_SECONDS
FILTER_OPTIONS_CACHE_TTL_SECONDS = filter_options_repository.FILTER_OPTIONS_CACHE_TTL_SECONDS


def active_embedding_config() -> dict:
    return settings.fast_search_embedding_config()


def active_embedding_model_key() -> str:
    return active_embedding_config()["model_key"]


normalize_source_path = catalog_repository.normalize_source_path
source_display_name = catalog_repository.source_display_name
active_source_join = catalog_repository.active_source_join
active_source_condition = catalog_repository.active_source_condition
active_image_condition = catalog_repository.active_image_condition


def _chunked(values: list[int], chunk_size: int = 500):
    for start in range(0, len(values), chunk_size):
        yield values[start:start + chunk_size]


def _invalidate_stats_cache():
    stats_repository.invalidate_full_stats_cache()
    _invalidate_past_matchups_cache()
    stats_repository.invalidate_catalog_image_counts_cache()
    _invalidate_catalog_cache()
    _invalidate_facet_caches()
    _invalidate_ranking_count_cache()
    _invalidate_rankable_image_ids_cache()
    _invalidate_embedding_count_cache()
    _invalidate_active_source_ids_cache()


def _invalidate_ai_status_counts_cache():
    stats_repository.invalidate_ai_status_counts_cache()


def _invalidate_catalog_summary_cache():
    catalog_repository.invalidate_catalog_summary_cache()


def _invalidate_rating_stats_cache():
    stats_repository.invalidate_full_stats_cache()
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
        stats_repository.invalidate_full_stats_cache()
        _invalidate_catalog_summary_cache()

    stats_repository.patch_ai_status_direct_rating_counts(
        pair_delta,
        rated_image_delta,
        active_cap=active_cap,
    )

    _invalidate_rating_facet_caches()
    _invalidate_rating_ranking_count_cache()


def _invalidate_filter_options_cache():
    filter_options_repository.invalidate_filter_options_cache()
    _sync_filter_options_refreshing_facade()


def clear_filter_options_cache():
    filter_options_repository.clear_filter_options_cache()
    _sync_filter_options_refreshing_facade()


def _invalidate_catalog_cache():
    catalog_repository.invalidate_catalog_cache()


def _invalidate_facet_caches():
    ranking_repository.invalidate_facet_caches()


def _invalidate_visible_facet_caches(cache_root: str | None = None, size: str | None = None):
    ranking_repository.invalidate_visible_facet_caches(cache_root, size)


def _invalidate_rating_facet_caches():
    ranking_repository.invalidate_rating_facet_caches()


def _invalidate_ranking_count_cache():
    ranking_repository.invalidate_ranking_count_cache()
    rating_repository.invalidate_visible_pairing_pool_counts_cache()


_cache_scope_matches = ranking_repository.cache_scope_matches


def _invalidate_visible_cache_dependent_counts(cache_root: str | None = None, size: str | None = None):
    ranking_repository.invalidate_visible_cache_dependent_counts(cache_root, size)
    rating_repository.invalidate_visible_pairing_pool_counts_cache(cache_root, size)


def _invalidate_rating_ranking_count_cache():
    ranking_repository.invalidate_rating_ranking_count_cache()


def invalidate_cached_image_ids_cache(cache_root: str | None = None, size: str | None = None):
    _invalidate_visible_cache_dependent_counts(cache_root, size)
    _invalidate_visible_facet_caches(cache_root, size)
    cache_entry_repository.invalidate_cached_image_ids_cache(cache_root=cache_root, size=size)


def note_cached_image_ids_added(cache_root: str, size: str, image_ids) -> None:
    """Patch hot cached-ID sets after append-only cache writes."""
    cache_entry_repository.note_cached_image_ids_added(cache_root, size, image_ids)


def _invalidate_rankable_image_ids_cache():
    ranking_repository.invalidate_rankable_image_ids_cache()


def _invalidate_embedding_count_cache():
    embedding_repository.invalidate_embedding_count_cache()
    _invalidate_ai_status_counts_cache()


def _invalidate_active_source_ids_cache():
    catalog_repository.invalidate_active_source_ids_cache()


def _invalidate_past_matchups_cache():
    rating_repository.invalidate_past_matchups_cache()


def invalidate_stats_cache():
    _invalidate_stats_cache()


def invalidate_rating_stats_cache():
    _invalidate_rating_stats_cache()


def _sync_stats_inflight_task_facade():
    global _stats_inflight_task
    _stats_inflight_task = stats_repository._stats_inflight_task


def _sync_filter_options_refreshing_facade():
    global _filter_options_refreshing
    _filter_options_refreshing = filter_options_repository._filter_options_refreshing


async def get_db() -> aiosqlite.Connection:
    return await data_connection.open_async(DB_PATH)


_ensure_catalog_source = catalog_repository.ensure_catalog_source_on_conn
_update_source_counts = catalog_repository.update_source_counts_on_conn
_refresh_source_online_states_on_conn = catalog_repository.refresh_source_online_states_on_conn


async def _normalize_legacy_image_state(conn):
    if await data_schema.normalize_legacy_image_state(conn):
        _invalidate_filter_options_cache()


async def _migrate_catalog_sources(conn):
    if await data_schema.migrate_catalog_sources(conn):
        _invalidate_filter_options_cache()


_table_columns = data_schema.table_columns
_schema_is_current = data_schema.schema_is_current


async def _check_embedding_dimension(conn):
    """Legacy no-op kept for callers from older code.

    Model upgrades now preserve old vectors by writing each model to
    embeddings_by_model instead of clearing the original embeddings table.
    """
    await _ensure_embedding_model_tables(conn)


def _legacy_embedding_model_key() -> str:
    return settings.embedding_model_key(settings.DEFAULT_SETTINGS)


def _embedding_repository_kwargs() -> dict:
    return {
        "active_config": active_embedding_config(),
        "default_settings": settings.DEFAULT_SETTINGS,
        "legacy_model_key": _legacy_embedding_model_key(),
        "ensured_keys": _ensured_embedding_model_keys,
    }


async def _ensure_embedding_model_tables(conn):
    await embedding_repository.ensure_embedding_model_tables(
        conn,
        **_embedding_repository_kwargs(),
    )


_ensure_embedding_model_row = embedding_repository.ensure_embedding_model_row


def normalize_deep_search_query(query: str) -> str:
    return embedding_repository.normalize_deep_search_query(query)


def deep_search_query_key(query: str) -> str:
    return embedding_repository.deep_search_query_key(query)


async def record_deep_search_query(query: str, *, source: str = "search", pinned: bool = False) -> dict | None:
    return await embedding_repository.record_deep_search_query(
        DB_PATH,
        query,
        source=source,
        pinned=pinned,
    )


async def sync_deep_search_terms(terms: list[str] | tuple[str, ...] | None):
    normalized_terms = settings.normalize_deep_search_terms(terms or [])
    await embedding_repository.sync_deep_search_terms(DB_PATH, normalized_terms)


async def get_deep_search_query_embedding(query: str, model_key: str) -> bytes | None:
    return await embedding_repository.get_deep_search_query_embedding(
        DB_PATH,
        query,
        model_key,
    )


async def store_deep_search_query_embedding(config: dict, query: str, blob: bytes):
    normalized = await embedding_repository.store_deep_search_query_embedding(
        DB_PATH,
        config=config,
        query=query,
        blob=blob,
    )
    if normalized:
        _notify_deep_search_query_embedding_stored(config["model_key"], normalized)


async def get_pending_deep_search_queries(config: dict, terms=None, limit: int = 16) -> list[dict]:
    if terms is not None:
        await sync_deep_search_terms(terms)
    limit = max(1, min(int(limit or 16), 128))
    return await embedding_repository.get_pending_deep_search_queries(
        DB_PATH,
        config=config,
        limit=limit,
    )


async def get_deep_search_cache_status(config: dict, terms=None) -> dict:
    if terms is not None:
        await sync_deep_search_terms(terms)
    return await embedding_repository.get_deep_search_cache_status(
        DB_PATH,
        config=config,
    )


async def list_deep_search_queries(config: dict, limit: int = 200) -> list[dict]:
    limit = max(1, min(int(limit or 200), 500))
    return await embedding_repository.list_deep_search_queries(
        DB_PATH,
        config=config,
        limit=limit,
    )


_ensure_metadata_fts = data_schema.ensure_metadata_fts
_apply_schema_and_migrations = data_schema.apply_schema_and_migrations


async def init_db():
    db_exists = os.path.exists(DB_PATH)
    db = await get_db()
    try:
        if not db_exists:
            await data_connection.enable_wal(db, db_path=DB_PATH)
        if db_exists and await _schema_is_current(db):
            await _normalize_legacy_image_state(db)
            await _refresh_source_online_states_on_conn(db)
            await _check_embedding_dimension(db)
            await _ensure_metadata_fts(db)
            await db.commit()
            return
        await _apply_schema_and_migrations(db, db_exists=db_exists)
        await _migrate_catalog_sources(db)
        await _refresh_source_online_states_on_conn(db)
        await _ensure_embedding_model_tables(db)
        await _ensure_metadata_fts(db)
        await db.commit()
    finally:
        await db.close()


async def set_image_orientation(image_id: int, orientation: str):
    await image_repository.set_image_orientation(DB_PATH, image_id, orientation)


async def get_unclassified_images(limit: int = 200):
    return await image_repository.get_unclassified_images(DB_PATH, limit)


async def batch_set_orientations(updates: list[tuple[str, float, int]]):
    """Set orientation and aspect_ratio for multiple images. Each tuple: (orientation, aspect_ratio, image_id)."""
    await image_repository.batch_set_orientations(DB_PATH, updates)
    _invalidate_filter_options_cache()


async def get_images_needing_metadata(limit: int = 100, metadata_version: int = 1):
    return await image_repository.get_images_needing_metadata(
        DB_PATH,
        limit=limit,
        metadata_version=metadata_version,
    )


async def batch_update_metadata(updates: list[tuple]):
    """Persist extracted metadata. Tuples are built in app._metadata_update_tuple."""
    if not updates:
        return
    await image_repository.batch_update_metadata(DB_PATH, updates)
    _invalidate_filter_options_cache()


def _insert_row_with_file_metadata(row):
    return catalog_repository.insert_row_with_file_metadata(row)


async def insert_images_batch(rows: list[tuple], source_id: int | None = None):
    """Insert image rows, ignoring duplicates."""
    if not rows:
        return
    await catalog_repository.insert_images_batch(DB_PATH, rows, source_id)
    _invalidate_stats_cache()
    _invalidate_filter_options_cache()


async def _mark_source_missing_files(conn, source_id: int, seen_filepaths: list[str], missing_at: float):
    await catalog_repository.mark_source_missing_files_on_conn(
        conn,
        source_id,
        seen_filepaths,
        missing_at,
    )


async def refresh_source_online_states():
    if await catalog_repository.refresh_source_online_states(DB_PATH):
        _invalidate_stats_cache()
        _invalidate_filter_options_cache()


async def add_or_restore_source(path: str):
    source = await catalog_repository.add_or_restore_source(DB_PATH, path)
    _invalidate_stats_cache()
    _invalidate_filter_options_cache()
    return source


async def mark_source_scan_started(source_id: int):
    if await catalog_repository.mark_source_scan_started(DB_PATH, source_id):
        _invalidate_stats_cache()
        _invalidate_filter_options_cache()


async def mark_source_scan_finished(source_id: int, seen_filepaths: list[str] | None = None):
    await catalog_repository.mark_source_scan_finished(DB_PATH, source_id, seen_filepaths)
    _invalidate_stats_cache()
    _invalidate_filter_options_cache()
    invalidate_cached_image_ids_cache()


def mark_image_missing_sync(image_id: int, missing_at: float | None = None) -> bool:
    """Mark one image missing from sync thumbnail/worker code."""
    changed = catalog_repository.mark_image_missing_sync(DB_PATH, image_id, missing_at)
    if changed:
        _invalidate_stats_cache()
        _invalidate_filter_options_cache()
        invalidate_cached_image_ids_cache()
    return changed


async def get_source(source_id: int):
    return await catalog_repository.get_source(DB_PATH, source_id)


async def get_source_by_path(path: str):
    return await catalog_repository.get_source_by_path(DB_PATH, path)


async def get_catalog_sources():
    return await catalog_repository.catalog_sources_cached(
        DB_PATH,
        refresh_source_online_states=refresh_source_online_states,
        ttl_seconds=CATALOG_CACHE_TTL_SECONDS,
    )


async def get_catalog_summary():
    return await catalog_repository.catalog_summary_cached(
        DB_PATH,
        get_stats=get_stats,
        refresh_source_online_states=refresh_source_online_states,
        ttl_seconds=CATALOG_CACHE_TTL_SECONDS,
    )


async def get_catalog_light_summary():
    return await catalog_repository.catalog_light_summary_cached(
        DB_PATH,
        get_catalog_image_counts=get_catalog_image_counts,
        refresh_source_online_states=refresh_source_online_states,
        ttl_seconds=CATALOG_CACHE_TTL_SECONDS,
    )


async def remove_source_keep_data(source_id: int):
    await catalog_repository.remove_source_keep_data(DB_PATH, source_id)
    _invalidate_stats_cache()
    _invalidate_filter_options_cache()


async def get_source_image_ids(source_id: int) -> list[int]:
    return await catalog_repository.get_source_image_ids(DB_PATH, source_id)


async def purge_source_catalog_data(source_id: int) -> dict:
    result = await catalog_repository.purge_source_catalog_data(DB_PATH, source_id)
    _invalidate_stats_cache()
    _invalidate_filter_options_cache()
    invalidate_cached_image_ids_cache()
    return result


async def get_recent_active_images(limit: int = 10):
    return await image_repository.get_recent_active_images(DB_PATH, limit)


async def set_image_status(image_id: int, status: str):
    await image_repository.set_image_status(DB_PATH, image_id, status)
    _invalidate_past_matchups_cache()
    _invalidate_rating_stats_cache()
    _invalidate_filter_options_cache()


async def set_image_flag(image_id: int, flag: str):
    await image_repository.set_image_flag(DB_PATH, image_id, flag)
    _invalidate_ranking_count_cache()
    _invalidate_filter_options_cache()


async def batch_set_image_flags(image_ids: list[int], flag: str, chunk_size: int = 500) -> int:
    updated = await image_repository.batch_set_image_flags(
        DB_PATH,
        image_ids,
        flag,
        chunk_size,
    )
    if updated:
        _invalidate_ranking_count_cache()
        _invalidate_filter_options_cache()
    return updated


async def get_active_images_for_pairing():
    return await rating_repository.get_active_images_for_pairing(
        DB_PATH,
        get_catalog_image_counts=get_catalog_image_counts,
    )


async def get_visible_images_for_pairing(
    size: str,
    cache_root: str,
    *,
    include_card_metadata: bool = True,
    limit: int | None = None,
    order: str = "elo",
):
    return await rating_repository.get_visible_images_for_pairing(
        DB_PATH,
        size,
        cache_root,
        include_card_metadata=include_card_metadata,
        limit=limit,
        order=order,
    )


async def get_visible_pairing_pool_counts(size: str, cache_root: str) -> dict:
    return await rating_repository.get_visible_pairing_pool_counts(
        DB_PATH,
        get_catalog_image_counts=get_catalog_image_counts,
        size=size,
        cache_root=cache_root,
        ttl_seconds=VISIBLE_PAIRING_POOL_COUNTS_TTL_SECONDS,
    )


async def get_visible_orientation_pairing_pool_counts(
    size: str,
    cache_root: str,
    orientation: str,
) -> dict:
    return await rating_repository.get_visible_orientation_pairing_pool_counts(
        DB_PATH,
        get_catalog_image_counts=get_catalog_image_counts,
        count_rankings=count_rankings,
        size=size,
        cache_root=cache_root,
        orientation=orientation,
        ttl_seconds=VISIBLE_PAIRING_POOL_COUNTS_TTL_SECONDS,
    )


async def get_past_matchups() -> set[tuple[int, int]]:
    return await rating_repository.get_past_matchups(
        DB_PATH,
        get_active_source_id_set=get_active_source_id_set,
    )


async def get_visible_past_matchups(size: str, cache_root: str) -> set[tuple[int, int]]:
    return await rating_repository.get_visible_past_matchups(
        DB_PATH,
        size,
        cache_root,
    )


async def get_past_matchups_for_image_ids(image_ids: list[int]) -> set[tuple[int, int]]:
    return await rating_repository.get_past_matchups_for_image_ids(
        DB_PATH,
        image_ids,
    )


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
    await rating_repository.record_comparison(
        DB_PATH,
        winner_id=winner_id,
        loser_id=loser_id,
        mode=mode,
        elo_before_winner=elo_before_winner,
        elo_before_loser=elo_before_loser,
        new_winner_elo=new_winner_elo,
        new_loser_elo=new_loser_elo,
        action_id=action_id,
    )
    _invalidate_rating_stats_cache()


async def record_active_comparison(
    winner_id: int,
    loser_id: int,
    mode: str,
    action_id: str | None = None,
) -> dict | None:
    """Validate active images and record a comparison in one DB round trip."""
    counts = await get_catalog_image_counts()
    result = await rating_repository.record_active_comparison(
        DB_PATH,
        winner_id=winner_id,
        loser_id=loser_id,
        mode=mode,
        action_id=action_id,
        catalog_counts=counts,
    )
    if result is None:
        return None
    rated_delta = int(result.pop("_rated_delta", 0) or 0)
    _invalidate_past_matchups_cache()
    _patch_direct_rating_stats_cache(1, rated_delta)
    return result


async def record_active_mosaic_pick(
    picked_id: int,
    other_ids: list[int],
    action_id: str,
) -> dict:
    """Validate active mosaic images and record the full pick action."""
    counts = await get_catalog_image_counts()
    result = await rating_repository.record_active_mosaic_pick(
        DB_PATH,
        picked_id=picked_id,
        other_ids=other_ids,
        action_id=action_id,
        catalog_counts=counts,
    )
    if result.get("ok"):
        rated_delta = int(result.pop("_rated_delta", 0) or 0)
        pair_delta = int(result.get("pairs_recorded") or 0)
        _invalidate_past_matchups_cache()
        _patch_direct_rating_stats_cache(pair_delta, rated_delta)
    return result


async def undo_last_comparison():
    """Undo the last comparison/action, restoring Elo ratings."""
    result = await rating_repository.undo_last_comparison(DB_PATH)
    if result is not None:
        _invalidate_rating_stats_cache()
    return result


def _invalidate_people_dependent_caches():
    _invalidate_ranking_count_cache()
    _invalidate_facet_caches()
    _invalidate_filter_options_cache()


parse_people_ids = people_repository.parse_people_ids


_face_embedding_blob = people_repository._face_embedding_blob
_face_embedding_vector = people_repository._face_embedding_vector


async def _canonical_person_ids_on_conn(conn, person_ids: tuple[int, ...]) -> tuple[int, ...]:
    return await people_repository.canonical_person_ids_on_conn(conn, person_ids)


async def _create_person_on_conn(conn, *, status: str = "unknown", name: str = "") -> int:
    return await people_repository.create_person_on_conn(conn, status=status, name=name)


async def _refresh_people_membership_on_conn(conn, person_ids: tuple[int, ...] | None = None) -> None:
    await people_repository.refresh_people_membership_on_conn(conn, person_ids)


async def refresh_people_membership(person_ids: tuple[int, ...] | None = None) -> None:
    await people_repository.refresh_people_membership(DB_PATH, person_ids)
    _invalidate_people_dependent_caches()


async def get_people_image_id_filter(person_ids: tuple[int, ...] | str) -> set[int] | None:
    return await people_repository.get_people_image_id_filter(DB_PATH, person_ids)


async def get_images_needing_faces(
    *,
    model_id: str,
    cache_root: str,
    limit: int = 16,
    retry_after_seconds: int = 86400,
) -> list[dict]:
    return await people_repository.get_images_needing_faces(
        DB_PATH,
        model_id=model_id,
        cache_root=cache_root,
        limit=limit,
        retry_after_seconds=retry_after_seconds,
    )


async def count_images_needing_faces(
    *,
    model_id: str,
    cache_root: str,
    retry_after_seconds: int = 86400,
) -> int:
    return await people_repository.count_images_needing_faces(
        DB_PATH,
        model_id=model_id,
        cache_root=cache_root,
        retry_after_seconds=retry_after_seconds,
    )


async def store_face_scan_result(
    *,
    image_id: int,
    model_id: str,
    cache_path: str = "",
    faces: list[dict] | None = None,
    status: str = "scanned",
    error: str = "",
) -> dict:
    result = await people_repository.store_face_scan_result(
        DB_PATH,
        image_id=image_id,
        model_id=model_id,
        cache_path=cache_path,
        faces=faces,
        status=status,
        error=error,
    )
    _invalidate_people_dependent_caches()
    return result


async def cluster_unassigned_faces(
    *,
    model_id: str,
    similarity_threshold: float = 0.52,
    merge_threshold: float = 0.62,
    limit: int = 500,
) -> dict:
    result = await people_repository.cluster_unassigned_faces(
        DB_PATH,
        model_id=model_id,
        similarity_threshold=similarity_threshold,
        merge_threshold=merge_threshold,
        limit=limit,
    )
    affected_people = result.pop("_affected_people", [])
    if affected_people:
        _invalidate_people_dependent_caches()
    return result


async def get_people_review(limit: int = 24, long_tail_threshold: int = 1) -> dict:
    current_settings = settings.get_settings()
    return await people_repository.get_people_review(
        DB_PATH,
        limit=limit,
        long_tail_threshold=long_tail_threshold,
        face_model_id=str(current_settings.get("face_model_id") or "buffalo_l"),
        cache_root=str(current_settings.get("ssd_cache_dir") or ""),
    )


async def get_face_thumbnail_context(face_id: int) -> dict | None:
    return await people_repository.get_face_thumbnail_context(DB_PATH, face_id)


async def label_person(person_id: int, name: str) -> dict:
    result = await people_repository.label_person(DB_PATH, person_id, name)
    if result.get("ok"):
        _invalidate_filter_options_cache()
    return result


async def merge_people(source_person_id: int, target_person_id: int) -> dict:
    result = await people_repository.merge_people(DB_PATH, source_person_id, target_person_id)
    if result.get("ok"):
        _invalidate_people_dependent_caches()
    return result


async def reject_merge_suggestion(suggestion_id: int) -> dict:
    return await people_repository.reject_merge_suggestion(DB_PATH, suggestion_id)


async def assign_face(face_id: int, person_id: int | None = None, name: str = "") -> dict:
    result = await people_repository.assign_face(DB_PATH, face_id, person_id=person_id, name=name)
    if result.get("ok"):
        _invalidate_people_dependent_caches()
    return result


async def ignore_face(face_id: int) -> dict:
    result = await people_repository.ignore_face(DB_PATH, face_id)
    if result.get("ok"):
        _invalidate_people_dependent_caches()
    return result


async def ignore_person(person_id: int) -> dict:
    result = await people_repository.ignore_person(DB_PATH, person_id)
    if result.get("ok"):
        _invalidate_people_dependent_caches()
    return result


RANKING_SORTS = ranking_repository.RANKING_SORTS
RANKING_INDEXES = ranking_repository.RANKING_INDEXES
SPARSE_VISIBLE_ID_FILTER_SORTS = ranking_repository.SPARSE_VISIBLE_ID_FILTER_SORTS
VISIBLE_CACHE_FIRST_SORTS = ranking_repository.VISIBLE_CACHE_FIRST_SORTS
STAR_THRESHOLDS = ranking_repository.STAR_THRESHOLDS
IMAGE_EXTENSION_SEARCH_TERMS = ranking_repository.IMAGE_EXTENSION_SEARCH_TERMS


_ranking_filter_parts = ranking_repository.ranking_filter_parts
_ranking_index_for_query = ranking_repository.ranking_index_for_query
_ranking_image_source = ranking_repository.ranking_image_source
_ranking_count_image_source = ranking_repository.ranking_count_image_source


async def get_cached_image_ids(
    image_ids: list[int],
    size: str,
    cache_root: str,
    chunk_size: int = 900,
) -> set[int]:
    """Return IDs with a cache_entries row for the exact cache root/tier."""
    del chunk_size
    return await cache_entry_repository.cached_image_ids(
        DB_PATH,
        image_ids,
        size,
        cache_root,
        ttl_seconds=CACHED_IMAGE_IDS_TTL_SECONDS,
    )


async def get_cached_image_id_set(size: str, cache_root: str) -> frozenset[int]:
    """Return cached image IDs for one cache root/tier with a short TTL."""
    return await cache_entry_repository.cached_image_id_set_cached(
        DB_PATH,
        size=size,
        cache_root=cache_root,
        ttl_seconds=CACHED_IMAGE_IDS_TTL_SECONDS,
    )


async def get_active_source_id_set() -> frozenset[int]:
    return await catalog_repository.active_source_id_set_cached(
        DB_PATH,
        ttl_seconds=ACTIVE_SOURCE_IDS_TTL_SECONDS,
    )


_metadata_fts_query = metadata_search_repository.metadata_fts_query


async def metadata_search_image_ids(text_query: str, *, max_results: int = 5000) -> set[int] | None:
    """Return a bounded metadata-search ID set using the trigram FTS index.

    None means "fall back to regular metadata LIKE filtering"; an empty set is
    a real no-match result and lets callers skip expensive count queries.
    """
    query = (text_query or "").strip()
    if len(query) < 3:
        return None
    return await metadata_search_repository.metadata_search_image_ids(
        DB_PATH,
        query,
        active_source_ids=await get_active_source_id_set(),
        max_results=max_results,
    )


async def _cache_entry_count(size: str, cache_root: str) -> int:
    return await cache_entry_repository.cache_entry_count_cached(
        DB_PATH,
        size=size,
        cache_root=cache_root,
        ttl_seconds=CACHE_ENTRY_COUNT_TTL_SECONDS,
    )


async def get_rankable_image_id_set() -> frozenset[int]:
    """Return active ranking image IDs with a short TTL for hot count paths."""
    return await ranking_repository.rankable_image_id_set_cached(
        DB_PATH,
        active_source_ids=await get_active_source_id_set(),
        ttl_seconds=RANKABLE_IMAGE_IDS_TTL_SECONDS,
    )


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
    return ranking_repository.has_ranking_count_filters(
        orientation,
        compared,
        min_stars,
        folder,
        flag,
        date_taken,
        file_type,
        camera,
        lens,
        id_filter,
        text_query,
    )


async def _count_rankings_with_id_filter(
    conn,
    conditions: list[str],
    params: list,
    id_values,
) -> int:
    return await ranking_repository.count_rankings_with_id_filter_on_conn(
        conn,
        conditions,
        params,
        id_values,
    )


async def get_rankings(limit: int = 100, offset: int = 0, sort: str = "elo",
                       orientation: str = "", compared: str = "", min_stars: int = 0,
                       folder: str = "", flag: str = "", date_taken: str = "",
                       file_type: str = "", camera: str = "", lens: str = "",
                       id_filter: set = None,
                       visible_thumb_size: str = "", cache_root: str = "",
                       text_query: str = ""):
    return await ranking_repository.rankings_cached(
        DB_PATH,
        get_catalog_image_counts=get_catalog_image_counts,
        cache_entry_count=_cache_entry_count,
        get_cached_image_id_set=get_cached_image_id_set,
        limit=limit,
        offset=offset,
        sort=sort,
        orientation=orientation,
        compared=compared,
        min_stars=min_stars,
        folder=folder,
        flag=flag,
        date_taken=date_taken,
        file_type=file_type,
        camera=camera,
        lens=lens,
        id_filter=id_filter,
        visible_thumb_size=visible_thumb_size,
        cache_root=cache_root,
        text_query=text_query,
    )


_ranking_count_cache_key = ranking_repository.ranking_count_cache_key
_facet_cache_key = ranking_repository.facet_cache_key


async def _count_rankings_uncached(orientation: str = "", compared: str = "", min_stars: int = 0,
                                  folder: str = "", flag: str = "", date_taken: str = "",
                                  file_type: str = "", camera: str = "", lens: str = "",
                                  id_filter: set = None,
                                  visible_thumb_size: str = "", cache_root: str = "",
                                  text_query: str = "") -> int:
    return await ranking_repository.count_rankings_uncached_with_visible_cache(
        DB_PATH,
        get_catalog_image_counts=get_catalog_image_counts,
        get_cached_image_id_set=get_cached_image_id_set,
        orientation=orientation,
        compared=compared,
        min_stars=min_stars,
        folder=folder,
        flag=flag,
        date_taken=date_taken,
        file_type=file_type,
        camera=camera,
        lens=lens,
        id_filter=id_filter,
        visible_thumb_size=visible_thumb_size,
        cache_root=cache_root,
        text_query=text_query,
    )


async def count_rankings(orientation: str = "", compared: str = "", min_stars: int = 0,
                         folder: str = "", flag: str = "", date_taken: str = "",
                         file_type: str = "", camera: str = "", lens: str = "",
                         id_filter: set = None,
                         visible_thumb_size: str = "", cache_root: str = "",
                         text_query: str = "") -> int:
    return await ranking_repository.count_rankings_cached(
        DB_PATH,
        get_catalog_image_counts=get_catalog_image_counts,
        get_cached_image_id_set=get_cached_image_id_set,
        orientation=orientation,
        compared=compared,
        min_stars=min_stars,
        folder=folder,
        flag=flag,
        date_taken=date_taken,
        file_type=file_type,
        camera=camera,
        lens=lens,
        id_filter=id_filter,
        visible_thumb_size=visible_thumb_size,
        cache_root=cache_root,
        text_query=text_query,
        ttl_seconds=RANKING_COUNT_CACHE_TTL_SECONDS,
    )


async def get_date_groups(orientation: str = "", compared: str = "", min_stars: int = 0,
                          folder: str = "", flag: str = "", date_taken: str = "",
                          file_type: str = "", camera: str = "", lens: str = "",
                          visible_thumb_size: str = "", cache_root: str = "",
                          id_filter: set | None = None, text_query: str = "",
                          _force_refresh: bool = False):
    return await ranking_repository.date_groups_cached(
        DB_PATH,
        get_catalog_image_counts=get_catalog_image_counts,
        orientation=orientation, compared=compared, min_stars=min_stars,
        folder=folder, flag=flag, date_taken=date_taken,
        file_type=file_type, camera=camera, lens=lens,
        visible_thumb_size=visible_thumb_size, cache_root=cache_root,
        id_filter=id_filter, text_query=text_query,
        force_refresh=_force_refresh,
        ttl_seconds=FACET_CACHE_TTL_SECONDS,
    )


async def get_map_markers(orientation: str = "", compared: str = "", min_stars: int = 0,
                          folder: str = "", flag: str = "", date_taken: str = "",
                          file_type: str = "", camera: str = "", lens: str = "",
                          visible_thumb_size: str = "", cache_root: str = "",
                          id_filter: set | None = None, text_query: str = ""):
    return await ranking_repository.map_markers_cached(
        DB_PATH,
        get_catalog_image_counts=get_catalog_image_counts,
        count_rankings_func=count_rankings,
        get_visible_pairing_pool_counts=get_visible_pairing_pool_counts,
        orientation=orientation,
        compared=compared,
        min_stars=min_stars,
        folder=folder,
        flag=flag,
        date_taken=date_taken,
        file_type=file_type,
        camera=camera,
        lens=lens,
        visible_thumb_size=visible_thumb_size,
        cache_root=cache_root,
        id_filter=id_filter,
        text_query=text_query,
        ttl_seconds=FACET_CACHE_TTL_SECONDS,
    )


async def get_filter_options():
    try:
        return await filter_options_repository.filter_options_cached(
            DB_PATH,
            get_catalog_image_counts=get_catalog_image_counts,
            get_active_source_id_set=get_active_source_id_set,
            ttl_seconds=FILTER_OPTIONS_CACHE_TTL_SECONDS,
        )
    finally:
        _sync_filter_options_refreshing_facade()


async def _load_filter_options_uncached():
    try:
        return await filter_options_repository.load_filter_options_uncached(
            DB_PATH,
            get_catalog_image_counts=get_catalog_image_counts,
            get_active_source_id_set=get_active_source_id_set,
            ttl_seconds=FILTER_OPTIONS_CACHE_TTL_SECONDS,
        )
    finally:
        _sync_filter_options_refreshing_facade()


async def get_stats():
    try:
        return await stats_repository.full_stats_cached(
            DB_PATH,
            ttl_seconds=STATS_CACHE_TTL_SECONDS,
            refresh=_get_stats_uncached,
        )
    finally:
        _sync_stats_inflight_task_facade()


async def _get_stats_uncached():
    try:
        return await stats_repository.refresh_full_stats_cache(
            DB_PATH,
            ttl_seconds=STATS_CACHE_TTL_SECONDS,
        )
    finally:
        _sync_stats_inflight_task_facade()


async def get_catalog_image_counts() -> dict:
    return await stats_repository.catalog_image_counts_cached(
        DB_PATH,
        ttl_seconds=CATALOG_CACHE_TTL_SECONDS,
    )


async def get_image_by_id(image_id: int):
    return await image_repository.get_image_by_id(DB_PATH, image_id)


async def get_images_by_ids(image_ids: list[int]) -> dict[int, dict]:
    """Fetch multiple images by ID in a single query. Returns {id: row_dict}."""
    return await image_repository.get_images_by_ids(DB_PATH, image_ids)


async def get_active_images_by_ids(image_ids: list[int]) -> dict[int, dict]:
    """Fetch active/online images by ID. Returns {id: row_dict}."""
    return await image_repository.get_active_images_by_ids(DB_PATH, image_ids)


async def get_top_images(limit: int = 50):
    """Get top N images by Elo for top-tier refinement."""
    counts = await get_catalog_image_counts()
    return await image_repository.get_top_images(
        DB_PATH,
        limit=limit,
        catalog_counts=counts,
    )


async def get_scan_folder():
    """Get a representative active source folder."""
    return await catalog_repository.get_scan_folder(DB_PATH)


# --- Embedding / Active Learning ---

async def get_unembedded_images(
    limit: int = 64,
    md_cache_root: str = "",
    cache_size: str = "md",
    embedding_config: dict | None = None,
):
    """Get kept/maybe images that don't have CLIP embeddings yet."""
    embedding_config = embedding_config or active_embedding_config()
    return await embedding_repository.get_unembedded_images(
        DB_PATH,
        embedding_config=embedding_config,
        **_embedding_repository_kwargs(),
        limit=limit,
        md_cache_root=md_cache_root,
        cache_size=cache_size,
    )


async def store_embeddings_batch(rows: list[tuple[int, bytes]], embedding_config: dict | None = None):
    """Store CLIP embedding blobs. Each row: (image_id, embedding_bytes)."""
    if not rows:
        return
    embedding_config = embedding_config or active_embedding_config()
    model_key = embedding_config["model_key"]
    image_ids = await embedding_repository.store_embeddings_batch(
        DB_PATH,
        rows=rows,
        embedding_config=embedding_config,
        **_embedding_repository_kwargs(),
        default_model_key=_legacy_embedding_model_key(),
    )
    _invalidate_embedding_count_cache()
    _notify_embedding_batch_stored(model_key, image_ids)


async def count_embeddings_for_model(embedding_config: dict, *, online_only: bool = False) -> int:
    if online_only:
        return await embedding_repository.count_embeddings_for_model(
            DB_PATH,
            embedding_config=embedding_config,
            **_embedding_repository_kwargs(),
            online_only=True,
        )
    counts = await get_catalog_image_counts()
    active = int(counts.get("active_images") or 0)
    if active <= 0:
        return 0
    active_source_ids = sorted(await get_active_source_id_set())
    return await embedding_repository.count_embeddings_for_model(
        DB_PATH,
        embedding_config=embedding_config,
        **_embedding_repository_kwargs(),
        online_only=False,
        catalog_counts=counts,
        active_source_ids=active_source_ids,
    )


async def get_all_embeddings():
    """Get all embeddings for prediction pass."""
    model_key = active_embedding_model_key()
    return await embedding_repository.get_all_embeddings(
        DB_PATH,
        model_key=model_key,
        **_embedding_repository_kwargs(),
    )


async def get_embedding_count() -> int:
    return await embedding_repository.embedding_count_cached(
        active_embedding_config=active_embedding_config,
        count_embeddings_for_model=count_embeddings_for_model,
        ttl_seconds=EMBEDDING_COUNT_CACHE_TTL_SECONDS,
    )


async def get_ai_status_counts() -> dict:
    """Return the small stats subset needed by the AI status poller."""
    return await stats_repository.ai_status_counts_cached(
        DB_PATH,
        get_embedding_count=get_embedding_count,
        get_active_source_ids=get_active_source_id_set,
        active_embedding_config=active_embedding_config,
        count_embeddings_for_model=count_embeddings_for_model,
        ttl_seconds=AI_STATUS_COUNTS_CACHE_TTL_SECONDS,
        stats_ttl_seconds=STATS_CACHE_TTL_SECONDS,
        refresh_full_stats=_get_stats_uncached,
        on_stats_refresh_scheduled=_sync_stats_inflight_task_facade,
    )
