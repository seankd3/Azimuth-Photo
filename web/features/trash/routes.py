"""Trash API routes."""

from __future__ import annotations

from core.catalog_path import catalog_path

from collections.abc import Callable

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from features.trash import service as trash_service
from features.trash.remote import FORWARDED_HEADER
from features.sync import satellite
from features.sync.contract import require_compatible_api_revision
from features.sync.sync_worker import get_worker


router = APIRouter()
DbPath = Callable[[], str]
Invalidate = Callable[[], None]
MAX_IMAGE_IDS_PER_REQUEST = 10000

_invalidate: Invalidate | None = None


class ImageIdsBody(BaseModel):
    ids: list[int] = Field(default_factory=list, max_length=MAX_IMAGE_IDS_PER_REQUEST)


class EmptyTrashBody(BaseModel):
    hub_image_ids: list[int] | None = Field(default=None, max_length=MAX_IMAGE_IDS_PER_REQUEST)


def configure(*, invalidate: Invalidate | None = None) -> None:
    global _invalidate
    _invalidate = invalidate


def _invalidate_after_write() -> None:
    if _invalidate is not None:
        _invalidate()


@router.post("/api/images/trash")
async def api_trash_images(payload: ImageIdsBody):
    result = await trash_service.trash_images(catalog_path(), payload.ids)
    if result["trashed"]:
        _invalidate_after_write()
    return result


@router.post("/api/images/restore")
async def api_restore_images(payload: ImageIdsBody):
    result = await trash_service.restore_images(catalog_path(), payload.ids)
    if result["restored"]:
        _invalidate_after_write()
    return result


@router.get("/api/trash")
async def api_trash(limit: int = 100, offset: int = 0):
    return await trash_service.list_trash(catalog_path(), limit=limit, offset=offset)


@router.post("/api/trash/empty")
async def api_empty_trash(request: Request, _payload: EmptyTrashBody | None = None):
    # Explicitly-empty id lists are a no-op: never let an ambiguous forward
    # fall through to a full purge, and never touch the DB for it.
    if _payload is not None and not _payload.hub_image_ids:
        return {"deleted_count": 0, "freed_bytes": 0, "errors": [], "skipped_offline": 0}
    await require_compatible_api_revision(request)
    forwarded = request.headers.get(FORWARDED_HEADER) == "1"
    target_ids = _payload.hub_image_ids if _payload is not None else None
    if not (satellite.is_satellite_mode() and not forwarded):
        result = await trash_service.empty_trash(catalog_path(), image_ids=target_ids)
        if result["deleted_count"]:
            _invalidate_after_write()
        return result

    # Satellite owners must never be held hostage by an unavailable hub. Local
    # originals delete now; hub mirrors stay visible until the scoped deletion is
    # safely confirmed by a compatible hub.
    local_ids = await trash_service.local_trash_ids(catalog_path())
    local_result = await trash_service.empty_trash(catalog_path(), image_ids=local_ids)
    mirror_refs = await trash_service.hub_mirror_trash_refs(catalog_path())
    if mirror_refs["count"]:
        # Law 1: the request path never awaits the hub. Mirrors go pending
        # immediately; the sync worker owns the hub leg (contract check, scoped
        # empty, mirror purge on confirmation) and retries with backoff.
        await trash_service.mark_hub_trash_pending(catalog_path(), mirror_refs["image_ids"])
        try:
            get_worker().sync_now()
        except Exception:
            pass  # No worker (hub unset / paused): the badge still tells the truth.
    pending = await trash_service.pending_hub_trash_refs(catalog_path())
    local_result["hub_pending"] = int(pending["count"])
    if local_result["deleted_count"] or mirror_refs["count"]:
        _invalidate_after_write()
    return local_result
