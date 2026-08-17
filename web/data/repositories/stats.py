"""How many photographs, and how much has been done to them. Four questions.

    catalog_image_counts   the source sums: how many, active, removed, offline
    full_stats             every number the settings panel shows
    ai_status              the six the AI panel shows
    catalog_summary        the sources, and their stats

There were twenty-one functions here. Twelve were caches over numbers that cost
0.01 ms to 200 ms to read, plus the scheduler and inflight-task global that fed
them; `ai_status_counts` was a second implementation of five numbers `full_stats`
already returns; two more had no callers at all. What is left holds no state, so
nothing here can be stale, and each function answers one question once.
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
    """Every number the settings panel shows, in four queries and no branches.

    Each of those four used to be written up to three times — a zero arm, a
    no-join arm for catalogs with nothing removed, and the general arm — and
    the arms had drifted: the general ranking query carried
    `INDEXED BY idx_images_source_missing_rating_signal`, a hint chosen for a
    query shape it no longer had. Interleaved, min-of-six, on the 157,236-photo
    catalog:

        general as shipped (that hint)      206 ms
        general, no hint                    128 ms
        general, hinted idx_images_status    97 ms
        the no-join "fast" arm it dodged     90 ms

    So the branch bought 7 ms and the hint cost 109. One query, hinted like the
    arm that was already right, is both simpler and twice as fast as what
    shipped. The zero arms go too: SUM over no rows is COALESCEd to 0 already,
    which is why an empty catalog never needed its own code path.
    """

    source_counts = await catalog_image_counts(db_path)
    active = int(source_counts["active_images"] or 0)
    catalog_images = int(source_counts["total_catalog_images"] or 0)

    conn = await connection.open_async(db_path)
    try:
        cursor = await conn.execute(
            "SELECT i.flag, COUNT(*) AS count "
            "FROM images i JOIN catalog_sources s ON s.id = i.source_id "
            "WHERE s.included = 1 "
            f"AND {visible_image_condition()} "
            "AND i.flag IN ('picked', 'rejected') "
            "GROUP BY i.flag"
        )
        flag_counts = {row["flag"]: int(row["count"] or 0) for row in await cursor.fetchall()}

        cursor = await conn.execute("SELECT COUNT(*) as c FROM comparisons")
        total_catalog_comparison_rows = int((await cursor.fetchone())["c"] or 0)

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
        direct_comparison_rows = max(
            0, total_catalog_comparison_rows - int(invalid_comparisons["invalid_rows"] or 0)
        )
        direct_image_history_count = max(
            0,
            (total_catalog_comparison_rows * 2) - int(invalid_comparisons["invalid_endpoints"] or 0),
        )

        cursor = await conn.execute(
            "SELECT "
            "COALESCE(SUM(COALESCE(i.comparisons, 0)), 0) AS image_comparison_count, "
            "COALESCE(SUM(COALESCE(i.propagated_updates, 0)), 0) AS propagated_update_count, "
            "SUM(CASE WHEN COALESCE(i.comparisons, 0) > 0 "
            "      OR COALESCE(i.propagated_updates, 0) > 0 "
            "      OR ABS(COALESCE(i.elo, 1200.0) - 1200.0) > 0.0001 "
            "    THEN 1 ELSE 0 END) AS rated_images "
            "FROM images i INDEXED BY idx_images_status "
            "JOIN catalog_sources s ON s.id = i.source_id "
            "WHERE s.included = 1 "
            f"AND {visible_image_condition()}"
        )
        ranking_counts = await cursor.fetchone()

        image_comparison_count = int(ranking_counts["image_comparison_count"] or 0)
        propagated_update_count = int(ranking_counts["propagated_update_count"] or 0)
        imported_ranking_without_history = max(0, image_comparison_count - direct_image_history_count)
        ranking_signal_count = direct_comparison_rows + imported_ranking_without_history + propagated_update_count

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


async def ai_status(db_path: str, *, get_embedding_count) -> dict:
    """The AI panel's counts: five numbers off `full_stats`, plus how many are embedded.

    There used to be a second implementation of those five — `ai_status_counts`,
    130 lines with the same three-way branching, spelling "the active sources"
    as `i.source_id IN (...)` where `full_stats` spells it `s.included = 1`. On
    the 157,236-photograph catalog both return
    `{cmp: 415097, prop: 2263, rated: 29107}`, because they are the same set
    written twice.

    The old code contained its own proof: whenever the stats cache was warm,
    `ai_status_counts_cached` skipped its queries and built this exact dict out
    of the `full_stats` payload. It only ever ran its own SQL on a cold cache —
    so the duplicate existed to answer a question the cache usually answered.
    Delete the cache and the reason for the duplicate goes with it.
    """

    stats = await full_stats(db_path)
    return {
        "embedded": int(await get_embedding_count() or 0) if int(stats["active_images"] or 0) > 0 else 0,
        "total_images": int(stats["active_images"] or stats["total_images"] or 0),
        "rated_images": int(stats["rated_images"] or 0),
        "direct_comparison_rows": int(stats["direct_comparison_rows"] or 0),
        "ranking_signal_count": int(stats["ranking_signal_count"] or stats["total_comparisons"] or 0),
        "imported_ranking_without_history": int(stats["imported_ranking_without_history"] or 0),
    }


async def catalog_summary(db_path: str) -> dict:
    """The sources and their counts — what every catalog surface displays.

    There were two of these. The "light" one existed because the full one was
    thought to be expensive, and both returned the same shape over the same
    three-row table. `refresh_source_online_states` used to be threaded in as a
    parameter and run on every read, which is why the answer had to be cached:
    a read that writes cannot be repeated freely. Startup and rescan refresh
    online state; this only reports it.
    """

    return {
        "sources": [dict(row) for row in await get_catalog_sources(db_path)],
        "stats": await full_stats(db_path),
    }
