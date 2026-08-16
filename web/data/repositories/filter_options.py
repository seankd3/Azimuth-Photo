"""Filter option facet queries for the Library filter bar."""

import logging
import time as _time


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
    # A CTE cannot be shared across statements, so stage the filtered set in a
    # temp table once and let both the facet and people queries read it.
    stage_sql = (
        "CREATE TEMP TABLE temp_filter_facet_rows AS "
        "SELECT i.id, i.date_taken, i.file_ext, i.camera_make, i.camera_model, i.lens "
        "FROM images i JOIN catalog_sources s ON s.id = i.source_id "
        f"WHERE {base_where}"
    )
    facet_sql = (
        "SELECT kind, value, count FROM ("
        "SELECT 'year' AS kind, SUBSTR(date_taken, 1, 4) AS value, COUNT(*) AS count "
        "FROM temp_filter_facet_rows WHERE date_taken IS NOT NULL AND LENGTH(date_taken) >= 4 GROUP BY value "
        "UNION ALL "
        "SELECT 'undated', '', COUNT(*) FROM temp_filter_facet_rows "
        "WHERE date_taken IS NULL OR LENGTH(date_taken) < 4 "
        "UNION ALL "
        "SELECT 'file_type', file_ext, COUNT(*) FROM temp_filter_facet_rows "
        "WHERE file_ext IS NOT NULL AND file_ext != '' GROUP BY file_ext "
        "UNION ALL "
        "SELECT 'camera', TRIM(COALESCE(camera_make, '') || ' ' || COALESCE(camera_model, '')) AS value, COUNT(*) "
        "FROM temp_filter_facet_rows WHERE camera_make IS NOT NULL OR camera_model IS NOT NULL "
        "GROUP BY value HAVING value != '' "
        "UNION ALL "
        "SELECT 'lens', lens, COUNT(*) FROM temp_filter_facet_rows WHERE lens IS NOT NULL AND lens != '' GROUP BY lens"
        ")"
    )
    people_sql = (
        "SELECT p.id, p.name, p.status, COUNT(DISTINCT pim.image_id) AS count "
        "FROM people p JOIN person_image_membership pim ON pim.person_id = p.id "
        "JOIN temp_filter_facet_rows filtered ON filtered.id = pim.image_id "
        "WHERE p.status != 'ignored' AND p.merged_into_person_id IS NULL "
        "GROUP BY p.id HAVING count > 0 "
        "ORDER BY count DESC, LOWER(COALESCE(NULLIF(p.name, ''), 'Person ' || p.id)) ASC, p.id ASC LIMIT 200"
    )
    conn = connection.open_sync(db_path)
    try:
        if staged_ids is not None:
            stage_temp_ids_sync(conn, "temp_filter_scope_ids", staged_ids)
        conn.execute(stage_sql, params)
        conn.execute("CREATE INDEX temp_filter_facet_rows_id ON temp_filter_facet_rows(id)")
        return conn.execute(facet_sql).fetchall(), conn.execute(people_sql).fetchall()
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



# The facet queries stood here. They are answered by library.facets() now --
# one grouped query per facet, each able to use an index, against a cached
# payload rebuilt in the background. What survives is the cache handle the
# invalidation hooks still call, which costs six lines instead of two
# hundred and five.