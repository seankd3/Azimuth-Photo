"""API routes for explicit Lightroom-compatible XMP write-back."""

from __future__ import annotations

from core.catalog_path import catalog_path

import asyncio
from typing import Literal

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from features.develop import xmp_write


router = APIRouter()


class XmpWriteBody(BaseModel):
    mode: Literal["sidecar", "embedded"] = "sidecar"


class XmpBatchWriteBody(BaseModel):
    image_ids: list[int] = Field(default_factory=list, min_length=1, max_length=1000)




@router.post("/api/develop/{image_id}/write-xmp")
async def api_write_xmp(image_id: int, body: XmpWriteBody):
    result = await asyncio.to_thread(
        xmp_write.write_image_xmp,
        catalog_path(),
        image_id,
        mode=body.mode,
    )
    if result["status"] == "not_found":
        return JSONResponse(result, status_code=404)
    if result["status"] in {"unavailable", "error"}:
        return JSONResponse(result, status_code=409)
    return result


