"""Library rankings response assembly and cache helpers."""

import asyncio
import json
import time
from collections.abc import Awaitable, Callable, Collection
from datetime import datetime

from fastapi.responses import Response

import helpers as app_helpers
import settings
from core import user_activity
from core import responses as response_helpers
from data.repositories import rankings as ranking_repository
from features.library import preview_priority
from features.library import taste as taste_service
from features.sync import satellite


_rankings_response_cache: dict[tuple, dict] = {}
_blended_rankings_order_cache: dict[tuple, dict] = {}
_taste_rankings_order_cache: dict[tuple, dict] = {}
_taste_id_elo_cache: dict[tuple, list[tuple[int, float]]] = {}
_rankings_response_cache_ttl_seconds = 1800.0
_rankings_response_cache_max_entries = 256
_blended_rankings_order_cache_max_entries = 12
_taste_rankings_order_cache_max_entries = 12
_taste_id_elo_cache_max_entries = 8
MAX_RANKINGS_LIMIT = 5000
ELO_FAMILY_SORTS = {"elo", "elo_asc"}

_resolve_library_constraints: Callable[..., object] | None = None
_cache_root: Callable[[], str] | None = None
_clamp_int: Callable[[object, int, int, int], int] | None = None
_normalize_search_query: Callable[[str], str] | None = None
_schedule_thumbnail_prefetch: Callable[..., None] | None = None
_schedule_result_thumbnail_memory_warm: Callable[..., None] | None = None
_rankings_response_cache_ttl_seconds_provider: Callable[[], float] | None = None
_extension_search_terms: Callable[[], Collection[str]] | None = None
_db_signature: Callable[[], str] | None = None
_get_date_groups: Callable[..., Awaitable[list]] | None = None
_get_map_markers: Callable[..., Awaitable[dict]] | None = None
_get_filter_options: Callable[[], Awaitable[dict]] | None = None
_get_stats: Callable[[], Awaitable[dict]] | None = None
_count_rankings: Callable[..., Awaitable[int]] | None = None
_get_rankings: Callable[..., Awaitable[list]] | None = None
_get_ranking_id_elo: Callable[..., Awaitable[list[tuple[int, float]]]] | None = None
_get_ranking_rows_by_ids: Callable[[list[int]], Awaitable[list]] | None = None
_get_rank_quality: Callable[..., Awaitable[dict]] | None = None
_get_date_histogram: Callable[..., Awaitable[dict]] | None = None
_get_scope_counts: Callable[..., Awaitable[dict]] | None = None
_get_visible_pairing_pool_counts: Callable[..., Awaitable[dict]] | None = None
_get_cached_image_ids: Callable[..., Awaitable[set[int]]] | None = None
_get_import_batch_image_ids: Callable[[int], Awaitable[set[int] | None]] | None = None
_get_stack_representative_counts: Callable[[list[int]], Awaitable[dict[int, dict]]] | None = None
_resolve_smart_collection_image_ids: Callable[[int], Awaitable[set[int] | None]] | None = None


def configure(
    *,
    resolve_library_constraints: Callable[..., object],
    cache_root: Callable[[], str],
    clamp_int: Callable[[object, int, int, int], int],
    normalize_search_query: Callable[[str], str],
    schedule_thumbnail_prefetch: Callable[..., None],
    schedule_result_thumbnail_memory_warm: Callable[..., None],
    extension_search_terms: Callable[[], Collection[str]],
    db_signature: Callable[[], str],
    get_date_groups: Callable[..., Awaitable[list]],
    get_map_markers: Callable[..., Awaitable[dict]],
    get_filter_options: Callable[[], Awaitable[dict]],
    get_stats: Callable[[], Awaitable[dict]],
    count_rankings: Callable[..., Awaitable[int]],
    get_rankings: Callable[..., Awaitable[list]],
    get_visible_pairing_pool_counts: Callable[..., Awaitable[dict]],
    get_cached_image_ids: Callable[..., Awaitable[set[int]]],
    resolve_smart_collection_image_ids: Callable[[int], Awaitable[set[int] | None]],
    rankings_response_cache_ttl_seconds: Callable[[], float] | None = None,
    get_rank_quality: Callable[..., Awaitable[dict]] | None = None,
    get_date_histogram: Callable[..., Awaitable[dict]] | None = None,
    get_scope_counts: Callable[..., Awaitable[dict]] | None = None,
    get_ranking_id_elo: Callable[..., Awaitable[list[tuple[int, float]]]] | None = None,
    get_ranking_rows_by_ids: Callable[[list[int]], Awaitable[list]] | None = None,
) -> None:
    global _resolve_library_constraints, _cache_root, _clamp_int, _normalize_search_query
    global _schedule_thumbnail_prefetch, _schedule_result_thumbnail_memory_warm
    global _rankings_response_cache_ttl_seconds_provider
    global _extension_search_terms, _db_signature, _get_date_groups, _get_map_markers
    global _get_filter_options, _get_stats, _count_rankings, _get_rankings
    global _get_ranking_id_elo, _get_ranking_rows_by_ids
    global _get_visible_pairing_pool_counts, _get_cached_image_ids, _get_rank_quality
    global _get_date_histogram, _get_scope_counts, _resolve_smart_collection_image_ids
    _resolve_library_constraints = resolve_library_constraints
    _cache_root = cache_root
    _clamp_int = clamp_int
    _normalize_search_query = normalize_search_query
    _schedule_thumbnail_prefetch = schedule_thumbnail_prefetch
    _schedule_result_thumbnail_memory_warm = schedule_result_thumbnail_memory_warm
    _extension_search_terms = extension_search_terms
    _db_signature = db_signature
    _get_date_groups = get_date_groups
    _get_map_markers = get_map_markers
    _get_filter_options = get_filter_options
    _get_stats = get_stats
    _count_rankings = count_rankings
    _get_rankings = get_rankings
    _get_ranking_id_elo = get_ranking_id_elo
    _get_ranking_rows_by_ids = get_ranking_rows_by_ids
    _get_visible_pairing_pool_counts = get_visible_pairing_pool_counts
    _get_cached_image_ids = get_cached_image_ids
    _resolve_smart_collection_image_ids = resolve_smart_collection_image_ids
    _get_rank_quality = get_rank_quality
    _get_date_histogram = get_date_histogram
    _get_scope_counts = get_scope_counts
    _rankings_response_cache_ttl_seconds_provider = rankings_response_cache_ttl_seconds


def configure_import_batches(*, get_import_batch_image_ids: Callable[[int], Awaitable[set[int] | None]]) -> None:
    global _get_import_batch_image_ids
    _get_import_batch_image_ids = get_import_batch_image_ids


def configure_stacks(
    *,
    get_stack_representative_counts: Callable[[list[int]], Awaitable[dict[int, dict]]],
) -> None:
    global _get_stack_representative_counts
    _get_stack_representative_counts = get_stack_representative_counts


def invalidate_rankings_response_cache(*, order_caches: bool = True) -> None:
    """Clear rankings HTTP/response cache.

    Thumbnail/pregen writes change preview_ready on cards, so they must clear
    the response cache. They do NOT change taste/Elo order — pass
    ``order_caches=False`` so the expensive ordered-id caches survive backfill.
    Ranking-affecting events (flags, picks, embeds, catalog) keep the default.
    """
    _rankings_response_cache.clear()
    if order_caches:
        _blended_rankings_order_cache.clear()
        _taste_rankings_order_cache.clear()
        _taste_id_elo_cache.clear()


def _configured_cache_root() -> str:
    if _cache_root is None:
        raise RuntimeError("Library service is not configured")
    return _cache_root()


def _configured(provider):
    if provider is None:
        raise RuntimeError("Library service is not configured")
    return provider


def _configured_db_signature() -> str:
    return _configured(_db_signature)()


def _configured_clamp_int(value, default: int, minimum: int, maximum: int) -> int:
    if _clamp_int is None:
        raise RuntimeError("Library service is not configured")
    return _clamp_int(value, default, minimum, maximum)


async def _configured_resolve_library_constraints(q: str, *, people: str = "", deep: bool = False) -> dict:
    if _resolve_library_constraints is None:
        raise RuntimeError("Library service is not configured")
    return await _resolve_library_constraints(q, people=people, deep=deep)


def _configured_normalize_search_query(query: str) -> str:
    if _normalize_search_query is None:
        raise RuntimeError("Library service is not configured")
    return _normalize_search_query(query)


def _configured_schedule_thumbnail_prefetch(rows, size: str, *, limit: int) -> None:
    if _schedule_thumbnail_prefetch is not None:
        _schedule_thumbnail_prefetch(rows, size, limit=limit)


def _configured_schedule_result_thumbnail_memory_warm(rows) -> None:
    if _schedule_result_thumbnail_memory_warm is not None:
        _schedule_result_thumbnail_memory_warm(rows)


def _configured_rankings_response_cache_ttl_seconds() -> float:
    if _rankings_response_cache_ttl_seconds_provider is not None:
        return float(_rankings_response_cache_ttl_seconds_provider())
    return float(_rankings_response_cache_ttl_seconds)


def _ranking_taste_blend_settings() -> tuple[bool, int]:
    values = settings.get_settings()
    return (
        bool(values.get("ranking_taste_blend", True)),
        int(values.get("taste_blend_min_signal") or 25),
    )


_taste_warm_task = None


def _schedule_taste_vector() -> None:
    """Build the taste vector off the request path."""

    global _taste_warm_task
    if _taste_warm_task is not None and not _taste_warm_task.done():
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return

    async def _warm() -> None:
        # Reading every embedding in the library is a chore like any other, and
        # it arrived here because someone opened the grid. Wait until they stop
        # looking before spending the disk on it.
        await user_activity.wait_for_quiet()
        taste = await taste_service.taste_vector()
        await taste_service.taste_scaled_scores(taste)

    _taste_warm_task = loop.create_task(_warm())


def _schedule_taste_scores(taste: dict) -> None:
    """Compute the taste blend off the request, once at a time."""

    global _taste_warm_task
    if _taste_warm_task is not None and not _taste_warm_task.done():
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    _taste_warm_task = loop.create_task(taste_service.taste_scaled_scores(taste))


async def _ranking_taste_blend_context(db_sort: str) -> dict:
    enabled, min_signal = _ranking_taste_blend_settings()
    if db_sort not in ELO_FAMILY_SORTS:
        return {"active": False, "cache_key": ("taste_blend", "not_elo", bool(enabled), min_signal)}
    if not enabled:
        return {"active": False, "cache_key": ("taste_blend", "off", False, min_signal)}

    # Warm only: building the taste vector loads every embedding in the library,
    # and the default grid view must never wait for that. See taste.py.
    taste = taste_service.taste_vector_if_warm()
    if taste is None:
        _schedule_taste_vector()
        return {"active": False, "cache_key": ("taste_blend", "warming", True, min_signal)}
    signature = taste_service.taste_vector_signature(taste)
    signal_count = int(taste.get("signal_count") or 0)
    if not taste.get("available") or signal_count < min_signal:
        return {
            "active": False,
            "cache_key": ("taste_blend", "disabled", True, min_signal, signature),
        }
    # Warm only. Computing this means loading every embedding in the library and
    # multiplying through all of them; the grid must never wait for it. When it
    # is not ready the stored rating is shown — which is the number being
    # refined — and the blend arrives on a later refresh.
    scores = await taste_service.taste_scaled_scores_if_warm(taste)
    if not scores:
        _schedule_taste_scores(taste)
        return {
            "active": False,
            "cache_key": ("taste_blend", "warming", True, min_signal, signature),
        }
    return {
        "active": True,
        "scores": scores,
        "cache_key": ("taste_blend", "active", True, min_signal, signature),
    }


def _with_blended_rank_data(rows, blend_context: dict) -> list[dict]:
    if not blend_context.get("active"):
        return [dict(row) for row in rows]
    scores = blend_context.get("scores") or {}
    ranked = []
    for row in rows:
        data = dict(row)
        stored_elo = float(data.get("elo") or 1200.0)
        taste_scaled = scores.get(int(data.get("id") or 0))
        if taste_scaled is None:
            display_score = stored_elo
            confidence = 1.0
            taste_weight = 0.0
        else:
            display_score, confidence = taste_service.blend_display_score(
                stored_elo,
                taste_scaled,
                int(data.get("comparisons") or 0),
            )
            taste_weight = 1.0 - confidence
        data["_display_score"] = display_score
        data["_rank_basis"] = taste_service.rank_basis(confidence)
        data["_taste_weight"] = taste_weight
        ranked.append(data)
    return ranked


def _sort_blended_rankings(rows: list[dict], db_sort: str) -> list[dict]:
    reverse = db_sort != "elo_asc"
    return sorted(
        rows,
        key=lambda row: (
            float(row.get("_display_score") or row.get("elo") or 1200.0),
            float(row.get("elo") or 1200.0),
            int(row.get("id") or 0),
        ),
        reverse=reverse,
    )


def _blended_order_cache_key(
    *,
    blend_context: dict,
    db_sort: str,
    orientation: str,
    compared: str,
    min_stars: int,
    folder,
    flag: str,
    date_taken: str,
    file_type: str,
    camera: str,
    lens: str,
    tag: str,
    search_ids,
    text_query: str,
    visible_thumb_size: str,
    exclude_collapsed_stack_members: bool,
    exclude_sources=(),
) -> tuple | None:
    if not blend_context.get("active") or search_ids is not None or text_query:
        return None
    return (
        _configured_db_signature(),
        _configured_cache_root(),
        db_sort,
        orientation,
        compared,
        int(min_stars or 0),
        _folder_cache_value(folder),
        flag,
        date_taken,
        file_type,
        camera,
        lens,
        tag,
        visible_thumb_size,
        bool(exclude_collapsed_stack_members),
        tuple(exclude_sources or ()),
        blend_context.get("cache_key"),
    )


def _cache_blended_order(cache_key: tuple | None, rows: list[dict], *, total_images: int) -> None:
    if cache_key is None:
        return
    annotations = {
        int(row["id"]): {
            "_display_score": row.get("_display_score"),
            "_rank_basis": row.get("_rank_basis"),
            "_taste_weight": row.get("_taste_weight"),
        }
        for row in rows
    }
    _blended_rankings_order_cache[cache_key] = {
        "ids": [int(row["id"]) for row in rows],
        "annotations": annotations,
        "total_images": int(total_images or 0),
    }
    while len(_blended_rankings_order_cache) > _blended_rankings_order_cache_max_entries:
        _blended_rankings_order_cache.pop(next(iter(_blended_rankings_order_cache)))


async def _blended_order_page(
    cached: dict,
    *,
    offset: int,
    limit: int,
) -> list[dict]:
    page_ids = cached["ids"][offset:offset + limit]
    if not page_ids:
        return []
    rows = await _configured(_get_rankings)(
        limit=len(page_ids),
        offset=0,
        sort="elo",
        id_filter=set(page_ids),
    )
    rows_by_id = {int(row["id"]): dict(row) for row in rows}
    annotations = cached.get("annotations") or {}
    ordered = []
    for image_id in page_ids:
        row = rows_by_id.get(image_id)
        if row is None:
            continue
        row.update(annotations.get(image_id) or {})
        ordered.append(row)
    return ordered


def _taste_order_cache_key(
    *,
    taste_signature: tuple,
    orientation: str,
    compared: str,
    min_stars: int,
    folder,
    flag: str,
    date_taken: str,
    file_type: str,
    camera: str,
    lens: str,
    tag: str,
    search_ids,
    text_query: str,
    collection_id: int,
    exclude_collapsed_stack_members: bool,
    exclude_sources=(),
) -> tuple:
    return (
        _configured_db_signature(),
        "taste_order",
        taste_signature,
        orientation,
        compared,
        int(min_stars or 0),
        _folder_cache_value(folder),
        flag,
        date_taken,
        file_type,
        camera,
        lens,
        tag,
        None if search_ids is None else tuple(sorted(int(image_id) for image_id in search_ids)),
        text_query or "",
        int(collection_id or 0),
        bool(exclude_collapsed_stack_members),
        tuple(exclude_sources or ()),
    )


def _cache_taste_order(
    cache_key: tuple,
    *,
    ordered_ids: list[int],
    taste_scores: dict[int, float | None],
    total_images: int,
) -> None:
    _taste_rankings_order_cache[cache_key] = {
        "ids": ordered_ids,
        "annotations": {
            image_id: {"taste_score": score}
            for image_id, score in taste_scores.items()
            if score is not None
        },
        "total_images": int(total_images or 0),
    }
    while len(_taste_rankings_order_cache) > _taste_rankings_order_cache_max_entries:
        _taste_rankings_order_cache.pop(next(iter(_taste_rankings_order_cache)))


def _sort_taste_order_keys(
    id_elo_rows: list[tuple[int, float]],
    similarities: dict[int, float],
) -> tuple[list[int], dict[int, float | None]]:
    """Sort by (has_score, score, elo) descending, then id ascending for stability.

    Same primary ranking meaning as the legacy per-row loop; id tie-break makes
    equal taste+elo pairs deterministic across query plans.
    """
    keyed = []
    taste_scores: dict[int, float | None] = {}
    for image_id, elo in id_elo_rows:
        score = similarities.get(image_id)
        taste_scores[image_id] = score
        keyed.append((
            0 if score is not None else 1,
            -(score if score is not None else -2.0),
            -float(elo),
            int(image_id),
        ))
    keyed.sort()
    return [image_id for *_rest, image_id in keyed], taste_scores


async def _taste_order_page(
    cached: dict,
    *,
    offset: int,
    limit: int,
    orientation: str = "",
    compared: str = "",
    min_stars: int = 0,
    folder="",
    flag: str = "",
    date_taken: str = "",
    file_type: str = "",
    camera: str = "",
    lens: str = "",
    tag: str = "",
    search_ids=None,
    collection_id: int = 0,
    text_query: str = "",
    exclude_collapsed_stack_members: bool = False,
    exclude_sources=(),
) -> list[dict]:
    page_ids = cached["ids"][offset:offset + limit]
    if not page_ids:
        return []
    if _get_ranking_rows_by_ids is not None and len(page_ids) <= 900:
        rows = await _get_ranking_rows_by_ids(list(page_ids))
    else:
        rows = await _configured(_get_rankings)(
            limit=len(page_ids),
            offset=0,
            sort="elo",
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
            id_filter=set(page_ids) if search_ids is None else set(page_ids) & set(search_ids),
            collection_id=collection_id,
            text_query=text_query,
            exclude_collapsed_stack_members=exclude_collapsed_stack_members,
            exclude_sources=exclude_sources,
        )
    rows_by_id = {int(row["id"]): dict(row) for row in rows}
    annotations = cached.get("annotations") or {}
    ordered = []
    for image_id in page_ids:
        row = rows_by_id.get(image_id)
        if row is None:
            continue
        score = (annotations.get(image_id) or {}).get("taste_score")
        ordered.append(app_helpers.image_card(row, "sm", taste_score=score))
    return ordered


async def _ranking_id_elo_rows(**kwargs) -> list[tuple[int, float]]:
    total = kwargs.pop("_total", None)
    cache_key = (
        _configured_db_signature(),
        "taste_id_elo",
        kwargs.get("orientation") or "",
        kwargs.get("compared") or "",
        int(kwargs.get("min_stars") or 0),
        _folder_cache_value(kwargs.get("folder")),
        kwargs.get("flag") or "",
        kwargs.get("date_taken") or "",
        kwargs.get("file_type") or "",
        kwargs.get("camera") or "",
        kwargs.get("lens") or "",
        kwargs.get("tag") or "",
        None
        if kwargs.get("id_filter") is None
        else tuple(sorted(int(i) for i in kwargs["id_filter"])),
        int(kwargs.get("collection_id") or 0),
        kwargs.get("text_query") or "",
        bool(kwargs.get("exclude_collapsed_stack_members")),
        tuple(kwargs.get("exclude_sources") or ()),
    )
    cached = _taste_id_elo_cache.get(cache_key)
    if cached is not None:
        return cached
    if _get_ranking_id_elo is not None:
        rows = await _get_ranking_id_elo(**kwargs)
    else:
        # Test/fallback path: derive from full rankings rows.
        fetched = await _configured(_get_rankings)(
            limit=int(total or 10_000_000),
            offset=0,
            sort="elo",
            **kwargs,
        )
        rows = [(int(row["id"]), float(row.get("elo") or 1200.0)) for row in fetched]
    _taste_id_elo_cache[cache_key] = rows
    while len(_taste_id_elo_cache) > _taste_id_elo_cache_max_entries:
        _taste_id_elo_cache.pop(next(iter(_taste_id_elo_cache)))
    return rows


def _blend_card_kwargs(data: dict, blend_context: dict) -> dict:
    if not blend_context.get("active"):
        return {}
    display_score = data.get("_display_score", data.get("elo"))
    return {
        "display_score": display_score,
        "rank_basis": data.get("_rank_basis") or "measured",
        "taste_weight": data.get("_taste_weight"),
    }


def _passes_blended_star_filter(data: dict, min_stars: int) -> bool:
    if min_stars <= 0:
        return True
    # Stored Elo projection (images.stars) — the taste blend reorders but the
    # star band a photo belongs to never depends on the blend.
    return int(data.get("stars") or 0) >= int(min_stars)


def _normalized_import_batch_id(value) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _folder_cache_value(folder):
    return ranking_repository.folder_cache_value(folder)


async def _combined_import_batch_filter(current_ids, import_batch: int = 0):
    batch_id = _normalized_import_batch_id(import_batch)
    if batch_id <= 0:
        return current_ids
    if _get_import_batch_image_ids is None:
        raise RuntimeError("Library service is not configured")
    batch_ids = await _get_import_batch_image_ids(batch_id)
    if batch_ids is None:
        return set()
    if current_ids is None:
        return set(batch_ids)
    return set(int(image_id) for image_id in current_ids).intersection(batch_ids)


def _id_scope(value: str = "") -> set[int] | None:
    values = set()
    for raw in (value or "").split(","):
        try:
            image_id = int(raw.strip())
        except (TypeError, ValueError):
            continue
        if image_id > 0:
            values.add(image_id)
    return values or None


def _combine_id_scopes(current_ids, requested_ids: set[int] | None):
    if requested_ids is None:
        return current_ids
    if current_ids is None:
        return requested_ids
    return set(int(image_id) for image_id in current_ids).intersection(requested_ids)


async def _resolve_collection_scope(current_ids, collection_id: int) -> tuple[set[int] | None, int]:
    collection_id = int(collection_id or 0)
    if collection_id <= 0:
        return current_ids, 0
    smart_ids = await _configured(_resolve_smart_collection_image_ids)(collection_id)
    if smart_ids is None:
        return current_ids, collection_id
    return _combine_id_scopes(current_ids, smart_ids), 0


def _normalize_stacks_mode(value: str = "") -> str:
    return "collapsed" if (value or "").strip().lower() == "collapsed" else "expanded"


def _exclude_collapsed_stack_members(stacks: str = "expanded") -> bool:
    return _normalize_stacks_mode(stacks) == "collapsed"


async def _attach_stack_counts(cards: list[dict], stacks: str = "expanded") -> list[dict]:
    if _normalize_stacks_mode(stacks) != "collapsed" or not cards:
        return cards
    if _get_stack_representative_counts is None:
        raise RuntimeError("Library service is not configured")
    mapping = await _get_stack_representative_counts([int(card["id"]) for card in cards])
    if not mapping:
        return cards
    for card in cards:
        stack_data = mapping.get(int(card["id"]))
        if stack_data:
            card["stack_id"] = stack_data["stack_id"]
            card["stack_count"] = stack_data["stack_count"]
    return cards


async def _attach_preview_state(cards: list[dict]) -> list[dict]:
    if not cards:
        return cards
    cached_ids = await _configured(_get_cached_image_ids)(
        [int(card["id"]) for card in cards],
        "sm",
        _configured_cache_root(),
    )
    result = []
    for card in cards:
        ready = int(card["id"]) in cached_ids
        data = dict(card)
        data["preview_ready"] = ready
        if not ready:
            data.pop("thumb_url", None)
        result.append(data)
    return result


async def _preview_ready_count(image_ids) -> int:
    ids = [int(image_id) for image_id in image_ids if int(image_id) > 0]
    if not ids:
        return 0
    cached_ids = await _configured(_get_cached_image_ids)(
        ids,
        "sm",
        _configured_cache_root(),
    )
    return len(cached_ids)


def _ranking_preview_metadata(total_images: int, preview_ready_images: int) -> dict:
    metadata = response_helpers.visibility_counts(total_images, preview_ready_images)
    # Every registered row is visible now; preview readiness is independent metadata.
    metadata["visible_images"] = metadata["total_images"]
    return metadata


def _visible_thumb_size_for_scope(import_batch: int = 0) -> str:
    return "" if satellite.is_satellite_mode() or _normalized_import_batch_id(import_batch) else "sm"


def _preview_thumb_size_for_scope() -> str:
    return "sm"


def copy_rankings_response(response: dict) -> dict:
    return response_helpers.copy_rankings_response(response)


def cache_rankings_response(cache_key, response: dict) -> None:
    _rankings_response_cache[cache_key] = {
        "data": copy_rankings_response(response),
        "json": json.dumps(response, separators=(",", ":")).encode("utf-8"),
        "expires": time.monotonic() + _configured_rankings_response_cache_ttl_seconds(),
    }
    while len(_rankings_response_cache) > _rankings_response_cache_max_entries:
        del _rankings_response_cache[next(iter(_rankings_response_cache))]


def _record_preview_priority_scope(response: dict, *, folder, collection_id: int) -> None:
    if int(response.get("hidden_pending_thumbnails") or 0) <= 0:
        return
    preview_priority.record_scope(folder=folder, collection_id=collection_id)


async def date_groups_payload(
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
    people: str = "",
    q: str = "",
    deep: bool = False,
    import_batch: int = 0,
    stacks: str = "expanded",
    collection_id: int = 0,
    exclude_sources=(),
) -> dict:
    if collection_id:
        histogram = await date_histogram_payload(
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
            people=people,
            q=q,
            deep=deep,
            import_batch=import_batch,
            stacks=stacks,
            collection_id=collection_id,
            exclude_sources=exclude_sources,
        )
        groups = [
            {
                "date": month["month"],
                "label": datetime.strptime(month["month"], "%Y-%m").strftime("%B %Y"),
                "count": month["count"],
            }
            for month in histogram["months"]
        ]
        if histogram["undated"]:
            groups.append({"date": "", "label": "No Date", "count": histogram["undated"]})
        return {"groups": groups}
    search = await _configured_resolve_library_constraints(q, people=people, deep=deep)
    search_ids = await _combined_import_batch_filter(search.get("id_filter"), import_batch)
    exclude_collapsed_stack_members = _exclude_collapsed_stack_members(stacks)
    visible_thumb_size = _visible_thumb_size_for_scope(import_batch)
    groups = await _configured(_get_date_groups)(
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
        cache_root=_configured_cache_root(),
        id_filter=search_ids,
        text_query=search.get("text_query") or "",
        exclude_collapsed_stack_members=exclude_collapsed_stack_members,
        exclude_sources=exclude_sources,
    )
    return {"groups": groups}


async def map_markers_payload(
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
    people: str = "",
    q: str = "",
    deep: bool = False,
    import_batch: int = 0,
    collection_id: int = 0,
    exclude_sources=(),
) -> dict:
    search = await _configured_resolve_library_constraints(q, people=people, deep=deep)
    search_ids = await _combined_import_batch_filter(search.get("id_filter"), import_batch)
    search_ids, collection_id = await _resolve_collection_scope(search_ids, collection_id)
    visible_thumb_size = _visible_thumb_size_for_scope(import_batch)
    payload = await _configured(_get_map_markers)(
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
        cache_root=_configured_cache_root(),
        id_filter=search_ids,
        collection_id=collection_id,
        text_query=search.get("text_query") or "",
        exclude_sources=exclude_sources,
    )
    if not satellite.is_satellite_mode() or not payload.get("markers"):
        return payload
    markers = [dict(marker) for marker in payload["markers"]]
    cached_ids = await _configured(_get_cached_image_ids)(
        [int(marker["id"]) for marker in markers],
        "sm",
        _configured_cache_root(),
    )
    for marker in markers:
        ready = int(marker["id"]) in cached_ids
        marker["preview_ready"] = ready
        if not ready:
            marker.pop("thumb_url", None)
    return {**payload, "markers": markers}


async def date_histogram_payload(
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
    people: str = "",
    q: str = "",
    deep: bool = False,
    import_batch: int = 0,
    stacks: str = "expanded",
    collection_id: int = 0,
    exclude_sources=(),
) -> dict:
    search = await _configured_resolve_library_constraints(q, people=people, deep=deep)
    search_ids = await _combined_import_batch_filter(search.get("id_filter"), import_batch)
    search_ids, collection_id = await _resolve_collection_scope(search_ids, collection_id)
    exclude_collapsed_stack_members = _exclude_collapsed_stack_members(stacks)
    return await _configured(_get_date_histogram)(
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
        id_filter=search_ids,
        collection_id=collection_id,
        text_query=search.get("text_query") or "",
        visible_thumb_size=_preview_thumb_size_for_scope(),
        cache_root=_configured_cache_root(),
        exclude_collapsed_stack_members=exclude_collapsed_stack_members,
        exclude_sources=exclude_sources,
    )


async def scope_counts_payload(
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
    people: str = "",
    q: str = "",
    deep: bool = False,
    import_batch: int = 0,
    stacks: str = "expanded",
    exclude_sources=(),
) -> dict:
    search = await _configured_resolve_library_constraints(q, people=people, deep=deep)
    search_ids = await _combined_import_batch_filter(search.get("id_filter"), import_batch)
    exclude_collapsed_stack_members = _exclude_collapsed_stack_members(stacks)
    return await _configured(_get_scope_counts)(
        orientation=orientation,
        compared=compared,
        min_stars=min_stars,
        folder=folder,
        date_taken=date_taken,
        file_type=file_type,
        camera=camera,
        lens=lens,
        tag=tag,
        id_filter=search_ids,
        text_query=search.get("text_query") or "",
        exclude_collapsed_stack_members=exclude_collapsed_stack_members,
        exclude_sources=exclude_sources,
    )


async def filter_options_payload(
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
    people: str = "",
    q: str = "",
    deep: bool = False,
    import_batch: int = 0,
    stacks: str = "expanded",
    collection_id: int = 0,
    exclude_sources=(),
) -> dict:
    search = await _configured_resolve_library_constraints(q, people=people, deep=deep)
    search_ids = await _combined_import_batch_filter(search.get("id_filter"), import_batch)
    search_ids, collection_id = await _resolve_collection_scope(search_ids, collection_id)
    return await _configured(_get_filter_options)(
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
        caption_model_key=search.get("caption_model_key") or "",
        id_filter=search_ids,
        collection_id=collection_id,
        text_query=search.get("text_query") or "",
        exclude_collapsed_stack_members=_exclude_collapsed_stack_members(stacks),
        exclude_sources=exclude_sources,
    )


async def stats_payload() -> dict:
    return await _configured(_get_stats)()



async def _hidden_in_quiet_sources_count(
    *,
    exclude_sources,
    search_active: bool,
    orientation: str,
    compared: str,
    min_stars: int,
    folder,
    flag: str,
    date_taken: str,
    file_type: str,
    camera: str,
    lens: str,
    tag: str,
    search_ids,
    collection_id: int,
    text_query: str,
    exclude_collapsed_stack_members: bool,
    visible_total: int | None = None,
) -> int:
    """How many matches live only in quiet sources for an active search."""
    excluded = tuple(int(source_id) for source_id in (exclude_sources or ()) if int(source_id) > 0)
    if not excluded or not search_active:
        return 0
    full_total = await _configured(_count_rankings)(
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
        id_filter=search_ids,
        collection_id=collection_id,
        text_query=text_query,
        exclude_collapsed_stack_members=exclude_collapsed_stack_members,
    )
    quiet_total = visible_total
    if quiet_total is None:
        quiet_total = await _configured(_count_rankings)(
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
            id_filter=search_ids,
            collection_id=collection_id,
            text_query=text_query,
            exclude_collapsed_stack_members=exclude_collapsed_stack_members,
            exclude_sources=excluded,
        )
    return max(0, int(full_total) - int(quiet_total))


async def api_rankings_impl(
    limit: int = 100, offset: int = 0, sort: str = "elo",
    orientation: str = "", compared: str = "", min_stars: int = 0,
    folder: str = "", flag: str = "", date_taken: str = "", file_type: str = "",
    camera: str = "", lens: str = "", tag: str = "", q: str = "", deep: bool = False, people: str = "",
    import_batch: int = 0, stacks: str = "expanded", ids: str = "", collection_id: int = 0,
    exclude_sources=(),
    request=None,
):
    limit = _configured_clamp_int(limit, 100, 1, MAX_RANKINGS_LIMIT)
    offset = _configured_clamp_int(offset, 0, 0, 1_000_000)
    search = await _configured_resolve_library_constraints(q, people=people, deep=deep)
    search_ids = await _combined_import_batch_filter(search["id_filter"], import_batch)
    search_ids = _combine_id_scopes(search_ids, _id_scope(ids))
    requested_collection_id = int(collection_id or 0)
    search_ids, collection_id = await _resolve_collection_scope(search_ids, requested_collection_id)
    stacks_mode = _normalize_stacks_mode(stacks)
    exclude_collapsed_stack_members = _exclude_collapsed_stack_members(stacks_mode)
    search_scores = search["scores"]
    search_mode = search["search_mode"]
    text_query = search["text_query"]
    visible_thumb_size = _preview_thumb_size_for_scope()
    if search_mode == "metadata" and text_query and not search_ids and not file_type:
        extension_query = text_query.lower().lstrip(".")
        if extension_query in _configured(_extension_search_terms)():
            file_type = extension_query
            text_query = ""
        elif search_ids is not None:
            return {
                "images": [],
                **response_helpers.visibility_counts(0, 0),
                "total_kept": 0,
                "search_mode": search_mode,
                "ai_unavailable": search["ai_unavailable"],
                "fallback_reason": search.get("fallback_reason", ""),
            }

    db_sort = "elo" if sort == "similarity" and not search_scores else sort
    blend_context = (
        await _ranking_taste_blend_context(db_sort)
        if sort != "taste" and not requested_collection_id and not (sort == "similarity" and search_scores)
        else {"active": False, "cache_key": ("taste_blend", "bypassed")}
    )
    rankings_cache_key = None
    cacheable_metadata_search = (
        search_mode == "metadata"
        and not search_scores
        and search.get("fallback_reason") != "model_loading"
    )
    cacheable_embedding_search = search_mode in ("embedding", "fused", "captions")
    cacheable_search = cacheable_metadata_search or cacheable_embedding_search
    normalized_search_query = _configured_normalize_search_query(q) if search["active"] else ""
    if (not search["active"] or cacheable_search) and not requested_collection_id and not _id_scope(ids):
        rankings_cache_key = (
            _configured_db_signature(),
            _configured_cache_root(),
            limit,
            offset,
            sort,
            db_sort,
            orientation,
            compared,
            int(min_stars or 0),
            _folder_cache_value(folder),
            flag,
            date_taken,
            file_type,
            camera,
            lens,
            tag,
            ",".join(str(person_id) for person_id in search.get("people_ids") or []),
            text_query if cacheable_metadata_search else normalized_search_query if cacheable_embedding_search else "",
            search_mode if cacheable_search else "",
            bool(search["ai_unavailable"]) if cacheable_search else False,
            str(search.get("fallback_reason") or ""),
            _normalized_import_batch_id(import_batch),
            int(collection_id or 0),
            visible_thumb_size,
            stacks_mode,
            blend_context.get("cache_key"),
            tuple(exclude_sources or ()),
        )
        cached = _rankings_response_cache.get(rankings_cache_key)
        if cached and cached["expires"] > time.monotonic():
            cached_data = cached.get("data") or {}
            _configured_schedule_result_thumbnail_memory_warm(cached_data.get("images") or [])
            _record_preview_priority_scope(
                cached_data,
                folder=folder,
                collection_id=requested_collection_id,
            )
            if request is not None and cached.get("json") is not None:
                return Response(content=cached["json"], media_type="application/json")
            return copy_rankings_response(cached_data)

    if sort == "taste":
        taste = await taste_service.taste_vector()
        taste_fields = {
            "taste_available": bool(taste.get("available")),
            "taste_signal_count": int(taste.get("signal_count") or 0),
            "taste_confidence": float(taste.get("confidence") or 0.0),
            "taste_pairwise_accuracy": float(taste.get("pairwise_accuracy") or 0.0),
            "fallback_reason": str(taste.get("fallback_reason") or ""),
        }
        if not taste.get("available"):
            response = {
                "images": [],
                **response_helpers.visibility_counts(0, 0),
                "total_kept": 0,
                "search_mode": search_mode,
                "search_sources": search.get("search_sources") or [],
                "ai_unavailable": search["ai_unavailable"],
                **taste_fields,
            }
            if rankings_cache_key is not None:
                cache_rankings_response(rankings_cache_key, response)
            return response
        if int(limit) <= 0:
            return {
                "images": [],
                **response_helpers.visibility_counts(0, 0),
                "total_kept": 0,
                "search_mode": search_mode,
                "search_sources": search.get("search_sources") or [],
                "ai_unavailable": search["ai_unavailable"],
                **taste_fields,
            }

        taste_signature = taste_service.taste_vector_signature(taste)
        taste_order_key = _taste_order_cache_key(
            taste_signature=taste_signature,
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
            search_ids=search_ids,
            text_query=text_query,
            collection_id=collection_id,
            exclude_collapsed_stack_members=exclude_collapsed_stack_members,
            exclude_sources=exclude_sources,
        )
        cached_taste_order = _taste_rankings_order_cache.get(taste_order_key)

        unfiltered_taste = not any(
            (
                orientation,
                compared,
                int(min_stars or 0),
                folder,
                flag,
                date_taken,
                file_type,
                camera,
                lens,
                tag,
                search_ids is not None,
                search.get("people_active"),
                text_query,
                int(collection_id or 0),
                exclude_collapsed_stack_members,
                exclude_sources,
            )
        )
        if unfiltered_taste:
            counts_task = asyncio.create_task(
                _configured(_get_visible_pairing_pool_counts)("sm", _configured_cache_root())
            )
            total_task = None
            visible_task = None
        else:
            counts_task = None
            total_task = asyncio.create_task(
                _configured(_count_rankings)(
                    orientation=orientation, compared=compared, min_stars=min_stars,
                    folder=folder, flag=flag, date_taken=date_taken, file_type=file_type,
                    camera=camera, lens=lens, tag=tag, id_filter=search_ids, collection_id=collection_id,
                    text_query=text_query,
                    exclude_collapsed_stack_members=exclude_collapsed_stack_members,
                    exclude_sources=exclude_sources,)
            )
            visible_task = asyncio.create_task(
                _configured(_count_rankings)(
                    orientation=orientation, compared=compared, min_stars=min_stars,
                    folder=folder, flag=flag, date_taken=date_taken, file_type=file_type,
                    camera=camera, lens=lens, tag=tag,
                    id_filter=search_ids,
                    collection_id=collection_id,
                    visible_thumb_size=visible_thumb_size, cache_root=_configured_cache_root(),
                    text_query=text_query,
                    exclude_collapsed_stack_members=exclude_collapsed_stack_members,
                    exclude_sources=exclude_sources,
                )
            )

        async def _taste_totals() -> tuple[int, int]:
            if counts_task is not None:
                counts = await counts_task
                total = int(counts.get("active_images") or 0)
                visible = (
                    int(counts.get("visible_images") or 0)
                    if visible_thumb_size
                    else total
                )
                return total, visible
            return int(await total_task), int(await visible_task)

        if cached_taste_order is None:
            similarities_task = asyncio.create_task(
                taste_service.taste_similarity_scores(taste)
            )
            id_elo_task = asyncio.create_task(
                _ranking_id_elo_rows(
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
                    id_filter=search_ids,
                    collection_id=collection_id,
                    text_query=text_query,
                    exclude_collapsed_stack_members=exclude_collapsed_stack_members,
                    exclude_sources=exclude_sources,
                )
            )
            similarities = await similarities_task or {}
            id_elo_rows = await id_elo_task
            ordered_ids, taste_scores = _sort_taste_order_keys(id_elo_rows, similarities)
            total_images, visible_images = await _taste_totals()
            if total_images <= 0:
                response = {
                    "images": [],
                    **_ranking_preview_metadata(total_images, visible_images),
                    "total_kept": total_images,
                    "search_mode": search_mode,
                    "search_sources": search.get("search_sources") or [],
                    "ai_unavailable": search["ai_unavailable"],
                    **taste_fields,
                }
                if rankings_cache_key is not None:
                    cache_rankings_response(rankings_cache_key, response)
                return response
            _cache_taste_order(
                taste_order_key,
                ordered_ids=ordered_ids,
                taste_scores=taste_scores,
                total_images=total_images,
            )
            cached_taste_order = _taste_rankings_order_cache[taste_order_key]
        else:
            total_images, visible_images = await _taste_totals()
            if total_images <= 0:
                response = {
                    "images": [],
                    **_ranking_preview_metadata(total_images, visible_images),
                    "total_kept": total_images,
                    "search_mode": search_mode,
                    "search_sources": search.get("search_sources") or [],
                    "ai_unavailable": search["ai_unavailable"],
                    **taste_fields,
                }
                if rankings_cache_key is not None:
                    cache_rankings_response(rankings_cache_key, response)
                return response
            total_images = int(cached_taste_order.get("total_images") or total_images)

        page = await _taste_order_page(
            cached_taste_order,
            offset=offset,
            limit=limit,
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
            search_ids=search_ids,
            collection_id=collection_id,
            text_query=text_query,
            exclude_collapsed_stack_members=exclude_collapsed_stack_members,
            exclude_sources=exclude_sources,
        )
        page = await _attach_stack_counts(page, stacks_mode)
        page = await _attach_preview_state(page)
        if page:
            _configured_schedule_thumbnail_prefetch(
                [{"id": row["id"], "filepath": ""} for row in page],
                "sm",
                limit=min(len(page), 48),
            )
            _configured_schedule_result_thumbnail_memory_warm(page)
        response = {
            "images": page,
            **_ranking_preview_metadata(total_images, visible_images),
            "total_kept": total_images,
            "search_mode": search_mode,
            "search_sources": search.get("search_sources") or [],
            "ai_unavailable": search["ai_unavailable"],
            **taste_fields,
        }
        if rankings_cache_key is not None:
            cache_rankings_response(rankings_cache_key, response)
        _record_preview_priority_scope(response, folder=folder, collection_id=requested_collection_id)
        return response

    if sort == "similarity" and search_scores:
        total_task = asyncio.create_task(
            _configured(_count_rankings)(
                orientation=orientation, compared=compared, min_stars=min_stars,
                folder=folder, flag=flag, date_taken=date_taken, file_type=file_type,
                camera=camera, lens=lens, tag=tag, id_filter=search_ids, collection_id=collection_id,
                text_query=text_query,
                exclude_collapsed_stack_members=exclude_collapsed_stack_members,
                exclude_sources=exclude_sources,)
        )
        visible_images = await _configured(_count_rankings)(
            orientation=orientation, compared=compared, min_stars=min_stars,
            folder=folder, flag=flag, date_taken=date_taken, file_type=file_type,
            camera=camera, lens=lens, tag=tag,
            id_filter=search_ids,
            collection_id=collection_id,
            visible_thumb_size=visible_thumb_size, cache_root=_configured_cache_root(),
            text_query=text_query,
            exclude_collapsed_stack_members=exclude_collapsed_stack_members,
        exclude_sources=exclude_sources,
    )
        total_images = await total_task
        images = await _configured(_get_rankings)(
            limit=total_images, offset=0, sort="elo",
            orientation=orientation, compared=compared, min_stars=min_stars,
            folder=folder, flag=flag, date_taken=date_taken, file_type=file_type,
            camera=camera, lens=lens, tag=tag,
            id_filter=search_ids,
            collection_id=collection_id,
            text_query=text_query,
            exclude_collapsed_stack_members=exclude_collapsed_stack_members,
        exclude_sources=exclude_sources,
    )
        all_results = []
        for img in images:
            data = dict(img)
            all_results.append(
                app_helpers.image_card(data, "sm", similarity=search_scores.get(data["id"], 0))
            )
        all_results.sort(key=lambda x: x["similarity"], reverse=(sort == "similarity"))
        page = all_results[offset:offset + limit]
        page = await _attach_stack_counts(page, stacks_mode)
        page = await _attach_preview_state(page)
        if page:
            _configured_schedule_thumbnail_prefetch(
                [{"id": row["id"], "filepath": ""} for row in page],
                "sm",
                limit=min(len(page), 48),
            )
            _configured_schedule_result_thumbnail_memory_warm(page)
        response = {
            "images": page,
            **_ranking_preview_metadata(total_images, visible_images),
            "total_kept": total_images,
            "search_mode": search_mode,
            "search_sources": search.get("search_sources") or [],
            "ai_unavailable": search["ai_unavailable"],
            "fallback_reason": search.get("fallback_reason", ""),
        }
        if rankings_cache_key is not None:
            cache_rankings_response(rankings_cache_key, response)
        _record_preview_priority_scope(response, folder=folder, collection_id=requested_collection_id)
        return response

    if search_ids is not None and not search_ids:
        response = {
            "images": [],
            **response_helpers.visibility_counts(0, 0),
            "total_kept": 0,
            "search_mode": search_mode,
            "search_sources": search.get("search_sources") or [],
            "ai_unavailable": search["ai_unavailable"],
            "fallback_reason": search.get("fallback_reason", ""),
        }
        if rankings_cache_key is not None:
            cache_rankings_response(rankings_cache_key, response)
        return response

    ranking_filter_min_stars = 0 if blend_context.get("active") and int(min_stars or 0) > 0 else min_stars
    blended_order_key = _blended_order_cache_key(
        blend_context=blend_context,
        db_sort=db_sort,
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
        search_ids=search_ids,
        text_query=text_query,
        visible_thumb_size=visible_thumb_size,
        exclude_collapsed_stack_members=exclude_collapsed_stack_members,
        exclude_sources=exclude_sources,
    )
    cached_blended_order = (
        _blended_rankings_order_cache.get(blended_order_key)
        if blended_order_key is not None
        else None
    )
    unfiltered_rankings = not any(
        (
            orientation,
            compared,
            int(min_stars or 0),
            folder,
            flag,
            date_taken,
            file_type,
            camera,
            lens,
            tag,
            search_ids is not None,
            search.get("people_active"),
            text_query,
            _normalized_import_batch_id(import_batch),
            int(collection_id or 0),
            exclude_collapsed_stack_members,
            exclude_sources,
        )
    )
    if unfiltered_rankings and cached_blended_order is None:
        counts_task = asyncio.create_task(
            _configured(_get_visible_pairing_pool_counts)("sm", _configured_cache_root())
        )
    elif not unfiltered_rankings and cached_blended_order is None:
        defer_empty_first_page_counts = bool(text_query) and offset == 0 and not blend_context.get("active")
        total_task = None
        visible_task = None
        if not defer_empty_first_page_counts:
            total_task = asyncio.create_task(
                _configured(_count_rankings)(
                    orientation=orientation, compared=compared, min_stars=ranking_filter_min_stars,
                    folder=folder, flag=flag, date_taken=date_taken, file_type=file_type,
                    camera=camera, lens=lens, tag=tag,
                    id_filter=search_ids,
                    collection_id=collection_id,
                    text_query=text_query,
                    exclude_collapsed_stack_members=exclude_collapsed_stack_members,
                    exclude_sources=exclude_sources,)
            )
            visible_task = asyncio.create_task(
                _configured(_count_rankings)(
                    orientation=orientation, compared=compared, min_stars=ranking_filter_min_stars,
                    folder=folder, flag=flag, date_taken=date_taken, file_type=file_type,
                    camera=camera, lens=lens, tag=tag,
                    id_filter=search_ids,
                    collection_id=collection_id,
                    visible_thumb_size=visible_thumb_size, cache_root=_configured_cache_root(),
                    text_query=text_query,
                    exclude_collapsed_stack_members=exclude_collapsed_stack_members,
                    exclude_sources=exclude_sources,)
            )
    quality_task = None
    if offset == 0 and _get_rank_quality is not None and not requested_collection_id:
        quality_task = asyncio.create_task(
            _get_rank_quality(
                orientation=orientation, compared=compared, min_stars=min_stars,
                folder=folder, flag=flag, date_taken=date_taken, file_type=file_type,
                camera=camera, lens=lens, tag=tag,
                id_filter=search_ids,
                text_query=text_query,
                exclude_collapsed_stack_members=exclude_collapsed_stack_members,
                exclude_sources=exclude_sources,
            )
        )
    if blend_context.get("active") and cached_blended_order is not None:
        total_images = int(cached_blended_order.get("total_images") or 0)
        visible_images = await _preview_ready_count(cached_blended_order.get("ids") or [])
        images = await _blended_order_page(
            cached_blended_order,
            offset=offset,
            limit=limit,
        )
    elif blend_context.get("active"):
        if unfiltered_rankings:
            counts = await counts_task
            total_images = int(counts.get("active_images") or 0)
            visible_images = (
                int(counts.get("visible_images") or 0)
                if visible_thumb_size
                else total_images
            )
        else:
            if total_task is None:
                total_task = asyncio.create_task(
                    _configured(_count_rankings)(
                        orientation=orientation, compared=compared, min_stars=ranking_filter_min_stars,
                        folder=folder, flag=flag, date_taken=date_taken, file_type=file_type,
                        camera=camera, lens=lens, tag=tag,
                        id_filter=search_ids,
                        text_query=text_query,
                        exclude_collapsed_stack_members=exclude_collapsed_stack_members,
                        exclude_sources=exclude_sources,)
                )
            if visible_task is None:
                visible_task = asyncio.create_task(
                    _configured(_count_rankings)(
                        orientation=orientation, compared=compared, min_stars=ranking_filter_min_stars,
                        folder=folder, flag=flag, date_taken=date_taken, file_type=file_type,
                        camera=camera, lens=lens, tag=tag,
                        id_filter=search_ids,
                        visible_thumb_size=visible_thumb_size, cache_root=_configured_cache_root(),
                        text_query=text_query,
                        exclude_collapsed_stack_members=exclude_collapsed_stack_members,
                        exclude_sources=exclude_sources,)
                )
            visible_images = await visible_task
            total_images = await total_task

        all_rows = []
        if total_images > 0:
            all_rows = await _configured(_get_rankings)(
                limit=total_images, offset=0, sort=db_sort,
                orientation=orientation, compared=compared, min_stars=ranking_filter_min_stars,
                folder=folder, flag=flag, date_taken=date_taken, file_type=file_type,
                camera=camera, lens=lens, tag=tag,
                id_filter=search_ids,
                collection_id=collection_id,
                text_query=text_query,
                exclude_collapsed_stack_members=exclude_collapsed_stack_members,
        exclude_sources=exclude_sources,
    )
        blended_rows = _sort_blended_rankings(_with_blended_rank_data(all_rows, blend_context), db_sort)
        if int(min_stars or 0) > 0:
            blended_rows = [
                row for row in blended_rows
                if _passes_blended_star_filter(row, int(min_stars or 0))
            ]
            total_images = len(blended_rows)
            visible_images = await _preview_ready_count(row["id"] for row in blended_rows)
        _cache_blended_order(
            blended_order_key,
            blended_rows,
            total_images=total_images,
        )
        images = blended_rows[offset:offset + limit]
    else:
        images = await _configured(_get_rankings)(
            limit=limit, offset=offset, sort=db_sort,
            orientation=orientation, compared=compared, min_stars=ranking_filter_min_stars,
            folder=folder, flag=flag, date_taken=date_taken, file_type=file_type,
            camera=camera, lens=lens, tag=tag,
            id_filter=search_ids,
            collection_id=collection_id,
            text_query=text_query,
            exclude_collapsed_stack_members=exclude_collapsed_stack_members,
        exclude_sources=exclude_sources,
    )
        if unfiltered_rankings:
            counts = await counts_task
            total_images = int(counts.get("active_images") or 0)
            visible_images = (
                int(counts.get("visible_images") or 0)
                if visible_thumb_size
                else total_images
            )
        else:
            if defer_empty_first_page_counts and not images:
                visible_images = 0
                total_images = 0
            else:
                if total_task is None:
                    total_task = asyncio.create_task(
                        _configured(_count_rankings)(
                            orientation=orientation, compared=compared, min_stars=ranking_filter_min_stars,
                            folder=folder, flag=flag, date_taken=date_taken, file_type=file_type,
                            camera=camera, lens=lens, tag=tag,
                            id_filter=search_ids,
                            collection_id=collection_id,
                            text_query=text_query,
                            exclude_collapsed_stack_members=exclude_collapsed_stack_members,
                            exclude_sources=exclude_sources,)
                    )
                if visible_task is None:
                    visible_task = asyncio.create_task(
                        _configured(_count_rankings)(
                            orientation=orientation, compared=compared, min_stars=ranking_filter_min_stars,
                            folder=folder, flag=flag, date_taken=date_taken, file_type=file_type,
                            camera=camera, lens=lens, tag=tag,
                            id_filter=search_ids,
                            collection_id=collection_id,
                            visible_thumb_size=visible_thumb_size, cache_root=_configured_cache_root(),
                            text_query=text_query,
                            exclude_collapsed_stack_members=exclude_collapsed_stack_members,
                            exclude_sources=exclude_sources,)
                    )
                visible_images = await visible_task
                total_images = await total_task
    if images:
        _configured_schedule_thumbnail_prefetch(
            [dict(img) for img in images],
            "sm",
            limit=min(len(images), 48),
        )
        _configured_schedule_result_thumbnail_memory_warm(images)
    result = []
    search_evidence = search.get("evidence_by_id") or {}
    for img in images:
        data = dict(img)
        kwargs = {}
        if search_scores:
            kwargs["similarity"] = search_scores.get(data["id"], 0)
        kwargs.update(_blend_card_kwargs(data, blend_context))
        if sort in ("date_taken", "date_taken_asc"):
            kwargs["date_group"] = app_helpers.date_group_for_image(data)
        card = app_helpers.image_card(data, "sm", **kwargs)
        evidence = search_evidence.get(int(data["id"]))
        if evidence:
            card["search_evidence"] = evidence
        result.append(card)
    result = await _attach_stack_counts(result, stacks_mode)
    result = await _attach_preview_state(result)
    response = {
        "images": result,
        **_ranking_preview_metadata(total_images, visible_images),
        "total_kept": total_images,
        "search_mode": search_mode,
        "search_sources": search.get("search_sources") or [],
        "ai_unavailable": search["ai_unavailable"],
        "fallback_reason": search.get("fallback_reason", ""),
        "query_plan": search.get("query_plan") or {},
    }
    if exclude_sources and search.get("active"):
        response["hidden_in_quiet_sources"] = await _hidden_in_quiet_sources_count(
            exclude_sources=exclude_sources,
            search_active=True,
            orientation=orientation,
            compared=compared,
            min_stars=ranking_filter_min_stars,
            folder=folder,
            flag=flag,
            date_taken=date_taken,
            file_type=file_type,
            camera=camera,
            lens=lens,
            tag=tag,
            search_ids=search_ids,
            collection_id=collection_id,
            text_query=text_query,
            exclude_collapsed_stack_members=exclude_collapsed_stack_members,
            visible_total=total_images,
        )
    if quality_task is not None:
        try:
            response["sort_quality"] = await quality_task
        except Exception:
            pass
    if rankings_cache_key is not None:
        cache_rankings_response(rankings_cache_key, response)
    _record_preview_priority_scope(response, folder=folder, collection_id=requested_collection_id)
    return response
