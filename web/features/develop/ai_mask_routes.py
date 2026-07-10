"""HTTP surface for cached Develop subject and sky mask rasters."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from typing import Literal

from fastapi import APIRouter
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from data.repositories import images as image_repository
from features.develop import ai_masks, rawproc


router = APIRouter()
DbPathProvider = Callable[[], str]
_db_path: DbPathProvider | None = None


class AiMaskRequest(BaseModel):
    kind: Literal["subject", "sky"]


def configure(*, db_path: DbPathProvider) -> None:
    global _db_path
    _db_path = db_path


def _configured_db_path() -> str:
    if _db_path is None:
        raise RuntimeError("Develop AI mask routes are not configured")
    return _db_path()


async def _image_or_error(image_id: int):
    image = await image_repository.get_image_by_id(_configured_db_path(), image_id)
    if not image:
        return None, JSONResponse({"error": "Image not found"}, status_code=404)
    image = dict(image)
    if not rawproc.is_raw_path(image.get("filepath") or ""):
        return None, JSONResponse({"error": "AI masks are available only for DNG, CR2, and CR3 images"}, status_code=400)
    if not await asyncio.to_thread(os.path.exists, image["filepath"]):
        return None, JSONResponse({"error": "RAW source file is unavailable"}, status_code=404)
    return image, None


@router.post("/api/develop/{image_id}/ai-mask")
async def api_create_ai_mask(image_id: int, body: AiMaskRequest):
    image, error = await _image_or_error(image_id)
    if error:
        return error
    try:
        base_paths, _meta = await asyncio.to_thread(rawproc.ensure_base_cache, image_id, image["filepath"])
        result = await asyncio.to_thread(ai_masks.create_mask, base_paths.preview, body.kind)
    except rawproc.RawDecodeError as exc:
        return JSONResponse({"error": str(exc)}, status_code=422)
    except ai_masks.AiMaskError as exc:
        return JSONResponse({"error": str(exc), "status": exc.status}, status_code=503 if exc.status == "MODEL_MISSING" else 422)
    return {"cache_key": result.cache_key, "url": f"/api/develop/ai-mask/{result.cache_key}.png"}


@router.get("/api/develop/ai-mask/{cache_key}.png")
async def api_get_ai_mask(cache_key: str):
    path = ai_masks.mask_path(cache_key)
    if path is None or not path.exists():
        return JSONResponse({"error": "AI mask not found"}, status_code=404)
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "public, max-age=31536000, immutable"})
