"""HTTP endpoints for HDR bracket discovery and asynchronous merges."""

from __future__ import annotations

from core.catalog_path import catalog_path


from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field



router = APIRouter()


class HdrDetectBody(BaseModel):
    image_ids: list[int] | None = Field(default=None, max_length=500)


class HdrMergeBody(BaseModel):
    image_ids: list[int] = Field(min_length=3, max_length=12)






@router.post("/api/develop/hdr/merge", status_code=202)
async def api_merge_hdr(body: HdrMergeBody):
    from features.develop import hdr  # deferred: keeps HDR pixel libraries off boot until an HDR request

    image_ids = list(dict.fromkeys(image_id for image_id in body.image_ids if image_id > 0))
    if len(image_ids) < 3:
        return JSONResponse({"error": "HDR merge needs at least three images"}, status_code=400)
    if not hdr.begin_merge(catalog_path(), image_ids):
        return JSONResponse({"error": "An HDR merge is already running", "status": hdr.hdr_status()}, status_code=409)
    return {"queued": image_ids, "status": hdr.hdr_status()}


