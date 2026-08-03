"""Stack API routes."""

from __future__ import annotations

from core.catalog_path import catalog_path

import asyncio
import logging
import time
from collections.abc import Callable

from fastapi import APIRouter, BackgroundTasks
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from data.repositories import stacks as stack_repository
from features.stacks import builders
from features.stacks import identical
from features.trash import service as trash_service


def _invalidate_after_stack_change() -> None:
    cache_events.invalidate_rankings_cache()
    cache_events.invalidate_ranking_count_cache()
    cache_events.invalidate_facet_caches()

from core import cache_events
router = APIRouter()
log = logging.getLogger(__name__)
DbPath = Callable[[], str]

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


class IdenticalCleanupBody(BaseModel):
    token: str = Field(min_length=1, max_length=64)



def _invalidate() -> None:
    if _invalidate_after_stack_change is not None:
        _invalidate_after_stack_change()


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
        result = await asyncio.to_thread(builders.rebuild_stacks, catalog_path(), kinds)
        _rebuild_status.update({
            "state": "complete",
            "finished_at": time.time(),
            "result": result,
            "error": "",
        })
        _invalidate()
    except Exception:
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
        catalog_path(),
        kind=kind or None,
        limit=limit,
        offset=offset,
    )
    response["rebuild_status"] = dict(_rebuild_status)
    return response


@router.get("/api/stacks/rebuild/status")
async def api_stacks_rebuild_status():
    return {"rebuild_status": dict(_rebuild_status)}


@router.get("/api/stacks/identical")
async def api_identical_stacks(limit: int = 25, offset: int = 0):
    return await asyncio.to_thread(
        identical.list_candidates,
        catalog_path(),
        limit=limit,
        offset=offset,
    )


@router.get("/api/stacks/identical/status")
async def api_identical_status():
    return {"verification_status": identical.verification_status()}


@router.get("/api/stacks/identical/summary")
async def api_identical_summary():
    return {"summary": await asyncio.to_thread(identical.candidate_summary, catalog_path())}


@router.post("/api/stacks/identical/verify", status_code=202)
async def api_verify_identical_stacks(background_tasks: BackgroundTasks):
    token = identical.queue_verification()
    if token is None:
        return JSONResponse({"error": "Identical-file verification is already running"}, status_code=409)
    background_tasks.add_task(identical.run_verification, catalog_path(), token)
    return {"accepted": True, "verification_status": identical.verification_status()}


@router.post("/api/stacks/identical/cleanup")
async def api_cleanup_identical_stacks(body: IdenticalCleanupBody):
    try:
        image_ids, skipped_groups = await asyncio.to_thread(
            identical.validated_cleanup_ids,
            body.token,
        )
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=409)
    if not image_ids:
        return JSONResponse(
            {"error": "No verified copies are still safe to move", "skipped_groups": skipped_groups},
            status_code=409,
        )
    result = await trash_service.trash_images(catalog_path(), image_ids)
    result["skipped_groups"] = skipped_groups
    result["requested"] = len(image_ids)
    if result.get("trashed"):
        _invalidate()
    identical.finish_cleanup(body.token)
    return result


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
    counts = await stack_repository.representative_stack_counts(catalog_path(), ids)
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




@router.get("/api/stacks/{stack_id}")
async def api_stack(stack_id: int):
    stack = await stack_repository.get_stack(catalog_path(), stack_id)
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
            catalog_path(),
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
        catalog_path(),
        stack_id,
        int(body.image_id),
    )
    if stack is None:
        return JSONResponse({"error": "Image is not a member of this stack"}, status_code=404)
    _invalidate()
    return stack


@router.post("/api/stacks/{stack_id}/unstack")
async def api_unstack(stack_id: int):
    deleted = await stack_repository.unstack(catalog_path(), stack_id)
    if not deleted:
        return JSONResponse({"error": "Stack not found"}, status_code=404)
    _invalidate()
    return {"ok": True}
