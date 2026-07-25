from collections.abc import Awaitable, Callable
import asyncio
import logging
import time

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

import caption_worker
import settings
from core import capabilities
from core.background import track_background_task


router = APIRouter()
log = logging.getLogger(__name__)
AsyncDictBuilder = Callable[..., Awaitable[dict]]
AsyncMaybeDictBuilder = Callable[..., Awaitable[dict | None]]
AsyncListBuilder = Callable[..., Awaitable[list]]
InvalidateStatus = Callable[[], None]

_get_caption_status_counts: AsyncDictBuilder | None = None
_get_image_caption: AsyncMaybeDictBuilder | None = None
_owner_update_caption: AsyncMaybeDictBuilder | None = None
_get_tags: AsyncListBuilder | None = None
_invalidate_settings_response_cache: InvalidateStatus | None = None
_caption_counts_cache: dict[str, object] = {
    "data": None,
    "key": None,
    "expires": 0.0,
}
_caption_counts_refreshing = False
_CAPTION_COUNTS_TTL_SECONDS = 10.0
_CAPTION_COUNTS_INITIAL_WAIT_SECONDS = 0.05


class CaptionBody(BaseModel):
    caption: str | None = Field(default=None, max_length=100_000)
    tags: list[str] | None = Field(default=None, max_length=500)


def configure(
    *,
    get_caption_status_counts: AsyncDictBuilder,
    get_image_caption: AsyncMaybeDictBuilder | None = None,
    owner_update_caption: AsyncMaybeDictBuilder | None = None,
    get_tags: AsyncListBuilder | None = None,
    invalidate_settings_response_cache: InvalidateStatus,
) -> None:
    global _get_caption_status_counts, _get_image_caption, _owner_update_caption, _get_tags
    global _invalidate_settings_response_cache
    _get_caption_status_counts = get_caption_status_counts
    _get_image_caption = get_image_caption
    _owner_update_caption = owner_update_caption
    _get_tags = get_tags
    _invalidate_settings_response_cache = invalidate_settings_response_cache
    invalidate_caption_status_cache()


def _configured() -> None:
    if _get_caption_status_counts is None or _invalidate_settings_response_cache is None:
        raise RuntimeError("Caption routes are not configured")


def _caption_routes_configured() -> None:
    _configured()
    if _get_image_caption is None or _owner_update_caption is None or _get_tags is None:
        raise RuntimeError("Caption routes are not configured")


def invalidate_caption_status_cache() -> None:
    global _caption_counts_refreshing
    _caption_counts_cache.update({"data": None, "key": None, "expires": 0.0})
    _caption_counts_refreshing = False


def _minimal_caption_counts(worker: dict) -> dict:
    return {
        "captioned": 0,
        "pending_cached_images": int(worker.get("pending_cached_images") or 0),
        "done": 0,
        "error": 0,
        "pending": 0,
        "scan": {},
    }


def _schedule_caption_counts_refresh(caption_config: dict) -> asyncio.Task | None:
    global _caption_counts_refreshing
    if _caption_counts_refreshing:
        return None
    _caption_counts_refreshing = True
    model_key = str(caption_config["model_key"])

    async def refresh() -> None:
        global _caption_counts_refreshing
        try:
            counts = await _get_caption_status_counts(caption_config=caption_config)
            _caption_counts_cache.update({
                "data": dict(counts or {}),
                "key": model_key,
                "expires": time.monotonic() + _CAPTION_COUNTS_TTL_SECONDS,
            })
        except Exception:
            log.exception("worker=caption operation=status-refresh failed")
        finally:
            _caption_counts_refreshing = False

    return track_background_task(refresh())


async def _cached_caption_counts(caption_config: dict, worker: dict) -> tuple[dict, bool]:
    model_key = str(caption_config["model_key"])
    cached = _caption_counts_cache.get("data")
    matches = _caption_counts_cache.get("key") == model_key
    fresh = float(_caption_counts_cache.get("expires") or 0.0) > time.monotonic()
    if not matches or not fresh:
        refresh_task = _schedule_caption_counts_refresh(caption_config)
        # Preserve already-cheap stored counts on a cold start, but never let a
        # contended catalog turn status polling into foreground work. asyncio.wait
        # leaves a slow refresh running instead of cancelling its SQLite cleanup.
        if not matches and refresh_task is not None:
            await asyncio.wait(
                {refresh_task},
                timeout=_CAPTION_COUNTS_INITIAL_WAIT_SECONDS,
            )
        cached = _caption_counts_cache.get("data")
        matches = _caption_counts_cache.get("key") == model_key
        fresh = float(_caption_counts_cache.get("expires") or 0.0) > time.monotonic()
    if matches and isinstance(cached, dict):
        return dict(cached), not fresh
    return _minimal_caption_counts(worker), True


async def caption_status_payload() -> dict:
    _configured()
    config = settings.get_settings()
    caption_config = settings.active_caption_config(config)
    worker = caption_worker.get_worker_status()
    capability = capabilities.capability_status("captions")
    if not capability["available"]:
        worker = {
            **worker,
            "state": "unavailable",
            "ready": False,
            "running": False,
            "message": capability["message"],
            "last_error": "",
        }
    counts, counts_stale = await _cached_caption_counts(caption_config, worker)
    return {
        "capability": capability,
        "active": capability["available"]
        and bool(config.get("caption_scan_enabled", False))
        and not caption_worker.manual_pause_active(),
        "automatic": False,
        "model_id": caption_config["model_id"],
        "model_key": caption_config["model_key"],
        "model_dir": caption_config["model_dir"],
        "quantization": caption_config["quantization"],
        "prompt_version": caption_config["prompt_version"],
        "source_files_preserved": True,
        "source_media_read": "app_owned_cached_previews_only",
        "gpu_policy": "single_gpu_owner_sequential_with_embeddings",
        "worker": worker,
        "counts": counts,
        "counts_stale": counts_stale,
        "status_stale": counts_stale,
    }


@router.get("/api/captions/status")
async def api_captions_status():
    return await caption_status_payload()


@router.get("/api/tags")
async def api_tags(limit: int = 100, q: str = ""):
    _caption_routes_configured()
    return {"tags": await _get_tags(q=q, limit=limit)}


@router.get("/api/image/{image_id}/caption")
async def api_image_caption(image_id: int):
    _caption_routes_configured()
    caption = await _get_image_caption(image_id=image_id)
    if caption is None:
        return {
            "image_id": int(image_id),
            "caption": "",
            "tags": [],
            "quality": "",
            "user_edited": False,
            "has_caption": False,
        }
    return {**caption, "has_caption": True}


@router.post("/api/image/{image_id}/caption")
async def api_update_image_caption(image_id: int, body: CaptionBody):
    _caption_routes_configured()
    caption = await _owner_update_caption(
        image_id=image_id,
        caption=body.caption,
        tags=body.tags,
    )
    if caption is None:
        return JSONResponse({"error": "Image not found"}, status_code=404)
    return {"ok": True, "caption": {**caption, "has_caption": True}}


@router.post("/api/captions/scan/pause")
async def api_pause_captions():
    _configured()
    try:
        caption_worker.pause_caption_worker()
    except Exception:
        log.exception("worker=caption operation=pause failed")
        return JSONResponse(
            {
                "error": "Captions could not be paused",
                "detail": "Check Background Work status and try again.",
            },
            status_code=503,
        )
    _invalidate_settings_response_cache()
    return {"ok": True, "captions_status": await caption_status_payload()}


@router.post("/api/captions/scan/resume")
async def api_resume_captions():
    _configured()
    capability = capabilities.capability_status("captions")
    if not capability["available"]:
        return JSONResponse(capabilities.unavailable_response("captions"), status_code=409)
    try:
        caption_worker.resume_caption_worker()
    except Exception:
        log.exception("worker=caption operation=resume failed")
        return JSONResponse(
            {
                "error": "Captions could not be started",
                "detail": "Check Background Work status and try again.",
            },
            status_code=503,
        )
    _invalidate_settings_response_cache()
    return {"ok": True, "captions_status": await caption_status_payload()}
