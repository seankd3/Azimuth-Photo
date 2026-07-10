"""HTTP endpoints for HDR bracket discovery and asynchronous merges."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from features.develop import hdr


router = APIRouter()
DbPathProvider = Callable[[], str]
_db_path: DbPathProvider | None = None


class HdrDetectBody(BaseModel):
    image_ids: list[int] | None = Field(default=None, max_length=500)


class HdrMergeBody(BaseModel):
    image_ids: list[int] = Field(min_length=3, max_length=12)


def configure(*, db_path: DbPathProvider) -> None:
    global _db_path
    _db_path = db_path


def _configured_db_path() -> str:
    if _db_path is None:
        raise RuntimeError("HDR routes are not configured")
    return _db_path()


@router.post("/api/develop/hdr/detect")
async def api_detect_hdr_brackets(body: HdrDetectBody):
    brackets = await asyncio.to_thread(hdr.detect_brackets, _configured_db_path(), body.image_ids)
    return {"brackets": brackets}


@router.post("/api/develop/hdr/merge", status_code=202)
async def api_merge_hdr(body: HdrMergeBody):
    image_ids = list(dict.fromkeys(image_id for image_id in body.image_ids if image_id > 0))
    if len(image_ids) < 3:
        return JSONResponse({"error": "HDR merge needs at least three images"}, status_code=400)
    if not hdr.begin_merge(_configured_db_path(), image_ids):
        return JSONResponse({"error": "An HDR merge is already running", "status": hdr.hdr_status()}, status_code=409)
    return {"queued": image_ids, "status": hdr.hdr_status()}


@router.get("/api/develop/hdr/status")
async def api_hdr_status():
    return hdr.hdr_status()
