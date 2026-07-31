"""Ranking query SQL fragments and constants."""

import re
import asyncio
from datetime import datetime
import logging
import time as _time

from core.background import track_background_task
from data import connection
from data.repositories.catalog import HUB_MIRROR_SOURCE_PATH
from data.repositories.common import chunked as _chunked, stage_temp_ids

log = logging.getLogger(__name__)

RANKING_SORTS = {
    "elo": "i.elo DESC",
    "elo_asc": "i.elo ASC",
    # Stored Elo projection first, Elo inside a band; starless photos trail.
    "stars": "COALESCE(i.stars, 0) DESC, i.elo DESC, i.id ASC",
    "stars_asc": "COALESCE(i.stars, 0) ASC, i.elo ASC, i.id ASC",
    "comparisons": "i.comparisons DESC",
    "least_compared": "i.comparisons ASC",
    # Sampler-only: random tie-break so compare/Refine pools cover the whole
    # tied tier instead of a deterministic head. Never use for grid pagination.
    "least_compared_shuffled": "i.comparisons ASC, RANDOM()",
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
    "least_compared_shuffled": "idx_images_active_comparisons_asc",
    "filename": "idx_images_active_filename",
    "filename_asc": "idx_images_active_filename",
    "filename_desc": None,
    "newest": "idx_images_active_id",
    "oldest": None,
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
    "least_compared_shuffled",
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
    "least_compared_shuffled",
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

RANKING_COUNT_CACHE_TTL_SECONDS = 30.0
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
    "i.propagated_updates, COALESCE(i.stars, 0) AS stars, "
    "i.status, i.flag, i.aspect_ratio, "
    "i.date_taken, i.date_source, i.camera_make, i.camera_model, i.lens, i.file_ext, i.file_size, "
    "i.file_modified_at, i.width, i.height, i.latitude, i.longitude, i.created_at"
)

_date_groups_cache: dict[tuple, dict] = {}
_date_groups_refreshing: set[tuple] = set()
_date_histogram_cache: dict[tuple, dict] = {}
_date_histogram_refreshing: set[tuple] = set()
_map_markers_cache: dict[tuple, dict] = {}
_ranking_count_cache: dict[tuple, dict] = {}
_rankable_image_ids_cache = {"ids": frozenset(), "expires": 0}
_MONTH_SOURCE_INDEX = "idx_images_active_month_source"


def escape_like(value: str) -> str:
    return (
        value
        .replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
    )


_WINDOWS_ABS_RE = re.compile(r"^[A-Za-z]:[/\\]")


def _is_windows_absolute(value: str) -> bool:
    return bool(_WINDOWS_ABS_RE.match(value)) or value.startswith("\\\\")


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
        raw = str(value or "").strip()
        if raw == HUB_MIRROR_SOURCE_PATH:
            clean = raw
        else:
            # Folder keys travel the API as '/'; local Windows rows store '\'.
            # Canonicalize here so both range and LIKE predicates line up with
            # whatever separators the matching rows actually use.
            clean = raw.rstrip("/\\")
            if _is_windows_absolute(clean):
                clean = clean.replace("/", "\\")
            elif not clean.startswith("/"):
                clean = clean.replace("\\", "/")
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


def _prefix_upper_bound(prefix: str) -> str:
    """Smallest string greater than every string starting with prefix (byte order)."""
    return prefix[:-1] + chr(ord(prefix[-1]) + 1)


def folder_filter_sql(folder) -> tuple[str, list] | None:
    values = normalized_folder_values(folder)
    if not values:
        return None
    parts = []
    params = []
    for value in values:
        if value == HUB_MIRROR_SOURCE_PATH:
            parts.append("i.source_id IN (SELECT id FROM catalog_sources WHERE path = ?)")
            params.append(value)
        elif value.startswith("/") or _is_windows_absolute(value):
            # Absolute folder scopes are hot (grid browse): a range predicate
            # rides idx_images_active_filepath instead of a LIKE full scan
            # (folder-scoped rankings on 141k rows: seconds -> milliseconds).
            sep = "/" if value.startswith("/") else "\\"
            prefix = f"{value}{sep}"
            parts.append("(i.filepath >= ? AND i.filepath < ?)")
            params.extend([prefix, _prefix_upper_bound(prefix)])
        else:
            # Relative scopes must match rows from local sources (native
            # separators) and hub mirrors ('/') alike.
            posix_like = escape_like(value)
            native_like = escape_like(value.replace("/", "\\"))
            parts.append("(i.filepath LIKE ? ESCAPE '\\' OR i.filepath LIKE ? ESCAPE '\\')")
            params.extend([f"%/{posix_like}/%", f"%\\\\{native_like}\\\\%"])
    if len(parts) == 1:
        return parts[0], params
    return f"({' OR '.join(parts)})", params



def normalized_exclude_sources(exclude_sources) -> tuple[int, ...]:
    """Client quiet-source IDs: optional, order-stable, deduped positive ints."""
    if not exclude_sources:
        return ()
    if isinstance(exclude_sources, str):
        raw = exclude_sources.replace(';', ',').split(',')
    elif isinstance(exclude_sources, (list, tuple, set, frozenset)):
        raw = exclude_sources
    else:
        raw = [exclude_sources]
    out: list[int] = []
    seen: set[int] = set()
    for value in raw:
        try:
            source_id = int(value)
        except (TypeError, ValueError):
            continue
        if source_id <= 0 or source_id in seen:
            continue
        seen.add(source_id)
        out.append(source_id)
    return tuple(out)


def has_absolute_folder_range(folder) -> bool:
    """Return whether every requested folder can use the filepath range indexes."""
    values = normalized_folder_values(folder)
    return bool(values) and all(
        value.startswith("/") or _is_windows_absolute(value) for value in values
    )


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
    caption_model_key: str = "",
    id_filter: set | None = None,
    visible_thumb_size: str = "",
    cache_root: str = "",
    text_query: str = "",
    collection_id: int = 0,
    exclude_collapsed_stack_members: bool = False,
    exclude_sources=(),
):
    if id_filter is not None or collection_id:
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
        (caption_model_key or "") if tag else "",
        visible_thumb_size or "",
        cache_root or "",
        text_query or "",
        bool(exclude_collapsed_stack_members),
        normalized_exclude_sources(exclude_sources),
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
    caption_model_key: str = "",
    visible_thumb_size: str = "",
    cache_root: str = "",
    id_filter: set | None = None,
    text_query: str = "",
    collection_id: int = 0,
    exclude_collapsed_stack_members: bool = False,
    exclude_sources=(),
) -> tuple | None:
    if id_filter is not None or text_query or collection_id:
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
        (caption_model_key or "") if tag else "",
        visible_thumb_size or "",
        cache_root or "",
        bool(exclude_collapsed_stack_members),
        normalized_exclude_sources(exclude_sources),
    )


def invalidate_ranking_count_cache() -> None:
    _ranking_count_cache.clear()


def invalidate_facet_caches() -> None:
    _date_groups_cache.clear()
    _date_groups_refreshing.clear()
    _date_histogram_cache.clear()
    _date_histogram_refreshing.clear()
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

    for cache, refreshing in (
        (_date_groups_cache, _date_groups_refreshing),
        (_date_histogram_cache, _date_histogram_refreshing),
        (_map_markers_cache, None),
    ):
        for key in list(cache.keys()):
            key_size = key[11]
            key_root = key[12]
            if key_size and key_root and cache_scope_matches(key_root, key_size, cache_root, size):
                cache.pop(key, None)
                if refreshing is not None:
                    refreshing.discard(key)


def invalidate_rating_facet_caches() -> None:
    for cache, refreshing in (
        (_date_groups_cache, _date_groups_refreshing),
        (_date_histogram_cache, _date_histogram_refreshing),
        (_map_markers_cache, None),
    ):
        for key in list(cache.keys()):
            _orientation, compared, min_stars, *_rest = key
            if compared or int(min_stars or 0) > 0:
                cache.pop(key, None)
                if refreshing is not None:
                    refreshing.discard(key)


def invalidate_visible_cache_dependent_counts(cache_root: str | None = None, size: str | None = None) -> None:
    for key in list(_ranking_count_cache.keys()):
        key_size = key[11]
        key_root = key[12]
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
    collection_id: int = 0,
    include_source: bool = True,
    exclude_collapsed_stack_members: bool = False,
    exclude_sources=(),
) -> tuple[list[str], list]:
    conditions = [
        "i.status IN ('kept', 'maybe')",
        "i.missing_at IS NULL",
        "i.vc_of IS NULL",
    ]
    if include_source:
        conditions[:0] = ["s.included = 1"]
    params = []

    excluded = normalized_exclude_sources(exclude_sources)
    if excluded:
        placeholders = ",".join("?" for _ in excluded)
        conditions.append(f"i.source_id NOT IN ({placeholders})")
        params.extend(excluded)

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

    if 1 <= min_stars <= 5:
        # Stored Elo projection (images.stars) — the same value grids display.
        conditions.append("COALESCE(i.stars, 0) >= ?")
        params.append(int(min_stars))

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

    if collection_id:
        conditions.append(
            "EXISTS (SELECT 1 FROM collection_images ci "
            "WHERE ci.collection_id = ? AND ci.image_id = i.id)"
        )
        params.append(int(collection_id))

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
    folder: str = "",
    orientation: str = "",
    id_filter: set | None,
    text_query: str,
) -> str | None:
    if id_filter is not None or text_query:
        return None
    if has_absolute_folder_range(folder):
        if sort in ("date_taken", "date_taken_asc"):
            return "idx_images_active_filepath_date_taken"
        return "idx_images_active_filepath_elo"
    if sort == "elo" and orientation in ("landscape", "portrait"):
        return "idx_images_active_visible_orientation_elo"
    return RANKING_INDEXES.get(sort)


def ranking_image_source(
    sort: str,
    *,
    folder: str = "",
    orientation: str = "",
    id_filter: set | None,
    text_query: str,
    allow_forced_index: bool = True,
) -> str:
    folder_range = has_absolute_folder_range(folder)
    if not allow_forced_index and not folder_range:
        return "images i"
    index_name = ranking_index_for_query(
        sort,
        folder=folder,
        orientation=orientation,
        id_filter=id_filter,
        text_query=text_query,
    )
    if not index_name:
        return "images i"
    return f"images i INDEXED BY {index_name}"


def ranking_count_image_source(
    *,
    folder: str = "",
    file_type: str = "",
    id_filter: set | None,
    text_query: str = "",
) -> str:
    if has_absolute_folder_range(folder) and id_filter is None and not text_query:
        return "images i INDEXED BY idx_images_active_filepath_elo"
    if file_type and id_filter is None and not text_query:
        return "images i INDEXED BY idx_images_missing_lower_file_ext_source"
    return "images i"


async def _month_source_index_available(conn) -> bool:
    cursor = await conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'index' AND name = ? LIMIT 1",
        (_MONTH_SOURCE_INDEX,),
    )
    return await cursor.fetchone() is not None


def date_histogram_image_source(
    *,
    folder: str = "",
    id_filter: set | None,
    text_query: str,
    month_index_available: bool,
) -> str:
    """Pick a forced index for month histograms when the planner path is safe.

    Absolute folder scopes keep the filepath+date index. Unscoped / all-photos
    histograms use the month index when present. Filtered text/id search scopes
    fall back to the planner — INDEXED BY would be wrong there.
    """
    if id_filter is not None or text_query:
        return "images i"
    if has_absolute_folder_range(folder):
        return "images i INDEXED BY idx_images_active_filepath_date_taken"
    if month_index_available:
        return f"images i INDEXED BY {_MONTH_SOURCE_INDEX}"
    return "images i"


def folder_page_projection(sort: str) -> tuple[str, str] | None:
    """Small tuple carried through a folder sort before loading full card rows."""
    if sort == "elo":
        return "i.id, i.elo", "page.elo DESC"
    if sort == "elo_asc":
        return "i.id, i.elo", "page.elo ASC"
    if sort == "date_taken":
        return (
            "i.id, i.date_taken",
            "page.date_taken IS NULL ASC, page.date_taken DESC, page.id DESC",
        )
    if sort == "date_taken_asc":
        return (
            "i.id, i.date_taken",
            "page.date_taken IS NULL ASC, page.date_taken ASC, page.id ASC",
        )
    return None


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
    collection_id: int = 0,
    visible_thumb_size: str = "",
    cache_root: str = "",
    text_query: str = "",
    use_cache_first_visible: bool = False,
    exclude_collapsed_stack_members: bool = False,
    exclude_sources=(),
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
        collection_id=collection_id,
        include_source=not all_catalog_images_active,
        exclude_collapsed_stack_members=exclude_collapsed_stack_members,
        exclude_sources=exclude_sources,)

    staged_id_filter = None
    if id_filter is not None:
        if not id_filter:
            return []
        staged_id_filter = id_filter

    conn = await connection.open_async(db_path)
    try:
        id_filter_join = ""
        if staged_id_filter is not None:
            await stage_temp_ids(conn, "temp_ranking_scope_ids", staged_id_filter)
            id_filter_join = (
                "JOIN temp_ranking_scope_ids ranking_scope "
                "ON ranking_scope.image_id = i.id "
            )
        if (
            visible_thumb_size
            and cache_root
            and id_filter is None
            and not collection_id
            and all_sources_available
            and use_cache_first_visible
            and not has_absolute_folder_range(folder)
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
                collection_id=collection_id,
                include_source=False,
                exclude_collapsed_stack_members=exclude_collapsed_stack_members,
        exclude_sources=exclude_sources,)
            cursor = await conn.execute(
                f"SELECT {IMAGE_ROW_SELECT} "
                "FROM cache_entries c INDEXED BY sqlite_autoindex_cache_entries_1 "
                "CROSS JOIN images i ON i.id = c.image_id "
                f"WHERE c.cache_root = ? AND c.size = ? AND {' AND '.join(conditions_no_source)} "
                f"ORDER BY {order} LIMIT ? OFFSET ?",
                [cache_root, visible_thumb_size] + params_no_source + [limit, offset],
            )
            return await cursor.fetchall()

        image_source = ranking_image_source(
            sort,
            folder=folder,
            orientation=orientation,
            id_filter=id_filter,
            text_query=text_query,
            allow_forced_index=all_catalog_images_active,
        )
        source_join = (
            "JOIN catalog_sources s ON s.id = i.source_id "
            if not all_catalog_images_active
            else ""
        )
        source_join += id_filter_join
        page_projection = (
            folder_page_projection(sort)
            if has_absolute_folder_range(folder) and id_filter is None and not text_query
            else None
        )
        if page_projection is not None:
            page_columns, page_order = page_projection
            cursor = await conn.execute(
                "WITH page AS MATERIALIZED ("
                f"SELECT {page_columns} FROM {image_source} {source_join}"
                f"WHERE {' AND '.join(conditions)} ORDER BY {order} LIMIT ? OFFSET ?"
                ") "
                f"SELECT {IMAGE_ROW_SELECT} FROM page JOIN images i ON i.id = page.id "
                f"ORDER BY {page_order}",
                params + [limit, offset],
            )
            return await cursor.fetchall()

        cursor = await conn.execute(
            f"SELECT {IMAGE_ROW_SELECT} "
            f"FROM {image_source} {source_join}"
            f"WHERE {' AND '.join(conditions)} ORDER BY {order} LIMIT ? OFFSET ?",
            params + [limit, offset],
        )
        return await cursor.fetchall()
    finally:
        await connection.close_async(conn, db_path=db_path)


async def ranking_id_elo(
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
    collection_id: int = 0,
    text_query: str = "",
    exclude_collapsed_stack_members: bool = False,
    exclude_sources=(),
) -> list[tuple[int, float]]:
    """Return (image_id, elo) for every ranking row matching filters — no captions/metadata.

    Used by taste sort to build a full-library order without materializing image cards.
    Runs sync SQLite in a worker thread — materially faster than aiosqlite for ~150k rows.
    """
    return await asyncio.to_thread(
        ranking_id_elo_sync,
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
        id_filter=id_filter,
        collection_id=collection_id,
        text_query=text_query,
        exclude_collapsed_stack_members=exclude_collapsed_stack_members,
        exclude_sources=exclude_sources,
    )


def ranking_id_elo_sync(
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
    collection_id: int = 0,
    text_query: str = "",
    exclude_collapsed_stack_members: bool = False,
    exclude_sources=(),
) -> list[tuple[int, float]]:
    """Sync (id, elo) fetch — faster than aiosqlite for the full-library taste order build."""
    import sqlite3

    active_images = int(catalog_counts.get("active_images") or 0)
    if active_images <= 0:
        return []

    all_catalog_images_active = (
        active_images > 0
        and active_images == int(catalog_counts.get("total_catalog_images") or 0)
        and int(catalog_counts.get("removed_images") or 0) == 0
    )
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
        collection_id=collection_id,
        include_source=not all_catalog_images_active,
        exclude_collapsed_stack_members=exclude_collapsed_stack_members,
        exclude_sources=exclude_sources,
    )
    if id_filter is not None and not id_filter:
        return []

    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        id_filter_join = ""
        if id_filter is not None:
            from data.repositories.common import stage_temp_ids_sync

            stage_temp_ids_sync(conn, "temp_ranking_scope_ids", id_filter)
            id_filter_join = (
                "JOIN temp_ranking_scope_ids ranking_scope "
                "ON ranking_scope.image_id = i.id "
            )
        image_source = ranking_image_source(
            "elo",
            folder=folder,
            orientation=orientation,
            id_filter=id_filter,
            text_query=text_query,
            allow_forced_index=all_catalog_images_active,
        )
        source_join = (
            "JOIN catalog_sources s ON s.id = i.source_id "
            if not all_catalog_images_active
            else ""
        )
        source_join += id_filter_join
        rows = conn.execute(
            f"SELECT i.id, i.elo FROM {image_source} {source_join}"
            f"WHERE {' AND '.join(conditions)}",
            params,
        ).fetchall()
        return [(int(row["id"]), float(row["elo"] or 1200.0)) for row in rows]
    finally:
        conn.close()


async def ranking_rows_by_ids(db_path: str, image_ids: list[int]) -> list[dict]:
    """Fetch full ranking rows for a small ordered id list via IN (avoids temp-table join)."""
    if not image_ids:
        return []
    placeholders = ",".join("?" for _ in image_ids)
    conn = await connection.open_async(db_path)
    try:
        cursor = await conn.execute(
            f"SELECT {IMAGE_ROW_SELECT} FROM images i "
            f"WHERE i.id IN ({placeholders})",
            [int(image_id) for image_id in image_ids],
        )
        return [dict(row) for row in await cursor.fetchall()]
    finally:
        await connection.close_async(conn, db_path=db_path)


async def rankings_cached(
    db_path: str,
    *,
    get_catalog_image_counts,
    cache_entry_count,
    get_cached_image_id_set,
    get_cached_image_ids,
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
    collection_id: int = 0,
    visible_thumb_size: str = "",
    cache_root: str = "",
    text_query: str = "",
    exclude_collapsed_stack_members: bool = False,
    exclude_sources=(),
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
        and not collection_id
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
        if id_filter is not None:
            # Visibility for just the filtered ids (e.g. ~789 FTS matches), not
            # the whole ~87k cached set — cold search spent 1.6s materializing it.
            cached_ids = set(await get_cached_image_ids(list(id_filter), visible_thumb_size, cache_root))
        else:
            cached_ids = set(await get_cached_image_id_set(visible_thumb_size, cache_root))
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
        collection_id=collection_id,
        visible_thumb_size=visible_thumb_size,
        cache_root=cache_root,
        text_query=text_query,
        use_cache_first_visible=use_cache_first_visible,
        exclude_collapsed_stack_members=exclude_collapsed_stack_members,
        exclude_sources=exclude_sources,)


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
    collection_id: int = 0,
    exclude_collapsed_stack_members: bool = False,
    exclude_sources=(),
) -> bool:
    return bool(
        orientation or compared or min_stars > 0 or folder or flag or date_taken
        or file_type or camera or lens or tag or id_filter is not None or text_query or collection_id
        or exclude_collapsed_stack_members
        or normalized_exclude_sources(exclude_sources)
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
    collection_id: int = 0,
    visible_thumb_size: str = "",
    cache_root: str = "",
    text_query: str = "",
    cached_visible_ids: set | None = None,
    exclude_collapsed_stack_members: bool = False,
    exclude_sources=(),
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
        collection_id,
        exclude_collapsed_stack_members,
        exclude_sources,
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
        if visible_thumb_size and cache_root and id_filter is None and not collection_id:
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
        exclude_sources=exclude_sources,)
            if has_absolute_folder_range(folder):
                source_join = (
                    "JOIN catalog_sources s ON s.id = i.source_id "
                    if not all_sources_available
                    else ""
                )
                cursor = await conn.execute(
                    "SELECT COUNT(*) AS count "
                    "FROM images i INDEXED BY idx_images_active_filepath_elo "
                    f"{source_join}"
                    f"WHERE {' AND '.join(conditions)} AND EXISTS ("
                    "SELECT 1 FROM cache_entries c "
                    "WHERE c.cache_root = ? AND c.size = ? AND c.image_id = i.id)",
                    params + [cache_root, visible_thumb_size],
                )
            else:
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
            collection_id=collection_id,
            include_source=not all_sources_available,
            exclude_collapsed_stack_members=exclude_collapsed_stack_members,
        exclude_sources=exclude_sources,)

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
            folder=folder,
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
    get_cached_image_ids,
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
    collection_id: int = 0,
    visible_thumb_size: str = "",
    cache_root: str = "",
    text_query: str = "",
    exclude_collapsed_stack_members: bool = False,
    exclude_sources=(),
) -> int:
    cached_visible_ids = None
    if visible_thumb_size and cache_root and id_filter is not None:
        cached_visible_ids = set(await get_cached_image_ids(list(id_filter), visible_thumb_size, cache_root))
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
        collection_id=collection_id,
        visible_thumb_size=visible_thumb_size,
        cache_root=cache_root,
        text_query=text_query,
        cached_visible_ids=cached_visible_ids,
        exclude_collapsed_stack_members=exclude_collapsed_stack_members,
        exclude_sources=exclude_sources,)


async def count_rankings_cached(
    db_path: str,
    *,
    get_catalog_image_counts,
    get_cached_image_id_set,
    get_cached_image_ids,
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
    collection_id: int = 0,
    visible_thumb_size: str = "",
    cache_root: str = "",
    text_query: str = "",
    ttl_seconds: float = RANKING_COUNT_CACHE_TTL_SECONDS,
    exclude_collapsed_stack_members: bool = False,
    exclude_sources=(),
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
        caption_model_key=caption_model_key,
        id_filter=id_filter,
        visible_thumb_size=visible_thumb_size,
        cache_root=cache_root,
        text_query=text_query,
        collection_id=collection_id,
        exclude_collapsed_stack_members=exclude_collapsed_stack_members,
        exclude_sources=exclude_sources,)
    if cache_key is not None:
        now = _time.time()
        cached = _ranking_count_cache.get(cache_key)
        if cached and cached["expires"] > now:
            return int(cached["value"])

    value = await count_rankings_uncached_with_visible_cache(
        db_path,
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
        caption_model_key=caption_model_key,
        id_filter=id_filter,
        collection_id=collection_id,
        visible_thumb_size=visible_thumb_size,
        cache_root=cache_root,
        text_query=text_query,
        exclude_collapsed_stack_members=exclude_collapsed_stack_members,
        exclude_sources=exclude_sources,)
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
    exclude_sources=(),
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
        exclude_sources=exclude_sources,)
    signals = "COALESCE(i.comparisons, 0) + COALESCE(i.propagated_updates, 0)"
    image_source = ranking_count_image_source(
        folder=folder,
        id_filter=id_filter,
        text_query=text_query,
    )
    select = (
        f"SELECT COUNT(*) AS total, "
        f"SUM(CASE WHEN {signals} >= ? THEN 1 ELSE 0 END) AS well_ranked, "
        f"AVG({signals}) AS avg_signals "
        f"FROM {image_source} JOIN catalog_sources s ON s.id = i.source_id WHERE "
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
    collection_id: int = 0,
    text_query: str = "",
    visible_thumb_size: str = "",
    cache_root: str = "",
    exclude_collapsed_stack_members: bool = False,
    exclude_sources=(),
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
        collection_id=collection_id,
        exclude_collapsed_stack_members=exclude_collapsed_stack_members,
        exclude_sources=exclude_sources,)
    conn = await connection.open_async(db_path)
    try:
        month_index_available = await _month_source_index_available(conn)
        image_source = date_histogram_image_source(
            folder=folder,
            id_filter=id_filter,
            text_query=text_query,
            month_index_available=month_index_available,
        )
        preview_expression = "1"
        preview_params = []
        if visible_thumb_size and cache_root:
            preview_expression = (
                "EXISTS (SELECT 1 FROM cache_entries c "
                "WHERE c.cache_root = ? AND c.size = ? AND c.image_id = i.id)"
            )
            preview_params = [cache_root, visible_thumb_size]
        select = (
            "SELECT month, COUNT(*) AS count, "
            "MAX(CASE WHEN month_rank = 1 AND preview_ready = 1 THEN id END) AS cover_id, "
            "MAX(CASE WHEN month_rank = 1 AND preview_ready = 1 THEN elo END) AS cover_elo FROM ("
            "SELECT ready_rows.*, ROW_NUMBER() OVER (PARTITION BY month "
            "ORDER BY preview_ready DESC, elo DESC, id DESC) AS month_rank FROM ("
            "SELECT i.id, substr(i.date_taken, 1, 7) AS month, i.elo, "
            f"{preview_expression} AS preview_ready "
            f"FROM {image_source} JOIN catalog_sources s ON s.id = i.source_id WHERE "
        )
        buckets: dict[str, dict] = {}
        undated = 0

        async def accumulate(where: str, query_params: list) -> None:
            nonlocal undated
            cursor = await conn.execute(
                select + where + ") ready_rows) ranked GROUP BY month",
                [*preview_params, *query_params],
            )
            for row in await cursor.fetchall():
                month = row["month"]
                count = int(row["count"] or 0)
                if month and len(month) == 7:
                    bucket = buckets.setdefault(
                        month,
                        {"count": 0, "cover_id": None, "cover_order": (float("-inf"), -1)},
                    )
                    bucket["count"] += count
                    cover_order = (
                        float(row["cover_elo"]) if row["cover_elo"] is not None else float("-inf"),
                        int(row["cover_id"] or -1),
                    )
                    if cover_order > bucket["cover_order"]:
                        bucket["cover_id"] = row["cover_id"]
                        bucket["cover_order"] = cover_order
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
            {"month": month, "count": bucket["count"], "cover_id": bucket["cover_id"]}
            for month, bucket in sorted(buckets.items(), reverse=True)
        ]
        return {
            "months": months,
            "undated": undated,
            "total": sum(bucket["count"] for bucket in buckets.values()) + undated,
        }
    finally:
        await connection.close_async(conn, db_path=db_path)


async def date_histogram_cached(
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
    id_filter: set | None = None,
    collection_id: int = 0,
    text_query: str = "",
    visible_thumb_size: str = "",
    cache_root: str = "",
    force_refresh: bool = False,
    ttl_seconds: float = FACET_CACHE_TTL_SECONDS,
    exclude_collapsed_stack_members: bool = False,
    exclude_sources=(),
) -> dict:
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
        caption_model_key=caption_model_key,
        id_filter=id_filter,
        text_query=text_query,
        collection_id=collection_id,
        visible_thumb_size=visible_thumb_size,
        cache_root=cache_root,
        exclude_collapsed_stack_members=exclude_collapsed_stack_members,
        exclude_sources=exclude_sources,)
    now = _time.time()
    cached = _date_histogram_cache.get(cache_key) if cache_key is not None else None
    if cached and not force_refresh:
        if cached["expires"] > now:
            return cached["data"]
        if cache_key not in _date_histogram_refreshing:
            _date_histogram_refreshing.add(cache_key)

            async def _refresh_date_histogram():
                try:
                    await date_histogram_cached(
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
                        id_filter=id_filter,
                        collection_id=collection_id,
                        text_query=text_query,
                        visible_thumb_size=visible_thumb_size,
                        cache_root=cache_root,
                        force_refresh=True,
                        ttl_seconds=ttl_seconds,
                        exclude_collapsed_stack_members=exclude_collapsed_stack_members,
                        exclude_sources=exclude_sources,
                    )
                except Exception:
                    log.exception("date histogram background refresh failed")
                finally:
                    _date_histogram_refreshing.discard(cache_key)

            track_background_task(_refresh_date_histogram())
        return cached["data"]

    catalog_counts = await get_catalog_image_counts()
    histogram = await date_histogram(
        db_path,
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
        collection_id=collection_id,
        text_query=text_query,
        visible_thumb_size=visible_thumb_size,
        cache_root=cache_root,
        exclude_collapsed_stack_members=exclude_collapsed_stack_members,
        exclude_sources=exclude_sources,)
    if cache_key is not None and int(catalog_counts.get("active_images") or 0) > 0:
        _date_histogram_cache[cache_key] = {
            "data": histogram,
            "expires": _time.time() + ttl_seconds,
        }
    return histogram


_UNFILTERED_SCOPE_COUNTS_SQL = (
    # Unfiltered /api/counts: avoid walking ~150k active rows.
    # total = maintained catalog_sources.active_image_count (scan/trash/import
    # keep it honest) minus active virtual copies, which rankings filters hide
    # via vc_of IS NULL but the denormalized counter historically includes.
    # Force idx_images_vc_of — without it the planner can walk every active
    # row via idx_images_source_missing_id looking for the rare VC case.
    # picked/rejected: partial flag index seeks, not a full active walk.
    "SELECT "
    "  (SELECT COALESCE(SUM(active_image_count), 0) FROM catalog_sources "
    "   WHERE included = 1) "
    "  - (SELECT COUNT(*) FROM images i INDEXED BY idx_images_vc_of "
    "     JOIN catalog_sources s ON s.id = i.source_id "
    "     WHERE s.included = 1 "
    "       AND i.status IN ('kept', 'maybe') AND i.missing_at IS NULL "
    "       AND i.vc_of IS NOT NULL) "
    "  AS total, "
    "  (SELECT COUNT(*) FROM images i INDEXED BY idx_images_active_flag_elo "
    "   JOIN catalog_sources s ON s.id = i.source_id "
    "   WHERE s.included = 1 "
    "     AND i.status IN ('kept', 'maybe') AND i.missing_at IS NULL "
    "     AND i.vc_of IS NULL AND i.flag = 'picked') "
    "  AS picked, "
    "  (SELECT COUNT(*) FROM images i INDEXED BY idx_images_active_flag_elo "
    "   JOIN catalog_sources s ON s.id = i.source_id "
    "   WHERE s.included = 1 "
    "     AND i.status IN ('kept', 'maybe') AND i.missing_at IS NULL "
    "     AND i.vc_of IS NULL AND i.flag = 'rejected') "
    "  AS rejected"
)


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
    exclude_sources=(),
) -> dict:
    """Cheap total/picked/rejected counts for a scope in one query."""
    if not has_ranking_count_filters(
        orientation,
        compared,
        min_stars,
        folder,
        "",
        date_taken,
        file_type,
        camera,
        lens,
        tag,
        id_filter,
        text_query,
        0,
        exclude_collapsed_stack_members,
        exclude_sources,
    ):
        conn = await connection.open_async(db_path)
        try:
            cursor = await conn.execute(_UNFILTERED_SCOPE_COUNTS_SQL)
            row = await cursor.fetchone()
            return {
                "total": int(row["total"] or 0),
                "picked": int(row["picked"] or 0),
                "rejected": int(row["rejected"] or 0),
            }
        finally:
            await connection.close_async(conn, db_path=db_path)

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
        exclude_sources=exclude_sources,)
    image_source = ranking_count_image_source(
        folder=folder,
        id_filter=id_filter,
        text_query=text_query,
    )
    select = (
        "SELECT COUNT(*) AS total, "
        "SUM(CASE WHEN i.flag = 'picked' THEN 1 ELSE 0 END) AS picked, "
        "SUM(CASE WHEN i.flag = 'rejected' THEN 1 ELSE 0 END) AS rejected "
        f"FROM {image_source} JOIN catalog_sources s ON s.id = i.source_id WHERE "
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
    exclude_sources=(),
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
        exclude_sources=exclude_sources,)
    staged_id_filter = None
    if id_filter is not None:
        if not id_filter:
            return []
        staged_id_filter = id_filter
        conditions.append(
            "EXISTS (SELECT 1 FROM temp_date_group_scope_ids scope_ids "
            "WHERE scope_ids.image_id = i.id)"
        )

    select_sql = (
        "SELECT "
        "CASE WHEN i.date_taken IS NOT NULL AND length(i.date_taken) >= 7 "
        "THEN substr(i.date_taken, 1, 7) ELSE '' END AS date_group, "
        "COUNT(*) AS count "
    )
    conn = await connection.open_async(db_path)
    try:
        if staged_id_filter is not None:
            await stage_temp_ids(conn, "temp_date_group_scope_ids", staged_id_filter)
        if visible_thumb_size and cache_root:
            if has_absolute_folder_range(folder):
                image_source = "images i INDEXED BY idx_images_active_filepath_date_taken"
                source_join = (
                    "JOIN catalog_sources s ON s.id = i.source_id "
                    if not all_sources_available
                    else ""
                )
                cursor = await conn.execute(
                    select_sql
                    + f"FROM {image_source} {source_join}"
                    f"WHERE {' AND '.join(conditions)} AND EXISTS ("
                    "SELECT 1 FROM cache_entries c "
                    "WHERE c.cache_root = ? AND c.size = ? AND c.image_id = i.id) "
                    "GROUP BY date_group ORDER BY date_group DESC",
                    params + [cache_root, visible_thumb_size],
                )
            elif all_sources_available:
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
                image_source = (
                    "images i INDEXED BY idx_images_active_filepath_date_taken"
                    if has_absolute_folder_range(folder) and id_filter is None and not text_query
                    else "images i"
                )
                cursor = await conn.execute(
                    select_sql
                    + f"FROM {image_source} "
                    f"WHERE {' AND '.join(conditions)} "
                    "GROUP BY date_group ORDER BY date_group DESC",
                    params,
                )
            else:
                image_source = (
                    "images i INDEXED BY idx_images_active_filepath_date_taken"
                    if has_absolute_folder_range(folder) and id_filter is None and not text_query
                    else "images i"
                )
                cursor = await conn.execute(
                    select_sql
                    + f"FROM {image_source} JOIN catalog_sources s ON s.id = i.source_id "
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
    exclude_sources=(),
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
        caption_model_key=caption_model_key,
        visible_thumb_size=visible_thumb_size,
        cache_root=cache_root,
        id_filter=id_filter,
        text_query=text_query,
        exclude_collapsed_stack_members=exclude_collapsed_stack_members,
        exclude_sources=exclude_sources,)
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
                        exclude_sources=exclude_sources,
                    )
                except Exception:
                    log.exception("date groups background refresh failed")
                finally:
                    _date_groups_refreshing.discard(cache_key)

            track_background_task(_refresh_date_groups())
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
        exclude_sources=exclude_sources,)
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
    collection_id: int = 0,
    exclude_sources=(),
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
        collection_id=collection_id,
        include_source=not all_sources_available,
        exclude_sources=exclude_sources,)
    staged_id_filter = None
    if id_filter is not None:
        if not id_filter:
            return empty_map_markers()
        staged_id_filter = id_filter
        conditions.append(
            "EXISTS (SELECT 1 FROM temp_map_scope_ids scope_ids "
            "WHERE scope_ids.image_id = i.id)"
        )

    gps_conditions = conditions + ["i.latitude IS NOT NULL", "i.longitude IS NOT NULL"]
    gps_image_source = (
        "images i INDEXED BY idx_images_active_filepath_elo"
        if has_absolute_folder_range(folder) and id_filter is None and not text_query and not collection_id
        else "images i INDEXED BY idx_images_active_gps_markers"
    )
    conn = await connection.open_async(db_path)
    try:
        if staged_id_filter is not None:
            await stage_temp_ids(conn, "temp_map_scope_ids", staged_id_filter)
        if all_sources_available:
            gps_total_cursor = await conn.execute(
                f"SELECT COUNT(*) AS count FROM {gps_image_source} "
                f"WHERE {' AND '.join(gps_conditions)}",
                params,
            )
        else:
            gps_total_cursor = await conn.execute(
                f"SELECT COUNT(*) AS count FROM {gps_image_source} "
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
            source_join = (
                "JOIN catalog_sources s ON s.id = i.source_id "
                if not all_sources_available
                else ""
            )
            cursor = await conn.execute(
                "SELECT i.id, i.filename, i.latitude, i.longitude "
                f"FROM {gps_image_source} {source_join}"
                f"WHERE {' AND '.join(gps_conditions)} AND EXISTS ("
                "SELECT 1 FROM cache_entries c "
                "WHERE c.cache_root = ? AND c.size = ? AND c.image_id = i.id)",
                params + [cache_root, visible_thumb_size],
            )
        elif all_sources_available:
            cursor = await conn.execute(
                f"SELECT i.id, i.filename, i.latitude, i.longitude FROM {gps_image_source} "
                f"WHERE {' AND '.join(gps_conditions)}",
                params,
            )
        else:
            cursor = await conn.execute(
                f"SELECT i.id, i.filename, i.latitude, i.longitude FROM {gps_image_source} "
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
                "preview_ready": True,
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
    collection_id: int = 0,
    ttl_seconds: float = FACET_CACHE_TTL_SECONDS,
    exclude_sources=(),
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
        caption_model_key=caption_model_key,
        visible_thumb_size=visible_thumb_size,
        cache_root=cache_root,
        id_filter=id_filter,
        text_query=text_query,
        collection_id=collection_id,
        exclude_sources=exclude_sources,)
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
        collection_id=collection_id,
        exclude_sources=exclude_sources,
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
            collection_id=collection_id,
            exclude_sources=exclude_sources,
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
                collection_id=collection_id,
                exclude_sources=exclude_sources,
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
        collection_id=collection_id,
        exclude_sources=exclude_sources,)
    if cache_key is not None:
        _map_markers_cache[cache_key] = {
            "data": result,
            "expires": _time.time() + ttl_seconds,
        }
    return result
