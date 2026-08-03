"""Catalog access for the thumbnail package."""

from __future__ import annotations

import db
from core.catalog_path import catalog_path


def db_path() -> str:
    return catalog_path()


async def get_db():
    return await db.get_db()


async def batch_set_orientations(updates: list[tuple[str, float, int]]):
    return await db.batch_set_orientations(updates)


def mark_image_missing_sync(image_id: int):
    return db.mark_image_missing_sync(image_id)


def invalidate_cached_image_ids_cache(*, cache_root: str | None = None, size: str | None = None):
    return db.invalidate_cached_image_ids_cache(cache_root=cache_root, size=size)


def note_cached_image_ids_added(cache_root: str, size: str, image_ids):
    return db.note_cached_image_ids_added(cache_root, size, image_ids)
