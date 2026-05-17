from collections.abc import Awaitable, Callable

from fastapi import APIRouter
from fastapi.responses import JSONResponse

import thumbnails
from features.cache import status as cache_status_service


router = APIRouter()
BuildAiStatus = Callable[..., Awaitable[dict]]
_build_ai_status: BuildAiStatus | None = None


def configure(
    *,
    build_ai_status: BuildAiStatus,
) -> None:
    global _build_ai_status
    _build_ai_status = build_ai_status


def _configured() -> BuildAiStatus:
    if _build_ai_status is None:
        raise RuntimeError("Cache routes are not configured")
    return _build_ai_status


@router.get("/api/cache/status")
async def cache_status(ahead: int = 0):
    return await cache_status_service.build_cache_status(ahead=ahead)


@router.post("/api/cache/pregen/start")
async def cache_pregen_start():
    _configured()
    thumbnails.start_pregeneration()
    cache_status_service.invalidate_cache_status_cache()
    return {"ok": True, "cache": await cache_status_service.build_cache_status(ahead=0, force=True)}


@router.post("/api/cache/pregen/stop")
async def cache_pregen_stop():
    _configured()
    thumbnails.stop_pregeneration()
    cache_status_service.invalidate_cache_status_cache()
    return {"ok": True, "cache": await cache_status_service.build_cache_status(ahead=0, force=True)}


@router.get("/api/cache/pregen/status")
async def cache_pregen_status():
    return (await cache_status_service.build_cache_status(ahead=0)).get("pregen", {})


@router.post("/api/cache/clear")
async def api_clear_thumbnail_cache():
    build_ai_status = _configured()
    result = thumbnails.clear_cache()
    if result.get("refused"):
        return JSONResponse({"ok": False, **result}, status_code=400)
    cache_status_service.invalidate_cache_status_cache()
    return {
        "ok": True,
        **result,
        "cache_stats": await cache_status_service.build_cache_status(ahead=0, force=True),
        "ai_status": await build_ai_status(),
    }
