from collections.abc import Awaitable, Callable
import asyncio

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
    _configured()
    thumbnails.start_pregeneration()
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
    """Ops/board poller — never block on a full cache rebuild.

    Kick a background refresh when the cached snapshot is missing/stale, but
    always answer immediately from live worker fields (+ cached tier counts when
    available). A 3s board poller must not contend with bulk meta-lock writers.
    """
    stale = cache_status_service.cached_cache_status(0)
    if stale is None or not isinstance(stale.get("pregen"), dict):
        # One background warm — never await on the poll path.
        if not getattr(cache_pregen_status, "_warm_started", False):
            cache_pregen_status._warm_started = True

            async def _warm():
                try:
                    await cache_status_service.build_cache_status(ahead=0)
                except Exception:
                    pass
                finally:
                    cache_pregen_status._warm_started = False

            asyncio.create_task(_warm())
        live = dict(getattr(thumbnails, "_pregen_status", {}) or {})
        return {
            "enabled": True,
            "manual_mode": bool(live.get("manual_mode", True)),
            "manual_pause": bool(live.get("manual_pause", False)),
            "state": live.get("state") or "unknown",
            "message": live.get("message") or "Cache status is refreshing.",
            "active_phase": live.get("active_phase"),
            "started_at": live.get("started_at"),
            "last_generated_at": live.get("last_generated_at"),
            "generated_this_session": int(live.get("generated_this_session") or 0),
            "last_error": live.get("last_error") or "",
            "priority_scope": live.get("priority_scope"),
            "idle_seconds": round(float(thumbnails.get_idle_seconds()), 2),
            "preview": {"count": 0, "total": 0, "remaining": 0, "progress_pct": 0.0},
        }

    # Cached full snapshot is present — overlay live worker fields so boards
    # see generation counters move without waiting on meta-lock stats.
    pregen = dict(stale["pregen"])
    live = getattr(thumbnails, "_pregen_status", {}) or {}
    for key in (
        "state",
        "message",
        "active_phase",
        "started_at",
        "last_generated_at",
        "generated_this_session",
        "last_error",
        "priority_scope",
        "manual_mode",
        "manual_pause",
    ):
        if key in live:
            pregen[key] = live[key]
    pregen["idle_seconds"] = round(float(thumbnails.get_idle_seconds()), 2)
    return pregen


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
