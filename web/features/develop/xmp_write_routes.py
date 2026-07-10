"""API routes for explicit Lightroom-compatible XMP write-back."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Literal

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from features.develop import xmp_write


router = APIRouter()
DbPathProvider = Callable[[], str]
_db_path: DbPathProvider | None = None


class XmpWriteBody(BaseModel):
    mode: Literal["sidecar", "embedded"] = "sidecar"


class XmpBatchWriteBody(BaseModel):
    image_ids: list[int] = Field(default_factory=list, min_length=1, max_length=1000)


def configure(*, db_path: DbPathProvider) -> None:
    global _db_path
    _db_path = db_path


def _configured_db_path() -> str:
    if _db_path is None:
        raise RuntimeError("Develop XMP write routes are not configured")
    return _db_path()


@router.post("/api/develop/{image_id}/write-xmp")
async def api_write_xmp(image_id: int, body: XmpWriteBody):
    result = await asyncio.to_thread(
        xmp_write.write_image_xmp,
        _configured_db_path(),
        image_id,
        mode=body.mode,
    )
    if result["status"] == "not_found":
        return JSONResponse(result, status_code=404)
    if result["status"] in {"unavailable", "error"}:
        return JSONResponse(result, status_code=409)
    return result


@router.post("/api/develop/write-xmp/batch")
async def api_write_xmp_batch(body: XmpBatchWriteBody):
    return await asyncio.to_thread(
        xmp_write.write_batch_xmp,
        _configured_db_path(),
        body.image_ids,
    )
