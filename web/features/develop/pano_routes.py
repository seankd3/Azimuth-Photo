"""HTTP endpoints for panorama sequence discovery and asynchronous merges."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field



router = APIRouter()
DbPathProvider = Callable[[], str]
_db_path: DbPathProvider | None = None


class PanoDetectBody(BaseModel):
    image_ids: list[int] | None = Field(default=None, max_length=500)


class PanoMergeBody(BaseModel):
    image_ids: list[int] = Field(min_length=2, max_length=8)


def configure(*, db_path: DbPathProvider) -> None:
    global _db_path
    _db_path = db_path


def _configured_db_path() -> str:
    if _db_path is None:
        raise RuntimeError("Panorama routes are not configured")
    return _db_path()


@router.post("/api/develop/pano/detect")
async def api_detect_pano_sequences(body: PanoDetectBody):
    from features.develop import pano  # deferred: keeps panorama pixel libraries off boot until a panorama request

    sequences = await asyncio.to_thread(pano.detect_sequences, _configured_db_path(), body.image_ids)
    return {"sequences": sequences}


@router.post("/api/develop/pano/merge", status_code=202)
async def api_merge_pano(body: PanoMergeBody):
    from features.develop import pano  # deferred: keeps panorama pixel libraries off boot until a panorama request

    image_ids = list(dict.fromkeys(image_id for image_id in body.image_ids if image_id > 0))
    if len(image_ids) < 2:
        return JSONResponse({"error": "Panorama merge needs at least two images"}, status_code=400)
    if len(image_ids) > 8:
        return JSONResponse({"error": "Panorama merge accepts at most eight images"}, status_code=400)
    if not pano.begin_merge(_configured_db_path(), image_ids):
        return JSONResponse({"error": "A panorama merge is already running", "status": pano.pano_status()}, status_code=409)
    return {"queued": image_ids, "status": pano.pano_status()}


@router.get("/api/develop/pano/status")
async def api_pano_status():
    from features.develop import pano  # deferred: keeps panorama pixel libraries off boot until a panorama request

    return pano.pano_status()
