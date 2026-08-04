"""Aggregate count queries and caches for catalog and AI status surfaces."""

from core.numbers import increment_cached_int as _increment_cached_int
import asyncio
import logging
import time as _time

from core.background import track_background_task
from data import connection
from data.repositories.catalog import active_image_condition, visible_image_condition

log = logging.getLogger(__name__)

CATALOG_IMAGE_COUNTS_TTL_SECONDS = 10.0
FULL_STATS_CACHE_TTL_SECONDS = 30.0
AI_STATUS_COUNTS_CACHE_TTL_SECONDS = 30.0
_catalog_image_counts_cache = {"data": None, "expires": 0}
_stats_cache = {"data": None, "expires": 0}
_stats_inflight_task = None
_ai_status_counts_cache = {"data": None, "expires": 0}


async def catalog_image_counts(db_path: str) -> dict:
    from data.repositories import catalog as catalog_repository

    # All Photos / rankings short-circuit on these denormalized sums. Repair
    # hub:// drift before reading so mirrored satellite libraries stay complete.
    await catalog_repository.repair_hub_mirror_source_counts(db_path)

    conn = await connection.open_async(db_path)
    try:
        cursor = await conn.execute(
            "SELECT "
            "COALESCE(SUM(image_count), 0) AS catalog_images, "
            "COALESCE(SUM(active_image_count), 0) AS active_images, "
            "COALESCE(SUM(CASE WHEN included = 0 THEN image_count ELSE 0 END), 0) AS removed_images, "
            "COALESCE(SUM(CASE WHEN included = 1 AND online = 0 THEN image_count ELSE 0 END), 0) AS offline_images "
            "FROM catalog_sources"
        )
        row = await cursor.fetchone()
        return {
            "total_catalog_images": int(row["catalog_images"] or 0),
            "active_images": int(row["active_images"] or 0),
            "removed_images": int(row["removed_images"] or 0),
            "offline_images": int(row["offline_images"] or 0),
        }
    finally:
        await connection.close_async(conn, db_path=db_path)


def invalidate_catalog_image_counts_cache() -> None:
    _catalog_image_counts_cache["data"] = None
    _catalog_image_counts_cache["expires"] = 0


async def catalog_image_counts_cached(
    db_path: str,
    *,
    ttl_seconds: float = CATALOG_IMAGE_COUNTS_TTL_SECONDS,
) -> dict:
    now = _time.time()
    if _catalog_image_counts_cache["data"] and now < _catalog_image_counts_cache["expires"]:
        return dict(_catalog_image_counts_cache["data"])
    result = await catalog_image_counts(db_path)
    _catalog_image_counts_cache["data"] = result
    _catalog_image_counts_cache["expires"] = _time.time() + ttl_seconds
    return dict(result)


async def full_stats(db_path: str) -> dict:
    from data.repositories import catalog as catalog_repository

    await catalog_repository.repair_hub_mirror_source_counts(db_path)

    conn = await connection.open_async(db_path)
    try:
        cursor = await conn.execute(
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
            cursor = await conn.execute(
                "SELECT flag, COUNT(*) AS count FROM images "
                f"WHERE {visible_image_condition('')} "
                "AND flag IN ('picked', 'rejected') GROUP BY flag"
            )
            flag_counts = {
                row["flag"]: int(row["count"] or 0)
                for row in await cursor.fetchall()
            }
        else:
            cursor = await conn.execute(
                "SELECT i.flag, COUNT(*) AS count "
                "FROM images i JOIN catalog_sources s ON s.id = i.source_id "
                "WHERE s.included = 1 "
                f"AND {visible_image_condition()} "
                "AND i.flag IN ('picked', 'rejected') "
                "GROUP BY i.flag"
            )
            flag_counts = {
                row["flag"]: int(row["count"] or 0)
                for row in await cursor.fetchall()
            }

        cursor = await conn.execute("SELECT COUNT(*) as c FROM comparisons")
        total_catalog_comparison_rows = int((await cursor.fetchone())["c"] or 0)

        invalid_comparison_rows = 0
        invalid_comparison_endpoints = 0
        if all_catalog_images_active:
            direct_comparison_rows = total_catalog_comparison_rows
        elif active <= 0:
            direct_comparison_rows = 0
        else:
            cursor = await conn.execute(
                "WITH invalid_endpoints AS ("
                "  SELECT c.rowid AS comparison_rowid "
                "  FROM images i JOIN catalog_sources s ON s.id = i.source_id "
                "  JOIN comparisons c INDEXED BY idx_comparisons_pair ON c.winner_id = i.id "
                f"  WHERE NOT ({active_image_condition()}) "
                "  UNION ALL "
                "  SELECT c.rowid AS comparison_rowid "
                "  FROM images i JOIN catalog_sources s ON s.id = i.source_id "
                "  JOIN comparisons c INDEXED BY idx_comparisons_loser ON c.loser_id = i.id "
                f"  WHERE NOT ({active_image_condition()})"
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
            cursor = await conn.execute(
                "SELECT "
                "COALESCE(SUM(COALESCE(comparisons, 0)), 0) AS image_comparison_count, "
                "COALESCE(SUM(COALESCE(propagated_updates, 0)), 0) AS propagated_update_count, "
                "SUM(CASE WHEN COALESCE(comparisons, 0) > 0 "
                "      OR COALESCE(propagated_updates, 0) > 0 "
                "      OR ABS(COALESCE(elo, 1200.0) - 1200.0) > 0.0001 "
                "    THEN 1 ELSE 0 END) AS rated_images "
                "FROM images INDEXED BY idx_images_status "
                f"WHERE {visible_image_condition('')}"
            )
            ranking_counts = await cursor.fetchone()
        else:
            cursor = await conn.execute(
                "SELECT "
                "COALESCE(SUM(COALESCE(i.comparisons, 0)), 0) AS image_comparison_count, "
                "COALESCE(SUM(COALESCE(i.propagated_updates, 0)), 0) AS propagated_update_count, "
                "SUM(CASE WHEN COALESCE(i.comparisons, 0) > 0 "
                "      OR COALESCE(i.propagated_updates, 0) > 0 "
                "      OR ABS(COALESCE(i.elo, 1200.0) - 1200.0) > 0.0001 "
                "    THEN 1 ELSE 0 END) AS rated_images "
                "FROM images i INDEXED BY idx_images_source_missing_rating_signal "
                "LEFT JOIN catalog_sources s ON s.id = i.source_id "
                "WHERE s.included = 1 "
                f"AND {visible_image_condition()}"
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
            cursor = await conn.execute(
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

        return {
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
    finally:
        await connection.close_async(conn, db_path=db_path)


def invalidate_full_stats_cache() -> None:
    _stats_cache["data"] = None
    _stats_cache["expires"] = 0


def invalidate_ai_status_counts_cache() -> None:
    _ai_status_counts_cache["data"] = None
    _ai_status_counts_cache["expires"] = 0


def full_stats_cache_expired(now: float | None = None) -> bool:
    checked_at = _time.time() if now is None else now
    return _stats_cache["data"] is not None and checked_at >= _stats_cache["expires"]


async def refresh_full_stats_cache(
    db_path: str,
    *,
    ttl_seconds: float = FULL_STATS_CACHE_TTL_SECONDS,
) -> dict:
    if _stats_cache["data"] and _time.time() < _stats_cache["expires"]:
        return _stats_cache["data"]
    result = await full_stats(db_path)
    _stats_cache["data"] = result
    _stats_cache["expires"] = _time.time() + ttl_seconds
    return result


async def _do_full_stats_refresh(db_path: str, ttl_seconds: float, refresh):
    if refresh is not None:
        return await refresh()
    return await refresh_full_stats_cache(db_path, ttl_seconds=ttl_seconds)


async def _swr_full_stats_refresh(db_path: str, ttl_seconds: float, refresh):
    try:
        return await _do_full_stats_refresh(db_path, ttl_seconds, refresh)
    except Exception:
        log.exception("full stats background refresh failed")


def schedule_full_stats_refresh(
    db_path: str,
    *,
    ttl_seconds: float = FULL_STATS_CACHE_TTL_SECONDS,
    refresh=None,
):
    global _stats_inflight_task
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return None
    task = _stats_inflight_task
    if task is None or task.done() or task.get_loop() is not loop:
        _stats_inflight_task = track_background_task(
            _swr_full_stats_refresh(db_path, ttl_seconds, refresh)
        )
    return _stats_inflight_task


async def full_stats_cached(
    db_path: str,
    *,
    ttl_seconds: float = FULL_STATS_CACHE_TTL_SECONDS,
    refresh=None,
) -> dict:
    global _stats_inflight_task
    cached_data = _stats_cache["data"]
    if cached_data and _time.time() < _stats_cache["expires"]:
        return cached_data
    loop = asyncio.get_running_loop()
    task = _stats_inflight_task
    if cached_data:
        if task is None or task.done() or task.get_loop() is not loop:
            _stats_inflight_task = track_background_task(
                _swr_full_stats_refresh(db_path, ttl_seconds, refresh)
            )
        return cached_data
    if task is not None and not task.done() and task.get_loop() is loop:
        return await task
    task = track_background_task(_do_full_stats_refresh(db_path, ttl_seconds, refresh))
    _stats_inflight_task = task
    try:
        return await task
    finally:
        if _stats_inflight_task is task:
            _stats_inflight_task = None


async def ai_status_source_counts(db_path: str) -> dict:
    conn = await connection.open_async(db_path)
    try:
        cursor = await conn.execute(
            "SELECT "
            "COALESCE(SUM(image_count), 0) AS catalog_images, "
            "COALESCE(SUM(CASE WHEN included = 1 THEN active_image_count ELSE 0 END), 0) AS active_images, "
            "COALESCE(SUM(CASE WHEN included = 0 THEN image_count ELSE 0 END), 0) AS removed_images, "
            "COALESCE(SUM(CASE WHEN included = 1 AND online = 0 THEN image_count ELSE 0 END), 0) AS offline_images "
            "FROM catalog_sources"
        )
        row = await cursor.fetchone()
        return {
            "catalog_images": int(row["catalog_images"] or 0),
            "active_images": int(row["active_images"] or 0),
            "removed_images": int(row["removed_images"] or 0),
            "offline_images": int(row["offline_images"] or 0),
        }
    finally:
        await connection.close_async(conn, db_path=db_path)



def patch_ai_status_direct_rating_counts(
    pair_delta: int,
    rated_image_delta: int,
    *,
    active_cap: int | None = None,
) -> bool:
    """Keep the AI status cache valid after direct Compare/Mosaic writes."""
    if not (
        _ai_status_counts_cache["data"]
        and _time.time() < _ai_status_counts_cache["expires"]
    ):
        invalidate_ai_status_counts_cache()
        return False

    ai_counts = _ai_status_counts_cache["data"]
    if active_cap is None:
        active_cap = int(ai_counts.get("total_images") or 0)
    for key in ("direct_comparison_rows", "ranking_signal_count"):
        _increment_cached_int(ai_counts, key, int(pair_delta or 0))
    _increment_cached_int(
        ai_counts,
        "rated_images",
        int(rated_image_delta or 0),
        cap=active_cap,
    )
    return True


async def ai_status_counts_cached(
    db_path: str,
    *,
    get_embedding_count,
    get_active_source_ids,
    active_embedding_config,
    count_embeddings_for_model,
    ttl_seconds: float = AI_STATUS_COUNTS_CACHE_TTL_SECONDS,
    stats_ttl_seconds: float = FULL_STATS_CACHE_TTL_SECONDS,
    refresh_full_stats=None,
    on_stats_refresh_scheduled=None,
) -> dict:
    now = _time.time()
    if _ai_status_counts_cache["data"] and now < _ai_status_counts_cache["expires"]:
        return dict(_ai_status_counts_cache["data"])

    if _stats_cache["data"]:
        stats = _stats_cache["data"]
        if full_stats_cache_expired(now):
            schedule_full_stats_refresh(
                db_path,
                ttl_seconds=stats_ttl_seconds,
                refresh=refresh_full_stats,
            )
            if on_stats_refresh_scheduled is not None:
                on_stats_refresh_scheduled()
        result = {
            "embedded": await get_embedding_count(),
            "total_images": int(stats.get("active_images") or stats.get("total_images") or 0),
            "rated_images": int(stats.get("rated_images") or 0),
            "direct_comparison_rows": int(stats.get("direct_comparison_rows") or 0),
            "ranking_signal_count": int(stats.get("ranking_signal_count") or stats.get("total_comparisons") or 0),
            "imported_ranking_without_history": int(stats.get("imported_ranking_without_history") or 0),
        }
        _ai_status_counts_cache["data"] = result
        _ai_status_counts_cache["expires"] = now + ttl_seconds
        return dict(result)

    source_counts = await ai_status_source_counts(db_path)
    active = int(source_counts["active_images"] or 0)
    active_source_ids = sorted(await get_active_source_ids()) if active > 0 else []
    embedded = (
        0
        if active <= 0
        else await get_embedding_count()
    )
    result = await ai_status_counts(
        db_path,
        source_counts=source_counts,
        active_source_ids=active_source_ids,
        embedded=embedded,
    )
    _ai_status_counts_cache["data"] = result
    _ai_status_counts_cache["expires"] = _time.time() + ttl_seconds
    return dict(result)


async def browser_original_summary(
    db_path: str,
    *,
    catalog_counts: dict,
    browser_extensions: tuple[str, ...],
    is_browser_displayable_original,
) -> dict:
    active_images = int(catalog_counts.get("active_images") or 0)
    all_catalog_images_active = (
        active_images > 0
        and active_images == int(catalog_counts.get("total_catalog_images") or 0)
        and int(catalog_counts.get("removed_images") or 0) == 0
    )
    placeholders = ",".join("?" for _ in browser_extensions)
    conn = await connection.open_async(db_path)
    try:
        if all_catalog_images_active:
            cursor = await conn.execute(
                "SELECT COUNT(*) AS count, COALESCE(SUM(i.file_size), 0) AS bytes FROM images i "
                f"WHERE {visible_image_condition()} "
                f"AND i.file_ext IN ({placeholders})",
                browser_extensions,
            )
        else:
            cursor = await conn.execute(
                "SELECT COUNT(*) AS count, COALESCE(SUM(i.file_size), 0) AS bytes FROM images i "
                "JOIN catalog_sources s ON s.id = i.source_id "
                "WHERE s.included = 1 "
                f"AND {visible_image_condition()} "
                f"AND i.file_ext IN ({placeholders})",
                browser_extensions,
            )
        row = await cursor.fetchone()
        total = int(row["count"] or 0)
        total_bytes = int(row["bytes"] or 0)

        for condition in ("i.file_ext IS NULL", "i.file_ext = ''"):
            if all_catalog_images_active:
                cursor = await conn.execute(
                    "SELECT i.filepath, i.file_size FROM images i "
                    "WHERE i.status IN ('kept', 'maybe') "
                    "AND i.missing_at IS NULL "
                    f"AND {condition}"
                )
            else:
                cursor = await conn.execute(
                    "SELECT i.filepath, i.file_size FROM images i "
                    "JOIN catalog_sources s ON s.id = i.source_id "
                    "WHERE s.included = 1 "
                    "AND i.status IN ('kept', 'maybe') "
                    "AND i.missing_at IS NULL "
                    f"AND {condition}"
                )
            rows = await cursor.fetchall()
            for row in rows:
                if is_browser_displayable_original(row["filepath"]):
                    total += 1
                    total_bytes += int(row["file_size"] or 0)
        return {"count": total, "bytes": total_bytes}
    finally:
        await connection.close_async(conn, db_path=db_path)


async def cache_ahead_counts(
    db_path: str,
    *,
    ahead: int,
    cache_root: str,
    size: str,
) -> dict:
    conn = await connection.open_async(db_path)
    try:
        cursor = await conn.execute(
            "WITH ahead_images AS ("
            "  SELECT i.id FROM images i "
            "  JOIN catalog_sources s ON s.id = i.source_id "
            "  WHERE s.included = 1 "
            "  AND i.status IN ('kept', 'maybe') "
            "  AND i.missing_at IS NULL "
            "  ORDER BY i.id LIMIT ?"
            ") "
            "SELECT COUNT(a.id) AS total, COUNT(c.image_id) AS cached "
            "FROM ahead_images a "
            "LEFT JOIN cache_entries c "
            "  ON c.image_id = a.id AND c.cache_root = ? AND c.size = ?",
            (ahead, cache_root, size),
        )
        row = await cursor.fetchone()
        return {
            "total": int(row["total"] or 0),
            "cached": int(row["cached"] or 0),
        }
    finally:
        await connection.close_async(conn, db_path=db_path)


async def ai_status_counts(
    db_path: str,
    *,
    source_counts: dict,
    active_source_ids: list[int],
    embedded: int,
) -> dict:
    active = int(source_counts.get("active_images") or 0)
    catalog_images = int(source_counts.get("catalog_images") or 0)
    all_catalog_images_active = (
        active == catalog_images
        and int(source_counts.get("removed_images") or 0) == 0
    )
    source_placeholders = ",".join("?" for _ in active_source_ids)

    conn = await connection.open_async(db_path)
    try:
        cursor = await conn.execute("SELECT COUNT(*) AS c FROM comparisons")
        total_catalog_comparison_rows = int((await cursor.fetchone())["c"] or 0)

        if active <= 0:
            direct_comparison_rows = 0
            image_comparison_count = 0
            propagated_update_count = 0
            rated_images = 0
            direct_image_history_count = 0
        elif all_catalog_images_active:
            direct_comparison_rows = total_catalog_comparison_rows
            cursor = await conn.execute(
                "SELECT "
                "COALESCE(SUM(COALESCE(comparisons, 0)), 0) AS image_comparison_count, "
                "COALESCE(SUM(COALESCE(propagated_updates, 0)), 0) AS propagated_update_count, "
                "SUM(CASE WHEN COALESCE(comparisons, 0) > 0 "
                "      OR COALESCE(propagated_updates, 0) > 0 "
                "      OR ABS(COALESCE(elo, 1200.0) - 1200.0) > 0.0001 "
                "    THEN 1 ELSE 0 END) AS rated_images "
                "FROM images INDEXED BY idx_images_status "
                f"WHERE {visible_image_condition('')}"
            )
            ranking_counts = await cursor.fetchone()
            image_comparison_count = int(ranking_counts["image_comparison_count"] or 0)
            propagated_update_count = int(ranking_counts["propagated_update_count"] or 0)
            rated_images = int(ranking_counts["rated_images"] or 0)
            direct_image_history_count = total_catalog_comparison_rows * 2
        else:
            cursor = await conn.execute(
                "WITH invalid_endpoints AS ("
                "  SELECT c.rowid AS comparison_rowid "
                "  FROM images i JOIN comparisons c INDEXED BY idx_comparisons_pair ON c.winner_id = i.id "
                f"  WHERE NOT (i.source_id IN ({source_placeholders}) "
                f"AND {visible_image_condition()}) "
                "  UNION ALL "
                "  SELECT c.rowid AS comparison_rowid "
                "  FROM images i JOIN comparisons c INDEXED BY idx_comparisons_loser ON c.loser_id = i.id "
                f"  WHERE NOT (i.source_id IN ({source_placeholders}) "
                f"AND {visible_image_condition()})"
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
            cursor = await conn.execute(
                "SELECT "
                "COALESCE(SUM(COALESCE(i.comparisons, 0)), 0) AS image_comparison_count, "
                "COALESCE(SUM(COALESCE(i.propagated_updates, 0)), 0) AS propagated_update_count, "
                "SUM(CASE WHEN COALESCE(i.comparisons, 0) > 0 "
                "      OR COALESCE(i.propagated_updates, 0) > 0 "
                "      OR ABS(COALESCE(i.elo, 1200.0) - 1200.0) > 0.0001 "
                "    THEN 1 ELSE 0 END) AS rated_images "
                "FROM images i INDEXED BY idx_images_source_missing_rating_signal "
                f"WHERE i.source_id IN ({source_placeholders}) "
                f"AND {visible_image_condition()}",
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
        return {
            "embedded": int(embedded or 0),
            "total_images": active,
            "rated_images": rated_images,
            "direct_comparison_rows": direct_comparison_rows,
            "ranking_signal_count": ranking_signal_count,
            "imported_ranking_without_history": imported_ranking_without_history,
        }
    finally:
        await connection.close_async(conn, db_path=db_path)
