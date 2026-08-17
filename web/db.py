"""Compatibility facade for database helpers.

Do Not Add New Logic Here: put SQL in ``data.repositories`` modules and keep
this module as a stable delegate for older callers during the migration.
"""

from core.catalog_path import catalog_path
import aiosqlite
import asyncio
import logging
import os
import time as _time  # noqa: F401  (test helpers reach for db._time)

from core import cache_events
from core.runtime_paths import resolve_runtime_paths
from data import connection as data_connection
from data import schema as data_schema
from data.repositories import cache_entries as cache_entry_repository
from data.repositories import catalog as catalog_repository
from data.repositories import captions as caption_repository
from data.repositories import embeddings as embedding_repository
from data.repositories import images as image_repository
from data.repositories import metadata_search as metadata_search_repository
from data.repositories import ratings as rating_repository
from data.repositories import stats as stats_repository
import settings

# The catalog's location lives in `core.catalog_path` now, because it is a path
# and a path has no business inside a data layer. Nothing here holds it: a
# module constant froze at import, so a test pointing at a temporary catalog
# was served the real one by anything that had already read the constant.
log = logging.getLogger(__name__)


def register_embedding_batch_listener(listener):
    cache_events.register_embedding_batch_listener(listener)


def _notify_embedding_batch_stored(model_key: str, image_ids: list[int]):
    cache_events.notify_embedding_batch_stored(model_key, image_ids)


SCHEMA_VERSION = data_schema.SCHEMA_VERSION


_ensured_embedding_model_keys: set[str] = set()


def active_embedding_config() -> dict:
    return settings.active_embedding_config()


def active_embedding_model_key() -> str:
    return active_embedding_config()["model_key"]


def active_caption_config() -> dict:
    return settings.active_caption_config()


def _rankings_moved() -> None:
    """What a rating write actually makes stale.

    Two things, both real: the text-search result caches, and the stored `stars`
    column, which is a projection of Elo and so has to be re-derived after Elo
    moves. This used to be five invalidators deep, and the star refresh was
    reached only as a side effect of expiring a stats cache — so removing the
    stats cache would have silently stopped stars updating. Naming the two
    genuine consequences is what makes that impossible to lose again.
    """

    cache_events.invalidate_rankings_cache()
    try:
        import elo_stars

        elo_stars.schedule_stored_stars_refresh(catalog_path())
    except Exception:
        logging.getLogger(__name__).debug("Stored star refresh skipped", exc_info=True)


async def get_db() -> aiosqlite.Connection:
    return await data_connection.open_async(catalog_path())


_refresh_source_online_states_on_conn = catalog_repository.refresh_source_online_states_on_conn


async def _normalize_legacy_image_state(conn):
    await data_schema.normalize_legacy_image_state(conn)


async def _migrate_catalog_sources(conn):
    await data_schema.migrate_catalog_sources(conn)


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
    _retain_active_embedding_cache()
    return result


async def get_search_query_embedding(config: dict, query: str) -> bytes | None:
    return await embedding_repository.get_search_query_embedding(
        catalog_path(),
        config=config,
        query=query,
    )


async def store_search_query_embedding(config: dict, query: str, blob: bytes):
    return await embedding_repository.store_search_query_embedding(
        catalog_path(),
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
        backups.backup_before_migration, catalog_path(), current, SCHEMA_VERSION
    )
    if not result or not result.get("ok"):
        raise RuntimeError("Pre-migration catalog backup failed; schema upgrade refused")


async def init_db():
    db_exists = os.path.exists(catalog_path())
    db = await get_db()
    try:
        if not db_exists:
            await data_connection.enable_wal(db, db_path=catalog_path())
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


async def set_image_dates(image_ids: list[int], *, date_taken: str, date_source: str):
    """Stamp an authoritative date — e.g. a film roll's delivery day."""
    await image_repository.set_image_dates(
        catalog_path(), image_ids, date_taken=date_taken, date_source=date_source
    )
    cache_events.invalidate_rankings_cache()


async def insert_images_batch(rows: list[tuple], source_id: int | None = None):
    """Insert image rows, ignoring duplicates."""
    if not rows:
        return
    await catalog_repository.insert_images_batch(catalog_path(), rows, source_id)
    zero_byte_paths = [str(row[1]) for row in rows if len(row) > 3 and row[3] == 0]
    quarantined = await catalog_repository.mark_zero_byte_images_missing(catalog_path(), zero_byte_paths)
    for image in quarantined:
        log.warning(
            "worker=catalog_scan image_id=%s skipped zero-byte image path=%r",
            image["id"],
            image["filepath"],
        )
    cache_events.invalidate_rankings_cache()


async def refresh_source_online_states():
    await catalog_repository.refresh_source_online_states(catalog_path())


async def add_or_restore_source(path: str):
    source = await catalog_repository.add_or_restore_source(catalog_path(), path)
    return source


async def mark_source_scan_started(source_id: int):
    await catalog_repository.mark_source_scan_started(catalog_path(), source_id)


async def mark_source_scan_finished(
    source_id: int,
    seen_filepaths: list[str] | None = None,
    excluded_directory_paths: list[str] | None = None,
):
    await catalog_repository.mark_source_scan_finished(
        catalog_path(),
        source_id,
        seen_filepaths,
        excluded_directory_paths,
    )


def mark_image_missing_sync(image_id: int, missing_at: float | None = None) -> bool:
    """Mark one image missing from sync thumbnail/worker code."""
    return catalog_repository.mark_image_missing_sync(catalog_path(), image_id, missing_at)


async def mark_image_missing(image_id: int, missing_at: float | None = None) -> bool:
    """Mark one image missing from async request/worker code."""
    changed = await catalog_repository.mark_image_missing(catalog_path(), image_id, missing_at)
    if changed:
        cache_events.invalidate_rankings_cache()
    return changed


async def get_catalog_summary():
    return await stats_repository.catalog_summary(catalog_path())


async def remove_source_keep_data(source_id: int):
    await catalog_repository.remove_source_keep_data(catalog_path(), source_id)


async def purge_source_catalog_data(source_id: int) -> dict:
    result = await catalog_repository.purge_source_catalog_data(catalog_path(), source_id)
    return result


async def set_image_status(image_id: int, status: str):
    await image_repository.set_image_status(catalog_path(), image_id, status)
    _rankings_moved()


async def set_image_flag(image_id: int, flag: str):
    await image_repository.set_image_flag(catalog_path(), image_id, flag)
    cache_events.invalidate_rankings_cache()


async def batch_set_image_flags(image_ids: list[int], flag: str, chunk_size: int = 500) -> int:
    updated = await image_repository.batch_set_image_flags(
        catalog_path(),
        image_ids,
        flag,
        chunk_size,
    )
    if updated:
        cache_events.invalidate_rankings_cache()
    return updated


async def get_active_images_for_pairing():
    return await rating_repository.get_active_images_for_pairing(
        catalog_path(),
        get_catalog_image_counts=get_catalog_image_counts,
    )


async def get_past_matchups() -> set[tuple[int, int]]:
    return await rating_repository.get_past_matchups(
        catalog_path(),
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
        catalog_path(),
        winner_id=winner_id,
        loser_id=loser_id,
        mode=mode,
        elo_before_winner=elo_before_winner,
        elo_before_loser=elo_before_loser,
        new_winner_elo=new_winner_elo,
        new_loser_elo=new_loser_elo,
        action_id=action_id,
    )
    _rankings_moved()


async def record_active_comparison(
    winner_id: int,
    loser_id: int,
    mode: str,
    action_id: str | None = None,
) -> dict | None:
    """Validate active images and record a comparison in one DB round trip."""
    counts = await get_catalog_image_counts()
    result = await rating_repository.record_active_comparison(
        catalog_path(),
        winner_id=winner_id,
        loser_id=loser_id,
        mode=mode,
        action_id=action_id,
        catalog_counts=counts,
    )
    if result is None:
        return None
    result.pop("_rated_delta", None)
    _rankings_moved()
    return result


async def record_active_mosaic_pick(
    picked_id: int,
    other_ids: list[int],
    action_id: str,
) -> dict:
    """Validate active mosaic images and record the full pick action."""
    counts = await get_catalog_image_counts()
    result = await rating_repository.record_active_mosaic_pick(
        catalog_path(),
        picked_id=picked_id,
        other_ids=other_ids,
        action_id=action_id,
        catalog_counts=counts,
    )
    if result.get("ok"):
        result.pop("_rated_delta", None)
        _rankings_moved()
    return result


async def undo_last_comparison():
    """Undo the last comparison/action, restoring Elo ratings."""
    result = await rating_repository.undo_last_comparison(catalog_path())
    if result is not None:
        _rankings_moved()
    return result




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
        catalog_path(),
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
    result.pop("_affected_people", None)
    return result


async def cluster_unassigned_faces(
    *,
    model_id: str,
    similarity_threshold: float = 0.52,
    merge_threshold: float = 0.62,
    limit: int = 500,
) -> dict:
    result = await people_repository.cluster_unassigned_faces(
        catalog_path(),
        model_id=model_id,
        similarity_threshold=similarity_threshold,
        merge_threshold=merge_threshold,
        limit=limit,
    )
    result.pop("_affected_people", None)
    return result


async def get_people_review(limit: int = 24, long_tail_threshold: int = 1) -> dict:
    current_settings = settings.get_settings()
    return await people_repository.get_people_review(
        catalog_path(),
        limit=limit,
        long_tail_threshold=long_tail_threshold,
        face_model_id=str(current_settings.get("face_model_id") or "buffalo_l"),
        cache_root=str(current_settings.get("ssd_cache_dir") or ""),
    )


async def get_people_status_counts(long_tail_threshold: int = 1) -> dict:
    current_settings = settings.get_settings()
    return await people_repository.get_people_status_counts(
        catalog_path(),
        long_tail_threshold=long_tail_threshold,
        face_model_id=str(current_settings.get("face_model_id") or "buffalo_l"),
        cache_root=str(current_settings.get("ssd_cache_dir") or ""),
    )


async def label_person(person_id: int, name: str) -> dict:
    return await people_repository.label_person(catalog_path(), person_id, name)


async def merge_people(source_person_id: int, target_person_id: int) -> dict:
    result = await people_repository.merge_people(catalog_path(), source_person_id, target_person_id)
    return result


async def assign_face(face_id: int, person_id: int | None = None, name: str = "") -> dict:
    result = await people_repository.assign_face(catalog_path(), face_id, person_id=person_id, name=name)
    return result


async def ignore_face(face_id: int) -> dict:
    result = await people_repository.ignore_face(catalog_path(), face_id)
    return result


async def ignore_person(person_id: int) -> dict:
    result = await people_repository.ignore_person(catalog_path(), person_id)
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
        catalog_path(),
        image_ids,
        size,
        cache_root,
        ttl_seconds=CACHED_IMAGE_IDS_TTL_SECONDS,
    )


async def get_active_source_id_set() -> frozenset[int]:
    return await catalog_repository.active_source_id_set(catalog_path())


async def metadata_search_image_ids(text_query: str, *, max_results: int = 5000) -> set[int] | None:
    """Return a bounded metadata-search ID set using the trigram FTS index.

    None means "fall back to regular metadata LIKE filtering"; an empty set is
    a real no-match result and lets callers skip expensive count queries.
    """
    query = (text_query or "").strip()
    if len(query) < 3:
        return None
    return await metadata_search_repository.metadata_search_image_ids(
        catalog_path(),
        query,
        active_source_ids=await get_active_source_id_set(),
        max_results=max_results,
    )


async def get_stats() -> dict:
    return await stats_repository.full_stats(catalog_path())


async def get_catalog_image_counts() -> dict:
    return await stats_repository.catalog_image_counts(catalog_path())


async def get_top_images(limit: int = 50):
    """Get top N images by Elo for top-tier refinement."""
    counts = await get_catalog_image_counts()
    return await image_repository.get_top_images(
        catalog_path(),
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
        catalog_path(),
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
        catalog_path(),
        rows=rows,
        embedding_config=embedding_config,
        **_embedding_repository_kwargs(),
        default_model_key=_legacy_embedding_model_key(),
    )
    _notify_embedding_batch_stored(model_key, image_ids)


async def poison_embedding_image(
    *,
    image_id: int,
    embedding_config: dict | None = None,
    error: str,
    force: bool = False,
) -> bool:
    return await embedding_repository.poison_embedding_image(
        catalog_path(),
        image_id=image_id,
        embedding_config=embedding_config or active_embedding_config(),
        error=error,
        force=force,
    )


async def clear_embedding_poison_ledger(
    embedding_config: dict | None = None,
) -> int:
    return await embedding_repository.clear_embedding_poison_ledger(
        catalog_path(),
        embedding_config=embedding_config or active_embedding_config(),
    )


async def count_embeddings_for_model(embedding_config: dict, *, online_only: bool = False) -> int:
    if online_only:
        return await embedding_repository.count_embeddings_for_model(
            catalog_path(),
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
        catalog_path(),
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
        catalog_path(),
        model_key=model_key,
        **_embedding_repository_kwargs(),
    )


async def get_embedding_count() -> int:
    return await count_embeddings_for_model(active_embedding_config(), online_only=True)


async def ensure_active_caption_fts_model(caption_config: dict | None = None) -> None:
    caption_config = caption_config or active_caption_config()
    await caption_repository.ensure_active_caption_fts_model(
        catalog_path(),
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
        catalog_path(),
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
        catalog_path(),
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
        catalog_path(),
        image_id=image_id,
        model_key=caption_config["model_key"],
        caption=caption,
        tags=tags or [],
        quality=quality,
        understanding=understanding,
        status=status,
        error=error,
    )
    cache_events.invalidate_rankings_cache()


async def get_caption_status_counts(caption_config: dict | None = None) -> dict:
    caption_config = caption_config or active_caption_config()
    return await caption_repository.caption_status_counts(
        catalog_path(),
        model_key=caption_config["model_key"],
        cache_root=settings.get_settings()["ssd_cache_dir"],
    )


async def get_image_caption(image_id: int, caption_config: dict | None = None) -> dict | None:
    caption_config = caption_config or active_caption_config()
    return await caption_repository.get_image_caption(
        catalog_path(),
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
        catalog_path(),
        image_id=image_id,
        model_key=caption_config["model_key"],
        caption=caption,
        tags=tags,
    )
    cache_events.invalidate_rankings_cache()
    return result


async def _annotate_caption_presence(rows, caption_config: dict | None = None) -> list[dict]:
    data = [dict(row) for row in rows or []]
    if not data:
        return data
    caption_config = caption_config or active_caption_config()
    summaries = await caption_repository.image_caption_summaries(
        catalog_path(),
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
        catalog_path(),
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
        catalog_path(),
        text_query,
        active_source_ids=await get_active_source_id_set(),
        max_results=max_results,
    )


async def caption_count_for_signature(caption_config: dict | None = None) -> int:
    caption_config = caption_config or active_caption_config()
    return await caption_repository.caption_count_for_signature(
        catalog_path(),
        model_key=caption_config["model_key"],
    )


async def get_ai_status_counts() -> dict:
    """Return the small stats subset needed by the AI status poller."""
    return await stats_repository.ai_status(
        catalog_path(),
        get_embedding_count=get_embedding_count,
        get_active_source_ids=get_active_source_id_set,
    )
