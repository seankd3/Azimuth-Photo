"""Local controls for the satellite sync worker."""

import asyncio

import db

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from core.version import API_REV, app_version
from features.sync import contract, oplog, satellite
from features.sync.sync_worker import get_worker
from features.trash import service as trash_service


router = APIRouter(tags=["sync"])
_manual_sync_tasks: set[asyncio.Task] = set()


def _start_manual_sync_task(coro) -> None:
    task = asyncio.create_task(coro)
    _manual_sync_tasks.add(task)
    task.add_done_callback(_manual_sync_tasks.discard)


async def _refresh_mirror(worker) -> None:
    try:
        await worker.mirror.refresh()
    except Exception as error:
        worker.mirror._status["last_error"] = str(error)


async def _prefetch_browse_tier(worker) -> None:
    try:
        await worker.prefetch.prefetch_once(size="sm")
    except Exception as error:
        worker.prefetch._status["last_error"] = str(error)


@router.post("/api/sync/hub")
async def attach_hub(request: Request):
    """Runtime standalone → satellite upgrade: store the hub and start syncing."""

    if not satellite.is_satellite_mode():
        return JSONResponse({"error": "A hub cannot attach to another hub"}, status_code=400)
    body = await request.json()
    try:
        result = await satellite.attach_hub(
            str(body.get("url") or ""), str(body.get("device_token") or "")
        )
    except ValueError as error:
        return JSONResponse({"error": str(error)}, status_code=400)
    return result


@router.get("/api/sync/status")
async def sync_status():
    worker = get_worker()
    if not satellite.is_satellite_mode() or worker is None:
        hub_state = (
            contract.hub_status(satellite.hub_url())
            if satellite.has_hub()
            else {"hub_health": "ok", "api_rev": API_REV, "app_version": app_version()}
        )
        status = {"mode": "satellite" if satellite.is_satellite_mode() else "hub", "paused": False, "queue_depth": 0, "bytes_remaining": 0, "throughput_bps": 0, "current_file": None, "recent_errors": [], "mirror": {"cursor": 0, "rows_applied": 0, "skipped_unhashed": 0, "last_refresh_at": None}, "prefetch": {"state": "idle", "cached": 0, "total": 0}, **hub_state}
    else:
        status = worker.status()
    pending = (
        await trash_service.pending_hub_trash_refs(db.DB_PATH)
        if satellite.is_satellite_mode()
        else {"count": 0}
    )
    status["pending_hub_trash"] = int(pending["count"])
    status["pending_ops"] = await oplog.pending_entry_count(db.DB_PATH)
    return status


@router.post("/api/sync/pause")
async def sync_pause():
    worker = get_worker()
    if worker is not None:
        worker.pause()
    return await sync_status()


@router.post("/api/sync/resume")
async def sync_resume():
    worker = get_worker()
    if worker is not None:
        worker.resume()
    return await sync_status()


@router.post("/api/sync/now")
async def sync_now():
    worker = get_worker()
    if worker is not None:
        worker.sync_now()
    return await sync_status()


@router.post("/api/sync/mirror/refresh")
async def sync_mirror_refresh():
    worker = get_worker()
    if worker is not None:
        _start_manual_sync_task(_refresh_mirror(worker))
    return JSONResponse(
        {"status": "pending", "job": "mirror_refresh"},
        status_code=202,
        headers={"Retry-After": "1"},
    )


@router.post("/api/sync/prefetch")
async def sync_prefetch():
    worker = get_worker()
    if worker is not None:
        _start_manual_sync_task(_prefetch_browse_tier(worker))
    return JSONResponse(
        {"status": "pending", "job": "thumb_prefetch"},
        status_code=202,
        headers={"Retry-After": "1"},
    )


@router.post("/api/sync/prefetch/loupe/{image_id}")
async def sync_prefetch_loupe(image_id: int):
    worker = get_worker()
    if worker is not None:
        await worker.prefetch.enqueue_loupe_neighbors(image_id)
    return await sync_status()


@router.post("/api/sync/prefetch/develop/{image_id}")
async def sync_prefetch_develop(image_id: int):
    worker = get_worker()
    if worker is not None:
        await worker.prefetch.enqueue_develop_siblings(image_id)
    return await sync_status()
