"""Trash API routes."""

from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from features.trash import service as trash_service
from features.trash.remote import FORWARDED_HEADER, HubTrashRequestError, empty_hub_trash
from features.sync import contract, satellite
from features.sync.contract import require_compatible_api_revision


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
    # Explicitly-empty id lists are a no-op: never let an ambiguous forward
    # fall through to a full purge, and never touch the DB for it.
    if _payload is not None and not _payload.hub_image_ids:
        return {"deleted_count": 0, "freed_bytes": 0, "errors": [], "skipped_offline": 0}
    await require_compatible_api_revision(request)
    forwarded = request.headers.get(FORWARDED_HEADER) == "1"
    target_ids = _payload.hub_image_ids if _payload is not None else None
    if not (satellite.is_satellite_mode() and not forwarded):
        result = await trash_service.empty_trash(_configured_db_path(), image_ids=target_ids)
        if result["deleted_count"]:
            _invalidate_after_write()
        return result

    # Satellite owners must never be held hostage by an unavailable hub. Local
    # originals delete now; hub mirrors stay visible until the scoped deletion is
    # safely confirmed by a compatible hub.
    local_ids = await trash_service.local_trash_ids(_configured_db_path())
    local_result = await trash_service.empty_trash(_configured_db_path(), image_ids=local_ids)
    mirror_refs = await trash_service.hub_mirror_trash_refs(_configured_db_path())
    remote_result = None
    hub_error = None
    if mirror_refs["count"]:
        hub = satellite.hub_url()
        if not hub:
            hub_error = "Connect to the hub before synced photos can be permanently deleted from here."
        elif len(mirror_refs["hub_image_ids"]) != mirror_refs["count"]:
            hub_error = "Some synced photos are missing their hub identity. They will remain queued until the catalog sync repairs them."
        elif not await contract.hub_supports("trash.scoped_empty", hub=hub, force=True):
            hub_error = "The hub is running an older version. Synced photos stay queued until it updates."
        else:
            try:
                remote_result = await empty_hub_trash(hub, mirror_refs["hub_image_ids"])
                if remote_result.get("errors") or int(remote_result.get("skipped_offline") or 0):
                    hub_error = "The hub could not permanently remove every synced photo. We'll keep trying in the background."
            except HubTrashRequestError as exc:
                hub_error = str(exc)
        if hub_error:
            await trash_service.mark_hub_trash_pending(_configured_db_path(), mirror_refs["image_ids"])
        else:
            mirror_result = await trash_service.empty_trash(_configured_db_path(), image_ids=mirror_refs["image_ids"])
            local_result["deleted_count"] += mirror_result["deleted_count"]
            local_result["freed_bytes"] += mirror_result["freed_bytes"]
            local_result["errors"].extend(mirror_result["errors"])
            local_result["skipped_offline"] += mirror_result["skipped_offline"]
    if remote_result is not None:
        local_result["hub_deleted_count"] = int(remote_result.get("deleted_count") or 0)
        local_result["freed_bytes"] += int(remote_result.get("freed_bytes") or 0)
    pending = await trash_service.pending_hub_trash_refs(_configured_db_path())
    local_result["hub_pending"] = int(pending["count"])
    if hub_error:
        local_result["hub_error"] = hub_error
    if local_result["deleted_count"] or mirror_refs["count"]:
        _invalidate_after_write()
    return local_result
