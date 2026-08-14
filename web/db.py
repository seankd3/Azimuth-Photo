"""Compatibility facade for database helpers.

Do Not Add New Logic Here: put SQL in ``data.repositories`` modules and keep
this module as a stable delegate for older callers during the migration.
"""

import aiosqlite
import asyncio
import logging
import os
import time as _time  # noqa: F401  (tests set cache expiries via db._time.time())

from core import cache_events
from core.runtime_paths import resolve_runtime_paths
from data import connection as data_connection
from data import schema as data_schema
from data.repositories import cache_entries as cache_entry_repository
from data.repositories import catalog as catalog_repository
from data.repositories import captions as caption_repository
from data.repositories import embeddings as embedding_repository
from data.repositories import filter_options as filter_options_repository
from data.repositories import images as image_repository
from data.repositories import metadata_search as metadata_search_repository
from data.repositories import people as people_repository
from data.repositories import ratings as rating_repository
from data.repositories import rankings as ranking_repository
from data.repositories import stats as stats_repository
import settings

DB_PATH = resolve_runtime_paths().catalog_db
log = logging.getLogger(__name__)


def register_embedding_batch_listener(listener):
    cache_events.register_embedding_batch_listener(listener)


def _notify_embedding_batch_stored(model_key: str, image_ids: list[int]):
    cache_events.notify_embedding_batch_stored(model_key, image_ids)


SCHEMA_VERSION = data_schema.SCHEMA_VERSION


_stats_inflight_task = stats_repository._stats_inflight_task
_filter_options_refreshing = filter_options_repository._filter_options_refreshing
_ensured_embedding_model_keys: set[str] = set()
_ai_status_counts_cache = stats_repository._ai_status_counts_cache
CACHED_IMAGE_IDS_TTL_SECONDS = cache_entry_repository.CACHED_IMAGE_IDS_TTL_SECONDS
RANKING_COUNT_CACHE_TTL_SECONDS = ranking_repository.RANKING_COUNT_CACHE_TTL_SECONDS
VISIBLE_PAIRING_POOL_COUNTS_TTL_SECONDS = rating_repository.VISIBLE_PAIRING_POOL_COUNTS_TTL_SECONDS
CACHE_ENTRY_COUNT_TTL_SECONDS = cache_entry_repository.CACHE_ENTRY_COUNT_TTL_SECONDS
STATS_CACHE_TTL_SECONDS = stats_repository.FULL_STATS_CACHE_TTL_SECONDS
EMBEDDING_COUNT_CACHE_TTL_SECONDS = embedding_repository.EMBEDDING_COUNT_CACHE_TTL_SECONDS
AI_STATUS_COUNTS_CACHE_TTL_SECONDS = stats_repository.AI_STATUS_COUNTS_CACHE_TTL_SECONDS
ACTIVE_SOURCE_IDS_TTL_SECONDS = catalog_repository.ACTIVE_SOURCE_IDS_TTL_SECONDS
CATALOG_CACHE_TTL_SECONDS = catalog_repository.CATALOG_CACHE_TTL_SECONDS
FACET_CACHE_TTL_SECONDS = ranking_repository.FACET_CACHE_TTL_SECONDS
FILTER_OPTIONS_CACHE_TTL_SECONDS = filter_options_repository.FILTER_OPTIONS_CACHE_TTL_SECONDS


def active_embedding_config() -> dict:
    return settings.active_embedding_config()


def active_embedding_model_key() -> str:
    return active_embedding_config()["model_key"]


def active_caption_config() -> dict:
    return settings.active_caption_config()


def active_caption_model_key() -> str:
    return active_caption_config()["model_key"]


def _invalidate_stats_cache():
    cache_events.invalidate_stats_cache()


def _invalidate_ai_status_counts_cache():
    cache_events.invalidate_ai_status_counts_cache()


def _invalidate_rating_stats_cache():
    cache_events.invalidate_rating_stats_cache()


def _patch_direct_rating_stats_cache(pair_delta: int, rated_image_delta: int):
    """Keep hot stats caches valid after direct Compare/Mosaic writes."""
    cache_events.patch_direct_rating_stats_cache(pair_delta, rated_image_delta)


def _invalidate_filter_options_cache():
    cache_events.invalidate_filter_options_cache()
    _sync_filter_options_refreshing_facade()


def clear_filter_options_cache():
    cache_events.clear_filter_options_cache()
    _sync_filter_options_refreshing_facade()


def _invalidate_ranking_count_cache():
    cache_events.invalidate_ranking_count_cache()


def invalidate_cached_image_ids_cache(cache_root: str | None = None, size: str | None = None):
    cache_events.invalidate_cached_image_ids_cache(cache_root, size)


def _invalidate_embedding_count_cache():
    cache_events.invalidate_embedding_count_cache()


def _invalidate_past_matchups_cache():
    cache_events.invalidate_past_matchups_cache()


def _sync_stats_inflight_task_facade():
    global _stats_inflight_task
    _stats_inflight_task = stats_repository._stats_inflight_task


def _sync_filter_options_refreshing_facade():
    global _filter_options_refreshing
    _filter_options_refreshing = filter_options_repository._filter_options_refreshing


async def get_db() -> aiosqlite.Connection:
    return await data_connection.open_async(DB_PATH)


_refresh_source_online_states_on_conn = catalog_repository.refresh_source_online_states_on_conn


async def _normalize_legacy_image_state(conn):
    if await data_schema.normalize_legacy_image_state(conn):
        _invalidate_filter_options_cache()


async def _migrate_catalog_sources(conn):
    if await data_schema.migrate_catalog_sources(conn):
        _invalidate_filter_options_cache()


_schema_is_current = data_schema.schema_is_current


async def _check_embedding_dimension(conn):
    """Legacy no-op kept for callers from older code.

    Model upgrades now preserve old vectors by writing each model to
    embeddings_by_model instead of clearing the original embeddings table.
    """
    await _ensure_embedding_model_tables(conn)


def _legacy_embedding_model_key() -> str:
    return active_embedding_model_key()


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


def _retain_active_embedding_cache() -> None:
    try:
        import embed_cache

        embed_cache.retain_only(active_embedding_model_key())
    except Exception:
        pass


async def purge_retired_embedding_data() -> dict:
    conn = await get_db()
    try:
        _ensured_embedding_model_keys.clear()
        result = await embedding_repository.purge_retired_embedding_data(
            conn,
            active_config=active_embedding_config(),
        )
    finally:
        await conn.close()
    _invalidate_embedding_count_cache()
    _invalidate_ai_status_counts_cache()
    _retain_active_embedding_cache()
    return result


async def get_search_query_embedding(config: dict, query: str) -> bytes | None:
    return await embedding_repository.get_search_query_embedding(
        DB_PATH,
        config=config,
        query=query,
    )


async def store_search_query_embedding(config: dict, query: str, blob: bytes):
    return await embedding_repository.store_search_query_embedding(
        DB_PATH,
        config=config,
        query=query,
        blob=blob,
    )


_ensure_metadata_fts = data_schema.ensure_metadata_fts
_apply_schema_and_migrations = data_schema.apply_schema_and_migrations
_backfill_image_date_sources = data_schema.backfill_image_date_sources


async def _backup_before_migration(conn) -> None:
    """Take a protected pre-migration snapshot when an existing catalog is
    about to be upgraded to a newer schema. Refuse migration without it."""
    try:
        cursor = await conn.execute("PRAGMA user_version")
        row = await cursor.fetchone()
        current = int(row[0]) if row else 0
    except Exception as exc:
        raise RuntimeError("Cannot determine catalog version before backup") from exc
    if current < 0 or current >= SCHEMA_VERSION:
        return
    if current == 0:
        cursor = await conn.execute(
            "SELECT 1 FROM sqlite_master "
            "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' LIMIT 1"
        )
        if await cursor.fetchone() is None:
            return
    from features.system import backups

    result = await asyncio.to_thread(
        backups.backup_before_migration, DB_PATH, current, SCHEMA_VERSION
    )
    if not result or not result.get("ok"):
        raise RuntimeError("Pre-migration catalog backup failed; schema upgrade refused")


async def init_db():
    db_exists = os.path.exists(DB_PATH)
    db = await get_db()
    try:
        if not db_exists:
            await data_connection.enable_wal(db, db_path=DB_PATH)
        if db_exists:
            await _backup_before_migration(db)
        if db_exists and await _schema_is_current(db):
            await _normalize_legacy_image_state(db)
            # Cheap, and it must not wait for some *other* thing to look out of
            # date: the row-version trigger decides how much every satellite
            # re-downloads, and a catalog that already looks current would
            # otherwise keep an older, costlier one forever.
            await data_schema.ensure_catalog_export_row_versions(db)
            await _refresh_source_online_states_on_conn(db)
            await _check_embedding_dimension(db)
            _ensured_embedding_model_keys.clear()
            await embedding_repository.purge_retired_embedding_data(
                db,
                active_config=active_embedding_config(),
            )
            _retain_active_embedding_cache()
            await _ensure_metadata_fts(db)
            await db.commit()
            return
        await _apply_schema_and_migrations(db, db_exists=db_exists)
        await _migrate_catalog_sources(db)
        if await _backfill_image_date_sources(db):
            cache_events.invalidate_rankings_cache()
            _invalidate_filter_options_cache()
        await _refresh_source_online_states_on_conn(db)
        await _ensure_embedding_model_tables(db)
        _ensured_embedding_model_keys.clear()
        await embedding_repository.purge_retired_embedding_data(
            db,
            active_config=active_embedding_config(),
        )
        _retain_active_embedding_cache()
        await _ensure_metadata_fts(db)
        await db.commit()
    finally:
        await db.close()


async def batch_set_orientations(updates: list[tuple[str, float, int]]):
    """Set orientation and aspect_ratio for multiple images. Each tuple: (orientation, aspect_ratio, image_id)."""
    await image_repository.batch_set_orientations(DB_PATH, updates)
    _invalidate_filter_options_cache()


async def batch_update_metadata(updates: list[tuple]):
    """Persist extracted metadata. Tuples are built in features.catalog.metadata.metadata_update_tuple."""
    if not updates:
        return
    await image_repository.batch_update_metadata(DB_PATH, updates)
    _invalidate_filter_options_cache()
    cache_events.invalidate_rankings_cache()


async def insert_images_batch(rows: list[tuple], source_id: int | None = None):
    """Insert image rows, ignoring duplicates."""
    if not rows:
        return
    await catalog_repository.insert_images_batch(DB_PATH, rows, source_id)
    zero_byte_paths = [str(row[1]) for row in rows if len(row) > 3 and row[3] == 0]
    quarantined = await catalog_repository.mark_zero_byte_images_missing(DB_PATH, zero_byte_paths)
    for image in quarantined:
        log.warning(
            "worker=catalog_scan image_id=%s skipped zero-byte image path=%r",
            image["id"],
            image["filepath"],
        )
    _invalidate_stats_cache()
    _invalidate_filter_options_cache()
    cache_events.invalidate_rankings_cache()


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


async def mark_source_scan_finished(
    source_id: int,
    seen_filepaths: list[str] | None = None,
    excluded_directory_paths: list[str] | None = None,
):
    await catalog_repository.mark_source_scan_finished(
        DB_PATH,
        source_id,
        seen_filepaths,
        excluded_directory_paths,
    )
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


async def mark_image_missing(image_id: int, missing_at: float | None = None) -> bool:
    """Mark one image missing from async request/worker code."""
    changed = await catalog_repository.mark_image_missing(DB_PATH, image_id, missing_at)
    if changed:
        _invalidate_stats_cache()
        _invalidate_filter_options_cache()
        invalidate_cached_image_ids_cache()
        cache_events.invalidate_rankings_cache()
    return changed


async def get_catalog_summary():
    return await catalog_repository.catalog_summary_cached(
        DB_PATH,
        get_stats=get_stats,
        refresh_source_online_states=refresh_source_online_states,
        ttl_seconds=CATALOG_CACHE_TTL_SECONDS,
    )


async def remove_source_keep_data(source_id: int):
    await catalog_repository.remove_source_keep_data(DB_PATH, source_id)
    _invalidate_stats_cache()
    _invalidate_filter_options_cache()


async def purge_source_catalog_data(source_id: int) -> dict:
    result = await catalog_repository.purge_source_catalog_data(DB_PATH, source_id)
    _invalidate_stats_cache()
    _invalidate_filter_options_cache()
    invalidate_cached_image_ids_cache()
    return result


async def set_image_status(image_id: int, status: str):
    await image_repository.set_image_status(DB_PATH, image_id, status)
    _invalidate_past_matchups_cache()
    _invalidate_rating_stats_cache()
    _invalidate_filter_options_cache()


async def set_image_flag(image_id: int, flag: str):
    await image_repository.set_image_flag(DB_PATH, image_id, flag)
    cache_events.invalidate_rankings_cache()
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
        cache_events.invalidate_rankings_cache()
        _invalidate_ranking_count_cache()
        _invalidate_filter_options_cache()
    return updated


async def get_active_images_for_pairing():
    return await rating_repository.get_active_images_for_pairing(
        DB_PATH,
        get_catalog_image_counts=get_catalog_image_counts,
    )


async def get_visible_pairing_pool_counts(size: str, cache_root: str) -> dict:
    return await rating_repository.visible_pairing_pool_counts_cached(
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
    return await rating_repository.visible_orientation_pairing_pool_counts_cached(
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
    cache_events.invalidate_people_dependent_caches(
        filter_options_invalidator=_invalidate_filter_options_cache,
    )


async def refresh_people_membership(person_ids: tuple[int, ...] | None = None) -> None:
    await people_repository.refresh_people_membership(DB_PATH, person_ids)
    _invalidate_people_dependent_caches()


async def get_people_image_id_filter(person_ids: tuple[int, ...] | str) -> set[int] | None:
    return await people_repository.get_people_image_id_filter(DB_PATH, person_ids)


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
    # Backlog scans store one result every few hundred ms; only a scan that
    # actually touched person memberships may clear the facet/count caches,
    # or the People worker keeps every warm path cold for hours.
    affected_people = result.pop("_affected_people", [])
    if affected_people:
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


async def get_people_status_counts(long_tail_threshold: int = 1) -> dict:
    current_settings = settings.get_settings()
    return await people_repository.get_people_status_counts(
        DB_PATH,
        long_tail_threshold=long_tail_threshold,
        face_model_id=str(current_settings.get("face_model_id") or "buffalo_l"),
        cache_root=str(current_settings.get("ssd_cache_dir") or ""),
    )


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


async def get_rankings(limit: int = 100, offset: int = 0, sort: str = "elo",
                       orientation: str = "", compared: str = "", min_stars: int = 0,
                       folder: str = "", flag: str = "", date_taken: str = "",
                       file_type: str = "", camera: str = "", lens: str = "",
                       tag: str = "",
                       id_filter: set = None,
                       collection_id: int = 0,
                       visible_thumb_size: str = "", cache_root: str = "",
                       text_query: str = "",
                       exclude_collapsed_stack_members: bool = False,
                       exclude_sources=()):
    rows = await ranking_repository.rankings_cached(
        DB_PATH,
        get_catalog_image_counts=get_catalog_image_counts,
        cache_entry_count=_cache_entry_count,
        get_cached_image_id_set=get_cached_image_id_set,
        get_cached_image_ids=get_cached_image_ids,
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
        tag=tag,
        id_filter=id_filter,
        collection_id=collection_id,
        visible_thumb_size=visible_thumb_size,
        cache_root=cache_root,
        text_query=text_query,
        exclude_collapsed_stack_members=exclude_collapsed_stack_members,
        exclude_sources=exclude_sources,
        caption_model_key=active_caption_model_key(),
    )
    return await _annotate_caption_presence(rows)


async def get_ranking_id_elo(
    orientation: str = "",
    compared: str = "",
    min_stars: int = 0,
    folder: str = "",
    flag: str = "",
    date_taken: str = "",
    file_type: str = "",
    camera: str = "",
    lens: str = "",
    tag: str = "",
    id_filter: set = None,
    collection_id: int = 0,
    text_query: str = "",
    exclude_collapsed_stack_members: bool = False,
    exclude_sources=(),
) -> list[tuple[int, float]]:
    """Lightweight (id, elo) pairs for taste-order construction."""
    return await ranking_repository.ranking_id_elo(
        DB_PATH,
        catalog_counts=await get_catalog_image_counts(),
        orientation=orientation,
        compared=compared,
        min_stars=min_stars,
        folder=folder,
        flag=flag,
        date_taken=date_taken,
        file_type=file_type,
        camera=camera,
        lens=lens,
        tag=tag,
        id_filter=id_filter,
        collection_id=collection_id,
        text_query=text_query,
        exclude_collapsed_stack_members=exclude_collapsed_stack_members,
        exclude_sources=exclude_sources,
        caption_model_key=active_caption_model_key(),
    )


async def get_ranking_rows_by_ids(image_ids: list[int]) -> list[dict]:
    """Fetch ranking rows for a small id page (IN-clause, then caption annotate)."""
    rows = await ranking_repository.ranking_rows_by_ids(DB_PATH, image_ids)
    return await _annotate_caption_presence(rows)


async def rank_quality(orientation: str = "", compared: str = "", min_stars: int = 0,
                       folder: str = "", flag: str = "", date_taken: str = "",
                       file_type: str = "", camera: str = "", lens: str = "",
                       tag: str = "",
                       id_filter: set = None, text_query: str = "",
                       exclude_collapsed_stack_members: bool = False,
                       exclude_sources=()) -> dict:
    return await ranking_repository.rank_quality(
        DB_PATH,
        orientation=orientation,
        compared=compared,
        min_stars=min_stars,
        folder=folder,
        flag=flag,
        date_taken=date_taken,
        file_type=file_type,
        camera=camera,
        lens=lens,
        tag=tag,
        id_filter=id_filter,
        text_query=text_query,
        exclude_collapsed_stack_members=exclude_collapsed_stack_members,
        exclude_sources=exclude_sources,
        caption_model_key=active_caption_model_key(),
    )


async def date_histogram(**kwargs) -> dict:
    kwargs.setdefault("caption_model_key", active_caption_model_key())
    force_refresh = bool(kwargs.pop("_force_refresh", False))
    return await ranking_repository.date_histogram_cached(
        DB_PATH,
        get_catalog_image_counts=get_catalog_image_counts,
        force_refresh=force_refresh,
        ttl_seconds=FACET_CACHE_TTL_SECONDS,
        **kwargs,
    )


async def scope_counts(**kwargs) -> dict:
    kwargs.setdefault("caption_model_key", active_caption_model_key())
    return await ranking_repository.scope_counts(DB_PATH, **kwargs)


async def count_rankings(orientation: str = "", compared: str = "", min_stars: int = 0,
                         folder: str = "", flag: str = "", date_taken: str = "",
                         file_type: str = "", camera: str = "", lens: str = "",
                         tag: str = "",
                         id_filter: set = None,
                         collection_id: int = 0,
                         visible_thumb_size: str = "", cache_root: str = "",
                         text_query: str = "",
                         exclude_collapsed_stack_members: bool = False,
                         exclude_sources=()) -> int:
    return await ranking_repository.count_rankings_cached(
        DB_PATH,
        get_catalog_image_counts=get_catalog_image_counts,
        get_cached_image_id_set=get_cached_image_id_set,
        get_cached_image_ids=get_cached_image_ids,
        orientation=orientation,
        compared=compared,
        min_stars=min_stars,
        folder=folder,
        flag=flag,
        date_taken=date_taken,
        file_type=file_type,
        camera=camera,
        lens=lens,
        tag=tag,
        id_filter=id_filter,
        collection_id=collection_id,
        visible_thumb_size=visible_thumb_size,
        cache_root=cache_root,
        text_query=text_query,
        exclude_collapsed_stack_members=exclude_collapsed_stack_members,
        exclude_sources=exclude_sources,
        caption_model_key=active_caption_model_key(),
        ttl_seconds=RANKING_COUNT_CACHE_TTL_SECONDS,
    )


async def get_date_groups(orientation: str = "", compared: str = "", min_stars: int = 0,
                          folder: str = "", flag: str = "", date_taken: str = "",
                          file_type: str = "", camera: str = "", lens: str = "",
                          tag: str = "",
                          visible_thumb_size: str = "", cache_root: str = "",
                          id_filter: set | None = None, text_query: str = "",
                          _force_refresh: bool = False,
                          exclude_collapsed_stack_members: bool = False,
                          exclude_sources=()):
    return await ranking_repository.date_groups_cached(
        DB_PATH,
        get_catalog_image_counts=get_catalog_image_counts,
        orientation=orientation, compared=compared, min_stars=min_stars,
        folder=folder, flag=flag, date_taken=date_taken,
        file_type=file_type, camera=camera, lens=lens, tag=tag,
        visible_thumb_size=visible_thumb_size, cache_root=cache_root,
        id_filter=id_filter, text_query=text_query,
        force_refresh=_force_refresh,
        ttl_seconds=FACET_CACHE_TTL_SECONDS,
        exclude_collapsed_stack_members=exclude_collapsed_stack_members,
        exclude_sources=exclude_sources,
        caption_model_key=active_caption_model_key(),
    )


async def get_map_markers(orientation: str = "", compared: str = "", min_stars: int = 0,
                          folder: str = "", flag: str = "", date_taken: str = "",
                          file_type: str = "", camera: str = "", lens: str = "",
                          tag: str = "",
                          visible_thumb_size: str = "", cache_root: str = "",
                          id_filter: set | None = None, text_query: str = "", collection_id: int = 0,
                          exclude_sources=()):
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
        tag=tag,
        visible_thumb_size=visible_thumb_size,
        cache_root=cache_root,
        id_filter=id_filter,
        text_query=text_query,
        collection_id=collection_id,
        exclude_sources=exclude_sources,
        ttl_seconds=FACET_CACHE_TTL_SECONDS,
        caption_model_key=active_caption_model_key(),
    )


async def get_filter_options(**scope):
    # Request wiring always passes the full kwargs spray, so drop default
    # values first: an all-default scope is the plain Library request and must
    # ride the warmed SWR cache. An empty-but-present id_filter is a real
    # scope (a search that matched nothing), never the cached payload.
    scope = {
        key: value
        for key, value in scope.items()
        if value or (key == "id_filter" and value is not None)
    }
    if scope:
        scope["caption_model_key"] = scope.get("caption_model_key") or active_caption_model_key()
        catalog_counts = await get_catalog_image_counts()
        return await filter_options_repository.filter_options(
            DB_PATH,
            catalog_counts=catalog_counts,
            active_source_ids=sorted(await get_active_source_id_set()),
            **scope,
        )
    try:
        return await filter_options_repository.filter_options_cached(
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


async def get_top_images(limit: int = 50):
    """Get top N images by Elo for top-tier refinement."""
    counts = await get_catalog_image_counts()
    return await image_repository.get_top_images(
        DB_PATH,
        limit=limit,
        catalog_counts=counts,
    )


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


async def poison_embedding_image(
    *,
    image_id: int,
    embedding_config: dict | None = None,
    error: str,
    force: bool = False,
) -> bool:
    return await embedding_repository.poison_embedding_image(
        DB_PATH,
        image_id=image_id,
        embedding_config=embedding_config or active_embedding_config(),
        error=error,
        force=force,
    )


async def clear_embedding_poison_ledger(
    embedding_config: dict | None = None,
) -> int:
    return await embedding_repository.clear_embedding_poison_ledger(
        DB_PATH,
        embedding_config=embedding_config or active_embedding_config(),
    )


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


async def ensure_active_caption_fts_model(caption_config: dict | None = None) -> None:
    caption_config = caption_config or active_caption_config()
    await caption_repository.ensure_active_caption_fts_model(
        DB_PATH,
        caption_config["model_key"],
    )


async def get_images_needing_captions(
    limit: int = 8,
    caption_config: dict | None = None,
    cache_root: str = "",
    cache_size: str = "md",
    include_understanding_backfill: bool = False,
) -> list[dict]:
    caption_config = caption_config or active_caption_config()
    return await caption_repository.get_images_needing_captions(
        DB_PATH,
        model_key=caption_config["model_key"],
        cache_root=cache_root or settings.get_settings()["ssd_cache_dir"],
        cache_size=cache_size,
        limit=limit,
        include_understanding_backfill=include_understanding_backfill,
    )


async def count_images_needing_captions(
    caption_config: dict | None = None,
    cache_root: str = "",
    cache_size: str = "md",
    include_understanding_backfill: bool = False,
) -> int:
    caption_config = caption_config or active_caption_config()
    return await caption_repository.count_images_needing_captions(
        DB_PATH,
        model_key=caption_config["model_key"],
        cache_root=cache_root or settings.get_settings()["ssd_cache_dir"],
        cache_size=cache_size,
        include_understanding_backfill=include_understanding_backfill,
    )


async def store_caption_result(
    *,
    image_id: int,
    caption_config: dict | None = None,
    caption: str = "",
    tags=None,
    quality: str | None = None,
    understanding: dict | None = None,
    status: str = "done",
    error: str = "",
) -> None:
    caption_config = caption_config or active_caption_config()
    await ensure_active_caption_fts_model(caption_config)
    await caption_repository.store_caption_result(
        DB_PATH,
        image_id=image_id,
        model_key=caption_config["model_key"],
        caption=caption,
        tags=tags or [],
        quality=quality,
        understanding=understanding,
        status=status,
        error=error,
    )
    caption_repository.invalidate_tags_cache()
    cache_events.invalidate_rankings_cache()
    cache_events.invalidate_ranking_count_cache()
    cache_events.invalidate_facet_caches()


async def get_caption_status_counts(caption_config: dict | None = None) -> dict:
    caption_config = caption_config or active_caption_config()
    return await caption_repository.caption_status_counts(
        DB_PATH,
        model_key=caption_config["model_key"],
        cache_root=settings.get_settings()["ssd_cache_dir"],
    )


async def get_image_caption(image_id: int, caption_config: dict | None = None) -> dict | None:
    caption_config = caption_config or active_caption_config()
    return await caption_repository.get_image_caption(
        DB_PATH,
        image_id=image_id,
        model_key=caption_config["model_key"],
    )


async def owner_update_caption(
    *,
    image_id: int,
    caption_config: dict | None = None,
    caption: str | None = None,
    tags=None,
) -> dict | None:
    caption_config = caption_config or active_caption_config()
    await ensure_active_caption_fts_model(caption_config)
    result = await caption_repository.owner_update_caption(
        DB_PATH,
        image_id=image_id,
        model_key=caption_config["model_key"],
        caption=caption,
        tags=tags,
    )
    cache_events.invalidate_rankings_cache()
    db_invalidate = getattr(cache_events, "invalidate_ranking_count_cache", None)
    if callable(db_invalidate):
        db_invalidate()
    facet_invalidate = getattr(cache_events, "invalidate_facet_caches", None)
    if callable(facet_invalidate):
        facet_invalidate()
    return result


async def get_tags(q: str = "", limit: int = 100, caption_config: dict | None = None) -> list[dict]:
    caption_config = caption_config or active_caption_config()
    return await caption_repository.list_tags(
        DB_PATH,
        model_key=caption_config["model_key"],
        q=q,
        limit=limit,
        signature=await caption_repository.tag_signature(DB_PATH, model_key=caption_config["model_key"]),
    )


async def _annotate_caption_presence(rows, caption_config: dict | None = None) -> list[dict]:
    data = [dict(row) for row in rows or []]
    if not data:
        return data
    caption_config = caption_config or active_caption_config()
    summaries = await caption_repository.image_caption_summaries(
        DB_PATH,
        model_key=caption_config["model_key"],
        image_ids=[row.get("id") for row in data],
    )
    for row in data:
        summary = summaries.get(int(row.get("id") or 0), {})
        row["has_caption"] = bool(summary.get("has_caption"))
        row["caption_tags"] = summary.get("caption_tags") or []
    return data


async def caption_search_ranked_image_ids(
    text_query: str,
    *,
    max_results: int = 5000,
    caption_config: dict | None = None,
) -> list[tuple[int, float]]:
    caption_config = caption_config or active_caption_config()
    return await caption_repository.caption_search_ranked_image_ids(
        DB_PATH,
        text_query,
        active_source_ids=await get_active_source_id_set(),
        model_key=caption_config["model_key"],
        max_results=max_results,
    )


async def metadata_search_ranked_image_ids(
    text_query: str,
    *,
    max_results: int = 5000,
) -> list[tuple[int, float]]:
    return await metadata_search_repository.metadata_search_ranked_image_ids(
        DB_PATH,
        text_query,
        active_source_ids=await get_active_source_id_set(),
        max_results=max_results,
    )


async def caption_count_for_signature(caption_config: dict | None = None) -> int:
    caption_config = caption_config or active_caption_config()
    return await caption_repository.caption_count_for_signature(
        DB_PATH,
        model_key=caption_config["model_key"],
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
