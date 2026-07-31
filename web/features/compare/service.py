"""Compare and mosaic cache/state helpers."""

import asyncio
import hashlib
import os
import time
from collections.abc import Awaitable, Callable

import elo_propagation
import helpers as app_helpers
import pairing
import settings
from core import query_constraints
from core import responses as response_helpers
from data.repositories.rankings import folder_cache_value
from features.compare import semantic_pairing
from features.library import taste as taste_service


_pairing_cache = {"data": None, "valid": False}
_matchups_cache = {"data": None, "valid": False}
_visible_matchups_cache: dict[str, dict] = {}
_visible_pairing_candidates_cache: dict[str, dict] = {}
_visible_pairing_candidates_refreshing: set[str] = set()
_visible_pairing_candidates_generation = 0
_disk_index_warming = False
_interaction_response_cache: dict[tuple, dict] = {}
_visible_pairing_candidates_cache_ttl_seconds = 15.0
_patched_pairing_candidates_ttl_seconds = 15.0
_interaction_response_cache_ttl_seconds = 600.0
# Picks and propagation patch reservoirs instead of clearing them, so the
# reservoir cache needs its own ceiling.
_VISIBLE_PAIRING_CANDIDATES_CACHE_MAX = 48
_COMPETE_PIVOT_BUCKET_ELO = 25.0
_SWISS_PAIR_WINDOW = 512
_FILTERED_SWISS_PAIR_WINDOW = 256
_FILTERED_MOSAIC_WINDOW = 192
_MOSAIC_EXPLORE_WINDOW = 768
# Scoped Refine (explicit ids / collection / import batch) samples from the whole
# selection, bounded so huge collections stay cheap; the shuffled tie-break covers
# any remainder statistically.
_SCOPED_MOSAIC_WINDOW_MAX = 5000
_MOSAIC_DIVERSE_WINDOW = 1536
# Random and compete draw a bounded window too: the user sees twelve tiles, and
# a full-universe reservoir measured 1107ms against 20ms for a bounded draw.
_MOSAIC_SAMPLE_WINDOW = 960
_DIRECT_UNCOMPARED_FILTER = "direct_uncompared"

_invalidate_rankings_cache: Callable[[], None] | None = None
_invalidate_interaction_response_cache: Callable[[], None] | None = None
_cache_root: Callable[[], str] | None = None
_resolve_library_constraints: Callable[..., object] | None = None
_schedule_thumbnail_prefetch: Callable[..., None] | None = None
_schedule_cached_thumbnail_memory_warm: Callable[..., None] | None = None
_db_signature: Callable[[], str] | None = None
_get_active_images_for_pairing: Callable[[], Awaitable[list]] | None = None
_get_past_matchups: Callable[[], Awaitable[set]] | None = None
_get_visible_past_matchups: Callable[..., Awaitable[set]] | None = None
_get_past_matchups_for_image_ids: Callable[[list[int]], Awaitable[set]] | None = None
_get_active_images_by_ids: Callable[[list[int]], Awaitable[dict[int, dict]]] | None = None
_get_visible_images_for_pairing: Callable[..., Awaitable[list]] | None = None
_get_visible_orientation_pairing_pool_counts: Callable[..., Awaitable[dict]] | None = None
_count_rankings: Callable[..., Awaitable[int]] | None = None
_get_rankings: Callable[..., Awaitable[list]] | None = None
_get_visible_pairing_pool_counts: Callable[..., Awaitable[dict]] | None = None
_get_top_images: Callable[..., Awaitable[list]] | None = None
_get_collection_image_ids: Callable[..., Awaitable[list[int] | None]] | None = None
_resolve_smart_collection_image_ids: Callable[[int], Awaitable[set[int] | None]] | None = None
_get_import_batch_image_ids: Callable[[int], Awaitable[set[int] | None]] | None = None


def configure(
    *,
    invalidate_rankings_cache: Callable[[], None],
    invalidate_interaction_response_cache: Callable[[], None],
    cache_root: Callable[[], str],
    resolve_library_constraints: Callable[..., object] | None = None,
    schedule_thumbnail_prefetch: Callable[..., None] | None = None,
    schedule_cached_thumbnail_memory_warm: Callable[..., None] | None = None,
    db_signature: Callable[[], str] | None = None,
    get_active_images_for_pairing: Callable[[], Awaitable[list]] | None = None,
    get_past_matchups: Callable[[], Awaitable[set]] | None = None,
    get_visible_past_matchups: Callable[..., Awaitable[set]] | None = None,
    get_past_matchups_for_image_ids: Callable[[list[int]], Awaitable[set]] | None = None,
    get_active_images_by_ids: Callable[[list[int]], Awaitable[dict[int, dict]]] | None = None,
    get_visible_images_for_pairing: Callable[..., Awaitable[list]] | None = None,
    get_visible_orientation_pairing_pool_counts: Callable[..., Awaitable[dict]] | None = None,
    count_rankings: Callable[..., Awaitable[int]] | None = None,
    get_rankings: Callable[..., Awaitable[list]] | None = None,
    get_visible_pairing_pool_counts: Callable[..., Awaitable[dict]] | None = None,
    get_top_images: Callable[..., Awaitable[list]] | None = None,
    get_collection_image_ids: Callable[..., Awaitable[list[int] | None]] | None = None,
    resolve_smart_collection_image_ids: Callable[[int], Awaitable[set[int] | None]] | None = None,
    get_import_batch_image_ids: Callable[[int], Awaitable[set[int] | None]] | None = None,
) -> None:
    global _invalidate_rankings_cache, _invalidate_interaction_response_cache, _cache_root
    global _resolve_library_constraints, _schedule_thumbnail_prefetch
    global _schedule_cached_thumbnail_memory_warm
    global _db_signature, _get_active_images_for_pairing, _get_past_matchups
    global _get_visible_past_matchups, _get_past_matchups_for_image_ids
    global _get_active_images_by_ids, _get_visible_images_for_pairing
    global _get_visible_orientation_pairing_pool_counts, _count_rankings
    global _get_rankings, _get_visible_pairing_pool_counts, _get_top_images
    global _get_collection_image_ids, _resolve_smart_collection_image_ids, _get_import_batch_image_ids
    _invalidate_rankings_cache = invalidate_rankings_cache
    _invalidate_interaction_response_cache = invalidate_interaction_response_cache
    _cache_root = cache_root
    if resolve_library_constraints is not None:
        _resolve_library_constraints = resolve_library_constraints
    if schedule_thumbnail_prefetch is not None:
        _schedule_thumbnail_prefetch = schedule_thumbnail_prefetch
    if schedule_cached_thumbnail_memory_warm is not None:
        _schedule_cached_thumbnail_memory_warm = schedule_cached_thumbnail_memory_warm
    if db_signature is not None:
        _db_signature = db_signature
    if get_active_images_for_pairing is not None:
        _get_active_images_for_pairing = get_active_images_for_pairing
    if get_past_matchups is not None:
        _get_past_matchups = get_past_matchups
    if get_visible_past_matchups is not None:
        _get_visible_past_matchups = get_visible_past_matchups
    if get_past_matchups_for_image_ids is not None:
        _get_past_matchups_for_image_ids = get_past_matchups_for_image_ids
    if get_active_images_by_ids is not None:
        _get_active_images_by_ids = get_active_images_by_ids
    if get_visible_images_for_pairing is not None:
        _get_visible_images_for_pairing = get_visible_images_for_pairing
    if get_visible_orientation_pairing_pool_counts is not None:
        _get_visible_orientation_pairing_pool_counts = get_visible_orientation_pairing_pool_counts
    if count_rankings is not None:
        _count_rankings = count_rankings
    if get_rankings is not None:
        _get_rankings = get_rankings
    if get_visible_pairing_pool_counts is not None:
        _get_visible_pairing_pool_counts = get_visible_pairing_pool_counts
    if get_top_images is not None:
        _get_top_images = get_top_images
    if get_collection_image_ids is not None:
        _get_collection_image_ids = get_collection_image_ids
    if resolve_smart_collection_image_ids is not None:
        _resolve_smart_collection_image_ids = resolve_smart_collection_image_ids
    if get_import_batch_image_ids is not None:
        _get_import_batch_image_ids = get_import_batch_image_ids


def _configured(provider):
    if provider is None:
        raise RuntimeError("Compare service is not configured")
    return provider


def _configured_db_signature() -> str:
    return _configured(_db_signature)()


def _configured_cache_root() -> str:
    if _cache_root is None:
        raise RuntimeError("Compare service is not configured")
    return _cache_root()


async def _configured_resolve_library_constraints(q: str, *, people: str = "", deep: bool = False) -> dict:
    if _resolve_library_constraints is None:
        raise RuntimeError("Compare service is not configured")
    return await _resolve_library_constraints(q, people=people, deep=deep)


async def _scoped_search(
    search: dict,
    ids: list[int] | None,
    collection_id: int = 0,
    import_batch: int = 0,
) -> dict:
    scoped_ids = (
        None
        if ids is None
        else {int(image_id) for image_id in ids if int(image_id) > 0}
    )
    if collection_id and collection_id > 0:
        collection_ids = await _configured(_resolve_smart_collection_image_ids)(int(collection_id))
        if collection_ids is None:
            collection_ids = await _configured(_get_collection_image_ids)(int(collection_id))
        collection_scope = {int(image_id) for image_id in collection_ids or []}
        scoped_ids = collection_scope if scoped_ids is None else scoped_ids.intersection(collection_scope)
    if import_batch and import_batch > 0:
        batch_ids = await _configured(_get_import_batch_image_ids)(int(import_batch))
        batch_scope = {int(image_id) for image_id in batch_ids or []}
        scoped_ids = batch_scope if scoped_ids is None else scoped_ids.intersection(batch_scope)
    if scoped_ids is None:
        return search
    scoped = dict(search)
    current_filter = scoped.get("id_filter")
    if current_filter is None:
        scoped["id_filter"] = scoped_ids
    else:
        scoped["id_filter"] = {int(image_id) for image_id in current_filter}.intersection(scoped_ids)
    return scoped


def _configured_schedule_thumbnail_prefetch(rows, size: str, *, limit: int) -> None:
    if _schedule_thumbnail_prefetch is not None:
        _schedule_thumbnail_prefetch(rows, size, limit=limit)


def _configured_schedule_cached_thumbnail_memory_warm(rows, size: str, *, limit: int) -> None:
    if _schedule_cached_thumbnail_memory_warm is not None:
        _schedule_cached_thumbnail_memory_warm(rows, size, limit=limit)


def _invalidate_rankings() -> None:
    taste_service.invalidate_taste_cache()
    if _invalidate_rankings_cache is not None:
        _invalidate_rankings_cache()


def invalidate_interaction_response_cache() -> None:
    _interaction_response_cache.clear()


async def get_pairing_images(size: str):
    """Return a bounded visible reservoir instead of materializing the archive."""
    return await default_visible_pairing_candidates(
        size,
        limit=_MOSAIC_DIVERSE_WINDOW,
        order="elo",
        include_card_metadata=True,
    )


def invalidate_pairing_cache(*, matchups: bool = False) -> None:
    global _visible_pairing_candidates_generation
    _pairing_cache["valid"] = False
    _visible_pairing_candidates_cache.clear()
    _visible_pairing_candidates_refreshing.clear()
    _visible_pairing_candidates_generation += 1
    _invalidate_rankings()
    invalidate_interaction_response_cache()
    if matchups:
        _matchups_cache["valid"] = False
        _visible_matchups_cache.clear()


async def get_past_matchups():
    if _matchups_cache["valid"] and _matchups_cache["data"] is not None:
        return _matchups_cache["data"]
    matchups = await _configured(_get_past_matchups)()
    _matchups_cache["data"] = matchups
    _matchups_cache["valid"] = True
    return matchups


async def get_visible_past_matchups(size: str):
    cache_root = _configured_cache_root()
    cache_key = f"{_configured_db_signature()}:{cache_root}:{size}"
    cached = _visible_matchups_cache.get(cache_key)
    if cached is not None:
        return cached["data"]
    matchups = await _configured(_get_visible_past_matchups)(size, cache_root)
    _visible_matchups_cache[cache_key] = {"data": matchups}
    return matchups


async def get_past_matchups_for_candidate_ids(size: str, image_ids: list[int]):
    unique_ids = tuple(dict.fromkeys(int(image_id) for image_id in image_ids or [] if int(image_id) > 0))
    if len(unique_ids) < 2:
        return set()
    id_digest = hashlib.blake2b(
        ",".join(str(image_id) for image_id in unique_ids).encode("ascii"),
        digest_size=12,
    ).hexdigest()
    cache_key = (
        f"{_configured_db_signature()}:{_configured_cache_root()}:"
        f"{size}:candidates:{len(unique_ids)}:{id_digest}"
    )
    cached = _visible_matchups_cache.get(cache_key)
    if cached is not None:
        return cached["data"]
    matchups = await _configured(_get_past_matchups_for_image_ids)(list(unique_ids))
    _visible_matchups_cache[cache_key] = {"data": matchups}
    return matchups


def add_past_matchups(pairs: list[tuple[int, int]]) -> None:
    invalidate_interaction_response_cache()
    normalized_pairs = [(min(a, b), max(a, b)) for a, b in pairs]
    if _matchups_cache["valid"] and _matchups_cache["data"] is not None:
        _matchups_cache["data"].update(normalized_pairs)
    for cached in _visible_matchups_cache.values():
        cached["data"].update(normalized_pairs)


def _pairing_candidates_entry(rows: list, **extra) -> dict:
    return {
        "data": rows,
        # id -> row position, so patching a twelve-image pick stays O(12)
        # instead of walking every reservoir row and rebuilding a six-figure
        # id set on the acknowledged pick path.
        "index_by_id": {int(row["id"]): position for position, row in enumerate(rows)},
        "expires": time.monotonic() + _visible_pairing_candidates_cache_ttl_seconds,
        **extra,
    }


def _store_pairing_candidates(cache_key: str, rows: list, **extra) -> None:
    _visible_pairing_candidates_cache[cache_key] = _pairing_candidates_entry(rows, **extra)
    overflow = len(_visible_pairing_candidates_cache) - _VISIBLE_PAIRING_CANDIDATES_CACHE_MAX
    if overflow <= 0:
        return
    stale_first = sorted(
        _visible_pairing_candidates_cache.items(),
        key=lambda item: item[1].get("expires") or 0.0,
    )
    for stale_key, _entry in stale_first[:overflow]:
        if stale_key == cache_key or stale_key in _visible_pairing_candidates_refreshing:
            continue
        _visible_pairing_candidates_cache.pop(stale_key, None)


def _patch_cached_reservoir_rows(update_map: dict, patch_row) -> None:
    """Apply row patches in place at the mapped positions.

    Every cached reservoir keeps an id -> position map, so a pick or a
    propagation batch touches only its own rows. Readers hold the same list and
    only read from it, so replacing elements in place is safe and keeps the
    patch off the O(reservoir) path.
    """
    now = time.monotonic()
    for cached in list(_visible_pairing_candidates_cache.values()):
        rows = cached.get("data")
        positions = cached.get("index_by_id")
        if rows is None or positions is None:
            continue
        patched = False
        for image_id in update_map:
            position = positions.get(image_id)
            if position is None:
                continue
            rows[position] = patch_row(rows[position], image_id)
            patched = True
        if patched:
            cached["expires"] = now + _patched_pairing_candidates_ttl_seconds


def patch_pairing_cache(updates: list[tuple[int, float, int]]) -> None:
    _invalidate_rankings()
    invalidate_interaction_response_cache()
    update_map = {
        int(image_id): (float(elo), int(comparison_delta))
        for image_id, elo, comparison_delta in updates
    }
    if not update_map:
        _visible_pairing_candidates_cache.clear()
        _pairing_cache["valid"] = False
        return

    def _patched_row(row, image_id: int):
        new_elo, comparison_delta = update_map[image_id]
        row_dict = dict(row)
        row_dict["elo"] = new_elo
        row_dict["comparisons"] = int(row_dict.get("comparisons") or 0) + comparison_delta
        return row_dict

    if _pairing_cache["valid"] and _pairing_cache["data"] is not None:
        patched = []
        changed = False
        for row in _pairing_cache["data"]:
            try:
                image_id = int(row["id"])
            except (KeyError, TypeError, ValueError):
                patched.append(row)
                continue
            if image_id in update_map:
                patched.append(_patched_row(row, image_id))
                changed = True
            else:
                patched.append(row)
        if changed:
            _pairing_cache["data"] = patched
        else:
            _pairing_cache["valid"] = False

    _patch_cached_reservoir_rows(update_map, _patched_row)


def patch_propagated_pairing_cache(deltas: list[tuple[int, float]]) -> None:
    """Propagation touched these ids — patch them instead of clearing the cache.

    A pick schedules propagation, so a blanket clear on drain threw away the
    reservoir the same pick had just patched and made the next click fully cold.
    Deltas are applied relatively, exactly as propagation writes them, so a pick
    that landed in between is not undone in the cache.
    """
    update_map: dict[int, float] = {}
    for image_id, delta in deltas:
        image_id = int(image_id)
        update_map[image_id] = update_map.get(image_id, 0.0) + float(delta)
    if not update_map:
        return
    _invalidate_rankings()
    invalidate_interaction_response_cache()

    def _patched_row(row, image_id: int):
        row_dict = dict(row)
        row_dict["elo"] = float(row_dict.get("elo") or 1200.0) + update_map[image_id]
        row_dict["propagated_updates"] = int(row_dict.get("propagated_updates") or 0) + 1
        return row_dict

    _pairing_cache["valid"] = False
    _patch_cached_reservoir_rows(update_map, _patched_row)


async def filter_visible_candidates(candidates: list[dict], size: str) -> list[dict]:
    return await app_helpers.filter_visible_candidates(candidates, size, _configured_cache_root())


async def hydrate_active_rows(rows: list[dict]) -> list[dict]:
    if not rows:
        return []
    try:
        rows[0]["created_at"]
        return rows
    except (KeyError, IndexError, TypeError):
        pass
    by_id = await _configured(_get_active_images_by_ids)([row["id"] for row in rows])
    return [by_id.get(row["id"], row) for row in rows]


async def default_visible_pairing_candidates(
    size: str,
    *,
    copy_rows: bool = False,
    limit: int | None = None,
    order: str = "elo",
    include_card_metadata: bool | None = None,
    elo_pivot: float | None = None,
) -> list[dict]:
    cache_root = _configured_cache_root()
    normalized_limit = int(limit or 0)
    if include_card_metadata is None:
        include_card_metadata = size != "md"
    # Bucket the compete pivot so a drifting grid average reuses one reservoir
    # instead of minting a cache entry per request.
    pivot_bucket = (
        int(round(float(elo_pivot) / _COMPETE_PIVOT_BUCKET_ELO))
        if elo_pivot
        else 0
    )
    cache_key = (
        f"{_configured_db_signature()}:{cache_root}:{size}:"
        f"{normalized_limit}:{order}:{int(include_card_metadata)}:{pivot_bucket}"
    )
    now = time.monotonic()
    cached = _visible_pairing_candidates_cache.get(cache_key)
    if cached and cached["expires"] > now:
        rows = cached["data"]
    elif cached and cached.get("data") is not None:
        rows = cached["data"]
        if cache_key not in _visible_pairing_candidates_refreshing:
            _visible_pairing_candidates_refreshing.add(cache_key)
            refresh_generation = _visible_pairing_candidates_generation

            async def _refresh_visible_pairing_candidates():
                try:
                    refreshed = await _configured(_get_visible_images_for_pairing)(
                        size,
                        cache_root,
                        include_card_metadata=include_card_metadata,
                        limit=normalized_limit or None,
                        order=order,
                        elo_pivot=elo_pivot,
                    )
                    if refresh_generation != _visible_pairing_candidates_generation:
                        return
                    _store_pairing_candidates(cache_key, refreshed)
                except Exception:
                    pass
                finally:
                    _visible_pairing_candidates_refreshing.discard(cache_key)

            asyncio.create_task(_refresh_visible_pairing_candidates())
    else:
        rows = await _configured(_get_visible_images_for_pairing)(
            size,
            cache_root,
            include_card_metadata=include_card_metadata,
            limit=normalized_limit or None,
            order=order,
            elo_pivot=elo_pivot,
        )
        _store_pairing_candidates(cache_key, rows)
    if copy_rows:
        return [dict(row) for row in rows]
    return rows


async def filtered_visible_ranked_candidates(
    size: str,
    *,
    limit: int,
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
    exclude_sources=(),
) -> tuple[list[dict], int, int]:
    cache_root = _configured_cache_root()
    cache_key = (
        f"filtered:{_configured_db_signature()}:{cache_root}:{size}:{max(1, int(limit))}:"
        f"{sort}:{orientation}:{compared}:{int(min_stars or 0)}:{folder_cache_value(folder)}:{flag}:"
        f"{date_taken}:{file_type}:{camera}:{lens}:{tag}:{tuple(exclude_sources or ())}"
    )
    now = time.monotonic()
    cached = _visible_pairing_candidates_cache.get(cache_key)
    if cached and cached["expires"] > now:
        return (
            cached["data"],
            int(cached.get("filtered_total") or len(cached["data"])),
            int(cached.get("visible_count") or len(cached["data"])),
        )
    if cached and cached.get("data") is not None:
        if cache_key not in _visible_pairing_candidates_refreshing:
            _visible_pairing_candidates_refreshing.add(cache_key)
            refresh_generation = _visible_pairing_candidates_generation

            async def _refresh_filtered_visible_ranked_candidates():
                try:
                    refreshed_rows, refreshed_total, refreshed_visible = (
                        await load_filtered_visible_ranked_candidates(
                            size,
                            limit=limit,
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
            exclude_sources=exclude_sources,)
                    )
                    if refresh_generation != _visible_pairing_candidates_generation:
                        return
                    _store_pairing_candidates(
                        cache_key,
                        refreshed_rows,
                        filtered_total=int(refreshed_total),
                        visible_count=int(refreshed_visible),
                    )
                except Exception:
                    pass
                finally:
                    _visible_pairing_candidates_refreshing.discard(cache_key)

            asyncio.create_task(_refresh_filtered_visible_ranked_candidates())
        return (
            cached["data"],
            int(cached.get("filtered_total") or len(cached["data"])),
            int(cached.get("visible_count") or len(cached["data"])),
        )

    result_rows, filtered_total, visible_count = await load_filtered_visible_ranked_candidates(
        size,
        limit=limit,
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
            exclude_sources=exclude_sources,)
    _store_pairing_candidates(
        cache_key,
        result_rows,
        filtered_total=int(filtered_total),
        visible_count=int(visible_count),
    )
    return result_rows, int(filtered_total), int(visible_count)


async def load_filtered_visible_ranked_candidates(
    size: str,
    *,
    limit: int,
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
    exclude_sources=(),
) -> tuple[list[dict], int, int]:
    cache_root = _configured_cache_root()
    normalized_limit = max(1, int(limit))
    orientation_only = bool(orientation) and not any(
        (
            compared,
            int(min_stars or 0),
            folder,
            flag,
            date_taken,
            file_type,
            camera,
            lens,
            tag,
            exclude_sources,
        )
    )
    if orientation_only:
        counts_task = asyncio.create_task(
            _configured(_get_visible_orientation_pairing_pool_counts)(size, cache_root, orientation)
        )
    else:
        filtered_total_task = asyncio.create_task(
            _configured(_count_rankings)(
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
                exclude_sources=exclude_sources,)
        )
        visible_count_task = asyncio.create_task(
            _configured(_count_rankings)(
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
                visible_thumb_size=size,
                cache_root=cache_root,
                exclude_sources=exclude_sources,)
        )
    rows = await _configured(_get_rankings)(
        limit=normalized_limit,
        offset=0,
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
        visible_thumb_size=size,
        cache_root=cache_root,
        exclude_sources=exclude_sources,)
    result_rows = [dict(row) for row in rows]
    if orientation_only:
        counts = await counts_task
        filtered_total = int(counts.get("active_images") or 0)
        visible_count = int(counts.get("visible_images") or 0)
    else:
        filtered_total = await filtered_total_task
        visible_count = await visible_count_task
    return result_rows, int(filtered_total), int(visible_count)


async def search_visible_ranked_candidates(
    size: str,
    *,
    limit: int,
    search: dict,
    sort: str = "elo",
    exclude_ids: set[int] | None = None,
    force_exact_counts: bool = False,
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
    exclude_sources=(),
) -> tuple[list[dict], int, int]:
    cache_root = _configured_cache_root()
    exclude_ids = exclude_ids or set()
    fetch_limit = max(1, int(limit)) + min(len(exclude_ids), 200)
    id_filter = search.get("id_filter")
    text_query = search.get("text_query") or ""
    exact_counts = bool(force_exact_counts) or not (text_query and id_filter is None)
    if exact_counts:
        total_task = asyncio.create_task(
            _configured(_count_rankings)(
                orientation=orientation, compared=compared, min_stars=min_stars,
                folder=folder, flag=flag, date_taken=date_taken, file_type=file_type,
                camera=camera, lens=lens,
                tag=tag,
                id_filter=id_filter,
                text_query=text_query,
                exclude_sources=exclude_sources,)
        )
        visible_task = asyncio.create_task(
            _configured(_count_rankings)(
                orientation=orientation, compared=compared, min_stars=min_stars,
                folder=folder, flag=flag, date_taken=date_taken, file_type=file_type,
                camera=camera, lens=lens,
                tag=tag,
                id_filter=id_filter,
                visible_thumb_size=size,
                cache_root=cache_root,
                text_query=text_query,
                exclude_sources=exclude_sources,)
        )
    else:
        total_task = None
        visible_task = None
    rows = await _configured(_get_rankings)(
        limit=fetch_limit,
        offset=0,
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
        id_filter=id_filter,
        visible_thumb_size=size,
        cache_root=cache_root,
        text_query=text_query,
        exclude_sources=exclude_sources,)
    result_rows = [
        dict(row)
        for row in rows
        if int(row["id"]) not in exclude_ids
    ][:max(1, int(limit))]
    if exact_counts:
        return result_rows, await total_task, await visible_task
    visible_count = len(result_rows)
    return result_rows, visible_count, visible_count


async def warm_filtered_visible_ranked_candidates(
    size: str,
    *,
    limit: int,
    orientation: str,
    warm_matchups: bool = False,
) -> None:
    try:
        rows, _filtered_total, _visible_count = await filtered_visible_ranked_candidates(
            size,
            limit=limit,
            orientation=orientation,
        )
        if warm_matchups:
            await get_past_matchups_for_candidate_ids(size, [row["id"] for row in rows])
    except Exception:
        pass


def candidate_value(candidate, key: str, default=None):
    if hasattr(candidate, "get"):
        return candidate.get(key, default)
    try:
        return candidate[key]
    except (KeyError, IndexError, TypeError):
        return default


def _candidate_id(candidate) -> int:
    return int(candidate_value(candidate, "id", 0) or 0)


def metadata_text_match(image: dict, query: str) -> bool:
    tokens = (query or "").strip().lower().split()
    if not tokens:
        return True
    fields = (
        "filename",
        "filepath",
        "date_taken",
        "camera_make",
        "camera_model",
        "lens",
        "file_ext",
    )
    # Every token must match at least one field, so multi-word queries like
    # "canon 85mm 2024" narrow the result instead of requiring one field to
    # contain the whole phrase.
    values = [str(candidate_value(image, field, "") or "").lower() for field in fields]
    return all(any(token in value for value in values) for token in tokens)


def apply_text_search_constraint(candidates: list[dict], search: dict) -> list[dict]:
    if not search.get("active"):
        return candidates
    id_filter = search.get("id_filter")
    if id_filter is not None:
        search_ids = {int(image_id) for image_id in id_filter}
        return [c for c in candidates if int(candidate_value(c, "id", 0) or 0) in search_ids]
    text_query = search.get("text_query") or ""
    return [c for c in candidates if metadata_text_match(c, text_query)]


def apply_tag_constraint(candidates: list[dict], tag: str = "") -> list[dict]:
    normalized = (tag or "").strip().lower()
    if not normalized:
        return candidates
    return [
        c for c in candidates
        if normalized in {
            str(value or "").strip().lower()
            for value in (candidate_value(c, "caption_tags", []) or [])
        }
    ]


async def add_explore_uncompared_stats(
    stats: dict,
    *,
    strategy: str,
    size: str,
    filtered_total: int,
    visible_count: int,
    search: dict,
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
) -> dict:
    """Attach direct user-uncompared pool counts for Explore mode."""
    if strategy != "explore" or compared:
        return stats

    count_kwargs = {
        "orientation": orientation,
        "compared": _DIRECT_UNCOMPARED_FILTER,
        "min_stars": min_stars,
        "folder": folder,
        "flag": flag,
        "date_taken": date_taken,
        "file_type": file_type,
        "camera": camera,
        "lens": lens,
        "tag": tag,
        "id_filter": search.get("id_filter"),
        "text_query": search.get("text_query") or "",
    }
    cache_root = _configured_cache_root()
    total_task = asyncio.create_task(_configured(_count_rankings)(**count_kwargs))
    visible_task = asyncio.create_task(
        _configured(_count_rankings)(
            **count_kwargs,
            visible_thumb_size=size,
            cache_root=cache_root,
        )
    )
    direct_total = await total_task
    direct_visible = await visible_task
    stats.update({
        "pool_metric": "direct_uncompared",
        "direct_uncompared_total": int(direct_total),
        "direct_uncompared_visible": int(direct_visible),
        "direct_uncompared_pool_total": int(filtered_total or 0),
        "direct_uncompared_pool_visible": int(visible_count or 0),
    })
    return stats


def lowest_comparison_candidate_pool(candidates: list[dict], count: int) -> list[dict]:
    if len(candidates) <= count:
        return candidates
    target_size = min(len(candidates), max(1, int(count)))
    sorted_candidates = sorted(
        candidates,
        key=lambda img: (
            int(candidate_value(img, "comparisons", 0) or 0),
            int(candidate_value(img, "propagated_updates", 0) or 0),
            -float(candidate_value(img, "elo", 1200.0) or 1200.0),
            int(candidate_value(img, "id", 0) or 0),
        ),
    )
    cutoff = int(candidate_value(sorted_candidates[target_size - 1], "comparisons", 0) or 0)
    return [
        img for img in sorted_candidates
        if int(candidate_value(img, "comparisons", 0) or 0) <= cutoff
    ]


def has_candidate_filters(
    *,
    exclude_ids: set[int] | None = None,
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
    search: dict | None = None,
    exclude_sources=(),
) -> bool:
    return bool(
        exclude_ids
        or orientation
        or compared
        or min_stars > 0
        or folder
        or flag
        or date_taken
        or file_type
        or camera
        or lens
        or tag
        or exclude_sources
        or query_constraints.search_constraint_active(search)
    )


async def diverse_sample(candidates: list[dict], count: int) -> list[dict]:
    """Select images that maximize visual diversity using embedding distance."""
    import random
    if len(candidates) <= count:
        return candidates

    try:
        import numpy as np
        import embed_cache

        model_key = elo_propagation.compare_embedding_model_key()
        image_ids, matrix = await embed_cache.get_matrix(model_key)
        id_to_idx = embed_cache.get_index(model_key)
        if image_ids is None:
            image_ids, matrix = embed_cache.get_warm_matrix()
            if image_ids is None:
                return random.sample(candidates, min(count, len(candidates)))
            id_to_idx = embed_cache.get_index()

        # Bound the expensive per-candidate work for large libraries. The final
        # diversity pool is only 500 images, so a 5k search window keeps the same
        # broad random/exploratory behavior without building arrays over 100k+ rows.
        search_pool = max(5000, count * 40)
        search_candidates = candidates
        if len(candidates) > search_pool:
            search_candidates = random.sample(candidates, search_pool)

        cand_indices = []
        cand_items = []
        without_emb = []
        for candidate in search_candidates:
            idx = id_to_idx.get(_candidate_id(candidate))
            if idx is not None:
                cand_indices.append(idx)
                cand_items.append(candidate)
            else:
                without_emb.append(candidate)

        if len(cand_items) < count and search_candidates is not candidates:
            seen = {_candidate_id(candidate) for candidate in search_candidates}
            for candidate in candidates:
                if _candidate_id(candidate) in seen:
                    continue
                idx = id_to_idx.get(_candidate_id(candidate))
                if idx is not None:
                    cand_indices.append(idx)
                    cand_items.append(candidate)
                else:
                    without_emb.append(candidate)
                if len(cand_items) >= count:
                    break

        if len(cand_items) < count:
            sample = list(cand_items)
            remaining = count - len(sample)
            if without_emb and remaining > 0:
                sample.extend(random.sample(without_emb, min(remaining, len(without_emb))))
            return sample

        pool = min(max(count * 16, 96), 192, len(cand_items))
        comp_counts = np.fromiter(
            (candidate_value(candidate, "comparisons", 0) or 0 for candidate in cand_items),
            dtype=np.float32,
            count=len(cand_items),
        )

        if len(cand_items) > pool:
            bucket_defs = (
                comp_counts == 0,
                (comp_counts > 0) & (comp_counts <= 2),
                (comp_counts > 2) & (comp_counts <= 5),
                (comp_counts > 5) & (comp_counts <= 10),
                comp_counts > 10,
            )
            bucket_indices = [np.flatnonzero(mask) for mask in bucket_defs]
            bucket_weights = np.array(
                [
                    float((1.0 / (comp_counts[idx] + 1.0)).sum()) if len(idx) else 0.0
                    for idx in bucket_indices
                ],
                dtype=np.float64,
            )

            if bucket_weights.sum() > 0:
                raw_quotas = bucket_weights / bucket_weights.sum() * pool
                quotas = np.minimum(
                    np.floor(raw_quotas).astype(int),
                    [len(idx) for idx in bucket_indices],
                )
                remaining = pool - int(quotas.sum())
                fractions = raw_quotas - np.floor(raw_quotas)
                for bucket in np.argsort(fractions)[::-1]:
                    if remaining <= 0:
                        break
                    capacity = len(bucket_indices[bucket]) - quotas[bucket]
                    if capacity <= 0:
                        continue
                    take = min(remaining, capacity)
                    quotas[bucket] += take
                    remaining -= take

                selected_idx = []
                for idx, quota in zip(bucket_indices, quotas):
                    if quota <= 0:
                        continue
                    selected_idx.extend(random.sample(idx.tolist(), int(quota)))

                if len(selected_idx) < pool:
                    selected_set = set(selected_idx)
                    remaining_idx = [i for i in range(len(cand_items)) if i not in selected_set]
                    selected_idx.extend(random.sample(remaining_idx, pool - len(selected_idx)))
                pool_idx = np.array(selected_idx, dtype=np.intp)
                np.random.shuffle(pool_idx)
            else:
                pool_idx = np.array(random.sample(range(len(cand_items)), pool), dtype=np.intp)
        else:
            pool_idx = np.arange(len(cand_items))

        pool_matrix_idx = np.fromiter(
            (cand_indices[int(i)] for i in pool_idx),
            dtype=np.intp,
            count=len(pool_idx),
        )
        pool_matrix = matrix[pool_matrix_idx]
        pool_items = [cand_items[int(i)] for i in pool_idx]
        pool_bias = 1.0 / (comp_counts[pool_idx] + 1.0)

        neighbor_cell_limit = min(
            semantic_pairing.MOSAIC_DIVERSE_NEIGHBOR_CELL_LIMIT,
            max(0, count - 1),
        )

        def neighbor_cell_count(selected_indices: list[int]) -> int:
            if len(selected_indices) < 2:
                return 0
            selected_matrix = pool_matrix[selected_indices]
            similarities = selected_matrix @ selected_matrix.T
            seen = set()
            for left in range(len(selected_indices)):
                for right in range(left + 1, len(selected_indices)):
                    if similarities[left, right] > semantic_pairing.MOSAIC_NEIGHBOR_THRESHOLD:
                        seen.add(left)
                        seen.add(right)
            return len(seen)

        def acceptable_pick(selected_indices: list[int], pick: int) -> bool:
            if not selected_indices:
                return True
            similarities = pool_matrix[selected_indices] @ pool_matrix[pick]
            if float(similarities.max()) >= semantic_pairing.MOSAIC_DIVERSE_SPREAD_THRESHOLD:
                return False
            return neighbor_cell_count([*selected_indices, pick]) <= neighbor_cell_limit

        first = random.randrange(len(pool_items))
        selected = [first]
        max_sim = pool_matrix @ pool_matrix[first]

        for _ in range(count - 1):
            max_sim[selected[-1]] = 999.0
            score = max_sim - pool_bias * 0.15
            ordered = np.argsort(score)
            next_pick = None
            for candidate_idx in ordered:
                candidate_pick = int(candidate_idx)
                if candidate_pick in selected:
                    continue
                if acceptable_pick(selected, candidate_pick):
                    next_pick = candidate_pick
                    break
            if next_pick is None:
                for candidate_idx in ordered:
                    candidate_pick = int(candidate_idx)
                    if candidate_pick not in selected:
                        next_pick = candidate_pick
                        break
            if next_pick is None:
                break
            selected.append(next_pick)
            new_sims = pool_matrix @ pool_matrix[next_pick]
            np.maximum(max_sim, new_sims, out=max_sim)

        return [pool_items[i] for i in selected]

    except Exception:
        import random
        return random.sample(candidates, min(count, len(candidates)))


def _effective_elo(img) -> float:
    return candidate_value(img, "elo", 1200.0) or 1200.0


def _strategy_pool_and_weights(
    candidates: list[dict],
    count: int,
    *,
    strategy: str,
    grid_elo: float = 0,
) -> tuple[list[dict], list[float]]:
    if strategy == "explore":
        pool = lowest_comparison_candidate_pool(candidates, count)
        weights = [1.0 / (int(candidate_value(img, "comparisons", 0) or 0) + 1) for img in pool]
    elif strategy == "compete" and grid_elo > 0:
        pool = candidates
        weights = [1.0 / (abs(_effective_elo(img) - grid_elo) + 50) for img in pool]
    elif strategy == "top":
        pool = candidates
        weights = [_effective_elo(img) for img in pool]
    else:
        pool = candidates
        weights = [1.0 for _ in pool]
    return pool, weights


def _strategy_scores(candidates: list[dict], weights: list[float]) -> dict[int, float]:
    return {
        _candidate_id(candidate): max(0.0, float(weight))
        for candidate, weight in zip(candidates, weights)
    }


def _weighted_unique_sample(candidates: list[dict], weights: list[float], count: int) -> list[dict]:
    import random

    draw_count = min(len(candidates), max(count * 4, count))
    sample = []
    seen_ids = set()
    for img in random.choices(candidates, weights=weights, k=draw_count):
        image_id = _candidate_id(img)
        if image_id in seen_ids:
            continue
        sample.append(img)
        seen_ids.add(image_id)
        if len(sample) >= count:
            break
    if len(sample) < count:
        remaining = [img for img in candidates if _candidate_id(img) not in seen_ids]
        sample.extend(random.sample(remaining, min(count - len(sample), len(remaining))))
    return sample


async def _strategy_sample(
    candidates: list[dict],
    count: int,
    *,
    strategy: str,
    grid_elo: float = 0,
) -> list[dict]:
    if strategy == "diverse":
        return await diverse_sample(candidates, count)
    pool, weights = _strategy_pool_and_weights(candidates, count, strategy=strategy, grid_elo=grid_elo)
    return _weighted_unique_sample(pool, weights, count)


def _compete_semantic_tiebreak(
    sample: list[dict],
    candidates: list[dict],
    *,
    count: int,
    grid_elo: float,
    context,
) -> list[dict]:
    if not sample or context is None or grid_elo <= 0:
        return sample
    selected_by_id = {_candidate_id(candidate): candidate for candidate in sample}
    selected_ids = set(selected_by_id)
    target_elo = grid_elo
    best_score = {
        image_id: abs(_effective_elo(candidate) - target_elo)
        for image_id, candidate in selected_by_id.items()
    }
    # One batched pass per selected image, instead of a cosine call inside the
    # sort key for every candidate/selection pair.
    candidate_ids = [_candidate_id(candidate) for candidate in candidates]
    affinity: dict[int, float] = {}
    for sample_id in selected_ids:
        if not context.has(sample_id):
            continue
        for candidate_id, cosine in zip(
            candidate_ids, context.cosine_many(sample_id, candidate_ids)
        ):
            if cosine is None:
                continue
            affinity[candidate_id] = affinity.get(candidate_id, 0.0) + max(0.0, cosine)
    ordered_pool = sorted(
        candidates,
        key=lambda img: (
            abs(_effective_elo(img) - target_elo),
            -affinity.get(_candidate_id(img), 0.0),
            _candidate_id(img),
        ),
    )
    if len(ordered_pool) <= count:
        return ordered_pool

    for candidate in ordered_pool:
        candidate_id = _candidate_id(candidate)
        if candidate_id in selected_ids:
            continue
        candidate_score = abs(_effective_elo(candidate) - target_elo)
        replace_id = None
        for selected_id in selected_ids:
            if candidate_score == best_score[selected_id]:
                replace_id = selected_id
                break
        if replace_id is None:
            continue
        selected_ids.remove(replace_id)
        selected_ids.add(candidate_id)
        selected_by_id.pop(replace_id, None)
        selected_by_id[candidate_id] = candidate
        best_score.pop(replace_id, None)
        best_score[candidate_id] = candidate_score

    return sorted(
        (selected_by_id[image_id] for image_id in selected_ids),
        key=lambda img: (abs(_effective_elo(img) - target_elo), _candidate_id(img)),
    )[:count]


# A duel is unreadable when its two frames differ wildly in shape, so pairing
# holds partners inside a narrow aspect band and only widens when the pool
# cannot supply one.
DUEL_ASPECT_TOLERANCE = 0.15


def _candidate_aspect(candidate) -> float | None:
    aspect = candidate_value(candidate, "aspect_ratio")
    if not aspect:
        width = candidate_value(candidate, "width", 0) or 0
        height = candidate_value(candidate, "height", 0) or 0
        if width and height:
            aspect = width / height
    try:
        aspect = float(aspect)
    except (TypeError, ValueError):
        return None
    return aspect if aspect > 0 else None


def duel_aspect_pool(seed, candidates: list[dict]) -> list[dict]:
    """Aspect-compatible duel partners for the seed.

    Tolerance band first (ratios within DUEL_ASPECT_TOLERANCE of each other),
    then orientation as the coarse gate, then the full pool, so a thin scope
    still duels instead of starving.
    """
    seed_aspect = _candidate_aspect(seed)
    if seed_aspect is None:
        return candidates
    banded = []
    same_orientation = []
    for candidate in candidates:
        aspect = _candidate_aspect(candidate)
        if aspect is None:
            continue
        if max(aspect, seed_aspect) / min(aspect, seed_aspect) <= 1.0 + DUEL_ASPECT_TOLERANCE:
            banded.append(candidate)
        elif (aspect >= 1.0) == (seed_aspect >= 1.0):
            same_orientation.append(candidate)
    return banded or same_orientation or candidates


def _aspect_align_duel(sample: list[dict], candidates: list[dict]) -> list[dict]:
    """Repair a strategy-drawn duel whose two images mismatch in aspect."""
    if len(sample) != 2:
        return sample
    import random

    seed = sample[0]
    pool = duel_aspect_pool(seed, candidates)
    pool_ids = {_candidate_id(img) for img in pool}
    if _candidate_id(sample[1]) in pool_ids:
        return sample
    replacements = [img for img in pool if _candidate_id(img) != _candidate_id(seed)]
    if not replacements:
        return sample
    return [seed, random.choice(replacements)]


async def _semantic_context_for(candidates: list[dict]):
    if not settings.get_settings().get("refine_semantic_pairing", True):
        return None
    model_key = elo_propagation.compare_embedding_model_key()
    return await semantic_pairing.load_context(candidates, model_key)


async def _semantic_duel_sample(
    candidates: list[dict],
    count: int,
    *,
    strategy: str,
    grid_elo: float,
    context,
) -> tuple[list[dict], str]:
    import random

    if count != 2 or len(candidates) < 2:
        return [], "strategy"
    if random.random() < semantic_pairing.SEMANTIC_DUEL_EXPLORATION_RATE:
        sample = await _strategy_sample(candidates, count, strategy=strategy, grid_elo=grid_elo)
        return _aspect_align_duel(sample, candidates), "strategy"

    if strategy == "diverse":
        seed_sample = await diverse_sample(candidates, 1)
        score_pool, score_weights = _strategy_pool_and_weights(candidates, count, strategy="random")
    else:
        score_pool, score_weights = _strategy_pool_and_weights(
            candidates,
            count,
            strategy=strategy,
            grid_elo=grid_elo,
        )
        seed_sample = _weighted_unique_sample(score_pool, score_weights, 1)
    if not seed_sample:
        return [], "strategy"
    seed = seed_sample[0]
    strategy_scores = _strategy_scores(score_pool, score_weights)
    # The partner search reads one embedding row per candidate, so look at a
    # pre-shuffled window rather than the whole scope. Aspect gating happens
    # here, in pairing, so every partner path below inherits it.
    partner_pool = duel_aspect_pool(seed, candidates)
    if len(partner_pool) > semantic_pairing.SEMANTIC_PARTNER_WINDOW:
        partner_pool = random.sample(partner_pool, semantic_pairing.SEMANTIC_PARTNER_WINDOW)
    if strategy == "diverse":
        # Diverse duels feed Elo propagation across clusters, so the partner
        # is the least similar image the pool offers, not the most.
        partner = semantic_pairing.most_different_partner(seed, partner_pool, strategy_scores, context)
    else:
        partner = semantic_pairing.best_partner(seed, partner_pool, strategy_scores, context)
    if partner is None:
        remaining = [img for img in partner_pool if int(img["id"]) != int(seed["id"])]
        if not remaining:
            remaining = [img for img in candidates if int(img["id"]) != int(seed["id"])]
        remaining_weights = [strategy_scores.get(int(img["id"]), 1.0) for img in remaining]
        fallback = _weighted_unique_sample(remaining, remaining_weights, 1)
        if not fallback:
            return [seed], "strategy"
        return [seed, fallback[0]], "strategy"
    return [seed, partner], "semantic"


async def _refine_sample(
    candidates: list[dict],
    count: int,
    *,
    strategy: str,
    grid_elo: float = 0,
) -> tuple[list[dict], str]:
    if count == 2:
        context = await _semantic_context_for(candidates)
        if context is not None:
            sample, pairing_mode = await _semantic_duel_sample(
                candidates,
                count,
                strategy=strategy,
                grid_elo=grid_elo,
                context=context,
            )
            if len(sample) >= count:
                return sample, pairing_mode
    sample = await _strategy_sample(candidates, count, strategy=strategy, grid_elo=grid_elo)
    if strategy == "compete":
        context = await _semantic_context_for(candidates)
        sample = _compete_semantic_tiebreak(
            sample,
            candidates,
            count=count,
            grid_elo=grid_elo,
            context=context,
        )
    if count == 2:
        sample = _aspect_align_duel(sample, candidates)
    return sample, "strategy"


def _mosaic_pool_tier(n: int = 0) -> str:
    """Gate the pool on the tier the client will actually paint.

    A candidate whose tile would paint blank must not be offered, but gating
    everything on md excluded every sm-only image for no visual gain: a wave of
    six or more tiles renders each photo a few hundred pixels tall, where the
    400px sm preview is already more than the tile can show. Small waves (2x2,
    duel) do render large, so they keep the md gate. The client picks its tier
    from the same wave size in refine.js thumbTier().

    Rollback override: AZIMUTH_REFINE_VISIBLE_TIER=md pins the old behaviour.
    """
    override = os.environ.get("AZIMUTH_REFINE_VISIBLE_TIER", "").strip().lower()
    if override in ("sm", "md"):
        return override
    return "sm" if n > 4 else "md"


def _schedule_disk_index_warm(thumbnails) -> None:
    """Build the cache path index in the background, once per gap."""
    global _disk_index_warming
    if _disk_index_warming:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return

    async def _warm_disk_index():
        global _disk_index_warming
        try:
            await thumbnails.warm_disk_path_index()
        except Exception:
            pass
        finally:
            _disk_index_warming = False

    _disk_index_warming = True
    loop.create_task(_warm_disk_index())


def _tier_file_exists(tier: str, image_id: int) -> bool:
    try:
        import thumbnails

        if not thumbnails.disk_index_ready():
            # Building the index reads every cache_entries row for this cache
            # root — 771ms measured on a 240k-row catalog. Warm it off the loop
            # and fail open until it lands, the same as any other
            # infrastructure gap.
            _schedule_disk_index_warm(thumbnails)
            return True
        return bool(thumbnails.fast_disk_has(tier, image_id))
    except Exception:
        # Fail open only on infrastructure errors — never for a known-missing file.
        return True


async def _servable_sample(
    rows: list[dict],
    candidates: list[dict],
    count: int,
    tier: str,
    exclude_ids,
) -> list[dict]:
    """Admit-time truth gate + refill: drop ids whose render-tier file is
    missing (phantom cache row — fast_disk_has self-heals the index), then top
    back up from the already-gated candidate reservoir so the grid never
    shrinks. Returns an honest short set only if the reservoir runs dry."""
    kept = [row for row in rows if _tier_file_exists(tier, int(row["id"]))]
    if len(kept) >= count or not candidates:
        return kept
    used = {int(row["id"]) for row in rows} | set(exclude_ids or ())
    refill = []
    for cand in candidates:
        if len(kept) + len(refill) >= count:
            break
        cand_id = int(cand["id"])
        if cand_id in used:
            continue
        used.add(cand_id)
        if _tier_file_exists(tier, cand_id):
            refill.append(cand)
    if refill:
        kept.extend(await hydrate_active_rows(refill))
    return kept


async def mosaic_next_impl(
    n: int = 12, exclude: str = "", strategy: str = "explore", grid_elo: float = 0,
    orientation: str = "", compared: str = "", min_stars: int = 0, folder: str = "",
    flag: str = "", date_taken: str = "", file_type: str = "", camera: str = "", lens: str = "",
    tag: str = "", q: str = "", deep: bool = False, people: str = "", ids: list[int] | None = None,
    collection_id: int = 0, import_batch: int = 0, exclude_sources=(),
):
    """Get active images for mosaic ranking with configurable sampling strategy."""
    pool_tier = _mosaic_pool_tier(n)
    candidate_source = "mosaic_window"
    cache_hit = False
    counts_stale = False
    exclude_ids = set()
    if exclude:
        exclude_ids = {int(x) for x in exclude.split(",") if x.strip().isdigit()}
    search = await _configured_resolve_library_constraints(q, people=people, deep=deep)
    search = await _scoped_search(search, ids, collection_id, import_batch)
    default_pool_only = not has_candidate_filters(
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
        search=search,
            exclude_sources=exclude_sources,)
    response_cache_key = None
    if default_pool_only and not exclude_ids and strategy == "explore" and int(n) != 2:
        response_cache_key = (
            "mosaic_next",
            _configured_db_signature(),
            _configured_cache_root(),
            int(n),
            strategy,
            pool_tier,
        )
        cached_response = _interaction_response_cache.get(response_cache_key)
        if cached_response and cached_response["expires"] > time.monotonic():
            response = response_helpers.copy_interaction_response(cached_response["data"])
            cached_images = response.get("images") or []
            # Re-run the admit-time gate on hits: the TTL is minutes, and a
            # tile file can vanish (reclaim) after the response was cached.
            if cached_images and all(
                _tier_file_exists(pool_tier, int(img["id"])) for img in cached_images
            ):
                response["candidate_source"] = "response_cache"
                response["cache_hit"] = True
                response.setdefault("counts_stale", False)
                response.setdefault("reservoir_remaining", 0)
                _configured_schedule_cached_thumbnail_memory_warm(
                    cached_images,
                    "sm",
                    limit=min(max(1, n), 48),
                )
                return response
            _interaction_response_cache.pop(response_cache_key, None)
    if default_pool_only and strategy != "top":
        candidate_source = f"default_{strategy}_reservoir"
        counts_task = asyncio.create_task(
            _configured(_get_visible_pairing_pool_counts)(pool_tier, _configured_cache_root())
        )
        if strategy == "explore":
            candidate_source = "default_explore_least_compared"
            candidates = await default_visible_pairing_candidates(
                pool_tier,
                limit=max(_MOSAIC_EXPLORE_WINDOW, n * 80),
                order="least_compared_shuffled",
            )
        elif strategy == "diverse":
            # diverse_sample already narrows to a random search pool before the
            # embedding spread, so a bounded random reservoir is equivalent to
            # the whole visible universe and an order of magnitude cheaper.
            candidates = await default_visible_pairing_candidates(
                pool_tier,
                limit=max(_MOSAIC_DIVERSE_WINDOW, n * 40),
                order="random",
                include_card_metadata=False,
            )
        elif strategy == "compete" and grid_elo > 0:
            candidates = await default_visible_pairing_candidates(
                pool_tier,
                limit=max(_MOSAIC_SAMPLE_WINDOW, n * 80),
                order="near_elo",
                elo_pivot=grid_elo,
            )
        else:
            candidates = await default_visible_pairing_candidates(
                pool_tier,
                limit=max(_MOSAIC_SAMPLE_WINDOW, n * 80),
                order="random",
            )
        if exclude_ids:
            candidates = [row for row in candidates if int(row["id"]) not in exclude_ids]
        counts = await counts_task
        visible_count = int(counts.get("visible_images") or 0)
        filtered_total = int(counts.get("active_images") or 0)
        stats = response_helpers.interaction_pool_stats(filtered_total, visible_count)
    elif strategy != "top" and not query_constraints.search_constraint_active(search):
        candidate_source = "filtered_reservoir"
        stats = None
        candidates, filtered_total, visible_count = await filtered_visible_ranked_candidates(
            pool_tier,
            limit=(
                max(_MOSAIC_DIVERSE_WINDOW, n * 40)
                if strategy == "diverse"
                else max(_FILTERED_MOSAIC_WINDOW, n * 40)
            ),
            # Diverse and Explore need a window that spans the filtered set
            # rather than its highest-rated head; the shuffled tie-break covers
            # the remainder statistically.
            sort="least_compared_shuffled" if strategy in ("explore", "diverse") else "elo",
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
            exclude_sources=exclude_sources,)
        if exclude_ids:
            candidates = [row for row in candidates if int(row["id"]) not in exclude_ids]
            filtered_total = max(0, int(filtered_total) - len(exclude_ids))
            visible_count = max(0, int(visible_count) - len(exclude_ids))
    elif query_constraints.search_constraint_active(search):
        candidate_source = "search_reservoir" if search.get("active") else "scoped_reservoir"
        stats = None
        scoped_id_filter = search.get("id_filter")
        scoped_window = max(
            _MOSAIC_DIVERSE_WINDOW if strategy == "diverse" else _FILTERED_MOSAIC_WINDOW,
            n * 40,
        )
        if scoped_id_filter:
            # The user asked to refine THIS set — the pool must span all of it,
            # not a fixed head of the ranking order.
            scoped_window = max(scoped_window, min(len(scoped_id_filter), _SCOPED_MOSAIC_WINDOW_MAX))
        candidates, filtered_total, visible_count = await search_visible_ranked_candidates(
            pool_tier,
            limit=scoped_window,
            search=search,
            sort="least_compared_shuffled" if strategy in ("explore", "diverse") else "elo",
            exclude_ids=exclude_ids,
            force_exact_counts=strategy == "diverse",
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
            exclude_sources=exclude_sources,)
    else:
        candidate_source = "full_candidate_scan"
        stats = None
        if strategy == "top":
            images = await _configured(_get_top_images)(limit=50)
        else:
            images = await get_pairing_images(pool_tier)
        candidates = app_helpers.filter_compare_mosaic_candidates(
            images,
            exclude_ids=exclude_ids,
            orientation=orientation,
            compared=compared,
            min_stars=min_stars,
            folder=folder,
            flag=flag,
            date_taken=date_taken,
            file_type=file_type,
            camera=camera,
            lens=lens,
        )
        candidates = apply_tag_constraint(candidates, tag)
        candidates = apply_text_search_constraint(candidates, search)
        filtered_total = len(candidates)
        candidates = await filter_visible_candidates(candidates, pool_tier)
        visible_count = len(candidates)

    if len(candidates) < 2 and default_pool_only and strategy == "explore" and visible_count > len(candidates):
        candidates = await default_visible_pairing_candidates(pool_tier, order="least_compared")
        if exclude_ids:
            candidates = [row for row in candidates if int(row["id"]) not in exclude_ids]
        visible_count = len(candidates)

    if len(candidates) < 2:
        stats = stats or response_helpers.interaction_pool_stats(filtered_total, visible_count)
        stats["filtered_pool"] = visible_count
        stats["filtered_pool_visible"] = visible_count
        stats["filtered_pool_total"] = filtered_total
        await add_explore_uncompared_stats(
            stats,
            strategy=strategy,
            size=pool_tier,
            filtered_total=filtered_total,
            visible_count=visible_count,
            search=search,
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
        )
        response = {
            "images": [],
            **response_helpers.visibility_counts(filtered_total, visible_count),
            "total_kept": filtered_total,
            "stats": stats,
            "search_mode": search["search_mode"],
            "ai_unavailable": search["ai_unavailable"],
            "fallback_reason": search.get("fallback_reason", ""),
            "candidate_source": candidate_source,
            "pairing": "strategy",
            "counts_stale": counts_stale,
            "cache_hit": cache_hit,
            "reservoir_remaining": 0,
        }
        if response_cache_key is not None:
            _interaction_response_cache[response_cache_key] = {
                "data": response_helpers.copy_interaction_response(response),
                "expires": time.monotonic() + _interaction_response_cache_ttl_seconds,
            }
        return response

    count = min(n, len(candidates))
    sample, pairing_mode = await _refine_sample(candidates, count, strategy=strategy, grid_elo=grid_elo)

    sample_elo_by_id = {img["id"]: _effective_elo(img) for img in sample}
    hydrated_sample = await hydrate_active_rows(sample)
    hydrated_sample = await _servable_sample(
        hydrated_sample, candidates, count, pool_tier, exclude_ids
    )
    result = [
        app_helpers.image_card(img, "sm", elo_value=sample_elo_by_id.get(img["id"], img["elo"]))
        for img in hydrated_sample
    ]

    if sample:
        config = settings.get_settings()
        _configured_schedule_thumbnail_prefetch(
            hydrated_sample,
            "md",
            limit=min(len(sample), config["mosaic_prefetch_limit"]),
        )
        _configured_schedule_cached_thumbnail_memory_warm(result, "sm", limit=min(len(result), 48))

    stats = stats or response_helpers.interaction_pool_stats(filtered_total, visible_count)
    stats["filtered_pool"] = visible_count
    stats["filtered_pool_visible"] = visible_count
    stats["filtered_pool_total"] = filtered_total
    await add_explore_uncompared_stats(
        stats,
        strategy=strategy,
        size=pool_tier,
        filtered_total=filtered_total,
        visible_count=visible_count,
        search=search,
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
    )
    response = {
        "images": result,
        **response_helpers.visibility_counts(filtered_total, visible_count),
        "total_kept": filtered_total,
        "stats": stats,
        "search_mode": search["search_mode"],
        "ai_unavailable": search["ai_unavailable"],
        "fallback_reason": search.get("fallback_reason", ""),
        "candidate_source": candidate_source,
        "pairing": pairing_mode if count == 2 else "strategy",
        "counts_stale": counts_stale,
        "cache_hit": cache_hit,
        "reservoir_remaining": max(0, len(candidates) - len(result)),
    }
    if response_cache_key is not None:
        _interaction_response_cache[response_cache_key] = {
            "data": response_helpers.copy_interaction_response(response),
            "expires": time.monotonic() + _interaction_response_cache_ttl_seconds,
        }
    return response


async def compare_next_impl(
    n: int = 5, mode: str = "swiss",
    orientation: str = "", compared: str = "", min_stars: int = 0, folder: str = "",
    flag: str = "", date_taken: str = "", file_type: str = "", camera: str = "", lens: str = "",
    tag: str = "", q: str = "", deep: bool = False, people: str = "", ids: list[int] | None = None,
    collection_id: int = 0, import_batch: int = 0, exclude_sources=(),
):
    candidate_source = "compare_window"
    cache_hit = False
    counts_stale = False
    search = await _configured_resolve_library_constraints(q, people=people, deep=deep)
    search = await _scoped_search(search, ids, collection_id, import_batch)
    default_limited_candidates = False
    ranked_candidate_order = False
    has_filters = has_candidate_filters(
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
        search=search,
            exclude_sources=exclude_sources,)
    response_cache_key = None
    if not has_filters and mode != "topn":
        response_cache_key = (
            "compare_next",
            _configured_db_signature(),
            _configured_cache_root(),
            int(n),
            mode,
        )
        cached_response = _interaction_response_cache.get(response_cache_key)
        if cached_response and cached_response["expires"] > time.monotonic():
            response = response_helpers.copy_interaction_response(cached_response["data"])
            response["candidate_source"] = "response_cache"
            response["cache_hit"] = True
            response.setdefault("counts_stale", False)
            response.setdefault("reservoir_remaining", 0)
            return response
    if not has_filters and mode != "topn":
        candidate_source = f"default_{mode}_reservoir"
        counts_task = asyncio.create_task(
            _configured(_get_visible_pairing_pool_counts)("md", _configured_cache_root())
        )
        image_dicts = await default_visible_pairing_candidates(
            "md",
            limit=max(_SWISS_PAIR_WINDOW, n * 30),
            include_card_metadata=True,
        )
        past_task = asyncio.create_task(
            get_past_matchups_for_candidate_ids("md", [row["id"] for row in image_dicts])
        )
        default_limited_candidates = True
        ranked_candidate_order = True
        counts = await counts_task
        visible_count = int(counts.get("visible_images") or 0)
        filtered_total = int(counts.get("active_images") or 0)
        stats = response_helpers.interaction_pool_stats(filtered_total, visible_count)
    elif mode != "topn" and not query_constraints.search_constraint_active(search):
        candidate_source = "filtered_reservoir"
        stats = None
        image_dicts, filtered_total, visible_count = await filtered_visible_ranked_candidates(
            "md",
            limit=max(_FILTERED_SWISS_PAIR_WINDOW, n * 30),
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
            exclude_sources=exclude_sources,)
        past_task = asyncio.create_task(
            get_past_matchups_for_candidate_ids("md", [row["id"] for row in image_dicts])
        )
        ranked_candidate_order = True
    elif query_constraints.search_constraint_active(search):
        candidate_source = "search_reservoir" if search.get("active") else "scoped_reservoir"
        stats = None
        image_dicts, filtered_total, visible_count = await search_visible_ranked_candidates(
            "md",
            limit=max(_FILTERED_SWISS_PAIR_WINDOW, n * 30),
            search=search,
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
            exclude_sources=exclude_sources,)
        past_task = asyncio.create_task(
            get_past_matchups_for_candidate_ids("md", [row["id"] for row in image_dicts])
        )
        ranked_candidate_order = True
    else:
        candidate_source = "topn_reservoir" if mode == "topn" else "full_candidate_scan"
        stats = None
        past_task = None
        if mode == "topn":
            images = await _configured(_get_top_images)(limit=50)
        else:
            images = await get_pairing_images("md")
        image_dicts = app_helpers.filter_compare_mosaic_candidates(
            images,
            orientation=orientation,
            compared=compared,
            min_stars=min_stars,
            folder=folder,
            flag=flag,
            date_taken=date_taken,
            file_type=file_type,
            camera=camera,
            lens=lens,
        )
        image_dicts = apply_tag_constraint(image_dicts, tag)
        image_dicts = apply_text_search_constraint(image_dicts, search)
        filtered_total = len(image_dicts)
        image_dicts = await filter_visible_candidates(image_dicts, "md")
        visible_count = len(image_dicts)
        past_task = asyncio.create_task(
            get_past_matchups_for_candidate_ids("md", [row["id"] for row in image_dicts])
        )

    if len(image_dicts) < 2:
        stats = stats or response_helpers.interaction_pool_stats(filtered_total, visible_count)
        stats["filtered_pool"] = visible_count
        stats["filtered_pool_visible"] = visible_count
        stats["filtered_pool_total"] = filtered_total
        response = {
            "pairs": [],
            **response_helpers.visibility_counts(filtered_total, visible_count),
            "total_kept": filtered_total,
            "stats": stats,
            "search_mode": search["search_mode"],
            "ai_unavailable": search["ai_unavailable"],
            "fallback_reason": search.get("fallback_reason", ""),
            "candidate_source": candidate_source,
            "pairing": "strategy",
            "counts_stale": counts_stale,
            "cache_hit": cache_hit,
            "reservoir_remaining": 0,
        }
        if response_cache_key is not None:
            _interaction_response_cache[response_cache_key] = {
                "data": response_helpers.copy_interaction_response(response),
                "expires": time.monotonic() + _interaction_response_cache_ttl_seconds,
            }
        return response
    past = await past_task if past_task is not None else await get_past_matchups()
    if not has_filters and mode != "topn" and len(image_dicts) > _SWISS_PAIR_WINDOW:
        pairs = pairing.swiss_pair(image_dicts[:_SWISS_PAIR_WINDOW], past, max_pairs=n, presorted=True)
        if len(pairs) < n:
            pairs = pairing.swiss_pair(image_dicts, past, max_pairs=n, presorted=True)
    else:
        pairs = pairing.swiss_pair(image_dicts, past, max_pairs=n, presorted=ranked_candidate_order)
    if default_limited_candidates and len(pairs) < n and visible_count > len(image_dicts):
        image_dicts = await default_visible_pairing_candidates("md")
        past = await get_visible_past_matchups("md")
        pairs = pairing.swiss_pair(image_dicts, past, max_pairs=n, presorted=True)
    elif (
        has_filters
        and mode != "topn"
        and not query_constraints.search_constraint_active(search)
        and len(pairs) < n
        and visible_count > len(image_dicts)
    ):
        image_dicts, _filtered_total, _visible_count = await filtered_visible_ranked_candidates(
            "md",
            limit=visible_count,
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
            exclude_sources=exclude_sources,)
        past = await get_visible_past_matchups("md")
        pairs = pairing.swiss_pair(image_dicts, past, max_pairs=n, presorted=True)

    result = []
    prefetch_rows = []
    pair_rows = [row for pair in pairs for row in pair]
    hydrated_rows = await hydrate_active_rows(pair_rows)
    hydrated_by_id = {row["id"]: row for row in hydrated_rows}
    for left, right in pairs:
        left_row = hydrated_by_id.get(left["id"], left)
        right_row = hydrated_by_id.get(right["id"], right)
        prefetch_rows.append(left_row)
        prefetch_rows.append(right_row)
        result.append({
            "left": app_helpers.image_card(left_row, "md"),
            "right": app_helpers.image_card(right_row, "md"),
        })

    if prefetch_rows:
        config = settings.get_settings()
        _configured_schedule_thumbnail_prefetch(
            prefetch_rows,
            "md",
            limit=min(len(prefetch_rows), config["compare_prefetch_limit"]),
        )
        _configured_schedule_cached_thumbnail_memory_warm(
            prefetch_rows,
            "md",
            limit=min(len(prefetch_rows), max(2, n * 2)),
        )

    stats = stats or response_helpers.interaction_pool_stats(filtered_total, visible_count)
    stats["filtered_pool"] = visible_count
    stats["filtered_pool_visible"] = visible_count
    stats["filtered_pool_total"] = filtered_total
    response = {
        "pairs": result,
        **response_helpers.visibility_counts(filtered_total, visible_count),
        "total_kept": filtered_total,
        "stats": stats,
        "search_mode": search["search_mode"],
        "ai_unavailable": search["ai_unavailable"],
        "fallback_reason": search.get("fallback_reason", ""),
        "candidate_source": candidate_source,
        "pairing": "strategy",
        "counts_stale": counts_stale,
        "cache_hit": cache_hit,
        "reservoir_remaining": max(0, len(image_dicts) - len(pair_rows)),
    }
    if response_cache_key is not None:
        _interaction_response_cache[response_cache_key] = {
            "data": response_helpers.copy_interaction_response(response),
            "expires": time.monotonic() + _interaction_response_cache_ttl_seconds,
        }
    return response
