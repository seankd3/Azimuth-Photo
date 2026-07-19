"""Embedding worker for Azimuth Photo.

Background worker that embeds images using Qwen3-VL-Embedding-2B (int4).
Embeddings power: text search, find similar, Elo propagation, duplicate
detection, and auto-collections.
"""

from __future__ import annotations

import asyncio
import importlib.util
import logging
import time
from collections import deque
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import numpy as np

def _new_embed_executor() -> ThreadPoolExecutor:
    return ThreadPoolExecutor(max_workers=1, thread_name_prefix="embed-gpu")


def _new_preload_executor() -> ThreadPoolExecutor:
    return ThreadPoolExecutor(max_workers=1, thread_name_prefix="embed-preload")


# Dedicated executors — separate CPU prep from GPU encode
_embed_executor = _new_embed_executor()
_preload_executor = _new_preload_executor()

import ai_models
import embed_cache
import settings
import thumbnails
from core import memory_pressure, work_coordination
from workers.caption_health import CaptionOomCircuit

log = logging.getLogger("embedding_worker")
log.setLevel(logging.INFO)
if not log.handlers:
    log.addHandler(logging.StreamHandler())

EMBEDDING_DIM = 2048  # Default legacy dimension for Qwen3-VL-Embedding-2B
INITIAL_EMBED_BATCH_SIZE = 4
DEFAULT_EMBED_BATCH_SIZE = 8
EMBED_BATCH_GROWTH_SUCCESS_BATCHES = 12
EMBED_OOM_GROWTH_COOLDOWN_SECONDS = 600
EMBED_SPEED_WINDOW_SECONDS = 1800
EMBED_CANDIDATE_MULTIPLIER = 16
EMBED_RETRY_SECONDS = 600
MODEL_LOAD_FAILURE_RETRY_SECONDS = 300
EMBED_COUNT_LOG_INTERVAL_BATCHES = 20
STARTUP_DEFER_PAUSE_MESSAGE = "AI work deferred by startup setting."
STARTUP_DEFER_PAUSE_REASON = "startup_defer"
USER_PAUSE_REASON = "user"

# Module-level reference for text search (set by run_embedding_worker on startup)
_model = None
_loaded_model_dir = None
_loaded_model_id = None
_loaded_model_revision = None
_model_load_lock = None
_search_model_load_task = None
_search_model_residency_task = None
_model_load_retry_after = 0.0
_model_load_error_key = None
_worker_status = {
    "state": "idle",
    "message": "",
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
    "active_batch_size": INITIAL_EMBED_BATCH_SIZE,
    "target_batch_size": DEFAULT_EMBED_BATCH_SIZE,
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
_embedding_history = deque()
_embed_retry_after: dict[int, float] = {}
_embedding_oom_circuit = CaptionOomCircuit(threshold=3)
_embedding_manual_pause = True
_embedding_manual_pause_message = "Search is stopped until you start it from Background Work."
_embedding_pause_reason = ""
_unembedded_candidate_cursor = {"model_key": "", "after_id": 0}
_embedding_count_log_batches = 0
_batch_control = {
    "active_batch_size": INITIAL_EMBED_BATCH_SIZE,
    "successful_batches": 0,
    "oom_backoffs": 0,
    "last_oom_at": None,
    "growth_paused_until": None,
}

AsyncDictProvider = Callable[..., Awaitable[dict[str, Any]]]
AsyncIntProvider = Callable[..., Awaitable[int]]
AsyncListProvider = Callable[..., Awaitable[list[dict[str, Any]]]]
AsyncNoneProvider = Callable[..., Awaitable[None]]
_get_catalog_image_counts: AsyncDictProvider | None = None
_count_embeddings_for_model: AsyncIntProvider | None = None
_get_unembedded_images: AsyncListProvider | None = None
_store_embeddings_batch: AsyncNoneProvider | None = None
_poison_embedding_image: AsyncNoneProvider | None = None
_get_embedding_count: AsyncIntProvider | None = None


def configure(
    *,
    get_catalog_image_counts: AsyncDictProvider | None = None,
    count_embeddings_for_model: AsyncIntProvider | None = None,
    get_unembedded_images: AsyncListProvider | None = None,
    store_embeddings_batch: AsyncNoneProvider | None = None,
    poison_embedding_image: AsyncNoneProvider | None = None,
    get_embedding_count: AsyncIntProvider | None = None,
) -> None:
    global _get_catalog_image_counts
    global _count_embeddings_for_model, _get_unembedded_images
    global _store_embeddings_batch, _poison_embedding_image, _get_embedding_count
    if get_catalog_image_counts is not None:
        _get_catalog_image_counts = get_catalog_image_counts
    if count_embeddings_for_model is not None:
        _count_embeddings_for_model = count_embeddings_for_model
    if get_unembedded_images is not None:
        _get_unembedded_images = get_unembedded_images
    if store_embeddings_batch is not None:
        _store_embeddings_batch = store_embeddings_batch
    if poison_embedding_image is not None:
        _poison_embedding_image = poison_embedding_image
    if get_embedding_count is not None:
        _get_embedding_count = get_embedding_count


def _configured(provider, name: str):
    if provider is None:
        raise RuntimeError(f"embedding_worker is missing configured dependency: {name}")
    return provider


def _target_embed_batch_size(config: dict | None = None) -> int:
    config = config or settings.get_settings()
    try:
        target = int(config.get("embed_batch_size", DEFAULT_EMBED_BATCH_SIZE))
    except (TypeError, ValueError):
        target = DEFAULT_EMBED_BATCH_SIZE
    return max(1, min(32, target))


def _refresh_batch_status(config: dict | None = None) -> tuple[int, int]:
    target = _target_embed_batch_size(config)
    active = int(_batch_control.get("active_batch_size") or INITIAL_EMBED_BATCH_SIZE)
    active = max(1, min(active, target))
    _batch_control["active_batch_size"] = active
    _worker_status.update({
        "active_batch_size": active,
        "target_batch_size": target,
        "successful_batches_at_size": int(_batch_control.get("successful_batches") or 0),
        "oom_backoffs": int(_batch_control.get("oom_backoffs") or 0),
        "last_oom_at": _batch_control.get("last_oom_at"),
        "batch_growth_paused_until": _batch_control.get("growth_paused_until"),
    })
    return active, target


def _note_successful_embedding_batch(config: dict | None = None):
    active, target = _refresh_batch_status(config)
    if active >= target:
        _batch_control["successful_batches"] = 0
        _refresh_batch_status(config)
        return

    _batch_control["successful_batches"] += 1
    now = time.time()
    growth_paused_until = _batch_control.get("growth_paused_until")
    can_grow = growth_paused_until is None or now >= growth_paused_until
    if (
        can_grow
        and _batch_control["successful_batches"] >= EMBED_BATCH_GROWTH_SUCCESS_BATCHES
    ):
        _batch_control["active_batch_size"] = min(target, active + 1)
        _batch_control["successful_batches"] = 0
    _refresh_batch_status(config)


def _note_cuda_oom(config: dict | None = None) -> int:
    active, _target = _refresh_batch_status(config)
    now = time.time()
    _batch_control["active_batch_size"] = max(1, active // 2)
    _batch_control["successful_batches"] = 0
    _batch_control["oom_backoffs"] = int(_batch_control.get("oom_backoffs") or 0) + 1
    _batch_control["last_oom_at"] = now
    _batch_control["growth_paused_until"] = now + EMBED_OOM_GROWTH_COOLDOWN_SECONDS
    _refresh_batch_status(config)
    return int(_batch_control["active_batch_size"])


def _is_cuda_oom_error(error) -> bool:
    name = type(error).__name__.lower()
    text = str(error).lower()
    return (
        "outofmemoryerror" in name
        or "cuda out of memory" in text
        or ("cuda" in text and "out of memory" in text)
    )


def _is_sqlite_locked_error(error) -> bool:
    text = str(error).lower()
    return (
        "database is locked" in text
        or "database table is locked" in text
        or "database schema is locked" in text
    )


def _clear_cuda_cache():
    from core.ml_device import empty_cuda_cache

    empty_cuda_cache()


def _cancel_search_model_residency_task() -> asyncio.Task | None:
    global _search_model_residency_task

    task = _search_model_residency_task
    _search_model_residency_task = None
    if task is None or task.done():
        return task
    try:
        current_task = asyncio.current_task()
    except RuntimeError:
        current_task = None
    if task is not current_task:
        task.cancel()
    return task


def _release_embedding_owners() -> None:
    work_coordination.release_manual_owner("embeddings")
    work_coordination.release_gpu_owner("embeddings")


def _drop_embedding_residency() -> None:
    """Clear embedding globals. Called by ModelPool on unload/evict."""
    global _model, _loaded_model_dir, _loaded_model_id, _loaded_model_revision
    _cancel_search_model_residency_task()
    _model = None
    _loaded_model_dir = None
    _loaded_model_id = None
    _loaded_model_revision = None
    _clear_cuda_cache()
    _release_embedding_owners()


def _unload_model() -> asyncio.Task | None:
    from core.model_pool import get_model_pool

    # Cancel first so we can return the task to callers that await it;
    # pool unload also cancels via _drop, but the task handle is needed here.
    residency_task = _cancel_search_model_residency_task()
    if not get_model_pool().unload("embeddings"):
        # Drop without a second cancel — residency already cancelled above.
        global _model, _loaded_model_dir, _loaded_model_id, _loaded_model_revision
        _model = None
        _loaded_model_dir = None
        _loaded_model_id = None
        _loaded_model_revision = None
        _clear_cuda_cache()
        _release_embedding_owners()
    return residency_task


async def _maintain_search_model_residency() -> None:
    global _search_model_residency_task

    current_task = asyncio.current_task()
    heartbeat_seconds = work_coordination.LEASE_HEARTBEAT_SECONDS
    try:
        async with work_coordination.lease_heartbeat(
            "embeddings",
            gpu=True,
            interval_seconds=heartbeat_seconds,
        ):
            while _model is not None:
                await asyncio.sleep(heartbeat_seconds)
                if work_coordination.lost_ownership("embeddings", gpu=True):
                    _unload_model()
                    _set_worker_status(
                        "idle",
                        "Search model released for other background work.",
                        ready=False,
                    )
                    return
    finally:
        _release_embedding_owners()
        if _search_model_residency_task is current_task:
            _search_model_residency_task = None


def _start_search_model_residency_task() -> bool:
    global _search_model_residency_task

    if _model is None:
        return False
    task = _search_model_residency_task
    if task is not None and not task.done():
        return True
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return False
    _search_model_residency_task = loop.create_task(
        _maintain_search_model_residency()
    )
    return True


def _retain_search_model_residency(config: dict, model_id: str) -> bool:
    if work_coordination.lost_ownership("embeddings", gpu=True):
        _set_worker_status(
            "waiting_for_turn",
            "Search model released for other background work.",
            ready=False,
            config=config,
        )
        return False
    if not _start_search_model_residency_task():
        message = "Could not start search model residency heartbeat"
        _set_worker_status(
            "error",
            message,
            ready=False,
            last_error=message,
            config=config,
        )
        return False
    _set_worker_status(
        "resident",
        "Search model warm.",
        ready=True,
        config=config,
    )
    return True


async def _wait_for_embedding_turn() -> None:
    if work_coordination.manual_turn_blocked("embeddings"):
        _set_worker_status(
            "waiting_for_turn",
            "Search is waiting for other background work.",
            ready=False,
        )
    await work_coordination.wait_for_manual_turn("embeddings")
    if work_coordination.gpu_turn_blocked("embeddings"):
        _set_worker_status(
            "waiting_for_gpu",
            "Search is waiting for the GPU.",
            ready=False,
        )
    await work_coordination.wait_for_gpu_turn("embeddings")


async def _renew_embedding_turn() -> bool:
    retained = not work_coordination.lost_ownership("embeddings", gpu=True)
    if not retained:
        _unload_model()
    await _wait_for_embedding_turn()
    return retained


async def shutdown_embedding_worker() -> None:
    global _embed_executor, _preload_executor, _search_model_load_task
    task = _search_model_load_task
    if task is not None and not task.done():
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    _search_model_load_task = None
    residency_task = _unload_model()
    if residency_task is not None and not residency_task.done():
        await asyncio.gather(residency_task, return_exceptions=True)
    _embed_executor.shutdown(wait=False, cancel_futures=True)
    _preload_executor.shutdown(wait=False, cancel_futures=True)
    _embed_executor = _new_embed_executor()
    _preload_executor = _new_preload_executor()


def _set_worker_status(
    state: str,
    message: str = "",
    ready: bool = False,
    last_error: str = "",
    config: dict | None = None,
):
    config = config or settings.get_settings()
    _refresh_batch_status(config)
    _worker_status.update({
        "state": state,
        "message": message,
        "ready": ready,
        "manual_pause": _embedding_manual_pause,
        "model_id": config.get("embed_model_id") or config.get("model_id", ""),
        "model_dir": config.get("embed_model_dir") or config.get("model_dir", ""),
        "last_error": last_error,
    })


def _recompute_speed_metrics(now: float | None = None):
    now = now or time.time()
    cutoff = now - EMBED_SPEED_WINDOW_SECONDS
    while _embedding_history and _embedding_history[0]["ended_at"] < cutoff:
        _embedding_history.popleft()

    recent_images = sum(item["count"] for item in _embedding_history)
    recent_seconds = sum(item["seconds"] for item in _embedding_history)
    recent_wall_seconds = sum(item.get("wall_seconds", item["seconds"]) for item in _embedding_history)
    overall_images = _worker_status["session_embedded"]
    overall_seconds = _worker_status["session_embed_seconds"]
    overall_wall_seconds = _worker_status["session_wall_seconds"]

    _worker_status["recent_images_per_min"] = (
        round((recent_images / recent_seconds) * 60, 2) if recent_seconds > 0 else 0.0
    )
    _worker_status["recent_wall_images_per_min"] = (
        round((recent_images / recent_wall_seconds) * 60, 2) if recent_wall_seconds > 0 else 0.0
    )
    _worker_status["overall_images_per_min"] = (
        round((overall_images / overall_seconds) * 60, 2) if overall_seconds > 0 else 0.0
    )
    _worker_status["overall_wall_images_per_min"] = (
        round((overall_images / overall_wall_seconds) * 60, 2) if overall_wall_seconds > 0 else 0.0
    )


def _record_embedding_batch(
    count: int,
    seconds: float,
    *,
    wall_seconds: float | None = None,
    stage_seconds: dict | None = None,
    failures: int = 0,
):
    wall_seconds = seconds if wall_seconds is None else wall_seconds
    if stage_seconds is not None:
        _worker_status["last_batch_stage_seconds"] = {
            key: round(max(float(value), 0.0), 3)
            for key, value in stage_seconds.items()
        }
    _worker_status["last_batch_failures"] = int(failures)

    if count <= 0:
        return

    now = time.time()
    if not _worker_status["session_started_at"]:
        _worker_status["session_started_at"] = now

    seconds = max(float(seconds), 0.001)
    wall_seconds = max(float(wall_seconds), 0.001)
    _worker_status["last_batch_size"] = count
    _worker_status["last_batch_seconds"] = round(seconds, 3)
    _worker_status["last_embedded_at"] = now
    _worker_status["session_embedded"] += count
    _worker_status["session_embed_seconds"] += seconds
    _worker_status["session_wall_seconds"] += wall_seconds
    _embedding_history.append({
        "ended_at": now,
        "count": count,
        "seconds": seconds,
        "wall_seconds": wall_seconds,
    })
    _recompute_speed_metrics(now)


def _schedule_embed_retry(image_id: int, error: str):
    wait_seconds = EMBED_RETRY_SECONDS
    if "No such file" in error or "FileNotFoundError" in error:
        wait_seconds = EMBED_RETRY_SECONDS
    elif "cannot identify image file" in error:
        wait_seconds = EMBED_RETRY_SECONDS * 3

    _embed_retry_after[image_id] = time.time() + wait_seconds


def _select_ready_candidates(rows, limit: int | None = None):
    now = time.time()
    selected = []
    cooled_down = 0
    next_retry_at = None

    for row in rows:
        retry_after = _embed_retry_after.get(row["id"], 0)
        if retry_after > now:
            cooled_down += 1
            if next_retry_at is None or retry_after < next_retry_at:
                next_retry_at = retry_after
            continue

        selected.append(row)
        if limit is not None and len(selected) >= limit:
            break

    return selected, cooled_down, next_retry_at


def get_worker_status() -> dict:
    _refresh_batch_status()
    _recompute_speed_metrics()
    _worker_status["manual_pause"] = _embedding_manual_pause
    return dict(_worker_status)


def _manual_pause_message() -> str:
    return _embedding_manual_pause_message or "Search is stopped."


def _pause_reason_for_message(message: str) -> str:
    if message == STARTUP_DEFER_PAUSE_MESSAGE:
        return STARTUP_DEFER_PAUSE_REASON
    return USER_PAUSE_REASON


def pause_embedding_worker(message: str = "Search is stopped.") -> dict:
    global _embedding_manual_pause, _embedding_manual_pause_message, _embedding_pause_reason
    _embedding_manual_pause = True
    _embedding_manual_pause_message = message
    _embedding_pause_reason = _pause_reason_for_message(message)
    work_coordination.release_manual_owner("embeddings")
    _unload_model()
    _set_worker_status("paused", message, ready=False)
    return get_worker_status()


def resume_embedding_worker() -> dict:
    global _embedding_manual_pause, _embedding_manual_pause_message, _embedding_pause_reason
    _embedding_manual_pause = False
    _embedding_manual_pause_message = ""
    _embedding_pause_reason = ""
    _embedding_oom_circuit.reset()
    work_coordination.claim_manual_owner("embeddings")
    _clear_model_load_failure()
    _set_worker_status("idle", "Search will run from Background Work.", ready=_model is not None)
    return get_worker_status()


def _load_model(model_dir: str, model_id: str, interactive: bool = False):
    """Load the embedding model strictly from the local filesystem via ModelPool."""
    from core.model_pool import (
        COST_EMBEDDINGS_RAM,
        COST_EMBEDDINGS_VRAM,
        get_model_pool,
    )

    def _load():
        import torch
        from sentence_transformers import SentenceTransformer

        from core.ml_device import sentence_transformers_device

        processor_kwargs = None
        if model_id == "Qwen/Qwen3-VL-Embedding-8B":
            processor_kwargs = {
                "min_pixels": 4096,
                "max_pixels": 65536,
            }
        model_kwargs = {}
        if importlib.util.find_spec("bitsandbytes") is not None:
            model_kwargs = {
                "quantization_config": {
                    "load_in_4bit": True,
                    "bnb_4bit_compute_dtype": torch.float16,
                    "bnb_4bit_use_double_quant": True,
                    "bnb_4bit_quant_type": "nf4",
                },
                "torch_dtype": torch.float16,
            }
        elif model_id == settings.EMBED_MODEL_PRESETS[settings.LEGACY_2B_PRESET_KEY]["model_id"]:
            # The compact model has a deliberate CPU-compatible path on platforms
            # where bitsandbytes is unavailable. Do not attempt the 8B model at
            # full precision: that can exhaust ordinary workstation memory.
            model_kwargs = {"torch_dtype": torch.float32}
        else:
            raise RuntimeError(
                "The configured 8B search model needs bitsandbytes. "
                "Install the search pack on Linux x86-64 or select the compact 2B search model."
            )
        device = sentence_transformers_device()
        model = SentenceTransformer(
            model_dir,
            device=device,
            model_kwargs=model_kwargs,
            processor_kwargs=processor_kwargs,
            trust_remote_code=True,
            local_files_only=True,
        )
        log.info(f"{model_id} loaded from {model_dir} device={device}")
        return model

    return get_model_pool().acquire(
        "embeddings",
        load_fn=_load,
        unload_fn=_drop_embedding_residency,
        vram_bytes=COST_EMBEDDINGS_VRAM,
        ram_bytes=COST_EMBEDDINGS_RAM,
        interactive=interactive,
    )


def _target_embedding_dim() -> int:
    config = settings.active_embedding_config()
    return int(config.get("embed_model_dim") or EMBEDDING_DIM)


def _coerce_embedding_dim(vec: np.ndarray, target_dim: int | None = None) -> np.ndarray:
    import numpy as np  # deferred: keeps numpy off boot until an embedding is encoded

    target_dim = int(target_dim or _target_embedding_dim())
    coerced = np.asarray(vec, dtype=np.float32)
    if coerced.shape[0] > target_dim:
        coerced = coerced[:target_dim].copy()
        norm = float(np.linalg.norm(coerced))
        if norm > 0:
            coerced /= norm
    elif coerced.shape[0] < target_dim:
        padded = np.zeros((target_dim,), dtype=np.float32)
        padded[:coerced.shape[0]] = coerced
        coerced = padded
    return coerced.astype(np.float32)


def _missing_model_dependency() -> str | None:
    for module_name in ("torch", "sentence_transformers"):
        if importlib.util.find_spec(module_name) is None:
            return module_name
    return None


def _block_model_load_for_missing_dependency(model_dir: str, model_id: str, model_revision: str) -> bool:
    missing_dependency = _missing_model_dependency()
    if not missing_dependency:
        return False
    _note_model_load_failure(
        model_dir,
        model_id,
        model_revision,
        ModuleNotFoundError(f"No module named '{missing_dependency}'"),
    )
    return True


def _model_load_blocked(model_dir: str, model_id: str, model_revision: str) -> bool:
    return (
        _model_load_error_key == (model_dir, model_id, model_revision)
        and time.time() < _model_load_retry_after
    )


def _note_model_load_failure(model_dir: str, model_id: str, model_revision: str, exc: Exception):
    global _model_load_retry_after, _model_load_error_key
    _model_load_error_key = (model_dir, model_id, model_revision)
    _model_load_retry_after = time.time() + MODEL_LOAD_FAILURE_RETRY_SECONDS
    _set_worker_status("error", str(exc), ready=False, last_error=str(exc))


def _clear_model_load_failure():
    global _model_load_retry_after, _model_load_error_key
    _model_load_retry_after = 0.0
    _model_load_error_key = None


def _model_is_current(model_dir: str, model_id: str, model_revision: str) -> bool:
    return (
        _model is not None
        and _loaded_model_dir == model_dir
        and _loaded_model_id == model_id
        and _loaded_model_revision == model_revision
    )


def _model_values(config: dict) -> tuple[str, str, str]:
    return (
        config.get("embed_model_dir") or config.get("model_dir", ""),
        config.get("embed_model_id") or config.get("model_id", ""),
        config.get("embed_model_revision") or config.get("revision", "main"),
    )


def _model_key_for_config(config: dict) -> str:
    return config.get("model_key") or settings.embedding_model_key(config)


def _model_dimension_for_config(config: dict) -> int:
    return int(config.get("embed_model_dim") or config.get("dimension") or EMBEDDING_DIM)


def _get_model_load_lock():
    global _model_load_lock
    if _model_load_lock is None:
        _model_load_lock = asyncio.Lock()
    return _model_load_lock


def search_model_ready(config: dict | None = None) -> bool:
    config = config or settings.active_embedding_config()
    return _model_is_current(*_model_values(config))


def _clear_search_model_load_task(task):
    global _search_model_load_task
    if _search_model_load_task is task:
        _search_model_load_task = None


def start_search_model_load() -> bool:
    """Start warming the 2B search model without blocking the caller."""
    global _search_model_load_task

    config = settings.active_embedding_config()
    model_dir, model_id, model_revision = _model_values(config)
    if _model_is_current(model_dir, model_id, model_revision):
        return True
    if not ai_models.model_files_present(model_dir):
        return False
    if _block_model_load_for_missing_dependency(model_dir, model_id, model_revision):
        return False
    if _model_load_blocked(model_dir, model_id, model_revision):
        return False
    if _search_model_load_task is not None and not _search_model_load_task.done():
        return True

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return False

    _set_worker_status("loading_model", f"Loading {model_id} for search...", ready=False, config=config)
    _search_model_load_task = loop.create_task(ensure_model_loaded_for_search())
    _search_model_load_task.add_done_callback(_clear_search_model_load_task)
    return True


async def _ensure_model_loaded_for_config(config: dict, reason: str) -> bool:
    global _model, _loaded_model_dir, _loaded_model_id, _loaded_model_revision

    model_dir, model_id, model_revision = _model_values(config)

    if _model_is_current(model_dir, model_id, model_revision):
        if _retain_search_model_residency(config, model_id):
            return True
        _unload_model()
    if not ai_models.model_files_present(model_dir):
        return False
    if _block_model_load_for_missing_dependency(model_dir, model_id, model_revision):
        return False
    if _model_load_blocked(model_dir, model_id, model_revision):
        return False

    async with _get_model_load_lock():
        if _model_is_current(model_dir, model_id, model_revision):
            if _retain_search_model_residency(config, model_id):
                return True
            _unload_model()
        if _model_load_blocked(model_dir, model_id, model_revision):
            return False

        loop = asyncio.get_running_loop()
        _set_worker_status("loading_model", f"Loading {model_id} for {reason}…", ready=False, config=config)
        loaded = False
        try:
            await _wait_for_embedding_turn()
            _set_worker_status("loading_model", f"Loading {model_id} for {reason}…", ready=False, config=config)
            with work_coordination.manual_bulk("embeddings"):
                async with work_coordination.lease_heartbeat("embeddings", gpu=True):
                    # Interactive search pins the model so a caption sweep
                    # cannot bounce it every few seconds (ModelPool policy).
                    _model = await loop.run_in_executor(
                        _embed_executor,
                        _load_model,
                        model_dir,
                        model_id,
                        True,
                    )
            _loaded_model_dir = model_dir
            _loaded_model_id = model_id
            _loaded_model_revision = model_revision
            _clear_model_load_failure()
            if not _retain_search_model_residency(config, model_id):
                raise RuntimeError("Could not start search model residency heartbeat")
            loaded = True
            return True
        except Exception as exc:
            _note_model_load_failure(model_dir, model_id, model_revision, exc)
            log.error(f"Search model load error: {exc}", exc_info=True)
            return False
        finally:
            if not loaded:
                _unload_model()


async def ensure_model_loaded_for_search() -> bool:
    """Load the embedding model for an explicit user search request."""
    return await _ensure_model_loaded_for_config(settings.active_embedding_config(), "search")


def _load_image_for_embedding(image_id: int, path: str):
    return thumbnails.load_embedding_image(path, image_id, require_cached=True)


def _preload_images(
    image_refs: list[tuple[int, str]],
) -> tuple[list, list[int], list[str | None]]:
    """CPU stage: load images from SSD cache and resize. No GPU work."""
    errors = [None] * len(image_refs)
    valid = []
    valid_indices = []
    for i, (image_id, path) in enumerate(image_refs):
        img = None
        try:
            img = _load_image_for_embedding(image_id, path)
            if img is None:
                errors[i] = "md thumbnail not cached yet"
                continue
            valid.append(img)
            valid_indices.append(i)
        except Exception as e:
            errors[i] = f"{type(e).__name__}: {e}"
            log.debug(f"Skipping image {path}: {e}")
            if img is not None:
                try:
                    img.close()
                except Exception:
                    pass
    return valid, valid_indices, errors


def _timed_preload_images(
    image_refs: list[tuple[int, str]],
) -> tuple[list, list[int], list[str | None], float]:
    started = time.perf_counter()
    valid, valid_indices, errors = _preload_images(image_refs)
    return valid, valid_indices, errors, time.perf_counter() - started


def _close_preloaded_images(valid: list):
    for img in valid:
        try:
            img.close()
        except Exception:
            pass


def _encode_images(
    model, valid: list, valid_indices: list[int], n_refs: int, target_dim: int | None = None,
) -> tuple[list[np.ndarray | None], list[str | None]]:
    """GPU stage: encode pre-loaded PIL images."""
    results = [None] * n_refs
    errors = [None] * n_refs

    if not valid:
        return results, errors

    try:
        embeddings = model.encode(valid, normalize_embeddings=True)
        for idx, valid_i in enumerate(valid_indices):
            results[valid_i] = _coerce_embedding_dim(embeddings[idx], target_dim=target_dim)
    except Exception as e:
        if _is_cuda_oom_error(e):
            raise
        failure = f"{type(e).__name__}: {e}"
        for valid_i in valid_indices:
            errors[valid_i] = failure
    finally:
        _close_preloaded_images(valid)
    return results, errors


_text_cache: dict[str, np.ndarray] = {}
_TEXT_CACHE_MAX = 100

def encode_text(query: str, config: dict | None = None) -> np.ndarray | None:
    """Encode a text query into an embedding. Cached for repeat queries."""
    config = config or settings.active_embedding_config()
    model_dir, model_id, model_revision = _model_values(config)
    if not _model_is_current(model_dir, model_id, model_revision):
        return None
    cache_key = (_model_key_for_config(config), query)
    cached = _text_cache.get(cache_key)
    if cached is not None:
        return cached
    embedding = _model.encode(
        [query],
        prompt="Retrieve images relevant to the query.",
        normalize_embeddings=True,
    )
    vec = _coerce_embedding_dim(embedding[0], target_dim=_model_dimension_for_config(config))
    if len(_text_cache) >= _TEXT_CACHE_MAX:
        _text_cache.pop(next(iter(_text_cache)))  # evict oldest
    _text_cache[cache_key] = vec
    return vec


def vec_to_blob(vec: np.ndarray) -> bytes:
    import numpy as np  # deferred: keeps numpy off boot until an embedding is stored

    return np.asarray(vec, dtype=np.float32).tobytes()


def blob_to_vec(blob: bytes) -> np.ndarray:
    import numpy as np  # deferred: keeps numpy off boot until an embedding is read

    return np.frombuffer(blob, dtype=np.float32).copy()


def _row_image_ref(row) -> tuple[int, str]:
    return int(row["id"]), row["filepath"]


def _candidate_cursor_after_id(model_key: str) -> int:
    if _unembedded_candidate_cursor.get("model_key") != model_key:
        _unembedded_candidate_cursor["model_key"] = model_key
        _unembedded_candidate_cursor["after_id"] = 0
    return int(_unembedded_candidate_cursor.get("after_id") or 0)


def _advance_candidate_cursor(rows) -> None:
    if not rows:
        _unembedded_candidate_cursor["after_id"] = 0
        return
    _unembedded_candidate_cursor["after_id"] = max(int(row["id"]) for row in rows)


def _schedule_preload(loop, rows):
    image_refs = [_row_image_ref(row) for row in rows]
    return loop.run_in_executor(_preload_executor, _timed_preload_images, image_refs)


async def _discard_preload_future(future):
    if future is None:
        return
    try:
        valid, _valid_indices, _errors, _seconds = await future
    except Exception:
        return
    _close_preloaded_images(valid)


async def _log_stored_embedding_batch(batch_size: int, embedding_config: dict | None) -> None:
    global _embedding_count_log_batches
    _embedding_count_log_batches += 1
    if _embedding_count_log_batches < EMBED_COUNT_LOG_INTERVAL_BATCHES:
        log.info(f"Embedded {batch_size} images")
        return

    _embedding_count_log_batches = 0
    if embedding_config:
        embedded_count = await _configured(
            _count_embeddings_for_model,
            "count_embeddings_for_model",
        )(embedding_config)
    else:
        embedded_count = await _configured(_get_embedding_count, "get_embedding_count")()
    log.info(f"Embedded {batch_size} images (total: {embedded_count})")


async def _process_embedding_candidates(
    loop,
    model,
    rows,
    *,
    max_batch_size: int | None = None,
    batch_pause_seconds: float = 0.0,
    embedding_config: dict | None = None,
) -> dict:
    """Embed one candidate window, splitting it into adaptive chunks."""
    index = 0
    stored_total = 0
    failed_total = 0
    chunks_completed = 0
    first_failure_error = None
    preload_future = None
    preload_rows = None
    ownership_lost = False

    while index < len(rows):
        if _embedding_manual_pause:
            break
        if not await _renew_embedding_turn():
            await _discard_preload_future(preload_future)
            ownership_lost = True
            break

        active_batch_size, _target = _refresh_batch_status(embedding_config)
        if max_batch_size is not None:
            active_batch_size = max(1, min(active_batch_size, int(max_batch_size or 1)))
        if preload_future is None:
            preload_rows = rows[index:index + active_batch_size]
            preload_future = _schedule_preload(loop, preload_rows)

        chunk_rows = preload_rows or []
        chunk_len = len(chunk_rows)
        if chunk_len <= 0:
            break

        wall_started = time.perf_counter()
        preload_seconds = 0.0
        encode_seconds = 0.0
        store_seconds = 0.0
        pause_seconds = 0.0

        try:
            valid, valid_indices, preload_errors, preload_seconds = await preload_future
        except Exception as e:
            valid = []
            valid_indices = []
            preload_errors = [f"{type(e).__name__}: {e}"] * chunk_len
            preload_seconds = time.perf_counter() - wall_started
        if _embedding_manual_pause:
            _close_preloaded_images(valid)
            break

        next_index = index + chunk_len
        next_future = None
        next_rows = None
        if next_index < len(rows):
            next_active_batch_size, _target = _refresh_batch_status(embedding_config)
            if max_batch_size is not None:
                next_active_batch_size = max(1, min(next_active_batch_size, int(max_batch_size or 1)))
            next_rows = rows[next_index:next_index + next_active_batch_size]
            next_future = _schedule_preload(loop, next_rows)

        try:
            encode_started = time.perf_counter()
            vectors, encode_errors = await loop.run_in_executor(
                _embed_executor,
                _encode_images,
                model,
                valid,
                valid_indices,
                chunk_len,
                _model_dimension_for_config(embedding_config or settings.get_settings()),
            )
            encode_seconds = time.perf_counter() - encode_started
        except Exception as e:
            if not _is_cuda_oom_error(e):
                await _discard_preload_future(next_future)
                raise

            await loop.run_in_executor(_embed_executor, _clear_cuda_cache)
            _note_cuda_oom()
            await _discard_preload_future(next_future)

            stage_seconds = {
                "candidate_query": float(_worker_status.get("last_candidate_query_seconds") or 0.0),
                "preload": preload_seconds,
                "encode": time.perf_counter() - encode_started,
                "store": 0.0,
                "pause": 0.0,
                "wall": time.perf_counter() - wall_started,
            }
            _record_embedding_batch(
                0,
                0.0,
                wall_seconds=stage_seconds["wall"],
                stage_seconds=stage_seconds,
                failures=chunk_len if chunk_len <= 1 else 0,
            )

            if chunk_len <= 1:
                failure = f"{type(e).__name__}: {e}"
                first_failure_error = first_failure_error or failure
                failed_total += chunk_len
                circuit_open = _embedding_oom_circuit.record_failure()
                for row in chunk_rows:
                    image_id = int(row["id"])
                    poisoned = await _configured(
                        _poison_embedding_image,
                        "poison_embedding_image",
                    )(
                        image_id=image_id,
                        embedding_config=embedding_config,
                        error=failure,
                        force=circuit_open,
                    )
                    if poisoned:
                        _embed_retry_after.pop(image_id, None)
                    else:
                        _schedule_embed_retry(image_id, failure)
                index = next_index
                if circuit_open:
                    pause_embedding_worker(
                        "Search paused after repeated GPU out-of-memory failures. "
                        "Free GPU memory, then start Search again."
                    )

            preload_future = None
            preload_rows = None
            if _embedding_manual_pause:
                break
            await asyncio.sleep(0)
            continue

        errors = [preload_errors[i] or encode_errors[i] for i in range(chunk_len)]
        batch = []
        cached_vectors = []
        failed = []
        for row, vec, error in zip(chunk_rows, vectors, errors):
            image_id = int(row["id"])
            if vec is not None:
                batch.append((image_id, vec_to_blob(vec)))
                cached_vectors.append((image_id, vec))
                _embed_retry_after.pop(image_id, None)
            elif error:
                failed.append((image_id, error))
                first_failure_error = first_failure_error or error
                _schedule_embed_retry(image_id, error)

        store_started = time.perf_counter()
        if batch:
            await _configured(_store_embeddings_batch, "store_embeddings_batch")(
                batch,
                embedding_config=embedding_config,
            )
            try:
                embed_cache.add_vectors(cached_vectors, model_key=(
                    _model_key_for_config(embedding_config) if embedding_config else None
                ))
            except Exception as exc:
                log.warning(f"Warm embedding cache update skipped: {exc}")
            await _log_stored_embedding_batch(len(batch), embedding_config)
            _note_successful_embedding_batch(embedding_config)
            _embedding_oom_circuit.reset()
        store_seconds = time.perf_counter() - store_started

        if batch_pause_seconds:
            pause_started = time.perf_counter()
            await asyncio.sleep(batch_pause_seconds)
            pause_seconds = time.perf_counter() - pause_started

        wall_seconds = time.perf_counter() - wall_started
        stage_seconds = {
            "candidate_query": float(_worker_status.get("last_candidate_query_seconds") or 0.0),
            "preload": preload_seconds,
            "encode": encode_seconds,
            "store": store_seconds,
            "pause": pause_seconds,
            "wall": wall_seconds,
        }
        work_seconds = max(wall_seconds - pause_seconds, 0.001)
        _record_embedding_batch(
            len(batch),
            work_seconds,
            wall_seconds=wall_seconds,
            stage_seconds=stage_seconds,
            failures=len(failed),
        )

        stored_total += len(batch)
        failed_total += len(failed)
        chunks_completed += 1
        index = next_index
        preload_future = next_future
        preload_rows = next_rows

    return {
        "stored": stored_total,
        "failed": failed_total,
        "chunks": chunks_completed,
        "first_error": first_failure_error,
        "lost_ownership": ownership_lost,
    }


async def run_embedding_worker():
    try:
        await _run_embedding_worker_loop()
    finally:
        work_coordination.release_manual_owner("embeddings")
        _unload_model()


async def _run_embedding_worker_loop():
    """Main background loop: embed images for search, similarity, and Elo propagation."""
    loop = asyncio.get_running_loop()

    global _model, _loaded_model_dir, _loaded_model_id, _loaded_model_revision


    while True:
        try:
            app_config = settings.get_settings()
            config = {**app_config, **settings.active_embedding_config()}
            model_id = config["embed_model_id"]
            model_revision = config["embed_model_revision"]
            model_dir = config["embed_model_dir"]
            batch_pause_seconds = max(
                0.0,
                min(
                    5.0,
                    float(config.get("embed_batch_pause_ms", 250)) / 1000.0,
                ),
            )
            model_installed = ai_models.model_files_present(model_dir)

            if _embedding_manual_pause:
                _unload_model()
                _set_worker_status("paused", _manual_pause_message(), ready=_model is not None)
                await asyncio.sleep(1)
                continue

            pressure = memory_pressure.gate_bulk_work()
            if pressure.pause_bulk:
                _set_worker_status(
                    "paused",
                    pressure.message or memory_pressure.PAUSE_MESSAGE,
                    ready=_model is not None,
                )
                await asyncio.sleep(2)
                continue

            if not model_installed:
                _unload_model()
                _set_worker_status(
                    "waiting_for_model",
                    f"Install {model_id} from Settings to enable AI features.",
                    ready=False,
                )
                await asyncio.sleep(10)
                continue

            needs_model_load = (
                not _model_is_current(model_dir, model_id, model_revision)
            )
            if needs_model_load and _block_model_load_for_missing_dependency(model_dir, model_id, model_revision):
                _unload_model()
                await asyncio.sleep(min(MODEL_LOAD_FAILURE_RETRY_SECONDS, 30))
                continue
            if needs_model_load and _model_load_blocked(model_dir, model_id, model_revision):
                wait_for = max(1, int(_model_load_retry_after - time.time()))
                _set_worker_status(
                    "waiting_retry",
                    f"Model load failed; retrying in about {wait_for}s.",
                    ready=False,
                    last_error=_worker_status.get("last_error", ""),
                )
                await asyncio.sleep(min(wait_for, 30))
                continue
            if needs_model_load:
                async with _get_model_load_lock():
                    if not _model_is_current(model_dir, model_id, model_revision):
                        if _model_load_blocked(model_dir, model_id, model_revision):
                            continue
                        _set_worker_status("loading_model", f"Loading {model_id} from disk…", ready=False)
                        try:
                            await _wait_for_embedding_turn()
                            _set_worker_status(
                                "loading_model",
                                f"Loading {model_id} from disk…",
                                ready=False,
                            )
                            with work_coordination.manual_bulk("embeddings"):
                                async with work_coordination.lease_heartbeat("embeddings", gpu=True):
                                    _model = await loop.run_in_executor(
                                        _embed_executor,
                                        _load_model,
                                        model_dir,
                                        model_id,
                                        False,
                                    )
                            _loaded_model_dir = model_dir
                            _loaded_model_id = model_id
                            _loaded_model_revision = model_revision
                            _clear_model_load_failure()
                            if not _retain_search_model_residency(config, model_id):
                                raise RuntimeError(
                                    "Could not start search model residency heartbeat"
                                )
                        except Exception as exc:
                            _unload_model()
                            _note_model_load_failure(model_dir, model_id, model_revision, exc)
                            await asyncio.sleep(min(MODEL_LOAD_FAILURE_RETRY_SECONDS, 30))
                            continue

            # Phase 1: Embed unembedded images with pipelined CPU/GPU
            active_batch_size, _target_batch_size = _refresh_batch_status(config)
            governed_batch_size = max(1, int(active_batch_size or 1))
            candidate_limit = max(
                1,
                governed_batch_size * EMBED_CANDIDATE_MULTIPLIER,
                governed_batch_size + len(_embed_retry_after),
            )
            cache_size = "sm" if int(config.get("embed_model_dim", 0) or 0) > 2048 else "md"
            cursor_after_id = _candidate_cursor_after_id(_model_key_for_config(config))
            query_started = time.perf_counter()
            candidates = await _configured(_get_unembedded_images, "get_unembedded_images")(
                limit=candidate_limit,
                md_cache_root=thumbnails.SSD_CACHE_DIR,
                cache_size=cache_size,
                after_id=cursor_after_id,
            )
            _advance_candidate_cursor(candidates)
            query_seconds = time.perf_counter() - query_started
            unembedded, cooled_down, next_retry_at = _select_ready_candidates(candidates)
            _worker_status.update({
                "last_candidate_query_seconds": round(query_seconds, 3),
                "last_candidate_count": len(candidates),
                "last_candidate_window_size": candidate_limit,
                "last_ready_count": len(unembedded),
                "last_cooled_down_count": cooled_down,
                "next_retry_at": next_retry_at,
            })
            if unembedded:
                await _wait_for_embedding_turn()
                _set_worker_status(
                    "embedding",
                    f"Embedding {len(unembedded)} images in batches up to {governed_batch_size}…",
                    ready=True,
                )
                with work_coordination.manual_bulk("embeddings"):
                    result = await _process_embedding_candidates(
                        loop,
                        _model,
                        unembedded,
                        max_batch_size=governed_batch_size,
                        batch_pause_seconds=batch_pause_seconds,
                        embedding_config=config,
                    )

                if result["failed"] and not result["stored"]:
                    sample_error = result.get("first_error") or "image unavailable"
                    _set_worker_status(
                        "embedding",
                        f"Skipping {result['failed']} unavailable files for now; retrying other images. Latest error: {sample_error}",
                        ready=True,
                    )
                continue

            if candidates and cooled_down:
                wait_for = max(1, int(next_retry_at - time.time())) if next_retry_at else 5
                _set_worker_status(
                    "waiting_retry",
                    f"Waiting to retry {cooled_down} unavailable files in about {wait_for}s.",
                    ready=True,
                )
                await asyncio.sleep(min(wait_for, 10))
                continue

            work_coordination.release_manual_owner("embeddings")
            _unload_model()
            _set_worker_status("idle", "Waiting for new images…", ready=True)
            await asyncio.sleep(2)

        except Exception as e:
            if _is_sqlite_locked_error(e):
                _set_worker_status(
                    "embedding",
                    "Waiting for the catalog database to finish another write.",
                    ready=_model is not None,
                    last_error="",
                )
                log.info("Embedding worker waiting for SQLite write lock")
                await asyncio.sleep(2)
                continue
            if isinstance(e, (ImportError, ModuleNotFoundError)):
                config = settings.active_embedding_config()
                _note_model_load_failure(
                    config["embed_model_dir"],
                    config["embed_model_id"],
                    config["embed_model_revision"],
                    e,
                )
            _set_worker_status("error", str(e), ready=False, last_error=str(e))
            log.error(f"Embedding worker error: {e}", exc_info=True)
            await asyncio.sleep(10)
