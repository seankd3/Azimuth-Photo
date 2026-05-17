"""App-configured data providers for the thumbnail package facade."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any


DbPath = Callable[[], str]
AsyncConnectionProvider = Callable[[], Awaitable[Any]]
BatchSetOrientations = Callable[[list[tuple[str, float, int]]], Awaitable[Any]]
MarkImageMissingSync = Callable[[int], Any]
InvalidateCachedImageIdsCache = Callable[..., Any]
NoteCachedImageIdsAdded = Callable[[str, str, object], Any]

_db_path: DbPath | None = None
_get_db: AsyncConnectionProvider | None = None
_batch_set_orientations: BatchSetOrientations | None = None
_mark_image_missing_sync: MarkImageMissingSync | None = None
_invalidate_cached_image_ids_cache: InvalidateCachedImageIdsCache | None = None
_note_cached_image_ids_added: NoteCachedImageIdsAdded | None = None


def configure(
    *,
    db_path: DbPath | None = None,
    get_db: AsyncConnectionProvider | None = None,
    batch_set_orientations: BatchSetOrientations | None = None,
    mark_image_missing_sync: MarkImageMissingSync | None = None,
    invalidate_cached_image_ids_cache: InvalidateCachedImageIdsCache | None = None,
    note_cached_image_ids_added: NoteCachedImageIdsAdded | None = None,
) -> None:
    global _db_path, _get_db, _batch_set_orientations, _mark_image_missing_sync
    global _invalidate_cached_image_ids_cache, _note_cached_image_ids_added
    if db_path is not None:
        _db_path = db_path
    if get_db is not None:
        _get_db = get_db
    if batch_set_orientations is not None:
        _batch_set_orientations = batch_set_orientations
    if mark_image_missing_sync is not None:
        _mark_image_missing_sync = mark_image_missing_sync
    if invalidate_cached_image_ids_cache is not None:
        _invalidate_cached_image_ids_cache = invalidate_cached_image_ids_cache
    if note_cached_image_ids_added is not None:
        _note_cached_image_ids_added = note_cached_image_ids_added


def _configured(provider, name: str):
    if provider is None:
        raise RuntimeError(f"thumbnails data provider is missing configured dependency: {name}")
    return provider


def db_path() -> str:
    return _configured(_db_path, "db_path")()


async def get_db():
    return await _configured(_get_db, "get_db")()


async def batch_set_orientations(updates: list[tuple[str, float, int]]):
    return await _configured(_batch_set_orientations, "batch_set_orientations")(updates)


def mark_image_missing_sync(image_id: int):
    return _configured(_mark_image_missing_sync, "mark_image_missing_sync")(image_id)


def invalidate_cached_image_ids_cache(*, cache_root: str | None = None, size: str | None = None):
    return _configured(
        _invalidate_cached_image_ids_cache,
        "invalidate_cached_image_ids_cache",
    )(cache_root=cache_root, size=size)


def note_cached_image_ids_added(cache_root: str, size: str, image_ids):
    return _configured(_note_cached_image_ids_added, "note_cached_image_ids_added")(
        cache_root,
        size,
        image_ids,
    )
