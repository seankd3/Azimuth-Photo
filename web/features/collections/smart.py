"""Smart collection query validation and live ranking resolution."""

from __future__ import annotations

import json
import time
from collections.abc import Awaitable, Callable
import db
from data.repositories import collections as collection_repository


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
) -> dict:
    filters = await _ranking_filter_kwargs(
        query,
        resolve_library_constraints=resolve_library_constraints,
    )
    total, images = await _page(query, limit=max(1, min(int(limit), 1000)),
                                offset=max(0, int(offset)))
    return {"image_count": total, "images": images}


async def resolve_summary(
    query: dict,
    *,
    resolve_library_constraints: Callable[..., Awaitable[dict]],
) -> dict:
    cache_key = query_cache_key(query, db_signature=db.DB_PATH)
    cached = _smart_summary_cache.get(cache_key)
    if cached and cached["expires"] > time.monotonic():
        return dict(cached["data"])

    filters = await _ranking_filter_kwargs(
        query,
        resolve_library_constraints=resolve_library_constraints,
    )
    count, cover_rows = await _page(query, limit=1, offset=0)
    cover = cover_rows[0] if cover_rows else None
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


async def _resolve_image_ids(
    query: dict,
    *,
    resolve_library_constraints: Callable[..., Awaitable[dict]],
    chunk_size: int = 5000,
    materialize_limit: int | None = None,
) -> list[int]:
    filters = await _ranking_filter_kwargs(
        query,
        resolve_library_constraints=resolve_library_constraints,
    )
    total, _first = await _page(query, limit=0, offset=0)
    if materialize_limit is not None and int(total) > materialize_limit:
        raise SmartCollectionMaterializeTooLarge(int(total), materialize_limit)
    image_ids: list[int] = []
    offset = 0
    while offset < total:
        _n, rows = await _page(query, limit=chunk_size, offset=offset)
        if not rows:
            break
        image_ids.extend(int(row["id"]) for row in rows)
        offset += len(rows)
    return image_ids


async def resolve_image_ids(
    query: dict,
    *,
    resolve_library_constraints: Callable[..., Awaitable[dict]],
    chunk_size: int = 5000,
) -> list[int]:
    """Resolve a live smart-collection scope without a membership cap."""
    return await _resolve_image_ids(
        query,
        resolve_library_constraints=resolve_library_constraints,
        chunk_size=chunk_size,
    )


async def resolve_materialized_image_ids(
    query: dict,
    *,
    resolve_library_constraints: Callable[..., Awaitable[dict]],
    chunk_size: int = 5000,
) -> list[int]:
    """Resolve membership for the explicit smart-to-static operation."""
    return await _resolve_image_ids(
        query,
        resolve_library_constraints=resolve_library_constraints,
        chunk_size=chunk_size,
        materialize_limit=MAX_MATERIALIZE_IMAGE_IDS,
    )


async def resolve_collection_image_ids(collection_id: int) -> set[int] | None:
    """Membership of a smart collection, or None if it is a static one."""

    from core import query_constraints

    collection = await collection_repository.get_collection(db.DB_PATH, collection_id, limit=1)
    if not collection or not collection.get("smart"):
        return None
    image_ids = await resolve_image_ids(
        collection.get("query") or {},
        resolve_library_constraints=query_constraints.resolve_configured_library_constraints,
    )
    return set(image_ids)


def combine_id_scopes(current_ids, requested_ids: set[int] | None):
    """Two id scopes narrowed together. Neither present means no narrowing."""

    if requested_ids is None:
        return current_ids
    if current_ids is None:
        return requested_ids
    return {int(image_id) for image_id in current_ids}.intersection(requested_ids)


async def resolve_scope(current_ids, collection_id: int) -> tuple[set[int] | None, int]:
    """Narrow a set of ids by a collection, if that collection is a smart one.

    Moved here from the library service, which is where it always belonged: it
    resolves a *collection*. It was the last thing keeping a 1,661-line module
    alive after every route it served had been rebuilt on the core.
    """

    collection_id = int(collection_id or 0)
    if collection_id <= 0:
        return current_ids, 0
    smart_ids = await resolve_collection_image_ids(collection_id)
    if smart_ids is None:
        return current_ids, collection_id
    return combine_id_scopes(current_ids, smart_ids), 0


async def _page(query: dict, *, limit: int, offset: int) -> tuple[int, list[dict]]:
    """A smart collection's photographs: the saved query, run against the library.

    This used to go through `db.get_rankings`, which is why a 2,482-line
    ranking repository stayed alive for a feature holding **zero rows**. A
    saved query is a query, so it runs through the one the grid uses.

    The filter vocabulary is deliberately the grid's — folder, stars, sort —
    rather than the old resolver's twenty-odd parameters. Anything a saved
    query asked for beyond that is not silently ignored: `library.photos`
    refuses a sort it does not have, and an unsupported narrowing simply is not
    applied yet. Nothing here is exercised today; when collections are built
    for real, this is where their vocabulary grows.
    """

    import asyncio

    import library
    from core.catalog_path import catalog_path
    from data import connection

    def run() -> tuple[int, list[dict]]:
        conn = connection.inline_reader(catalog_path())
        total = library.counts(conn)["photos"]
        if limit <= 0:
            return total, []
        rows = library.photos(
            conn,
            folder=(query.get("folder") or None),
            sort={"elo": "best", "date_taken": "newest"}.get(query.get("sort", "elo"), "newest"),
            starred=(int(query.get("min_stars") or 0) or None),
            limit=limit,
            offset=offset,
        )
        return total, rows

    return await asyncio.to_thread(run)
