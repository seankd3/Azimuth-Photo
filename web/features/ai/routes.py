from collections.abc import Awaitable, Callable
import asyncio
import time

from fastapi import APIRouter
from fastapi.responses import JSONResponse

import ai_models
import resource_governor
import settings
import thumbnails
from core import responses as response_helpers


router = APIRouter()
BuildAiStatus = Callable[..., Awaitable[dict]]
AsyncDictBuilder = Callable[..., Awaitable[dict]]
AsyncListBuilder = Callable[..., Awaitable[list]]
AsyncIntBuilder = Callable[..., Awaitable[int]]
InvalidateStatus = Callable[[], None]
_invalidate_settings_response_cache: InvalidateStatus | None = None
_get_ai_status_counts: AsyncDictBuilder | None = None
_count_embeddings_for_model: AsyncIntBuilder | None = None
_get_deep_search_cache_status: AsyncDictBuilder | None = None
_list_deep_search_queries: AsyncListBuilder | None = None
_ai_status_response_cache: dict[str, dict | tuple | float | None] = {"data": None, "key": None, "expires": 0}
_ai_status_response_cache_ttl_seconds = 5.0
_ai_status_response_refreshing = False


def configure(
    *,
    invalidate_settings_response_cache: InvalidateStatus,
    get_ai_status_counts: AsyncDictBuilder,
    count_embeddings_for_model: AsyncIntBuilder,
    get_deep_search_cache_status: AsyncDictBuilder,
    list_deep_search_queries: AsyncListBuilder,
    build_ai_status: BuildAiStatus | None = None,
    invalidate_ai_status_response_cache: InvalidateStatus | None = None,
) -> None:
    del build_ai_status, invalidate_ai_status_response_cache
    global _invalidate_settings_response_cache, _get_ai_status_counts
    global _count_embeddings_for_model, _get_deep_search_cache_status
    global _list_deep_search_queries
    _invalidate_settings_response_cache = invalidate_settings_response_cache
    _get_ai_status_counts = get_ai_status_counts
    _count_embeddings_for_model = count_embeddings_for_model
    _get_deep_search_cache_status = get_deep_search_cache_status
    _list_deep_search_queries = list_deep_search_queries


def _configured() -> InvalidateStatus:
    if (
        _invalidate_settings_response_cache is None
        or _get_ai_status_counts is None
        or _count_embeddings_for_model is None
        or _get_deep_search_cache_status is None
        or _list_deep_search_queries is None
    ):
        raise RuntimeError("AI routes are not configured")
    return _invalidate_settings_response_cache


def invalidate_ai_status_response_cache() -> None:
    global _ai_status_response_refreshing
    _ai_status_response_cache["data"] = None
    _ai_status_response_cache["key"] = None
    _ai_status_response_cache["expires"] = 0
    _ai_status_response_refreshing = False


def _ai_model_status_cache_key(model_status: dict) -> tuple:
    install = model_status.get("install") or {}
    return (
        bool(model_status.get("installed")),
        str(model_status.get("model_id") or ""),
        str(model_status.get("model_dir") or ""),
        int(model_status.get("dimension") or 0),
        str(model_status.get("model_key") or ""),
        bool(install.get("running")),
        str(install.get("status") or ""),
        str(install.get("message") or ""),
    )


def _copy_ai_status_response(response: dict) -> dict:
    return response_helpers.copy_ai_status_response(response)


def _refresh_ai_status_response_cache(model_status: dict) -> bool:
    global _ai_status_response_refreshing
    if _ai_status_response_refreshing:
        return False
    _ai_status_response_refreshing = True

    async def _refresh():
        global _ai_status_response_refreshing
        try:
            # Let the stale response flush before refresh work competes for the event loop.
            await asyncio.sleep(0.25)
            await build_ai_status(model_status, force=True)
        except Exception as exc:
            print(f"AI status refresh error: {exc}")
        finally:
            _ai_status_response_refreshing = False

    try:
        asyncio.create_task(_refresh())
        return True
    except Exception:
        _ai_status_response_refreshing = False
        raise


async def build_ai_status(model_status: dict | None = None, *, force: bool = False):
    """Embedding worker + model install status for UI surfaces."""
    model_status = model_status or ai_models.get_model_status()
    cache_key = _ai_model_status_cache_key(model_status)
    if not force:
        cached = _ai_status_response_cache.get("data")
        if (
            cached is not None
            and _ai_status_response_cache.get("key") == cache_key
        ):
            if float(_ai_status_response_cache.get("expires") or 0) <= time.monotonic():
                _refresh_ai_status_response_cache(model_status)
            return _copy_ai_status_response(cached)

    _configured()
    counts = await _get_ai_status_counts()
    embedded = counts["embedded"]
    total_images = counts["total_images"]
    remaining = max(total_images - embedded, 0)
    fast_config = settings.fast_search_embedding_config()
    deep_config = settings.deep_search_embedding_config()
    fast_model_status = ai_models.get_model_status(fast_config)
    deep_model_status = ai_models.get_model_status(deep_config)

    def index_install_fields(status: dict) -> dict:
        install = status.get("install") or {}
        install_applies = (
            bool(install.get("model_dir"))
            and str(install.get("model_dir")) == str(status.get("model_dir"))
        )
        return {
            "installed": bool(status.get("installed")),
            "installing": bool(install.get("running")) and install_applies,
            "install_status": str(install.get("status") or "idle") if install_applies else "idle",
            "install_message": str(install.get("message") or "") if install_applies else "",
        }

    worker_status = {}
    try:
        import embedding_worker
        worker_status = embedding_worker.get_worker_status()
    except Exception:
        worker_status = {
            "state": "unavailable",
            "message": "AI worker unavailable",
            "ready": False,
            "manual_pause": False,
            "model_id": "",
            "model_dir": "",
            "last_error": "",
            "last_batch_size": 0,
            "last_batch_seconds": 0.0,
            "last_embedded_at": None,
            "session_embedded": 0,
            "session_started_at": None,
            "session_embed_seconds": 0.0,
            "session_wall_seconds": 0.0,
            "recent_images_per_min": 0.0,
            "recent_wall_images_per_min": 0.0,
            "overall_images_per_min": 0.0,
            "overall_wall_images_per_min": 0.0,
            "active_batch_size": 0,
            "target_batch_size": 0,
            "successful_batches_at_size": 0,
            "last_batch_failures": 0,
            "last_batch_stage_seconds": {},
            "last_candidate_query_seconds": 0.0,
            "last_candidate_count": 0,
            "last_candidate_window_size": 0,
            "last_ready_count": 0,
            "last_cooled_down_count": 0,
            "next_retry_at": None,
            "oom_backoffs": 0,
            "last_oom_at": None,
            "batch_growth_paused_until": None,
            "governor": resource_governor.get_background_decision(
                thumbnails.get_idle_seconds()
            ).to_dict(),
        }

    compared = int(counts.get("rated_images") or 0)

    recent_rate = float(worker_status.get("recent_images_per_min") or 0.0)
    overall_rate = float(worker_status.get("overall_images_per_min") or 0.0)
    effective_rate = recent_rate if recent_rate > 0 else overall_rate
    eta_seconds = int((remaining / effective_rate) * 60) if remaining > 0 and effective_rate > 0 else None
    progress_pct = round((embedded / total_images) * 100, 1) if total_images > 0 else 0.0
    deep_embedded = (
        await _count_embeddings_for_model(deep_config, online_only=True)
        if total_images > 0
        else 0
    )
    deep_remaining = max(total_images - deep_embedded, 0)
    deep_progress_pct = round((deep_embedded / total_images) * 100, 1) if total_images > 0 else 0.0
    try:
        deep_cache_counts = await _get_deep_search_cache_status(deep_config, None)
        deep_queries = await _list_deep_search_queries(deep_config)
    except Exception:
        deep_cache_counts = {"pending_queries": 0, "embedded_queries": 0}
        deep_queries = []
    deep_worker = worker_status.get("deep_search") or {}
    embedding_indexes = {
        "fast": {
            "role": "fast",
            "label": "Daily Search",
            "description": "Fast 2B image and text embeddings for normal browsing.",
            "model_id": fast_config["model_id"],
            "model_key": fast_config["model_key"],
            "model_dir": fast_config["model_dir"],
            "dimension": int(fast_config["dimension"]),
            **index_install_fields(fast_model_status),
            "embedded": embedded,
            "total_images": total_images,
            "remaining": remaining,
            "progress_pct": progress_pct,
            "worker_state": worker_status["state"],
            "worker_message": worker_status["message"],
            "manual_pause": bool(worker_status.get("manual_pause")),
        },
        "deep": {
            "role": "deep",
            "label": "Deep Search",
            "description": "Smarter scheduled 8B image index and saved-query cache.",
            "model_id": deep_config["model_id"],
            "model_key": deep_config["model_key"],
            "model_dir": deep_config["model_dir"],
            "dimension": int(deep_config["dimension"]),
            **index_install_fields(deep_model_status),
            "embedded": deep_embedded,
            "total_images": total_images,
            "remaining": deep_remaining,
            "progress_pct": deep_progress_pct,
            "worker_state": deep_worker.get("state") or "idle",
            "worker_message": deep_worker.get("message") or "",
            "schedule": deep_worker.get("schedule") or settings.deep_search_schedule_status(settings.get_settings()),
            "pending_queries": int(deep_cache_counts.get("pending_queries") or 0),
            "embedded_queries": int(deep_cache_counts.get("embedded_queries") or 0),
            "queries": deep_queries,
        },
    }

    response = {
        "embedded": embedded,
        "total_images": total_images,
        "total_kept": total_images,
        "remaining": remaining,
        "progress_pct": progress_pct,
        "compared": compared,
        "rated_images": compared,
        "direct_comparison_rows": int(counts.get("direct_comparison_rows") or 0),
        "ranking_signal_count": int(counts.get("ranking_signal_count") or 0),
        "imported_ranking_without_history": int(counts.get("imported_ranking_without_history") or 0),
        "model_installed": model_status["installed"],
        "installing": model_status["install"]["running"],
        "install_status": model_status["install"]["status"],
        "install_message": model_status["install"]["message"],
        "model_id": model_status["model_id"],
        "model_dir": model_status["model_dir"],
        "model_key": model_status.get("model_key", ""),
        "model_dimension": int(model_status.get("dimension") or 0),
        "worker_state": worker_status["state"],
        "worker_message": worker_status["message"],
        "worker_ready": worker_status["ready"],
        "embedding_manual_pause": bool(worker_status.get("manual_pause")),
        "worker_error": worker_status["last_error"],
        "last_batch_size": worker_status.get("last_batch_size", 0),
        "last_batch_seconds": worker_status.get("last_batch_seconds", 0.0),
        "last_embedded_at": worker_status.get("last_embedded_at"),
        "session_embedded": worker_status.get("session_embedded", 0),
        "session_started_at": worker_status.get("session_started_at"),
        "session_embed_seconds": worker_status.get("session_embed_seconds", 0.0),
        "session_wall_seconds": worker_status.get("session_wall_seconds", 0.0),
        "recent_images_per_min": recent_rate,
        "recent_wall_images_per_min": float(worker_status.get("recent_wall_images_per_min") or 0.0),
        "overall_images_per_min": overall_rate,
        "overall_wall_images_per_min": float(worker_status.get("overall_wall_images_per_min") or 0.0),
        "active_batch_size": worker_status.get("active_batch_size", 0),
        "target_batch_size": worker_status.get("target_batch_size", 0),
        "successful_batches_at_size": worker_status.get("successful_batches_at_size", 0),
        "last_batch_failures": worker_status.get("last_batch_failures", 0),
        "last_batch_stage_seconds": worker_status.get("last_batch_stage_seconds") or {},
        "last_candidate_query_seconds": worker_status.get("last_candidate_query_seconds", 0.0),
        "last_candidate_count": worker_status.get("last_candidate_count", 0),
        "last_candidate_window_size": worker_status.get("last_candidate_window_size", 0),
        "last_ready_count": worker_status.get("last_ready_count", 0),
        "last_cooled_down_count": worker_status.get("last_cooled_down_count", 0),
        "next_retry_at": worker_status.get("next_retry_at"),
        "oom_backoffs": worker_status.get("oom_backoffs", 0),
        "last_oom_at": worker_status.get("last_oom_at"),
        "batch_growth_paused_until": worker_status.get("batch_growth_paused_until"),
        "deep_search": worker_status.get("deep_search") or {},
        "embedding_indexes": embedding_indexes,
        "eta_seconds": eta_seconds,
        "governor": worker_status.get("governor") or resource_governor.get_background_decision(
            thumbnails.get_idle_seconds()
        ).to_dict(),
    }
    _ai_status_response_cache["data"] = _copy_ai_status_response(response)
    _ai_status_response_cache["key"] = cache_key
    _ai_status_response_cache["expires"] = time.monotonic() + _ai_status_response_cache_ttl_seconds
    return response


@router.post("/api/ai/embeddings/pause")
async def api_pause_embeddings():
    invalidate_settings_response_cache = _configured()
    try:
        import embedding_worker
    except ImportError:
        return JSONResponse({"error": "Embeddings not available"}, status_code=503)
    embedding_worker.pause_embedding_worker()
    invalidate_ai_status_response_cache()
    invalidate_settings_response_cache()
    return {"ok": True, "ai_status": await build_ai_status(force=True)}


@router.post("/api/ai/embeddings/resume")
async def api_resume_embeddings():
    invalidate_settings_response_cache = _configured()
    try:
        import embedding_worker
    except ImportError:
        return JSONResponse({"error": "Embeddings not available"}, status_code=503)
    embedding_worker.resume_embedding_worker()
    invalidate_ai_status_response_cache()
    invalidate_settings_response_cache()
    return {"ok": True, "ai_status": await build_ai_status(force=True)}


@router.post("/api/ai/model/install")
async def api_install_ai_model(role: str = "fast"):
    _configured()
    selected_role = "deep" if str(role or "").lower() == "deep" else "fast"
    install_config = (
        settings.deep_search_embedding_config()
        if selected_role == "deep"
        else settings.fast_search_embedding_config()
    )
    state = ai_models.start_model_install(install_config)
    active_install_dir = str(state.get("model_dir") or "")
    requested_install_dir = str(install_config["model_dir"] or "")
    if (
        state.get("running")
        and active_install_dir
        and requested_install_dir
        and active_install_dir != requested_install_dir
    ):
        return JSONResponse(
            {
                "ok": False,
                "role": selected_role,
                "error": f"Another model install is already running: {state.get('model_id') or active_install_dir}",
                "install": state,
                "model_status": ai_models.get_model_status(install_config),
                "ai_status": await build_ai_status(force=True),
            },
            status_code=409,
        )
    return {
        "ok": True,
        "role": selected_role,
        "install": state,
        "model_status": ai_models.get_model_status(install_config),
        "ai_status": await build_ai_status(force=True),
    }


@router.get("/api/ai/status")
async def ai_status():
    """Embedding worker and taste model status for the bottom bar."""
    _configured()
    return await build_ai_status()
