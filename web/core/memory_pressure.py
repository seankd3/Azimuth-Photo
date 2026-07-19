"""Process RSS watermarks for background bulk work.

All bulk drivers (pregen, captions, embeddings, people) consult
``gate_bulk_work`` before taking a batch so memory pressure pauses
everyone through one path — not a pregen-only special case.
"""

from __future__ import annotations

import gc
import logging
import os
import threading
from dataclasses import dataclass, replace
from typing import Any, Callable


log = logging.getLogger(__name__)

_GIB = 1024**3
PAUSE_REASON = "memory pressure"
PAUSE_MESSAGE = "Paused: memory pressure"


def _env_bytes(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        lowered = raw.lower()
        if lowered.endswith("g"):
            return int(float(lowered[:-1]) * _GIB)
        if lowered.endswith("m"):
            return int(float(lowered[:-1]) * 1024 * 1024)
        return int(raw)
    except ValueError:
        return default


SOFT_WATERMARK_BYTES = _env_bytes("PHOTOARCHIVE_MEMORY_SOFT_BYTES", 5 * _GIB)
HARD_WATERMARK_BYTES = _env_bytes("PHOTOARCHIVE_MEMORY_HARD_BYTES", 7 * _GIB)
RESUME_WATERMARK_BYTES = _env_bytes(
    "PHOTOARCHIVE_MEMORY_RESUME_BYTES",
    max(SOFT_WATERMARK_BYTES - (512 * 1024 * 1024), SOFT_WATERMARK_BYTES * 85 // 100),
)

_lock = threading.Lock()
_paused = False
_rss_reader: Callable[[], int] | None = None


@dataclass(frozen=True)
class MemoryPressure:
    rss_bytes: int
    soft_bytes: int
    hard_bytes: int
    resume_bytes: int
    level: str
    pause_bulk: bool
    unload_models: bool
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "rss_bytes": self.rss_bytes,
            "soft_bytes": self.soft_bytes,
            "hard_bytes": self.hard_bytes,
            "resume_bytes": self.resume_bytes,
            "level": self.level,
            "pause_bulk": self.pause_bulk,
            "unload_models": self.unload_models,
            "message": self.message,
        }


def set_rss_reader(reader: Callable[[], int] | None) -> None:
    """Inject an RSS reader (tests). ``None`` restores ``/proc/self/status``."""
    global _rss_reader
    _rss_reader = reader


def reset_for_tests() -> None:
    global _paused, _rss_reader
    with _lock:
        _paused = False
        _rss_reader = None


def read_rss_bytes() -> int:
    if _rss_reader is not None:
        try:
            return max(0, int(_rss_reader()))
        except Exception:
            return 0
    try:
        with open("/proc/self/status", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    parts = line.split()
                    # VmRSS is reported in kB.
                    return max(0, int(parts[1]) * 1024)
    except (OSError, ValueError, IndexError):
        pass
    return 0


def evaluate_memory_pressure(*, rss_bytes: int | None = None) -> MemoryPressure:
    """Classify RSS against soft/hard watermarks with resume hysteresis."""
    global _paused

    rss = read_rss_bytes() if rss_bytes is None else max(0, int(rss_bytes))
    soft = max(1, int(SOFT_WATERMARK_BYTES))
    hard = max(soft, int(HARD_WATERMARK_BYTES))
    resume = min(max(0, int(RESUME_WATERMARK_BYTES)), soft)

    with _lock:
        if rss >= hard:
            _paused = True
            level = "hard"
        elif rss >= soft:
            _paused = True
            level = "soft"
        elif _paused and rss > resume:
            level = "soft"
        else:
            _paused = False
            level = "ok"
        paused = _paused

    return MemoryPressure(
        rss_bytes=rss,
        soft_bytes=soft,
        hard_bytes=hard,
        resume_bytes=resume,
        level=level if paused else "ok",
        pause_bulk=paused,
        # Soft pause must shed ML residency too — waiting at soft with models
        # still resident is not a stable wait state under a cgroup swap cap.
        unload_models=paused,
        message=PAUSE_MESSAGE if paused else "",
    )


def release_discardable_buffers() -> dict[str, Any]:
    """Drop caches that are safe to rebuild under pressure."""
    released: dict[str, Any] = {}
    try:
        from features.develop import render as develop_render

        develop_render.clear_proof_decode_cache()
        released["proof_decode_cache"] = True
    except Exception:
        released["proof_decode_cache"] = False
        log.debug("memory_pressure: proof decode cache release failed", exc_info=True)

    try:
        import thumbnails

        released["thumbnail_memory"] = thumbnails._clear_memory_cache()
    except Exception:
        released["thumbnail_memory"] = None
        log.debug("memory_pressure: thumbnail memory release failed", exc_info=True)

    try:
        gc.collect()
        released["gc"] = True
    except Exception:
        released["gc"] = False
    return released


def request_model_unload() -> list[str]:
    """Shed every ModelPool resident (search/caption/people/subject mask).

    Same contract as before — memory pressure pauses bulk work and drops
    ML residency for a lazy reload later. Pool is the choke point; worker
    unload helpers clear any straggler globals set outside the pool.
    """
    unloaded: list[str] = []
    try:
        from core.model_pool import get_model_pool

        unloaded.extend(get_model_pool().unload_all())
    except Exception:
        log.debug("memory_pressure: model_pool unload_all failed", exc_info=True)

    # Straggler sweep: clear worker globals even if they bypassed the pool
    # (tests, legacy paths). Pool-aware unload helpers are no-ops when empty.
    try:
        import embedding_worker

        embedding_worker._unload_model()
        if "embeddings" not in unloaded:
            unloaded.append("embeddings")
    except Exception:
        log.debug("memory_pressure: embedding unload failed", exc_info=True)

    try:
        import caption_worker

        caption_worker._unload_model()
        if "captions" not in unloaded:
            unloaded.append("captions")
    except Exception:
        log.debug("memory_pressure: caption unload failed", exc_info=True)

    try:
        import face_worker

        face_worker._unload_face_app()
        if "people" not in unloaded:
            unloaded.append("people")
    except Exception:
        log.debug("memory_pressure: people unload failed", exc_info=True)

    try:
        from features.develop import ai_masks

        ai_masks.unload_subject_session()
        if "subject_mask" not in unloaded:
            unloaded.append("subject_mask")
    except Exception:
        log.debug("memory_pressure: subject_mask unload failed", exc_info=True)

    try:
        from core.ml_device import empty_cuda_cache

        empty_cuda_cache()
    except Exception:
        log.debug("memory_pressure: cuda empty_cache failed", exc_info=True)

    return unloaded


def gate_bulk_work(*, rss_bytes: int | None = None) -> MemoryPressure:
    """Shared bulk gate: pause above soft, shed buffers + models, log RSS delta."""
    pressure = evaluate_memory_pressure(rss_bytes=rss_bytes)
    if not pressure.pause_bulk:
        return pressure
    before = read_rss_bytes() if rss_bytes is None else pressure.rss_bytes
    released = release_discardable_buffers()
    unloaded: list[str] = []
    if pressure.unload_models:
        unloaded = request_model_unload()
    after = read_rss_bytes() if rss_bytes is None else pressure.rss_bytes
    log.info(
        "worker=memory_pressure event=%s_watermark rss_before=%s rss_after=%s "
        "released=%s unloaded=%s",
        pressure.level,
        before,
        after,
        released,
        ",".join(unloaded) or "none",
    )
    return pressure


def apply_to_decision(decision: Any, *, rss_bytes: int | None = None) -> Any:
    """Overlay memory pressure onto a BackgroundDecision-like object."""
    pressure = gate_bulk_work(rss_bytes=rss_bytes)
    if not pressure.pause_bulk:
        return decision
    fields = getattr(type(decision), "__dataclass_fields__", {})
    updates: dict[str, Any] = {}
    if "pause" in fields:
        updates["pause"] = True
    if "sleep_seconds" in fields:
        updates["sleep_seconds"] = max(
            float(getattr(decision, "sleep_seconds", 0.0) or 0.0),
            2.0,
        )
    if "reason" in fields:
        updates["reason"] = PAUSE_REASON
    if "intensity" in fields:
        updates["intensity"] = 0.0
    if "mode" in fields:
        updates["mode"] = "paused"
    if "thumbnail_batch_size" in fields:
        updates["thumbnail_batch_size"] = 0
    if not updates:
        return decision
    return replace(decision, **updates)


def effective_prefetch_workers(configured: int, *, rss_bytes: int | None = None) -> int:
    """Halve in-flight decode concurrency above the resume watermark.

    Full configured size restores only when RSS is at or below resume.
    """
    configured = max(1, int(configured or 1))
    pressure = evaluate_memory_pressure(rss_bytes=rss_bytes)
    if pressure.rss_bytes > pressure.resume_bytes:
        return max(1, configured // 2)
    return configured
