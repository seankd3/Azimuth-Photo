from collections.abc import Awaitable, Callable

from fastapi import APIRouter
from fastapi.responses import JSONResponse

import ai_models
import db
import settings
import work
from core import capabilities
from core.catalog_path import catalog_path


from data import connection
from features.settings import status as settings_status
router = APIRouter()
AsyncDictBuilder = Callable[..., Awaitable[dict]]
AsyncListBuilder = Callable[..., Awaitable[list]]
InvalidateStatus = Callable[[], None]
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


def _chore_state() -> tuple[bool, int] | None:
    """Whether chores are paused and how many embeddings are owed, or None.

    None means the catalog could not be read — an unwritable or non-WAL file, a
    library that is not there yet. A status panel that raises because it could
    not read a status is worse than one that says it does not know, so the
    caller degrades instead.
    """

    import search

    try:
        conn = connection.reading(catalog_path())
        # Owed under the model actually in use. Asked without one it counted
        # every photograph in the catalog -- 144,271, more than the catalog
        # holds -- because the 42,937 vectors already there were stored under a
        # recipe naming no model and matched nothing.
        return work.paused(conn), work.owing(
            conn, search.EMBEDDING, recipe={"model": search.active_model()}
        )
    except Exception:
        return None


async def build_ai_status(model_status: dict | None = None):
    """Embedding worker + model install status for UI surfaces."""
    capability = capabilities.capability_status("search")
    runtime = embedding_runtime_status(capability)
    model_status = model_status or ai_models.get_model_status()
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

    # `embedding_worker.get_worker_status()` stood here, behind a try that
    # turned its ImportError into a thirty-field dict of zeros — batch growth,
    # OOM backoffs, candidate window sizes, session throughput. The module went
    # with the other three workers in 6fc7e31c, so the panel had been reading
    # that dict ever since, and `eta_seconds` was computed from its zero rate
    # and so was always None.
    #
    # There is one loop now and its queue is a query, so its state is a fact
    # about the catalog rather than telemetry a worker has to keep: paused if
    # the owner paused chores, running while anything is owed, idle otherwise.
    chores = _chore_state()
    manual_pause = bool(chores and chores[0])
    if not runtime["ready"]:
        state, message = "unavailable", runtime["message"]
    elif chores is None:
        state, message = "unknown", "The catalog could not be read."
    elif manual_pause:
        state, message = "paused", "Chores are paused."
    elif chores[1]:
        state, message = "running", f"{chores[1]:,} to embed."
    else:
        state, message = "idle", "Everything is embedded."
    compared = int(counts.get("rated_images") or 0)

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
        "worker_state": state,
        "worker_message": message,
        "manual_pause": manual_pause,
        "capability": capability,
        "runtime": runtime,
    }

    response = {
        "capability": capability,
        "runtime": runtime,
        "embedded": embedded,
        "total_images": total_images,
        "remaining": remaining,
        "progress_pct": progress_pct,
        "compared": compared,
        "model_installed": model_status["installed"],
        "installing": model_status["install"]["running"],
        "install_status": model_status["install"]["status"],
        "install_message": model_status["install"]["message"],
        "model_id": model_status["model_id"],
        "model_dir": model_status["model_dir"],
        "model_dimension": int(model_status.get("dimension") or 0),
        "worker_state": state,
        "worker_message": message,
        "embedding_manual_pause": manual_pause,
        "worker_error": "",
        "embedding_index": embedding_index,
    }
    return response


@router.post("/api/ai/embeddings/pause")
async def api_pause_embeddings():
    """Stop making embeddings — which is to say, stop chores.

    This imported `embedding_worker` and 503'd when it was not there, which it
    has not been since 6fc7e31c. The drawer's pause button has been calling it
    the whole time. There is no separate embedding worker to pause now: one
    loop makes every kind of missing thing, so pausing embeddings *is* pausing
    chores, and that is a decision in the log — it survives a restart, because
    someone who paused work to save battery would not thank us for resuming it
    on the next launch.
    """

    return await _set_chores(paused=True)


@router.post("/api/ai/embeddings/resume")
async def api_resume_embeddings():
    capability = capabilities.capability_status("search")
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
    return await _set_chores(paused=False)


async def _set_chores(*, paused: bool) -> dict:
    capability = capabilities.capability_status("search")
    if not capability["available"]:
        return JSONResponse(capabilities.unavailable_response("search"), status_code=409)
    await connection.writing(catalog_path(), work.set_paused, paused)
    settings_status.invalidate_settings_response_cache()
    return {"ok": True, "ai_status": await build_ai_status()}


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
            "ai_status": await build_ai_status(),
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
                "ai_status": await build_ai_status(),
            },
            status_code=409,
        )
    return {
        "ok": True,
        "role": selected_role,
        "install": state,
        "model_status": ai_models.get_model_status(install_config),
        "ai_status": await build_ai_status(),
    }


@router.get("/api/ai/status")
async def ai_status():
    """Embedding worker and taste model status for the bottom bar."""
    return await build_ai_status()
