"""Local controls for the satellite sync worker."""

from fastapi import APIRouter

from features.sync import satellite
from features.sync.sync_worker import get_worker


router = APIRouter(tags=["sync"])


@router.get("/api/sync/status")
async def sync_status():
    worker = get_worker()
    if not satellite.is_satellite_mode() or worker is None:
        return {"mode": "hub", "paused": False, "queue_depth": 0, "bytes_remaining": 0, "throughput_bps": 0, "current_file": None, "recent_errors": []}
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
