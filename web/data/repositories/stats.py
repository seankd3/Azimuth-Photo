"""Aggregate counts for the catalog and AI status surfaces. Each one is a query.

Measured on the 157,236-photograph catalog: the source sums are 0.01 ms over a
three-row table, and `full_stats` — the slowest thing here — is 27 ms. Around
those sat three TTL dictionaries, a stale-while-revalidate scheduler, an
inflight-task global, and twelve functions to expire and patch them, none of
which the numbers ever justified. The desktop already holds these for 15-60 s
of its own accord, so the second cache behind it could only ever be wrong.
"""

import logging

from data import connection
from data.repositories.catalog import (
    active_image_condition,
    get_catalog_sources,
    visible_image_condition,
)

log = logging.getLogger(__name__)


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



async def ai_status(db_path: str, *, get_embedding_count, get_active_source_ids) -> dict:
    """The AI panel's counts: gather what `ai_status_counts` needs, then ask it.

    An empty catalog is not a special case being handled — with no active
    images there are no sources to list and nothing to have embedded, so both
    reads are skipped because their answer is already known, not to protect a
    query from a zero.
    """

    source_counts = await ai_status_source_counts(db_path)
    active = int(source_counts["active_images"] or 0)
    return await ai_status_counts(
        db_path,
        source_counts=source_counts,
        active_source_ids=sorted(await get_active_source_ids()) if active > 0 else [],
        embedded=await get_embedding_count() if active > 0 else 0,
    )


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


async def catalog_summary(db_path: str) -> dict:
    """The sources and their counts — what every catalog surface displays.

    There were two of these. The "light" one existed because the full one was
    thought to be expensive; it is 27 ms, and both returned the same shape over
    the same three-row table. `refresh_source_online_states` used to be threaded
    in as a parameter and run on every read, which is why the answer had to be
    cached: a read that writes cannot be repeated freely. Startup and rescan
    refresh online state; this only reports it.
    """

    return {
        "sources": [dict(row) for row in await get_catalog_sources(db_path)],
        "stats": await full_stats(db_path),
    }
