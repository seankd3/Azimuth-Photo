"""Filter option facet queries for the Library filter bar."""

import asyncio
from collections import Counter
import logging
import time as _time

from core.background import track_background_task
from data import connection
from data.repositories.common import stage_temp_ids_sync
from data.repositories.rankings import ranking_filter_parts

log = logging.getLogger(__name__)

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


def _filter_row_groups(db_path: str, base_where: str, params=(), staged_ids=None):
    """Read all image facets from one materialized eligible-image set.

    Running six full-library facet scans concurrently makes cold filter opening
    compete with itself on a real archive. Materializing the already-filtered
    image columns once keeps the answer exact while leaving the disk free for
    browsing and preview work.
    """
    facet_sql = (
        "WITH filtered AS MATERIALIZED ("
        "SELECT i.id, i.date_taken, i.file_ext, i.camera_make, i.camera_model, i.lens "
        "FROM images i JOIN catalog_sources s ON s.id = i.source_id "
        f"WHERE {base_where}"
        ") "
        "SELECT kind, value, count FROM ("
        "SELECT 'year' AS kind, SUBSTR(date_taken, 1, 4) AS value, COUNT(*) AS count "
        "FROM filtered WHERE date_taken IS NOT NULL AND LENGTH(date_taken) >= 4 GROUP BY value "
        "UNION ALL "
        "SELECT 'undated', '', COUNT(*) FROM filtered "
        "WHERE date_taken IS NULL OR LENGTH(date_taken) < 4 "
        "UNION ALL "
        "SELECT 'file_type', file_ext, COUNT(*) FROM filtered "
        "WHERE file_ext IS NOT NULL AND file_ext != '' GROUP BY file_ext "
        "UNION ALL "
        "SELECT 'camera', TRIM(COALESCE(camera_make, '') || ' ' || COALESCE(camera_model, '')), COUNT(*) "
        "FROM filtered WHERE camera_make IS NOT NULL OR camera_model IS NOT NULL "
        "GROUP BY 2 HAVING 2 != '' "
        "UNION ALL "
        "SELECT 'lens', lens, COUNT(*) FROM filtered WHERE lens IS NOT NULL AND lens != '' GROUP BY lens"
        ")"
    )
    people_sql = (
        "WITH filtered AS MATERIALIZED ("
        "SELECT i.id FROM images i JOIN catalog_sources s ON s.id = i.source_id "
        f"WHERE {base_where}"
        ") "
        "SELECT p.id, p.name, p.status, COUNT(DISTINCT pim.image_id) AS count "
        "FROM people p JOIN person_image_membership pim ON pim.person_id = p.id "
        "JOIN filtered ON filtered.id = pim.image_id "
        "WHERE p.status != 'ignored' AND p.merged_into_person_id IS NULL "
        "GROUP BY p.id HAVING count > 0 "
        "ORDER BY count DESC, LOWER(COALESCE(NULLIF(p.name, ''), 'Person ' || p.id)) ASC, p.id ASC LIMIT 200"
    )
    conn = connection.open_sync(db_path)
    try:
        if staged_ids is not None:
            stage_temp_ids_sync(conn, "temp_filter_scope_ids", staged_ids)
        return conn.execute(facet_sql, params).fetchall(), conn.execute(people_sql, params).fetchall()
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
                except Exception:
                    log.exception("filter options background refresh failed")
                finally:
                    _filter_options_refreshing = False

            track_background_task(_refresh_filter_options())
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
    caption_model_key: str = "",
    id_filter: set[int] | None = None,
    collection_id: int = 0,
    text_query: str = "",
    exclude_collapsed_stack_members: bool = False,
    exclude_sources=(),
) -> dict:
    active_images = int(catalog_counts.get("active_images") or 0)
    if active_images <= 0:
        return empty_filter_options()

    all_catalog_images_active = (
        active_images == int(catalog_counts.get("total_catalog_images") or 0)
        and int(catalog_counts.get("removed_images") or 0) == 0
    )
    all_sources_available = int(catalog_counts.get("removed_images") or 0) == 0
    if not (all_catalog_images_active or all_sources_available) and not active_source_ids:
        return empty_filter_options()
    conditions, params = ranking_filter_parts(
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
        caption_model_key=caption_model_key,
        collection_id=collection_id,
        text_query=text_query,
        exclude_collapsed_stack_members=exclude_collapsed_stack_members,
        exclude_sources=exclude_sources,
    )
    if not (all_catalog_images_active or all_sources_available):
        source_placeholders = ",".join("?" for _ in active_source_ids)
        conditions.append(f"i.source_id IN ({source_placeholders})")
        params.extend(int(source_id) for source_id in active_source_ids)

    staged_id_filter = None
    if id_filter is not None:
        ids = [int(image_id) for image_id in id_filter]
        if not ids:
            return empty_filter_options()
        staged_id_filter = ids
        conditions.append(
            "EXISTS (SELECT 1 FROM temp_filter_scope_ids scope_ids "
            "WHERE scope_ids.image_id = i.id)"
        )
    base_where = " AND ".join(conditions)
    facet_rows, people_rows = await asyncio.to_thread(
        _filter_row_groups,
        db_path,
        base_where,
        params,
        staged_id_filter,
    )
    facets: dict[str, list] = {"year": [], "file_type": [], "camera": [], "lens": []}
    undated = 0
    for row in facet_rows:
        kind = str(row["kind"])
        if kind == "undated":
            undated = int(row["count"] or 0)
        else:
            facets.setdefault(kind, []).append(row)
    year_rows = facets["year"]
    file_type_rows = facets["file_type"]
    camera_rows = facets["camera"]
    lens_rows = facets["lens"]


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
        "undated": undated,
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
