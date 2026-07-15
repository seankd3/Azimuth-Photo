"""Stack API routes."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable

from fastapi import APIRouter, BackgroundTasks
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from data.repositories import stacks as stack_repository
from features.stacks import builders


router = APIRouter()
log = logging.getLogger(__name__)
DbPath = Callable[[], str]
Invalidate = Callable[[], None]

_db_path: DbPath | None = None
_invalidate_rankings_cache: Invalidate | None = None
_rebuild_status: dict = {
    "state": "idle",
    "started_at": None,
    "finished_at": None,
    "requested_kinds": [],
    "result": None,
    "error": "",
}


class CreateStackBody(BaseModel):
    image_ids: list[int] = Field(default_factory=list, max_length=10_000)
    representative_id: int | None = None


class RepresentativeBody(BaseModel):
    image_id: int


class RebuildBody(BaseModel):
    kinds: list[str] | None = Field(default=None, max_length=16)


def configure(*, db_path: DbPath, invalidate_rankings_cache: Invalidate | None = None) -> None:
    global _db_path, _invalidate_rankings_cache
    _db_path = db_path
    _invalidate_rankings_cache = invalidate_rankings_cache


def _configured_db_path() -> str:
    if _db_path is None:
        raise RuntimeError("Stack routes are not configured")
    return _db_path()


def _invalidate() -> None:
    if _invalidate_rankings_cache is not None:
        _invalidate_rankings_cache()


def _clean_kinds(kinds) -> list[str]:
    if not kinds:
        return list(builders.STACK_KINDS)
    clean = []
    for kind in kinds:
        value = str(kind or "").strip().lower()
        if value in builders.STACK_KINDS and value not in clean:
            clean.append(value)
    return clean


async def _run_rebuild_task(kinds: list[str]) -> None:
    _rebuild_status.update({
        "state": "running",
        "started_at": time.time(),
        "finished_at": None,
        "requested_kinds": kinds,
        "result": None,
        "error": "",
    })
    try:
        result = await asyncio.to_thread(builders.rebuild_stacks, _configured_db_path(), kinds)
        _rebuild_status.update({
            "state": "complete",
            "finished_at": time.time(),
            "result": result,
            "error": "",
        })
        _invalidate()
    except Exception as exc:
        log.exception("worker=stack_rebuild kinds=%s failed", ",".join(kinds))
        _rebuild_status.update({
            "state": "error",
            "finished_at": time.time(),
            "result": None,
            "error": "Stack rebuild failed. Check the server log and try again.",
        })


@router.get("/api/stacks")
async def api_stacks(kind: str = "", limit: int = 50, offset: int = 0):
    response = await stack_repository.list_stacks(
        _configured_db_path(),
        kind=kind or None,
        limit=limit,
        offset=offset,
    )
    response["rebuild_status"] = dict(_rebuild_status)
    return response


@router.get("/api/stacks/rebuild/status")
async def api_stacks_rebuild_status():
    return {"rebuild_status": dict(_rebuild_status)}


@router.get("/api/stacks/representatives")
async def api_stack_representatives(image_ids: str = ""):
    """Small grid hydration payload for collapsed stack badges."""
    ids = []
    for token in image_ids.split(","):
        try:
            image_id = int(token)
        except ValueError:
            continue
        if image_id > 0 and image_id not in ids:
            ids.append(image_id)
        if len(ids) >= 500:
            break
    counts = await stack_repository.representative_stack_counts(_configured_db_path(), ids)
    return {"representatives": {str(image_id): value for image_id, value in counts.items()}}


@router.post("/api/stacks/rebuild")
async def api_rebuild_stacks(body: RebuildBody, background_tasks: BackgroundTasks):
    kinds = _clean_kinds(body.kinds)
    if not kinds:
        return JSONResponse({"error": "No supported stack kinds requested"}, status_code=400)
    if _rebuild_status.get("state") == "running":
        return JSONResponse({"error": "Stack rebuild already running"}, status_code=409)
    background_tasks.add_task(_run_rebuild_task, kinds)
    return JSONResponse({"accepted": True, "rebuild_status": {**_rebuild_status, "requested_kinds": kinds}}, status_code=202)


@router.post("/api/stacks/version/scan", status_code=202)
async def api_scan_version_stacks(background_tasks: BackgroundTasks):
    """Incrementally rebuild RAW/export version stacks without an embedding pass."""
    if _rebuild_status.get("state") == "running":
        return JSONResponse({"error": "Stack rebuild already running"}, status_code=409)
    background_tasks.add_task(_run_rebuild_task, ["version"])
    return {"accepted": True, "rebuild_status": {**_rebuild_status, "requested_kinds": ["version"]}}


@router.get("/api/stacks/{stack_id}")
async def api_stack(stack_id: int):
    stack = await stack_repository.get_stack(_configured_db_path(), stack_id)
    if stack is None:
        return JSONResponse({"error": "Stack not found"}, status_code=404)
    return stack


@router.post("/api/stacks")
async def api_create_stack(body: CreateStackBody):
    image_ids = []
    seen = set()
    for image_id in body.image_ids:
        value = int(image_id)
        if value > 0 and value not in seen:
            seen.add(value)
            image_ids.append(value)
    if len(image_ids) < 2:
        return JSONResponse({"error": "A manual stack needs at least two images"}, status_code=400)
    representative_id = int(body.representative_id or image_ids[0])
    if representative_id not in image_ids:
        return JSONResponse({"error": "Representative must be one of the stack images"}, status_code=400)
    try:
        stack = await stack_repository.create_stack(
            _configured_db_path(),
            kind="manual",
            representative_image_id=representative_id,
            member_rows=[{"image_id": image_id} for image_id in image_ids],
            auto=False,
        )
    except stack_repository.ManualStackConflict as exc:
        return JSONResponse(
            {"error": "Images already belong to a manual stack", "image_ids": exc.image_ids},
            status_code=409,
        )
    except stack_repository.UnknownStackImages as exc:
        return JSONResponse(
            {"error": "Stack image ids were not found", "image_ids": exc.image_ids},
            status_code=400,
        )
    _invalidate()
    return stack


@router.post("/api/stacks/{stack_id}/representative")
async def api_set_stack_representative(stack_id: int, body: RepresentativeBody):
    stack = await stack_repository.set_representative(
        _configured_db_path(),
        stack_id,
        int(body.image_id),
    )
    if stack is None:
        return JSONResponse({"error": "Image is not a member of this stack"}, status_code=404)
    _invalidate()
    return stack


@router.post("/api/stacks/{stack_id}/unstack")
async def api_unstack(stack_id: int):
    deleted = await stack_repository.unstack(_configured_db_path(), stack_id)
    if not deleted:
        return JSONResponse({"error": "Stack not found"}, status_code=404)
    _invalidate()
    return {"ok": True}
