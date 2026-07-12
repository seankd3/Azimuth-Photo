"""Local controls for the satellite sync worker."""

from fastapi import APIRouter

from features.sync import satellite
from features.sync.sync_worker import get_worker


router = APIRouter(tags=["sync"])


@router.get("/api/sync/status")
async def sync_status():
    worker = get_worker()
    if not satellite.is_satellite_mode() or worker is None:
        return {"mode": "hub", "paused": False, "queue_depth": 0, "bytes_remaining": 0, "throughput_bps": 0, "current_file": None, "recent_errors": [], "mirror": {"cursor": 0, "rows_applied": 0, "skipped_unhashed": 0, "last_refresh_at": None}, "prefetch": {"state": "idle", "cached": 0, "total": 0}}
    return worker.status()


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
        try:
            await worker.mirror.refresh()
        except Exception as error:
            worker.mirror._status["last_error"] = str(error)
    return await sync_status()


@router.post("/api/sync/prefetch")
async def sync_prefetch():
    worker = get_worker()
    if worker is not None:
        try:
            await worker.prefetch.prefetch_once(size="sm")
        except Exception as error:
            worker.prefetch._status["last_error"] = str(error)
    return await sync_status()


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
