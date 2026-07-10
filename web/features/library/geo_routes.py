"""Small API surface for deliberate, inspectable geotag enrichment work."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from features.library import geodata
from features.library.timeline_import import parse_timeline_file


router = APIRouter()
_db_path: Callable[[], str] | None = None
_backfill_status: dict = {"state": "idle", "counts": {}, "error": ""}
_infer_status: dict = {"state": "idle", "counts": {}, "error": ""}
_backfill_task: asyncio.Task | None = None
_infer_task: asyncio.Task | None = None


class TimelineImportRequest(BaseModel):
    path: str


def configure(*, db_path: Callable[[], str]) -> None:
    global _db_path
    _db_path = db_path


def _configured_db_path() -> str:
    if _db_path is None:
        raise RuntimeError("geodata routes are not configured")
    return _db_path()


def _start(task: asyncio.Task | None, state: dict, worker) -> bool:
    if task and not task.done():
        return False
    state.clear()
    state.update(state="queued", counts={}, error="")
    return True


@router.get("/api/geo/status")
async def geo_status():
    status = await geodata.geo_status(_configured_db_path())
    return {**status, "backfill": dict(_backfill_status), "inference": dict(_infer_status)}


@router.post("/api/geo/backfill/start")
async def start_geo_backfill():
    global _backfill_task
    if not _start(_backfill_task, _backfill_status, geodata.run_backfill):
        return {"ok": True, "started": False, "backfill": dict(_backfill_status)}
    _backfill_task = asyncio.create_task(geodata.run_backfill(_configured_db_path(), _backfill_status))
    return {"ok": True, "started": True, "backfill": dict(_backfill_status)}


@router.post("/api/geo/infer/start")
async def start_geo_inference():
    global _infer_task
    if not _start(_infer_task, _infer_status, geodata.run_inference):
        return {"ok": True, "started": False, "inference": dict(_infer_status)}
    _infer_task = asyncio.create_task(geodata.run_inference(_configured_db_path(), _infer_status))
    return {"ok": True, "started": True, "inference": dict(_infer_status)}


@router.post("/api/geo/timeline/import")
async def import_timeline(request: TimelineImportRequest):
    path = os.path.abspath(os.path.expanduser(request.path))
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="Timeline export file was not found")
    try:
        points = await asyncio.to_thread(parse_timeline_file, path)
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"Could not read Timeline export: {exc}") from exc
    db_path = _configured_db_path()
    await geodata.ensure_geo_schema(db_path)
    from data import connection
    conn = await connection.open_async(db_path)
    try:
        await conn.execute("DELETE FROM geo_trail WHERE source = 'timeline'")
        await conn.executemany(
            "INSERT INTO geo_trail(ts, lat, lon, source) VALUES (?, ?, ?, 'timeline')", points
        )
        await conn.commit()
    finally:
        await connection.close_async(conn, db_path=db_path)
    changes = await geodata.infer_locations(db_path)
    return {"ok": True, "points_imported": len(points), "inference": changes}
