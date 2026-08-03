from collections.abc import Awaitable, Callable
import asyncio
import time

from fastapi import APIRouter
from fastapi.responses import JSONResponse

import ai_models
import db
import settings
from core import capabilities
from core import responses as response_helpers


from features.settings import status as settings_status
router = APIRouter()
AsyncDictBuilder = Callable[..., Awaitable[dict]]
AsyncListBuilder = Callable[..., Awaitable[list]]
AsyncIntBuilder = Callable[..., Awaitable[int]]
InvalidateStatus = Callable[[], None]
_ai_status_response_cache: dict[str, dict | tuple | float | None] = {"data": None, "key": None, "expires": 0}
_ai_status_response_cache_ttl_seconds = 5.0
_ai_status_response_refreshing = False




def invalidate_ai_status_response_cache() -> None:
    global _ai_status_response_refreshing
    _ai_status_response_cache["data"] = None
    _ai_status_response_cache["key"] = None
    _ai_status_response_cache["expires"] = 0
    _ai_status_response_refreshing = False


def embedding_runtime_status(capability: dict | None = None) -> dict:
    capability = capability or capabilities.capability_status("search")
    config = settings.active_embedding_config()
    needs_bitsandbytes = config["model_id"] == "Qwen/Qwen3-VL-Embedding-8B"
    missing_bitsandbytes = "bitsandbytes" in capability.get("optional_missing", ())
    ready = bool(capability["available"]) and not (needs_bitsandbytes and missing_bitsandbytes)
    if ready:
        message = "The configured search model can run on this system."
    elif not capability["available"]:
        message = capability["message"]
    else:
        message = (
            "The configured 8B search model needs bitsandbytes. Install the search pack "
            "on Linux x86-64 or select the compact 2B search model."
        )
    return {
        "ready": ready,
        "model_id": config["model_id"],
        "requires_bitsandbytes": needs_bitsandbytes,
        "message": message,
    }


def _ai_model_status_cache_key(
    model_status: dict,
    capability: dict | None = None,
    runtime: dict | None = None,
) -> tuple:
    capability = capability or capabilities.capability_status("search")
    runtime = runtime or embedding_runtime_status(capability)
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
        bool(capability["available"]),
        tuple(capability["missing"]),
        bool(runtime["ready"]),
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
    capability = capabilities.capability_status("search")
    runtime = embedding_runtime_status(capability)
    model_status = model_status or ai_models.get_model_status()
    cache_key = _ai_model_status_cache_key(model_status, capability, runtime)
    if not force:
        cached = _ai_status_response_cache.get("data")
        if (
            cached is not None
            and _ai_status_response_cache.get("key") == cache_key
        ):
            if float(_ai_status_response_cache.get("expires") or 0) <= time.monotonic():
                _refresh_ai_status_response_cache(model_status)
            return _copy_ai_status_response(cached)
    counts = await db.get_ai_status_counts()
    embedded = counts["embedded"]
    total_images = counts["total_images"]
    remaining = max(total_images - embedded, 0)
    active_config = settings.active_embedding_config()
    active_model_status = ai_models.get_model_status(active_config)

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
        if not runtime["ready"]:
            raise ImportError(runtime["message"])
        import embedding_worker
        worker_status = embedding_worker.get_worker_status()
    except Exception:
        worker_status = {
            "state": "unavailable",
            "message": runtime["message"],
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
        }

    compared = int(counts.get("rated_images") or 0)

    recent_rate = float(worker_status.get("recent_images_per_min") or 0.0)
    overall_rate = float(worker_status.get("overall_images_per_min") or 0.0)
    effective_rate = recent_rate if recent_rate > 0 else overall_rate
    eta_seconds = int((remaining / effective_rate) * 60) if remaining > 0 and effective_rate > 0 else None
    progress_pct = round((embedded / total_images) * 100, 1) if total_images > 0 else 0.0
    embedding_index = {
        "role": "active",
        "label": "Search",
        "description": "Active local image and text embedding index.",
        "model_id": active_config["model_id"],
        "model_key": active_config["model_key"],
        "model_dir": active_config["model_dir"],
        "dimension": int(active_config["dimension"]),
        **index_install_fields(active_model_status),
        "embedded": embedded,
        "total_images": total_images,
        "remaining": remaining,
        "progress_pct": progress_pct,
        "worker_state": worker_status["state"],
        "worker_message": worker_status["message"],
        "manual_pause": bool(worker_status.get("manual_pause")),
        "capability": capability,
        "runtime": runtime,
    }

    response = {
        "capability": capability,
        "runtime": runtime,
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
        "automatic": bool(settings.get_settings().get("embedding_scan_enabled", True)),
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
        "embedding_index": embedding_index,
        "eta_seconds": eta_seconds,
    }
    _ai_status_response_cache["data"] = _copy_ai_status_response(response)
    _ai_status_response_cache["key"] = cache_key
    _ai_status_response_cache["expires"] = time.monotonic() + _ai_status_response_cache_ttl_seconds
    return response


@router.post("/api/ai/embeddings/pause")
async def api_pause_embeddings():
    capability = capabilities.capability_status("search")
    if not capability["available"]:
        return JSONResponse(capabilities.unavailable_response("search"), status_code=409)
    try:
        import embedding_worker
    except ImportError:
        return JSONResponse({"error": "Embeddings not available"}, status_code=503)
    embedding_worker.pause_embedding_worker()
    invalidate_ai_status_response_cache()
    settings_status.invalidate_settings_response_cache()
    return {"ok": True, "ai_status": await build_ai_status(force=True)}


@router.post("/api/ai/embeddings/resume")
async def api_resume_embeddings():
    capability = capabilities.capability_status("search")
    if not capability["available"]:
        return JSONResponse(capabilities.unavailable_response("search"), status_code=409)
    runtime = embedding_runtime_status(capability)
    if not runtime["ready"]:
        return JSONResponse(
            {
                "error": runtime["message"],
                "capability": capability,
                "runtime": runtime,
                "install_command": capability["install_command"],
            },
            status_code=409,
        )
    try:
        import embedding_worker
        import db
    except ImportError:
        return JSONResponse({"error": "Embeddings not available"}, status_code=503)
    try:
        import thumbnails
        thumbnails.start_pregeneration()
    except ImportError:
        pass
    await db.clear_embedding_poison_ledger(settings.active_embedding_config())
    embedding_worker.resume_embedding_worker()
    invalidate_ai_status_response_cache()
    settings_status.invalidate_settings_response_cache()
    return {"ok": True, "ai_status": await build_ai_status(force=True)}


@router.post("/api/ai/model/install")
async def api_install_ai_model(role: str = "fast"):
    del role
    capability = capabilities.capability_status("search")
    if not capability["available"]:
        return JSONResponse(capabilities.unavailable_response("search"), status_code=409)
    selected_role = "active"
    install_config = settings.active_embedding_config()
    existing_status = ai_models.get_model_status(install_config)
    if existing_status.get("installed"):
        return {
            "ok": True,
            "role": selected_role,
            "already_installed": True,
            "install": existing_status.get("install", {}),
            "model_status": existing_status,
            "ai_status": await build_ai_status(force=True),
        }
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
    return await build_ai_status()
