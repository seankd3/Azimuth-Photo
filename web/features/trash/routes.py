"""Trash API routes."""

from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter
from pydantic import BaseModel, Field

from features.trash import service as trash_service


router = APIRouter()
DbPath = Callable[[], str]
Invalidate = Callable[[], None]
MAX_IMAGE_IDS_PER_REQUEST = 10000

_db_path: DbPath | None = None
_invalidate: Invalidate | None = None


class ImageIdsBody(BaseModel):
    ids: list[int] = Field(default_factory=list, max_length=MAX_IMAGE_IDS_PER_REQUEST)


class EmptyTrashBody(BaseModel):
    pass


def configure(*, db_path: DbPath, invalidate: Invalidate | None = None) -> None:
    global _db_path, _invalidate
    _db_path = db_path
    _invalidate = invalidate


def _configured_db_path() -> str:
    if _db_path is None:
        raise RuntimeError("Trash routes are not configured")
    return _db_path()


def _invalidate_after_write() -> None:
    if _invalidate is not None:
        _invalidate()


@router.post("/api/images/trash")
async def api_trash_images(payload: ImageIdsBody):
    result = await trash_service.trash_images(_configured_db_path(), payload.ids)
    if result["trashed"]:
        _invalidate_after_write()
    return result


@router.post("/api/images/restore")
async def api_restore_images(payload: ImageIdsBody):
    result = await trash_service.restore_images(_configured_db_path(), payload.ids)
    if result["restored"]:
        _invalidate_after_write()
    return result


@router.get("/api/trash")
async def api_trash(limit: int = 100, offset: int = 0):
    return await trash_service.list_trash(_configured_db_path(), limit=limit, offset=offset)


@router.post("/api/trash/empty")
async def api_empty_trash(_payload: EmptyTrashBody | None = None):
    result = await trash_service.empty_trash(_configured_db_path())
    if result["deleted_count"]:
        _invalidate_after_write()
    return result
