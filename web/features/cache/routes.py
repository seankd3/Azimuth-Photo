import asyncio

from fastapi import APIRouter
from fastapi.responses import JSONResponse

import thumbnails
from features.cache import status as cache_status_service


from features.ai import routes as ai_routes


router = APIRouter()


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


@router.post("/api/cache/pregen/start")
async def cache_pregen_start():
    thumbnails.start_pregeneration()
    cache_status_service.invalidate_cache_status_cache()
    return {"ok": True, "cache": await cache_status_service.build_cache_status(ahead=0, force=True)}


@router.post("/api/cache/pregen/stop")
async def cache_pregen_stop():
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


@router.post("/api/cache/clear")
async def api_clear_thumbnail_cache():
    result = thumbnails.clear_cache()
    if result.get("refused"):
        return JSONResponse({"ok": False, **result}, status_code=400)
    cache_status_service.invalidate_cache_status_cache()
    return {
        "ok": True,
        **result,
        "cache_stats": await cache_status_service.build_cache_status(ahead=0, force=True),
        "ai_status": await ai_routes.build_ai_status(),
    }
