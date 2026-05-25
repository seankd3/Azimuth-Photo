"""Filter option facet queries for the Library filter bar."""

import asyncio
from collections import Counter
import time as _time

from data import connection

_filter_options_cache = {"data": None, "expires": 0}
_filter_options_refreshing = False
FILTER_OPTIONS_CACHE_TTL_SECONDS = 300.0


def empty_filter_options() -> dict:
    return {
        "years": [],
        "file_types": [],
        "undated": 0,
        "cameras": [],
        "lenses": [],
        "people": [],
    }


def _filter_rows(db_path: str, sql: str, params=()):
    conn = connection.open_sync(db_path)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        connection.close_sync(conn, db_path=db_path)


def invalidate_filter_options_cache() -> None:
    # Keep the last payload available for stale-while-refresh reads. Metadata
    # scanning can invalidate this often, and a cold facet rebuild is visible.
    _filter_options_cache["expires"] = 0


def clear_filter_options_cache() -> None:
    global _filter_options_refreshing
    _filter_options_cache["data"] = None
    _filter_options_cache["expires"] = 0
    _filter_options_refreshing = False


async def filter_options_cached(
    db_path: str,
    *,
    get_catalog_image_counts,
    get_active_source_id_set,
    ttl_seconds: float = FILTER_OPTIONS_CACHE_TTL_SECONDS,
) -> dict:
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
                    await load_filter_options_uncached(
                        db_path,
                        get_catalog_image_counts=get_catalog_image_counts,
                        get_active_source_id_set=get_active_source_id_set,
                        ttl_seconds=ttl_seconds,
                    )
                finally:
                    _filter_options_refreshing = False

            asyncio.create_task(_refresh_filter_options())
        return data
    return await load_filter_options_uncached(
        db_path,
        get_catalog_image_counts=get_catalog_image_counts,
        get_active_source_id_set=get_active_source_id_set,
        ttl_seconds=ttl_seconds,
    )


async def load_filter_options_uncached(
    db_path: str,
    *,
    get_catalog_image_counts,
    get_active_source_id_set,
    ttl_seconds: float = FILTER_OPTIONS_CACHE_TTL_SECONDS,
) -> dict:
    if _filter_options_cache["data"] and _time.time() < _filter_options_cache["expires"]:
        return _filter_options_cache["data"]
    counts = await get_catalog_image_counts()
    active_images = int(counts.get("active_images") or 0)
    if active_images <= 0:
        result = empty_filter_options()
        _filter_options_cache["data"] = result
        _filter_options_cache["expires"] = _time.time() + ttl_seconds
        return result

    all_catalog_images_active = (
        active_images == int(counts.get("total_catalog_images") or 0)
        and int(counts.get("removed_images") or 0) == 0
    )
    all_sources_available = int(counts.get("removed_images") or 0) == 0
    active_source_ids = ()
    if not (all_catalog_images_active or all_sources_available):
        active_source_ids = sorted(await get_active_source_id_set())
    result = await filter_options(
        db_path,
        catalog_counts=counts,
        active_source_ids=active_source_ids,
    )
    _filter_options_cache["data"] = result
    _filter_options_cache["expires"] = _time.time() + ttl_seconds
    return result


async def filter_options(
    db_path: str,
    *,
    catalog_counts: dict,
    active_source_ids: list[int] | tuple[int, ...] = (),
) -> dict:
    active_images = int(catalog_counts.get("active_images") or 0)
    if active_images <= 0:
        return empty_filter_options()

    all_catalog_images_active = (
        active_images == int(catalog_counts.get("total_catalog_images") or 0)
        and int(catalog_counts.get("removed_images") or 0) == 0
    )
    all_sources_available = int(catalog_counts.get("removed_images") or 0) == 0
    if all_catalog_images_active or all_sources_available:
        image_source_clause = ""
        bare_source_clause = ""
        source_params = ()
    else:
        if not active_source_ids:
            return empty_filter_options()
        source_placeholders = ",".join("?" for _ in active_source_ids)
        image_source_clause = f"i.source_id IN ({source_placeholders}) AND "
        bare_source_clause = f"source_id IN ({source_placeholders}) AND "
        source_params = tuple(int(source_id) for source_id in active_source_ids)

    base_where = f"{bare_source_clause}missing_at IS NULL"
    (
        year_rows,
        undated_rows,
        file_type_rows,
        camera_rows,
        lens_rows,
        people_rows,
    ) = await asyncio.gather(
        asyncio.to_thread(
            _filter_rows,
            db_path,
            "SELECT SUBSTR(date_taken, 1, 4) AS value, COUNT(*) AS count "
            f"FROM images WHERE {base_where} "
            "AND date_taken IS NOT NULL AND LENGTH(date_taken) >= 4 "
            "GROUP BY value",
            source_params,
        ),
        asyncio.to_thread(
            _filter_rows,
            db_path,
            "SELECT COUNT(*) AS count "
            f"FROM images WHERE {base_where} "
            "AND (date_taken IS NULL OR LENGTH(date_taken) < 4)",
            source_params,
        ),
        asyncio.to_thread(
            _filter_rows,
            db_path,
            "SELECT file_ext AS value, COUNT(*) AS count "
            f"FROM images WHERE {base_where} "
            "AND file_ext IS NOT NULL AND file_ext != '' "
            "GROUP BY value",
            source_params,
        ),
        asyncio.to_thread(
            _filter_rows,
            db_path,
            "SELECT TRIM(COALESCE(camera_make, '') || ' ' || COALESCE(camera_model, '')) AS value, "
            "COUNT(*) AS count "
            f"FROM images WHERE {base_where} "
            "AND (camera_make IS NOT NULL OR camera_model IS NOT NULL) "
            "GROUP BY value HAVING value != ''",
            source_params,
        ),
        asyncio.to_thread(
            _filter_rows,
            db_path,
            "SELECT lens AS value, COUNT(*) AS count "
            f"FROM images WHERE {base_where} "
            "AND lens IS NOT NULL AND lens != '' "
            "GROUP BY value",
            source_params,
        ),
        asyncio.to_thread(
            _filter_rows,
            db_path,
            "SELECT p.id, p.name, p.status, COUNT(DISTINCT pim.image_id) AS count "
            "FROM people p "
            "JOIN person_image_membership pim ON pim.person_id = p.id "
            "JOIN images i ON i.id = pim.image_id "
            "JOIN catalog_sources s ON s.id = i.source_id "
            "WHERE p.status != 'ignored' "
            "AND p.merged_into_person_id IS NULL "
            "AND s.included = 1 "
            f"AND {image_source_clause}"
            "i.status IN ('kept', 'maybe') "
            "AND i.missing_at IS NULL "
            "GROUP BY p.id "
            "HAVING count > 0 "
            "ORDER BY count DESC, LOWER(COALESCE(NULLIF(p.name, ''), 'Person ' || p.id)) ASC, p.id ASC "
            "LIMIT 200",
            source_params,
        ),
    )

    file_type_counts: Counter[str] = Counter()
    for row in file_type_rows:
        ext = str(row["value"] or "").strip().lower().lstrip(".")
        if ext:
            file_type_counts[ext] += int(row["count"] or 0)

    return {
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
        "people": [
            {
                "id": int(row["id"]),
                "name": str(row["name"] or ""),
                "label": str(row["name"] or f"Person {row['id']}"),
                "status": str(row["status"] or "unknown"),
                "count": int(row["count"] or 0),
            }
            for row in people_rows
        ],
    }
