"""Library rankings response assembly and cache helpers."""

import asyncio
import json
import time
from collections.abc import Awaitable, Callable, Collection

from fastapi.responses import Response

import helpers as app_helpers
from core import responses as response_helpers


_rankings_response_cache: dict[tuple, dict] = {}
_rankings_response_cache_ttl_seconds = 1800.0

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
_get_visible_pairing_pool_counts: Callable[..., Awaitable[dict]] | None = None


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
    rankings_response_cache_ttl_seconds: Callable[[], float] | None = None,
) -> None:
    global _resolve_library_constraints, _cache_root, _clamp_int, _normalize_search_query
    global _schedule_thumbnail_prefetch, _schedule_result_thumbnail_memory_warm
    global _rankings_response_cache_ttl_seconds_provider
    global _extension_search_terms, _db_signature, _get_date_groups, _get_map_markers
    global _get_filter_options, _get_stats, _count_rankings, _get_rankings
    global _get_visible_pairing_pool_counts
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
    _get_visible_pairing_pool_counts = get_visible_pairing_pool_counts
    _rankings_response_cache_ttl_seconds_provider = rankings_response_cache_ttl_seconds


def invalidate_rankings_response_cache() -> None:
    _rankings_response_cache.clear()


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


def copy_rankings_response(response: dict) -> dict:
    return response_helpers.copy_rankings_response(response)


def cache_rankings_response(cache_key, response: dict) -> None:
    _rankings_response_cache[cache_key] = {
        "data": copy_rankings_response(response),
        "json": json.dumps(response, separators=(",", ":")).encode("utf-8"),
        "expires": time.monotonic() + _configured_rankings_response_cache_ttl_seconds(),
    }


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
    people: str = "",
    q: str = "",
    deep: bool = False,
) -> dict:
    search = await _configured_resolve_library_constraints(q, people=people, deep=deep)
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
        visible_thumb_size="sm",
        cache_root=_configured_cache_root(),
        id_filter=search.get("id_filter"),
        text_query=search.get("text_query") or "",
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
    people: str = "",
    q: str = "",
    deep: bool = False,
) -> dict:
    search = await _configured_resolve_library_constraints(q, people=people, deep=deep)
    return await _configured(_get_map_markers)(
        orientation=orientation,
        compared=compared,
        min_stars=min_stars,
        folder=folder,
        flag=flag,
        date_taken=date_taken,
        file_type=file_type,
        camera=camera,
        lens=lens,
        visible_thumb_size="sm",
        cache_root=_configured_cache_root(),
        id_filter=search.get("id_filter"),
        text_query=search.get("text_query") or "",
    )


async def filter_options_payload() -> dict:
    return await _configured(_get_filter_options)()


async def stats_payload() -> dict:
    return await _configured(_get_stats)()


async def api_rankings_impl(
    limit: int = 100, offset: int = 0, sort: str = "elo",
    orientation: str = "", compared: str = "", min_stars: int = 0,
    folder: str = "", flag: str = "", date_taken: str = "", file_type: str = "",
    camera: str = "", lens: str = "", q: str = "", deep: bool = False, people: str = "",
    request=None,
):
    limit = _configured_clamp_int(limit, 100, 1, 500)
    offset = _configured_clamp_int(offset, 0, 0, 1_000_000)
    search = await _configured_resolve_library_constraints(q, people=people, deep=deep)
    search_ids = search["id_filter"]
    search_scores = search["scores"]
    search_mode = search["search_mode"]
    text_query = search["text_query"]
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
                "deep_requested": search.get("deep_requested", False),
                "deep_search_cached": search.get("deep_search_cached", False),
                "fallback_reason": search.get("fallback_reason", ""),
            }

    db_sort = "elo" if sort == "similarity" and not search_scores else sort
    rankings_cache_key = None
    cacheable_metadata_search = (
        search_mode == "metadata"
        and not search_scores
        and search.get("fallback_reason") != "model_loading"
    )
    cacheable_embedding_search = search_mode in {"embedding", "deep_embedding"}
    cacheable_search = cacheable_metadata_search or cacheable_embedding_search
    normalized_search_query = _configured_normalize_search_query(q) if search["active"] else ""
    if not search["active"] or cacheable_search:
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
            folder,
            flag,
            date_taken,
            file_type,
            camera,
            lens,
            ",".join(str(person_id) for person_id in search.get("people_ids") or []),
            text_query if cacheable_metadata_search else normalized_search_query if cacheable_embedding_search else "",
            search_mode if cacheable_search else "",
            bool(search["ai_unavailable"]) if cacheable_search else False,
            bool(search.get("deep_requested")),
            bool(search.get("deep_search_cached")),
            str(search.get("fallback_reason") or ""),
        )
        cached = _rankings_response_cache.get(rankings_cache_key)
        if cached and cached["expires"] > time.monotonic():
            _configured_schedule_result_thumbnail_memory_warm((cached.get("data") or {}).get("images") or [])
            if request is not None and cached.get("json") is not None:
                return Response(content=cached["json"], media_type="application/json")
            return copy_rankings_response(cached["data"])

    if sort == "similarity" and search_scores:
        total_task = asyncio.create_task(
            _configured(_count_rankings)(
                orientation=orientation, compared=compared, min_stars=min_stars,
                folder=folder, flag=flag, date_taken=date_taken, file_type=file_type,
                camera=camera, lens=lens, id_filter=search_ids, text_query=text_query,
            )
        )
        visible_images = await _configured(_count_rankings)(
            orientation=orientation, compared=compared, min_stars=min_stars,
            folder=folder, flag=flag, date_taken=date_taken, file_type=file_type,
            camera=camera, lens=lens,
            id_filter=search_ids,
            visible_thumb_size="sm", cache_root=_configured_cache_root(),
            text_query=text_query,
        )
        total_images = await total_task
        images = await _configured(_get_rankings)(
            limit=visible_images, offset=0, sort="elo",
            orientation=orientation, compared=compared, min_stars=min_stars,
            folder=folder, flag=flag, date_taken=date_taken, file_type=file_type,
            camera=camera, lens=lens,
            id_filter=search_ids,
            visible_thumb_size="sm", cache_root=_configured_cache_root(),
            text_query=text_query,
        )
        all_results = []
        for img in images:
            data = dict(img)
            all_results.append(
                app_helpers.image_card(data, "sm", similarity=search_scores.get(data["id"], 0))
            )
        all_results.sort(key=lambda x: x["similarity"], reverse=(sort == "similarity"))
        page = all_results[offset:offset + limit]
        if page:
            _configured_schedule_thumbnail_prefetch(
                [{"id": row["id"], "filepath": ""} for row in page],
                "sm",
                limit=min(len(page), 48),
            )
            _configured_schedule_result_thumbnail_memory_warm(page)
        response = {
            "images": page,
            **response_helpers.visibility_counts(total_images, visible_images),
            "total_kept": total_images,
            "search_mode": search_mode,
            "ai_unavailable": search["ai_unavailable"],
            "deep_requested": search.get("deep_requested", False),
            "deep_search_cached": search.get("deep_search_cached", False),
            "fallback_reason": search.get("fallback_reason", ""),
        }
        if rankings_cache_key is not None:
            cache_rankings_response(rankings_cache_key, response)
        return response

    if search_ids is not None and not search_ids:
        response = {
            "images": [],
            **response_helpers.visibility_counts(0, 0),
            "total_kept": 0,
            "search_mode": search_mode,
            "ai_unavailable": search["ai_unavailable"],
            "deep_requested": search.get("deep_requested", False),
            "deep_search_cached": search.get("deep_search_cached", False),
            "fallback_reason": search.get("fallback_reason", ""),
        }
        if rankings_cache_key is not None:
            cache_rankings_response(rankings_cache_key, response)
        return response

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
            search_ids is not None,
            search.get("people_active"),
            text_query,
        )
    )
    if unfiltered_rankings:
        counts_task = asyncio.create_task(
            _configured(_get_visible_pairing_pool_counts)("sm", _configured_cache_root())
        )
    else:
        defer_empty_first_page_counts = bool(text_query) and offset == 0
        total_task = None
        visible_task = None
        if not defer_empty_first_page_counts:
            total_task = asyncio.create_task(
                _configured(_count_rankings)(
                    orientation=orientation, compared=compared, min_stars=min_stars,
                    folder=folder, flag=flag, date_taken=date_taken, file_type=file_type,
                    camera=camera, lens=lens,
                    id_filter=search_ids,
                    text_query=text_query,
                )
            )
            visible_task = asyncio.create_task(
                _configured(_count_rankings)(
                    orientation=orientation, compared=compared, min_stars=min_stars,
                    folder=folder, flag=flag, date_taken=date_taken, file_type=file_type,
                    camera=camera, lens=lens,
                    id_filter=search_ids,
                    visible_thumb_size="sm", cache_root=_configured_cache_root(),
                    text_query=text_query,
                )
            )
    images = await _configured(_get_rankings)(
        limit=limit, offset=offset, sort=db_sort,
        orientation=orientation, compared=compared, min_stars=min_stars,
        folder=folder, flag=flag, date_taken=date_taken, file_type=file_type,
        camera=camera, lens=lens,
        id_filter=search_ids,
        visible_thumb_size="sm", cache_root=_configured_cache_root(),
        text_query=text_query,
    )
    if unfiltered_rankings:
        counts = await counts_task
        total_images = int(counts.get("active_images") or 0)
        visible_images = int(counts.get("visible_images") or 0)
    else:
        if defer_empty_first_page_counts and not images:
            visible_images = 0
            total_images = 0
        else:
            if total_task is None:
                total_task = asyncio.create_task(
                    _configured(_count_rankings)(
                        orientation=orientation, compared=compared, min_stars=min_stars,
                        folder=folder, flag=flag, date_taken=date_taken, file_type=file_type,
                        camera=camera, lens=lens,
                        id_filter=search_ids,
                        text_query=text_query,
                    )
                )
            if visible_task is None:
                visible_task = asyncio.create_task(
                    _configured(_count_rankings)(
                        orientation=orientation, compared=compared, min_stars=min_stars,
                        folder=folder, flag=flag, date_taken=date_taken, file_type=file_type,
                        camera=camera, lens=lens,
                        id_filter=search_ids,
                        visible_thumb_size="sm", cache_root=_configured_cache_root(),
                        text_query=text_query,
                    )
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
    for img in images:
        data = dict(img)
        kwargs = {}
        if search_scores:
            kwargs["similarity"] = search_scores.get(data["id"], 0)
        if sort in ("date_taken", "date_taken_asc"):
            kwargs["date_group"] = app_helpers.date_group_for_image(data)
        result.append(app_helpers.image_card(data, "sm", **kwargs))
    response = {
        "images": result,
        **response_helpers.visibility_counts(total_images, visible_images),
        "total_kept": total_images,
        "search_mode": search_mode,
        "ai_unavailable": search["ai_unavailable"],
        "deep_requested": search.get("deep_requested", False),
        "deep_search_cached": search.get("deep_search_cached", False),
        "fallback_reason": search.get("fallback_reason", ""),
    }
    if rankings_cache_key is not None:
        cache_rankings_response(rankings_cache_key, response)
    return response
