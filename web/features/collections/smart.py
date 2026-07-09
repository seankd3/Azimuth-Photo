"""Smart collection query validation and live ranking resolution."""

from __future__ import annotations

import json
import time
from collections.abc import Awaitable, Callable


STRING_QUERY_KEYS = {
    "q",
    "people",
    "folder",
    "camera",
    "lens",
    "tag",
    "flag",
    "date_taken",
    "file_type",
    "orientation",
    "compared",
    "sort",
}
INT_QUERY_KEYS = {"min_stars"}
ALLOWED_QUERY_KEYS = STRING_QUERY_KEYS | INT_QUERY_KEYS
SMART_SUMMARY_CACHE_TTL_SECONDS = 2.0
MAX_QUERY_STRING_LENGTH = 500
MAX_TEXT_QUERY_LENGTH = 1000
MAX_MATERIALIZE_IMAGE_IDS = 10000

_smart_summary_cache: dict[tuple, dict] = {}


class SmartCollectionQueryError(ValueError):
    pass


class SmartCollectionMaterializeTooLarge(ValueError):
    def __init__(self, count: int, limit: int = MAX_MATERIALIZE_IMAGE_IDS):
        self.count = int(count)
        self.limit = int(limit)
        super().__init__(f"Smart collection has {self.count} images; materialization is capped at {self.limit}.")


def invalidate_smart_collection_cache() -> None:
    _smart_summary_cache.clear()


def normalize_query(query: object) -> dict | None:
    if query is None:
        return None
    if not isinstance(query, dict) or isinstance(query, list):
        raise SmartCollectionQueryError("query must be an object")

    normalized = {}
    for key, value in query.items():
        if key not in ALLOWED_QUERY_KEYS:
            raise SmartCollectionQueryError(f"Unknown smart collection query key: {key}")
        if key in STRING_QUERY_KEYS:
            if value is None:
                continue
            if not isinstance(value, str):
                raise SmartCollectionQueryError(f"query.{key} must be a string")
            clean = value.strip()
            limit = MAX_TEXT_QUERY_LENGTH if key == "q" else MAX_QUERY_STRING_LENGTH
            if len(clean) > limit:
                raise SmartCollectionQueryError(f"query.{key} must be {limit} characters or less")
            if clean:
                normalized[key] = clean
            continue

        if isinstance(value, bool) or not isinstance(value, int):
            raise SmartCollectionQueryError(f"query.{key} must be an integer")
        if value > 0:
            normalized[key] = int(value)

    return normalized


def query_to_json(query: dict | None) -> str | None:
    if query is None:
        return None
    return json.dumps(normalize_query(query) or {}, sort_keys=True, separators=(",", ":"))


def query_from_json(value: str | None) -> dict | None:
    if not value:
        return None
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return {}
    try:
        return normalize_query(parsed) or {}
    except SmartCollectionQueryError:
        return {}


def query_cache_key(query: dict, *, db_signature: str) -> tuple:
    return (db_signature, query_to_json(query) or "{}")


def ranking_params(query: dict | None) -> dict:
    query = query or {}
    return {
        "orientation": query.get("orientation", ""),
        "compared": query.get("compared", ""),
        "min_stars": int(query.get("min_stars") or 0),
        "folder": query.get("folder", ""),
        "flag": query.get("flag", ""),
        "date_taken": query.get("date_taken", ""),
        "file_type": query.get("file_type", ""),
        "camera": query.get("camera", ""),
        "lens": query.get("lens", ""),
        "tag": query.get("tag", ""),
    }


async def _resolved_search(
    query: dict,
    *,
    resolve_library_constraints: Callable[..., Awaitable[dict]],
) -> dict:
    return await resolve_library_constraints(
        query.get("q", ""),
        people=query.get("people", ""),
        deep=False,
    )


async def _ranking_filter_kwargs(
    query: dict,
    *,
    resolve_library_constraints: Callable[..., Awaitable[dict]],
) -> dict:
    search = await _resolved_search(query, resolve_library_constraints=resolve_library_constraints)
    return {
        **ranking_params(query),
        "id_filter": search.get("id_filter"),
        "text_query": search.get("text_query") or "",
        # Smart collections are saved scopes, not collapsed stack displays.
        "exclude_collapsed_stack_members": False,
    }


async def resolve_detail(
    query: dict,
    *,
    limit: int,
    offset: int,
    resolve_library_constraints: Callable[..., Awaitable[dict]],
    count_rankings: Callable[..., Awaitable[int]],
    get_rankings: Callable[..., Awaitable[list]],
) -> dict:
    filters = await _ranking_filter_kwargs(
        query,
        resolve_library_constraints=resolve_library_constraints,
    )
    total = await count_rankings(**filters)
    images = await get_rankings(
        limit=max(1, min(int(limit), 1000)),
        offset=max(0, int(offset)),
        sort=query.get("sort", "elo"),
        **filters,
    )
    return {"image_count": int(total), "images": [dict(row) for row in images]}


async def resolve_summary(
    query: dict,
    *,
    resolve_library_constraints: Callable[..., Awaitable[dict]],
    count_rankings: Callable[..., Awaitable[int]],
    get_rankings: Callable[..., Awaitable[list]],
    db_signature: Callable[[], str],
) -> dict:
    cache_key = query_cache_key(query, db_signature=db_signature())
    cached = _smart_summary_cache.get(cache_key)
    if cached and cached["expires"] > time.monotonic():
        return dict(cached["data"])

    filters = await _ranking_filter_kwargs(
        query,
        resolve_library_constraints=resolve_library_constraints,
    )
    count = await count_rankings(**filters)
    cover_rows = await get_rankings(
        limit=1,
        offset=0,
        sort=query.get("sort", "elo"),
        **filters,
    )
    cover = dict(cover_rows[0]) if cover_rows else None
    summary = {
        "image_count": int(count),
        "cover_image_id": int(cover["id"]) if cover else None,
        "cover_filename": cover.get("filename") if cover else "",
    }
    _smart_summary_cache[cache_key] = {
        "data": dict(summary),
        "expires": time.monotonic() + SMART_SUMMARY_CACHE_TTL_SECONDS,
    }
    return summary


async def resolve_image_ids(
    query: dict,
    *,
    resolve_library_constraints: Callable[..., Awaitable[dict]],
    count_rankings: Callable[..., Awaitable[int]],
    get_rankings: Callable[..., Awaitable[list]],
    chunk_size: int = 5000,
) -> list[int]:
    filters = await _ranking_filter_kwargs(
        query,
        resolve_library_constraints=resolve_library_constraints,
    )
    total = await count_rankings(**filters)
    if int(total) > MAX_MATERIALIZE_IMAGE_IDS:
        raise SmartCollectionMaterializeTooLarge(int(total))
    image_ids: list[int] = []
    offset = 0
    while offset < total:
        rows = await get_rankings(
            limit=chunk_size,
            offset=offset,
            sort=query.get("sort", "elo"),
            **filters,
        )
        if not rows:
            break
        image_ids.extend(int(row["id"]) for row in rows)
        offset += len(rows)
    return image_ids
