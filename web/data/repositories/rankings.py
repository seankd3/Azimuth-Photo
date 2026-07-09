"""Ranking query SQL fragments and constants."""

import asyncio
from datetime import datetime
import time as _time

from data import connection
from data.repositories.common import chunked as _chunked

RANKING_SORTS = {
    "elo": "i.elo DESC",
    "elo_asc": "i.elo ASC",
    "comparisons": "i.comparisons DESC",
    "least_compared": "i.comparisons ASC",
    "filename": "i.filename ASC",
    "filename_asc": "i.filename ASC",
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
    "camera_asc": "i.camera_make IS NULL ASC, i.camera_make ASC, i.camera_model ASC, i.id ASC",
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
    "filename_asc": "idx_images_active_filename",
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
    "camera_asc": "idx_images_active_camera_sort_asc",
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
    "filename_asc",
    "filename_desc",
    "date_taken",
    "date_taken_asc",
    "date_modified",
    "date_modified_asc",
    "camera",
    "camera_asc",
    "camera_desc",
    "resolution",
    "resolution_asc",
}
VISIBLE_CACHE_FIRST_SORTS = {
    "comparisons",
    "least_compared",
    "filename",
    "filename_asc",
    "filename_desc",
    "date_taken",
    "date_taken_asc",
    "date_modified",
    "date_modified_asc",
    "file_size",
    "file_size_asc",
    "camera",
    "camera_asc",
    "camera_desc",
    "resolution",
    "resolution_asc",
}

STAR_THRESHOLDS = {5: 1500, 4: 1350, 3: 1250, 2: 1150, 1: 0}
RANKING_COUNT_CACHE_TTL_SECONDS = 2.0
FACET_CACHE_TTL_SECONDS = 30.0
RANKING_VISIBLE_ID_FILTER_LIMIT = 5000
RANKING_CACHE_FIRST_VISIBLE_LIMIT = 12000
RANKABLE_IMAGE_IDS_TTL_SECONDS = 5.0
IMAGE_EXTENSION_SEARCH_TERMS = {
    "avif", "bmp", "gif", "jpeg", "jpg", "png", "webp",
    "arw", "cr2", "cr3", "dng", "nef", "orf", "raf", "rw2",
}
# file_type accepts group aliases as well as literal extensions.
FILE_TYPE_GROUPS = {
    "raw": ("arw", "cr2", "cr3", "dng", "nef", "orf", "raf", "rw2"),
    "jpg": ("jpg", "jpeg"),
    "jpeg": ("jpg", "jpeg"),
    "tif": ("tif", "tiff"),
    "tiff": ("tif", "tiff"),
}
IMAGE_ROW_SELECT = (
    "i.id, i.source_id, i.filename, i.filepath, i.elo, i.comparisons, "
    "i.propagated_updates, "
    "i.status, i.flag, i.aspect_ratio, "
    "i.date_taken, i.date_source, i.camera_make, i.camera_model, i.lens, i.file_ext, i.file_size, "
    "i.file_modified_at, i.width, i.height, i.latitude, i.longitude, i.created_at"
)

_date_groups_cache: dict[tuple, dict] = {}
_date_groups_refreshing: set[tuple] = set()
_map_markers_cache: dict[tuple, dict] = {}
_ranking_count_cache: dict[tuple, dict] = {}
_rankable_image_ids_cache = {"ids": frozenset(), "expires": 0}


def escape_like(value: str) -> str:
    return (
        value
        .replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
    )


def normalized_folder_values(folder) -> list[str]:
    if not folder:
        return []
    if isinstance(folder, (list, tuple)):
        values = folder
    else:
        values = [folder]
    normalized = []
    seen = set()
    for value in values:
        clean = str(value or "").strip().rstrip("/")
        if not clean or clean in seen:
            continue
        seen.add(clean)
        normalized.append(clean)
    return normalized


def folder_cache_value(folder):
    values = normalized_folder_values(folder)
    if not values:
        return ""
    if len(values) == 1:
        return values[0]
    return tuple(values)


def folder_filter_sql(folder) -> tuple[str, list] | None:
    values = normalized_folder_values(folder)
    if not values:
        return None
    parts = []
    params = []
    for value in values:
        parts.append("i.filepath LIKE ? ESCAPE '\\'")
        if value.startswith("/"):
            params.append(f"{escape_like(value)}/%")
        else:
            params.append(f"%/{escape_like(value)}/%")
    if len(parts) == 1:
        return parts[0], params
    return f"({' OR '.join(parts)})", params


def ranking_count_cache_key(
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
    id_filter: set | None = None,
    visible_thumb_size: str = "",
    cache_root: str = "",
    text_query: str = "",
    exclude_collapsed_stack_members: bool = False,
):
    if id_filter is not None:
        return None
    return (
        orientation or "",
        compared or "",
        int(min_stars or 0),
        folder_cache_value(folder),
        flag or "",
        date_taken or "",
        file_type or "",
        camera or "",
        lens or "",
        tag or "",
        visible_thumb_size or "",
        cache_root or "",
        text_query or "",
        bool(exclude_collapsed_stack_members),
    )


def facet_cache_key(
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
    visible_thumb_size: str = "",
    cache_root: str = "",
    id_filter: set | None = None,
    text_query: str = "",
    exclude_collapsed_stack_members: bool = False,
) -> tuple | None:
    if id_filter is not None or text_query:
        return None
    return (
        orientation or "",
        compared or "",
        int(min_stars or 0),
        folder_cache_value(folder),
        flag or "",
        date_taken or "",
        file_type or "",
        camera or "",
        lens or "",
        tag or "",
        visible_thumb_size or "",
        cache_root or "",
        bool(exclude_collapsed_stack_members),
    )


def invalidate_ranking_count_cache() -> None:
    _ranking_count_cache.clear()


def invalidate_facet_caches() -> None:
    _date_groups_cache.clear()
    _date_groups_refreshing.clear()
    _map_markers_cache.clear()


def cache_scope_matches(cache_root: str, size: str, target_root: str | None, target_size: str | None) -> bool:
    if target_root is not None and cache_root != target_root:
        return False
    if target_size is not None and size != target_size:
        return False
    return True


def invalidate_visible_facet_caches(cache_root: str | None = None, size: str | None = None) -> None:
    if cache_root is None and size is None:
        invalidate_facet_caches()
        return

    for cache in (_date_groups_cache, _map_markers_cache):
        for key in list(cache.keys()):
            key_size = key[10]
            key_root = key[11]
            if key_size and key_root and cache_scope_matches(key_root, key_size, cache_root, size):
                cache.pop(key, None)
                _date_groups_refreshing.discard(key)


def invalidate_rating_facet_caches() -> None:
    for cache in (_date_groups_cache, _map_markers_cache):
        for key in list(cache.keys()):
            _orientation, compared, min_stars, *_rest = key
            if compared or int(min_stars or 0) > 0:
                cache.pop(key, None)
                _date_groups_refreshing.discard(key)


def invalidate_visible_cache_dependent_counts(cache_root: str | None = None, size: str | None = None) -> None:
    for key in list(_ranking_count_cache.keys()):
        key_size = key[10]
        key_root = key[11]
        if key_size and key_root and cache_scope_matches(key_root, key_size, cache_root, size):
            _ranking_count_cache.pop(key, None)


def invalidate_rating_ranking_count_cache() -> None:
    for key in list(_ranking_count_cache.keys()):
        _orientation, compared, min_stars, *_rest = key
        if compared or int(min_stars or 0) > 0:
            _ranking_count_cache.pop(key, None)


def ranking_filter_parts(
    orientation: str = "", compared: str = "", min_stars: int = 0,
    folder: str = "", flag: str = "", date_taken: str = "",
    file_type: str = "", camera: str = "", lens: str = "",
    tag: str = "", caption_model_key: str = "",
    visible_thumb_size: str = "", cache_root: str = "",
    text_query: str = "",
    include_source: bool = True,
    exclude_collapsed_stack_members: bool = False,
) -> tuple[list[str], list]:
    conditions = [
        "i.status IN ('kept', 'maybe')",
        "i.missing_at IS NULL",
    ]
    if include_source:
        conditions[:0] = ["s.included = 1"]
    params = []

    if exclude_collapsed_stack_members:
        conditions.append(
            "i.id NOT IN ("
            "SELECT sm.image_id FROM stack_members sm "
            "JOIN stacks s ON s.id = sm.stack_id "
            "WHERE sm.image_id <> s.representative_image_id"
            ")"
        )

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
    elif compared == "direct_uncompared":
        conditions.append("COALESCE(i.comparisons, 0) = 0")
    elif compared == "confident":
        conditions.append("i.comparisons >= 10")

    if min_stars > 0 and min_stars in STAR_THRESHOLDS:
        conditions.append("i.elo >= ?")
        params.append(STAR_THRESHOLDS[min_stars])

    folder_filter = folder_filter_sql(folder)
    if folder_filter is not None:
        condition, folder_params = folder_filter
        conditions.append(condition)
        params.extend(folder_params)

    if flag in ("picked", "unflagged", "rejected"):
        conditions.append("i.flag = ?")
        params.append(flag)

    date_range = date_taken_filter_range(date_taken)
    if date_taken == "undated":
        conditions.append("i.date_taken IS NULL")
    elif date_range is not None:
        start, end = date_range
        conditions.append("i.date_taken >= ? AND i.date_taken < ?")
        params.extend([start, end])

    if file_type:
        normalized_type = file_type.lower().lstrip(".")
        group_exts = FILE_TYPE_GROUPS.get(normalized_type, (normalized_type,))
        variants = [variant for ext in group_exts for variant in (ext, f".{ext}")]
        conditions.append("i.file_ext IS NOT NULL")
        conditions.append("i.file_ext != ''")
        conditions.append(f"LOWER(i.file_ext) IN ({','.join('?' for _ in variants)})")
        params.extend(variants)

    if camera:
        conditions.append(
            "TRIM(COALESCE(i.camera_make, '') || ' ' || COALESCE(i.camera_model, '')) = ?"
        )
        params.append(camera)

    if lens:
        conditions.append("i.lens = ?")
        params.append(lens)

    if tag:
        conditions.append(
            "EXISTS ("
            "SELECT 1 FROM image_tags it "
            "WHERE it.image_id = i.id AND it.model_key = ? AND it.tag = ?"
            ")"
        )
        params.extend([caption_model_key, tag.strip().lower()])

    if text_query:
        # Each whitespace token must match at least one metadata field, so
        # multi-word queries narrow results instead of requiring one field to
        # contain the entire phrase.
        for token in text_query.strip().split():
            escaped = escape_like(token)
            if not escaped:
                continue
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


def date_taken_filter_range(date_taken: str) -> tuple[str, str] | None:
    value = (date_taken or "").strip()
    if value.isdigit() and len(value) == 4:
        year = int(value)
        return f"{year:04d}-01-01 00:00:00", f"{year + 1:04d}-01-01 00:00:00"
    try:
        parsed = datetime.strptime(value, "%Y-%m")
    except ValueError:
        return None
    if parsed.month == 12:
        next_year = parsed.year + 1
        next_month = 1
    else:
        next_year = parsed.year
        next_month = parsed.month + 1
    return (
        f"{parsed.year:04d}-{parsed.month:02d}-01 00:00:00",
        f"{next_year:04d}-{next_month:02d}-01 00:00:00",
    )


def ranking_index_for_query(
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


def ranking_image_source(
    sort: str,
    *,
    orientation: str = "",
    id_filter: set | None,
    text_query: str,
) -> str:
    index_name = ranking_index_for_query(
        sort,
        orientation=orientation,
        id_filter=id_filter,
        text_query=text_query,
    )
    if not index_name:
        return "images i"
    return f"images i INDEXED BY {index_name}"


def ranking_count_image_source(
    *,
    file_type: str = "",
    id_filter: set | None,
    text_query: str = "",
) -> str:
    if file_type and id_filter is None and not text_query:
        return "images i INDEXED BY idx_images_missing_lower_file_ext_source"
    return "images i"


async def rankings(
    db_path: str,
    *,
    catalog_counts: dict,
    limit: int = 100,
    offset: int = 0,
    sort: str = "elo",
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
    id_filter: set | None = None,
    visible_thumb_size: str = "",
    cache_root: str = "",
    text_query: str = "",
    use_cache_first_visible: bool = False,
    exclude_collapsed_stack_members: bool = False,
):
    active_images = int(catalog_counts.get("active_images") or 0)
    if active_images <= 0:
        return []

    all_catalog_images_active = (
        active_images > 0
        and active_images == int(catalog_counts.get("total_catalog_images") or 0)
        and int(catalog_counts.get("removed_images") or 0) == 0
    )
    all_sources_available = int(catalog_counts.get("removed_images") or 0) == 0
    order = RANKING_SORTS.get(sort, "elo DESC")
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
        visible_thumb_size=visible_thumb_size,
        cache_root=cache_root,
        text_query=text_query,
        include_source=not all_catalog_images_active,
        exclude_collapsed_stack_members=exclude_collapsed_stack_members,
    )

    if id_filter is not None:
        if not id_filter:
            return []
        placeholders = ",".join("?" * len(id_filter))
        conditions.append(f"i.id IN ({placeholders})")
        params.extend(id_filter)

    conn = await connection.open_async(db_path)
    try:
        if (
            visible_thumb_size
            and cache_root
            and id_filter is None
            and all_sources_available
            and use_cache_first_visible
        ):
            conditions_no_source, params_no_source = ranking_filter_parts(
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
                text_query=text_query,
                include_source=False,
                exclude_collapsed_stack_members=exclude_collapsed_stack_members,
            )
            cursor = await conn.execute(
                f"SELECT {IMAGE_ROW_SELECT} "
                "FROM cache_entries c INDEXED BY sqlite_autoindex_cache_entries_1 "
                "CROSS JOIN images i ON i.id = c.image_id "
                f"WHERE c.cache_root = ? AND c.size = ? AND {' AND '.join(conditions_no_source)} "
                f"ORDER BY {order} LIMIT ? OFFSET ?",
                [cache_root, visible_thumb_size] + params_no_source + [limit, offset],
            )
            return await cursor.fetchall()

        params.extend([limit, offset])
        image_source = ranking_image_source(
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
        cursor = await conn.execute(
            f"SELECT {IMAGE_ROW_SELECT} "
            f"FROM {image_source} {source_join}"
            f"WHERE {' AND '.join(conditions)} ORDER BY {order} LIMIT ? OFFSET ?",
            params,
        )
        return await cursor.fetchall()
    finally:
        await connection.close_async(conn, db_path=db_path)


async def rankings_cached(
    db_path: str,
    *,
    get_catalog_image_counts,
    cache_entry_count,
    get_cached_image_id_set,
    limit: int = 100,
    offset: int = 0,
    sort: str = "elo",
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
    id_filter: set | None = None,
    visible_thumb_size: str = "",
    cache_root: str = "",
    text_query: str = "",
    exclude_collapsed_stack_members: bool = False,
):
    catalog_counts = await get_catalog_image_counts()
    active_images = int(catalog_counts.get("active_images") or 0)
    if active_images <= 0:
        return []
    all_sources_available_for_visible = (
        int(catalog_counts.get("removed_images") or 0) == 0
    )
    use_cache_first_visible = (
        visible_thumb_size
        and cache_root
        and id_filter is None
        and (sort in VISIBLE_CACHE_FIRST_SORTS or bool(text_query))
        and all_sources_available_for_visible
        and await cache_entry_count(visible_thumb_size, cache_root) <= RANKING_CACHE_FIRST_VISIBLE_LIMIT
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

    return await rankings(
        db_path,
        catalog_counts=catalog_counts,
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
        caption_model_key=caption_model_key,
        id_filter=id_filter,
        visible_thumb_size=visible_thumb_size,
        cache_root=cache_root,
        text_query=text_query,
        use_cache_first_visible=use_cache_first_visible,
        exclude_collapsed_stack_members=exclude_collapsed_stack_members,
    )


def has_ranking_count_filters(
    orientation: str,
    compared: str,
    min_stars: int,
    folder: str,
    flag: str,
    date_taken: str,
    file_type: str,
    camera: str,
    lens: str,
    tag: str = "",
    id_filter: set | None = None,
    text_query: str = "",
    exclude_collapsed_stack_members: bool = False,
) -> bool:
    return bool(
        orientation or compared or min_stars > 0 or folder or flag or date_taken
        or file_type or camera or lens or tag or id_filter is not None or text_query
        or exclude_collapsed_stack_members
    )


async def count_rankings_uncached(
    db_path: str,
    *,
    catalog_counts: dict,
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
    id_filter: set | None = None,
    visible_thumb_size: str = "",
    cache_root: str = "",
    text_query: str = "",
    cached_visible_ids: set | None = None,
    exclude_collapsed_stack_members: bool = False,
) -> int:
    active_images = int(catalog_counts.get("active_images") or 0)
    if not has_ranking_count_filters(
        orientation,
        compared,
        min_stars,
        folder,
        flag,
        date_taken,
        file_type,
        camera,
        lens,
        tag,
        id_filter,
        text_query,
        exclude_collapsed_stack_members,
    ):
        if not visible_thumb_size or not cache_root:
            return active_images
        conn = await connection.open_async(db_path)
        try:
            all_active_or_available = (
                active_images > 0
                and (
                    active_images == int(catalog_counts.get("total_catalog_images") or 0)
                    or int(catalog_counts.get("removed_images") or 0) == 0
                )
            )
            if all_active_or_available:
                cursor = await conn.execute(
                    "SELECT COUNT(*) AS count FROM cache_entries "
                    "WHERE cache_root = ? AND size = ?",
                    (cache_root, visible_thumb_size),
                )
                return min(active_images, int((await cursor.fetchone())["count"] or 0))
            cursor = await conn.execute(
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
            await connection.close_async(conn, db_path=db_path)

    conn = await connection.open_async(db_path)
    try:
        all_sources_available = int(catalog_counts.get("removed_images") or 0) == 0
        if visible_thumb_size and cache_root and id_filter is None:
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
                text_query=text_query,
                include_source=not all_sources_available,
                exclude_collapsed_stack_members=exclude_collapsed_stack_members,
            )
            source_join = (
                "JOIN catalog_sources s ON s.id = i.source_id "
                if not all_sources_available
                else ""
            )
            cursor = await conn.execute(
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
            cached_id_filter = set(cached_visible_ids or ())
            visible_thumb_size = ""
            cache_root = ""

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
            visible_thumb_size=visible_thumb_size,
            cache_root=cache_root,
            text_query=text_query,
            include_source=not all_sources_available,
            exclude_collapsed_stack_members=exclude_collapsed_stack_members,
        )

        if cached_id_filter is not None:
            if id_filter is not None:
                cached_id_filter.intersection_update(int(image_id) for image_id in id_filter)
            return await count_rankings_with_id_filter_on_conn(
                conn,
                conditions,
                params,
                cached_id_filter,
            )

        if id_filter is not None:
            if not id_filter:
                return 0
            return await count_rankings_with_id_filter_on_conn(conn, conditions, params, id_filter)
        source_join = (
            "JOIN catalog_sources s ON s.id = i.source_id "
            if not all_sources_available
            else ""
        )
        image_source = ranking_count_image_source(
            file_type=file_type,
            id_filter=id_filter,
            text_query=text_query,
        )
        cursor = await conn.execute(
            f"SELECT COUNT(*) AS count FROM {image_source} "
            f"{source_join}"
            f"WHERE {' AND '.join(conditions)}",
            params,
        )
        row = await cursor.fetchone()
        return int(row["count"] or 0)
    finally:
        await connection.close_async(conn, db_path=db_path)


async def count_rankings_uncached_with_visible_cache(
    db_path: str,
    *,
    get_catalog_image_counts,
    get_cached_image_id_set,
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
    id_filter: set | None = None,
    visible_thumb_size: str = "",
    cache_root: str = "",
    text_query: str = "",
    exclude_collapsed_stack_members: bool = False,
) -> int:
    cached_visible_ids = None
    if visible_thumb_size and cache_root and id_filter is not None:
        cached_visible_ids = set(await get_cached_image_id_set(visible_thumb_size, cache_root))
    return await count_rankings_uncached(
        db_path,
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
        caption_model_key=caption_model_key,
        id_filter=id_filter,
        visible_thumb_size=visible_thumb_size,
        cache_root=cache_root,
        text_query=text_query,
        cached_visible_ids=cached_visible_ids,
        exclude_collapsed_stack_members=exclude_collapsed_stack_members,
    )


async def count_rankings_cached(
    db_path: str,
    *,
    get_catalog_image_counts,
    get_cached_image_id_set,
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
    id_filter: set | None = None,
    visible_thumb_size: str = "",
    cache_root: str = "",
    text_query: str = "",
    ttl_seconds: float = RANKING_COUNT_CACHE_TTL_SECONDS,
    exclude_collapsed_stack_members: bool = False,
) -> int:
    cache_key = ranking_count_cache_key(
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
        visible_thumb_size=visible_thumb_size,
        cache_root=cache_root,
        text_query=text_query,
        exclude_collapsed_stack_members=exclude_collapsed_stack_members,
    )
    if cache_key is not None:
        now = _time.time()
        cached = _ranking_count_cache.get(cache_key)
        if cached and cached["expires"] > now:
            return int(cached["value"])

    value = await count_rankings_uncached_with_visible_cache(
        db_path,
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
        tag=tag,
        caption_model_key=caption_model_key,
        id_filter=id_filter,
        visible_thumb_size=visible_thumb_size,
        cache_root=cache_root,
        text_query=text_query,
        exclude_collapsed_stack_members=exclude_collapsed_stack_members,
    )
    if cache_key is not None:
        _ranking_count_cache[cache_key] = {
            "value": int(value),
            "expires": _time.time() + ttl_seconds,
        }
    return value


async def rankable_image_id_set(db_path: str) -> frozenset[int]:
    conn = await connection.open_async(db_path)
    try:
        cursor = await conn.execute(
            "SELECT i.id FROM images i "
            "JOIN catalog_sources s ON s.id = i.source_id "
            "WHERE s.included = 1 "
            "AND i.status IN ('kept', 'maybe') AND i.missing_at IS NULL"
        )
        return frozenset(int(row["id"]) for row in await cursor.fetchall())
    finally:
        await connection.close_async(conn, db_path=db_path)


def invalidate_rankable_image_ids_cache() -> None:
    _rankable_image_ids_cache["ids"] = frozenset()
    _rankable_image_ids_cache["expires"] = 0


async def rankable_image_id_set_cached(
    db_path: str,
    *,
    active_source_ids: frozenset[int] | None = None,
    ttl_seconds: float = RANKABLE_IMAGE_IDS_TTL_SECONDS,
) -> frozenset[int]:
    now = _time.time()
    if now < _rankable_image_ids_cache["expires"]:
        return _rankable_image_ids_cache["ids"]
    if active_source_ids is not None and not active_source_ids:
        _rankable_image_ids_cache["ids"] = frozenset()
        _rankable_image_ids_cache["expires"] = now + ttl_seconds
        return frozenset()

    frozen = await rankable_image_id_set(db_path)
    _rankable_image_ids_cache["ids"] = frozen
    _rankable_image_ids_cache["expires"] = now + ttl_seconds
    return frozen


async def count_rankings_with_id_filter_on_conn(
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


RANK_QUALITY_MIN_SIGNALS = 3


async def rank_quality(
    db_path: str,
    *,
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
    id_filter: set | None = None,
    text_query: str = "",
    exclude_collapsed_stack_members: bool = False,
) -> dict:
    """Summarize how well the filtered set is ranked.

    An image counts as well-ranked once its direct comparisons plus
    propagated updates reach RANK_QUALITY_MIN_SIGNALS.
    """
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
        text_query=text_query,
        exclude_collapsed_stack_members=exclude_collapsed_stack_members,
    )
    signals = "COALESCE(i.comparisons, 0) + COALESCE(i.propagated_updates, 0)"
    select = (
        f"SELECT COUNT(*) AS total, "
        f"SUM(CASE WHEN {signals} >= ? THEN 1 ELSE 0 END) AS well_ranked, "
        f"AVG({signals}) AS avg_signals "
        f"FROM images i JOIN catalog_sources s ON s.id = i.source_id WHERE "
    )
    conn = await connection.open_async(db_path)
    try:
        total = 0
        well_ranked = 0
        signal_sum = 0.0
        if id_filter is not None:
            ids = list(dict.fromkeys(int(image_id) for image_id in id_filter))
            for chunk in _chunked(ids, 900):
                placeholders = ",".join("?" for _ in chunk)
                cursor = await conn.execute(
                    select + " AND ".join(conditions + [f"i.id IN ({placeholders})"]),
                    [RANK_QUALITY_MIN_SIGNALS, *params, *chunk],
                )
                row = await cursor.fetchone()
                chunk_total = int(row["total"] or 0)
                total += chunk_total
                well_ranked += int(row["well_ranked"] or 0)
                signal_sum += float(row["avg_signals"] or 0.0) * chunk_total
        else:
            cursor = await conn.execute(
                select + " AND ".join(conditions),
                [RANK_QUALITY_MIN_SIGNALS, *params],
            )
            row = await cursor.fetchone()
            total = int(row["total"] or 0)
            well_ranked = int(row["well_ranked"] or 0)
            signal_sum = float(row["avg_signals"] or 0.0) * total
        return {
            "total": total,
            "well_ranked": well_ranked,
            "avg_signals": round(signal_sum / total, 2) if total else 0.0,
            "percent": round((well_ranked / total) * 100) if total else 0,
            "min_signals": RANK_QUALITY_MIN_SIGNALS,
        }
    finally:
        await connection.close_async(conn, db_path=db_path)


async def date_histogram(
    db_path: str,
    *,
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
    id_filter: set | None = None,
    text_query: str = "",
    exclude_collapsed_stack_members: bool = False,
) -> dict:
    """Month histogram for the whole filtered scope.

    Powers the timeline scrubber and month view without paging photos:
    returns per-month counts plus an undated bucket.
    """
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
        text_query=text_query,
        exclude_collapsed_stack_members=exclude_collapsed_stack_members,
    )
    select = (
        "SELECT substr(i.date_taken, 1, 7) AS month, COUNT(*) AS count "
        "FROM images i JOIN catalog_sources s ON s.id = i.source_id WHERE "
    )
    conn = await connection.open_async(db_path)
    try:
        buckets: dict[str, int] = {}
        undated = 0

        async def accumulate(where: str, query_params: list) -> None:
            nonlocal undated
            cursor = await conn.execute(select + where + " GROUP BY month", query_params)
            for row in await cursor.fetchall():
                month = row["month"]
                count = int(row["count"] or 0)
                if month and len(month) == 7:
                    buckets[month] = buckets.get(month, 0) + count
                else:
                    undated += count

        if id_filter is not None:
            ids = list(dict.fromkeys(int(image_id) for image_id in id_filter))
            for chunk in _chunked(ids, 900):
                placeholders = ",".join("?" for _ in chunk)
                await accumulate(
                    " AND ".join(conditions + [f"i.id IN ({placeholders})"]),
                    [*params, *chunk],
                )
        else:
            await accumulate(" AND ".join(conditions), list(params))

        months = [
            {"month": month, "count": count}
            for month, count in sorted(buckets.items(), reverse=True)
        ]
        return {
            "months": months,
            "undated": undated,
            "total": sum(buckets.values()) + undated,
        }
    finally:
        await connection.close_async(conn, db_path=db_path)


async def scope_counts(
    db_path: str,
    *,
    orientation: str = "",
    compared: str = "",
    min_stars: int = 0,
    folder: str = "",
    date_taken: str = "",
    file_type: str = "",
    camera: str = "",
    lens: str = "",
    tag: str = "",
    caption_model_key: str = "",
    id_filter: set | None = None,
    text_query: str = "",
    exclude_collapsed_stack_members: bool = False,
) -> dict:
    """Cheap total/picked/rejected counts for a scope in one query."""
    conditions, params = ranking_filter_parts(
        orientation=orientation,
        compared=compared,
        min_stars=min_stars,
        folder=folder,
        date_taken=date_taken,
        file_type=file_type,
        camera=camera,
        lens=lens,
        tag=tag,
        caption_model_key=caption_model_key,
        text_query=text_query,
        exclude_collapsed_stack_members=exclude_collapsed_stack_members,
    )
    select = (
        "SELECT COUNT(*) AS total, "
        "SUM(CASE WHEN i.flag = 'picked' THEN 1 ELSE 0 END) AS picked, "
        "SUM(CASE WHEN i.flag = 'rejected' THEN 1 ELSE 0 END) AS rejected "
        "FROM images i JOIN catalog_sources s ON s.id = i.source_id WHERE "
    )
    conn = await connection.open_async(db_path)
    try:
        total = 0
        picked = 0
        rejected = 0

        async def accumulate(where: str, query_params: list) -> None:
            nonlocal total, picked, rejected
            cursor = await conn.execute(select + where, query_params)
            row = await cursor.fetchone()
            total += int(row["total"] or 0)
            picked += int(row["picked"] or 0)
            rejected += int(row["rejected"] or 0)

        if id_filter is not None:
            ids = list(dict.fromkeys(int(image_id) for image_id in id_filter))
            for chunk in _chunked(ids, 900):
                placeholders = ",".join("?" for _ in chunk)
                await accumulate(
                    " AND ".join(conditions + [f"i.id IN ({placeholders})"]),
                    [*params, *chunk],
                )
        else:
            await accumulate(" AND ".join(conditions), list(params))

        return {"total": total, "picked": picked, "rejected": rejected}
    finally:
        await connection.close_async(conn, db_path=db_path)


def _date_group_label(date_group: str) -> str:
    if date_group:
        try:
            return datetime.strptime(date_group, "%Y-%m").strftime("%B %Y")
        except ValueError:
            return date_group
    return "No Date"


async def date_groups(
    db_path: str,
    *,
    catalog_counts: dict,
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
    visible_thumb_size: str = "",
    cache_root: str = "",
    id_filter: set | None = None,
    text_query: str = "",
    exclude_collapsed_stack_members: bool = False,
) -> list[dict]:
    active_images = int(catalog_counts.get("active_images") or 0)
    if active_images <= 0:
        return []

    all_sources_available = (
        int(catalog_counts.get("removed_images") or 0) == 0
    )
    conditions, params = ranking_filter_parts(
        orientation=orientation, compared=compared, min_stars=min_stars,
        folder=folder, flag=flag, date_taken=date_taken,
        file_type=file_type, camera=camera, lens=lens, tag=tag,
        caption_model_key=caption_model_key,
        text_query=text_query,
        include_source=not all_sources_available,
        exclude_collapsed_stack_members=exclude_collapsed_stack_members,
    )
    if id_filter is not None:
        if not id_filter:
            return []
        placeholders = ",".join("?" for _ in id_filter)
        conditions.append(f"i.id IN ({placeholders})")
        params.extend(int(image_id) for image_id in id_filter)

    select_sql = (
        "SELECT "
        "CASE WHEN i.date_taken IS NOT NULL AND length(i.date_taken) >= 7 "
        "THEN substr(i.date_taken, 1, 7) ELSE '' END AS date_group, "
        "COUNT(*) AS count "
    )
    conn = await connection.open_async(db_path)
    try:
        if visible_thumb_size and cache_root:
            if all_sources_available:
                cursor = await conn.execute(
                    select_sql
                    + "FROM cache_entries c INDEXED BY sqlite_autoindex_cache_entries_1 "
                    "CROSS JOIN images i ON i.id = c.image_id "
                    "WHERE c.cache_root = ? AND c.size = ? "
                    f"AND {' AND '.join(conditions)} "
                    "GROUP BY date_group ORDER BY date_group DESC",
                    [cache_root, visible_thumb_size] + params,
                )
            else:
                cursor = await conn.execute(
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
                cursor = await conn.execute(
                    select_sql
                    + "FROM images i "
                    f"WHERE {' AND '.join(conditions)} "
                    "GROUP BY date_group ORDER BY date_group DESC",
                    params,
                )
            else:
                cursor = await conn.execute(
                    select_sql
                    + "FROM images i JOIN catalog_sources s ON s.id = i.source_id "
                    f"WHERE {' AND '.join(conditions)} "
                    "GROUP BY date_group ORDER BY date_group DESC",
                    params,
                )
        groups = []
        for row in await cursor.fetchall():
            date_group = row["date_group"] or ""
            groups.append({
                "date": date_group,
                "label": _date_group_label(date_group),
                "count": row["count"],
            })
        return groups
    finally:
        await connection.close_async(conn, db_path=db_path)


async def date_groups_cached(
    db_path: str,
    *,
    get_catalog_image_counts,
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
    visible_thumb_size: str = "",
    cache_root: str = "",
    id_filter: set | None = None,
    text_query: str = "",
    force_refresh: bool = False,
    ttl_seconds: float = FACET_CACHE_TTL_SECONDS,
    exclude_collapsed_stack_members: bool = False,
):
    cache_key = facet_cache_key(
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
        exclude_collapsed_stack_members=exclude_collapsed_stack_members,
    )
    now = _time.time()
    cached = _date_groups_cache.get(cache_key) if cache_key is not None else None
    if cached and not force_refresh:
        if cached["expires"] > now:
            return cached["data"]
        if cache_key not in _date_groups_refreshing:
            _date_groups_refreshing.add(cache_key)

            async def _refresh_date_groups():
                try:
                    await date_groups_cached(
                        db_path,
                        get_catalog_image_counts=get_catalog_image_counts,
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
                        visible_thumb_size=visible_thumb_size,
                        cache_root=cache_root,
                        id_filter=id_filter,
                        text_query=text_query,
                        force_refresh=True,
                        ttl_seconds=ttl_seconds,
                        exclude_collapsed_stack_members=exclude_collapsed_stack_members,
                    )
                finally:
                    _date_groups_refreshing.discard(cache_key)

            asyncio.create_task(_refresh_date_groups())
        return cached["data"]

    catalog_counts = await get_catalog_image_counts()
    groups = await date_groups(
        db_path,
        catalog_counts=catalog_counts,
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
        visible_thumb_size=visible_thumb_size,
        cache_root=cache_root,
        id_filter=id_filter,
        text_query=text_query,
        exclude_collapsed_stack_members=exclude_collapsed_stack_members,
    )
    if cache_key is not None and int(catalog_counts.get("active_images") or 0) > 0:
        _date_groups_cache[cache_key] = {
            "data": groups,
            "expires": _time.time() + ttl_seconds,
        }
    return groups


def empty_map_markers(*, total_count: int = 0, visible_count: int = 0) -> dict:
    return {
        "markers": [],
        "total_count": int(total_count or 0),
        "visible_count": int(visible_count or 0),
        "gps_count": 0,
        "gps_total_count": 0,
        "hidden_pending_thumbnails": 0,
    }


async def map_markers(
    db_path: str,
    *,
    catalog_counts: dict,
    total_count: int,
    visible_total_count: int,
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
    visible_thumb_size: str = "",
    cache_root: str = "",
    id_filter: set | None = None,
    text_query: str = "",
) -> dict:
    if int(catalog_counts.get("active_images") or 0) <= 0:
        return empty_map_markers()

    all_sources_available = int(catalog_counts.get("removed_images") or 0) == 0
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
        text_query=text_query,
        include_source=not all_sources_available,
    )
    if id_filter is not None:
        if not id_filter:
            return empty_map_markers()
        placeholders = ",".join("?" for _ in id_filter)
        conditions.append(f"i.id IN ({placeholders})")
        params.extend(int(image_id) for image_id in id_filter)

    gps_conditions = conditions + ["i.latitude IS NOT NULL", "i.longitude IS NOT NULL"]
    conn = await connection.open_async(db_path)
    try:
        if all_sources_available:
            gps_total_cursor = await conn.execute(
                "SELECT COUNT(*) AS count FROM images i "
                f"WHERE {' AND '.join(gps_conditions)}",
                params,
            )
        else:
            gps_total_cursor = await conn.execute(
                "SELECT COUNT(*) AS count FROM images i INDEXED BY idx_images_active_gps_count "
                "JOIN catalog_sources s ON s.id = i.source_id "
                f"WHERE {' AND '.join(gps_conditions)}",
                params,
            )
        gps_total_count = int((await gps_total_cursor.fetchone())["count"] or 0)
        if gps_total_count <= 0:
            return empty_map_markers(
                total_count=total_count,
                visible_count=visible_total_count,
            )

        if visible_thumb_size and cache_root:
            if all_sources_available:
                cursor = await conn.execute(
                    "SELECT i.id, i.filename, i.latitude, i.longitude "
                    "FROM cache_entries c INDEXED BY sqlite_autoindex_cache_entries_1 "
                    "CROSS JOIN images i ON i.id = c.image_id "
                    "WHERE c.cache_root = ? AND c.size = ? "
                    f"AND {' AND '.join(gps_conditions)}",
                    [cache_root, visible_thumb_size] + params,
                )
            else:
                cursor = await conn.execute(
                    "SELECT i.id, i.filename, i.latitude, i.longitude FROM cache_entries c "
                    "JOIN images i ON i.id = c.image_id "
                    "JOIN catalog_sources s ON s.id = i.source_id "
                    f"WHERE c.cache_root = ? AND c.size = ? AND {' AND '.join(gps_conditions)}",
                    [cache_root, visible_thumb_size] + params,
                )
        elif all_sources_available:
            cursor = await conn.execute(
                "SELECT i.id, i.filename, i.latitude, i.longitude FROM images i "
                f"WHERE {' AND '.join(gps_conditions)}",
                params,
            )
        else:
            cursor = await conn.execute(
                "SELECT i.id, i.filename, i.latitude, i.longitude FROM images i "
                "JOIN catalog_sources s ON s.id = i.source_id "
                f"WHERE {' AND '.join(gps_conditions)}",
                params,
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
        return {
            "markers": markers,
            "total_count": int(total_count or 0),
            "visible_count": int(visible_total_count or 0),
            "gps_count": len(markers),
            "gps_total_count": gps_total_count,
            "hidden_pending_thumbnails": max(gps_total_count - len(markers), 0),
        }
    finally:
        await connection.close_async(conn, db_path=db_path)


async def map_markers_cached(
    db_path: str,
    *,
    get_catalog_image_counts,
    count_rankings_func,
    get_visible_pairing_pool_counts,
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
    visible_thumb_size: str = "",
    cache_root: str = "",
    id_filter: set | None = None,
    text_query: str = "",
    ttl_seconds: float = FACET_CACHE_TTL_SECONDS,
):
    cache_key = facet_cache_key(
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
    )
    now = _time.time()
    cached = _map_markers_cache.get(cache_key) if cache_key is not None else None
    if cached and cached["expires"] > now:
        return cached["data"]

    catalog_counts = await get_catalog_image_counts()
    active_images = int(catalog_counts.get("active_images") or 0)
    if active_images <= 0:
        return empty_map_markers()
    all_sources_available = (
        int(catalog_counts.get("removed_images") or 0) == 0
    )
    if id_filter is not None and not id_filter:
        return empty_map_markers()

    has_filters = has_ranking_count_filters(
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
    )
    if not has_filters:
        total_count = active_images
    else:
        total_count = await count_rankings_func(
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
        )

    visible_total_count = total_count
    if visible_thumb_size and cache_root:
        if all_sources_available and not has_filters:
            visible_counts = await get_visible_pairing_pool_counts(visible_thumb_size, cache_root)
            visible_total_count = int(visible_counts.get("visible_images") or 0)
        else:
            visible_total_count = await count_rankings_func(
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
                visible_thumb_size=visible_thumb_size,
                cache_root=cache_root,
                text_query=text_query,
            )

    result = await map_markers(
        db_path,
        catalog_counts=catalog_counts,
        total_count=total_count,
        visible_total_count=visible_total_count,
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
        visible_thumb_size=visible_thumb_size,
        cache_root=cache_root,
        id_filter=id_filter,
        text_query=text_query,
    )
    if cache_key is not None:
        _map_markers_cache[cache_key] = {
            "data": result,
            "expires": _time.time() + ttl_seconds,
        }
    return result
