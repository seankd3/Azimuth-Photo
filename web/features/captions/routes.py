from collections.abc import Awaitable, Callable

from fastapi import APIRouter
from fastapi.responses import JSONResponse

import caption_worker
import settings


router = APIRouter()
AsyncDictBuilder = Callable[..., Awaitable[dict]]
InvalidateStatus = Callable[[], None]

_get_caption_status_counts: AsyncDictBuilder | None = None
_invalidate_settings_response_cache: InvalidateStatus | None = None


def configure(
    *,
    get_caption_status_counts: AsyncDictBuilder,
    invalidate_settings_response_cache: InvalidateStatus,
) -> None:
    global _get_caption_status_counts, _invalidate_settings_response_cache
    _get_caption_status_counts = get_caption_status_counts
    _invalidate_settings_response_cache = invalidate_settings_response_cache


def _configured() -> None:
    if _get_caption_status_counts is None or _invalidate_settings_response_cache is None:
        raise RuntimeError("Caption routes are not configured")


async def caption_status_payload() -> dict:
    _configured()
    config = settings.get_settings()
    caption_config = settings.active_caption_config(config)
    worker = caption_worker.get_worker_status()
    counts = await _get_caption_status_counts(caption_config=caption_config)
    return {
        "active": bool(config.get("caption_scan_enabled", False))
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
    }


@router.get("/api/captions/status")
async def api_captions_status():
    return await caption_status_payload()


@router.post("/api/captions/scan/pause")
async def api_pause_captions():
    _configured()
    try:
        caption_worker.pause_caption_worker()
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=503)
    _invalidate_settings_response_cache()
    return {"ok": True, "captions_status": await caption_status_payload()}


@router.post("/api/captions/scan/resume")
async def api_resume_captions():
    _configured()
    try:
        caption_worker.resume_caption_worker()
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=503)
    _invalidate_settings_response_cache()
    return {"ok": True, "captions_status": await caption_status_payload()}
