"""Trash API routes."""

from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from features.trash import service as trash_service
from features.trash.remote import FORWARDED_HEADER, HubTrashRequestError, empty_hub_trash
from features.sync import satellite


router = APIRouter()
DbPath = Callable[[], str]
Invalidate = Callable[[], None]
MAX_IMAGE_IDS_PER_REQUEST = 10000

_db_path: DbPath | None = None
_invalidate: Invalidate | None = None


class ImageIdsBody(BaseModel):
    ids: list[int] = Field(default_factory=list, max_length=MAX_IMAGE_IDS_PER_REQUEST)


class EmptyTrashBody(BaseModel):
    hub_image_ids: list[int] | None = Field(default=None, max_length=MAX_IMAGE_IDS_PER_REQUEST)


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
async def api_empty_trash(request: Request, _payload: EmptyTrashBody | None = None):
    remote_result = None
    mirror_refs = await trash_service.hub_mirror_trash_refs(_configured_db_path())
    mirror_count = int(mirror_refs["count"])
    forwarded = request.headers.get(FORWARDED_HEADER) == "1"
    if mirror_count and satellite.is_satellite_mode() and not forwarded:
        hub = satellite.hub_url()
        if not hub:
            raise HTTPException(
                status_code=503,
                detail="Connect to the hub before permanently emptying mirrored Trash.",
            )
        hub_image_ids = mirror_refs["hub_image_ids"]
        if len(hub_image_ids) != mirror_count:
            raise HTTPException(
                status_code=409,
                detail="Mirrored Trash is missing hub identity. Sync the catalog, then try again.",
            )
        try:
            remote_result = await empty_hub_trash(hub, hub_image_ids)
        except HubTrashRequestError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        if remote_result.get("errors") or int(remote_result.get("skipped_offline") or 0):
            raise HTTPException(
                status_code=409,
                detail="The hub could not permanently remove every Trash item. Nothing was hidden locally.",
            )
    target_ids = _payload.hub_image_ids if _payload is not None else None
    result = await trash_service.empty_trash(_configured_db_path(), image_ids=target_ids)
    if remote_result is not None:
        result["hub_deleted_count"] = int(remote_result.get("deleted_count") or 0)
        result["freed_bytes"] += int(remote_result.get("freed_bytes") or 0)
    if result["deleted_count"]:
        _invalidate_after_write()
    return result
