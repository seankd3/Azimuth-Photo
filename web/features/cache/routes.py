from collections.abc import Awaitable, Callable
import asyncio

from fastapi import APIRouter
from fastapi.responses import JSONResponse

import thumbnails
from features.cache import status as cache_status_service


router = APIRouter()
BuildAiStatus = Callable[..., Awaitable[dict]]
_build_ai_status: BuildAiStatus | None = None
CACHE_UNAVAILABLE_MESSAGE = "Preview cache is unavailable. Your photos are safe — try again."


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


def _cache_unavailable_response() -> JSONResponse:
    return JSONResponse(
        {"error": CACHE_UNAVAILABLE_MESSAGE, "available": False},
        status_code=503,
    )


@router.get("/api/cache/status")
async def cache_status(ahead: int = 0):
    refresh = asyncio.create_task(cache_status_service.build_cache_status(ahead=ahead))
    try:
        return await asyncio.wait_for(asyncio.shield(refresh), timeout=0.9)
    except asyncio.TimeoutError:
        async def _finish_refresh():
            try:
                await refresh
            except Exception:
                pass

        asyncio.create_task(_finish_refresh())
        stale = cache_status_service.cached_cache_status(
            ahead,
            stale_reason="status_refresh_timeout",
        )
        if stale is not None:
            return stale
        return cache_status_service.deferred_cache_status(
            ahead,
            reason="status_refresh_timeout",
        )
    except OSError:
        return _cache_unavailable_response()


@router.post("/api/cache/pregen/start")
async def cache_pregen_start():
    _configured()
    try:
        thumbnails.start_pregeneration()
    except OSError:
        return _cache_unavailable_response()
    cache_status_service.invalidate_cache_status_cache()
    return {"ok": True, "cache": await cache_status_service.build_cache_status(ahead=0, force=True)}


@router.post("/api/cache/pregen/stop")
async def cache_pregen_stop():
    _configured()
    thumbnails.stop_pregeneration()
    try:
        import embedding_worker
        embedding_worker.pause_embedding_worker("Search stopped because Previews stopped.")
    except ImportError:
        pass
    try:
        import face_worker
        face_worker.pause_face_worker()
    except ImportError:
        pass
    cache_status_service.invalidate_cache_status_cache()
    return {"ok": True, "cache": await cache_status_service.build_cache_status(ahead=0, force=True)}


@router.get("/api/cache/pregen/status")
async def cache_pregen_status():
    return (await cache_status_service.build_cache_status(ahead=0)).get("pregen", {})


@router.post("/api/cache/clear")
async def api_clear_thumbnail_cache():
    build_ai_status = _configured()
    try:
        result = thumbnails.clear_cache()
    except OSError:
        return _cache_unavailable_response()
    if result.get("refused"):
        return JSONResponse({"ok": False, **result}, status_code=400)
    cache_status_service.invalidate_cache_status_cache()
    return {
        "ok": True,
        **result,
        "cache_stats": await cache_status_service.build_cache_status(ahead=0, force=True),
        "ai_status": await build_ai_status(),
    }
