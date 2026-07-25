"""Swap-aware memory watermarks for background bulk work.

All bulk drivers (pregen, captions, embeddings, people) consult
``gate_bulk_work`` before taking a batch so memory pressure pauses
everyone through one path — not a pregen-only special case.

Signal (periodic poll, not PSI watch):
  1. cgroup ``memory.current`` + ``memory.swap.current`` when readable
  2. else ``VmRSS`` + ``VmSwap`` from ``/proc/self/status``
  3. else ``VmRSS`` alone
  4. **host** ``MemAvailable`` — roommates (grind, agents) count too
  5. **startup calm** — after boot/restart, bulk waits until serve is proven

Defaults come from ``host_profile`` (cgroup limits or fraction-of-RAM)
so any machine size shares one formula. Env knobs still win.

No operator controls: intent (feature settings) is separate; permission is
recomputed automatically from these signals.
"""

from __future__ import annotations

import gc
import logging
import os
import threading
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable


log = logging.getLogger(__name__)

_GIB = 1024**3
PAUSE_REASON = "memory pressure"
PAUSE_MESSAGE = "Paused: memory pressure"

# Headroom under cgroup MemoryHigh / MemoryMax when deriving defaults.
_HIGH_HEADROOM_BYTES = 2 * _GIB
_MAX_HEADROOM_BYTES = 2 * _GIB
# Fallback when no cgroup limits: soft=55% / hard=72% of detected total RAM.
_FALLBACK_SOFT_FRACTION = 0.55
_FALLBACK_HARD_FRACTION = 0.72


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


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return max(0.0, float(raw))
    except ValueError:
        return default


def _read_cgroup_limit(path: Path) -> int | None:
    try:
        raw = path.read_text(encoding="utf-8").strip().lower()
    except OSError:
        return None
    if not raw or raw == "max":
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value > 0 else None


def _self_cgroup_dir() -> Path | None:
    try:
        with open("/proc/self/cgroup", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                # Unified hierarchy: ``0::/system.slice/photoarchive.service``
                parts = line.split(":", 2)
                if len(parts) != 3:
                    continue
                rel = parts[2].lstrip("/")
                if not rel:
                    continue
                candidate = Path("/sys/fs/cgroup") / rel
                if (candidate / "memory.current").is_file():
                    return candidate
    except OSError:
        pass
    return None


def read_cgroup_limits() -> tuple[int | None, int | None]:
    """Return ``(memory.high, memory.max)`` bytes when readable."""
    cgroup = _self_cgroup_dir()
    if cgroup is None:
        return None, None
    return (
        _read_cgroup_limit(cgroup / "memory.high"),
        _read_cgroup_limit(cgroup / "memory.max"),
    )


def _detect_total_ram_bytes() -> int | None:
    """Total system RAM — psutil first, then sysconf."""

    try:
        import psutil

        total = int(psutil.virtual_memory().total)
        if total > 0:
            return total
    except Exception:
        pass
    try:
        page_size = os.sysconf("SC_PAGE_SIZE")
        phys_pages = os.sysconf("SC_PHYS_PAGES")
        if page_size > 0 and phys_pages > 0:
            return int(page_size) * int(phys_pages)
    except (AttributeError, OSError, ValueError):
        pass
    return None


def _default_watermarks() -> tuple[int, int, int]:
    """Soft/hard/resume from host_profile (proportional headroom, any box size)."""
    try:
        from core.host_profile import detect_host_profile

        return detect_host_profile().memory_watermarks()
    except Exception:
        log.debug("memory_pressure: host_profile unavailable", exc_info=True)
    high, maximum = read_cgroup_limits()
    if high is not None or maximum is not None:
        soft = (high - _HIGH_HEADROOM_BYTES) if high is not None else None
        hard = (maximum - _MAX_HEADROOM_BYTES) if maximum is not None else None
        if soft is None and hard is not None:
            soft = int(hard * _FALLBACK_SOFT_FRACTION / _FALLBACK_HARD_FRACTION)
        if hard is None and soft is not None:
            hard = int(soft * _FALLBACK_HARD_FRACTION / _FALLBACK_SOFT_FRACTION)
        soft = max(1 * _GIB, int(soft or 1 * _GIB))
        hard = max(soft + 256 * 1024 * 1024, int(hard or soft + 256 * 1024 * 1024))
        resume = min(soft, max(soft * 85 // 100, soft - 512 * 1024 * 1024))
        return soft, hard, resume

    total = _detect_total_ram_bytes() or (16 * _GIB)
    soft = max(1 * _GIB, int(total * _FALLBACK_SOFT_FRACTION))
    hard = max(soft + 256 * 1024 * 1024, int(total * _FALLBACK_HARD_FRACTION))
    resume = min(soft, max(soft * 85 // 100, soft - 512 * 1024 * 1024))
    return soft, hard, resume


_DEFAULT_SOFT, _DEFAULT_HARD, _DEFAULT_RESUME = _default_watermarks()
SOFT_WATERMARK_BYTES = _env_bytes("PHOTOARCHIVE_MEMORY_SOFT_BYTES", _DEFAULT_SOFT)
HARD_WATERMARK_BYTES = _env_bytes("PHOTOARCHIVE_MEMORY_HARD_BYTES", _DEFAULT_HARD)
RESUME_WATERMARK_BYTES = _env_bytes(
    "PHOTOARCHIVE_MEMORY_RESUME_BYTES",
    _DEFAULT_RESUME,
)

# Idle model residency TTL (seconds). Used by workers / pool idle shed.
MODEL_IDLE_TTL_SECONDS = _env_float("PHOTOARCHIVE_MODEL_IDLE_TTL_SECONDS", 120.0)

# After restart/heal: bulk work waits this long so the library proves Ready
# before models reload. Automatic — no operator step.
STARTUP_CALM_SECONDS = _env_float("PHOTOARCHIVE_STARTUP_CALM_SECONDS", 120.0)

# Host MemAvailable floors (the room, not only our cgroup). Below soft →
# pause bulk; below hard → pause + shed models. Resume needs soft + slack.
def _default_host_floors() -> tuple[int, int, int]:
    total = _detect_total_ram_bytes() or (16 * _GIB)
    # Loyal on shared boxes: keep ~12%/6% free before bulk runs.
    soft = max(int(1.5 * _GIB), int(total * 0.12))
    hard = max(int(0.75 * _GIB), int(total * 0.06))
    hard = min(hard, soft - 256 * 1024 * 1024) if soft > hard else hard
    if hard >= soft:
        hard = max(256 * 1024 * 1024, soft // 2)
    resume = soft + 512 * 1024 * 1024
    return soft, hard, resume


_DEFAULT_HOST_SOFT, _DEFAULT_HOST_HARD, _DEFAULT_HOST_RESUME = _default_host_floors()
HOST_SOFT_AVAILABLE_BYTES = _env_bytes(
    "PHOTOARCHIVE_HOST_SOFT_AVAILABLE_BYTES", _DEFAULT_HOST_SOFT
)
HOST_HARD_AVAILABLE_BYTES = _env_bytes(
    "PHOTOARCHIVE_HOST_HARD_AVAILABLE_BYTES", _DEFAULT_HOST_HARD
)
HOST_RESUME_AVAILABLE_BYTES = _env_bytes(
    "PHOTOARCHIVE_HOST_RESUME_AVAILABLE_BYTES", _DEFAULT_HOST_RESUME
)

_lock = threading.Lock()
_paused = False
_host_paused = False
_startup_calm_until_mono = 0.0
_rss_reader: Callable[[], int] | None = None
_memory_reader: Callable[[], "MemoryReading"] | None = None
_host_available_reader: Callable[[], int | None] | None = None


@dataclass(frozen=True)
class MemoryReading:
    """One pressure sample."""

    rss_bytes: int
    swap_bytes: int
    pressure_bytes: int
    source: str  # cgroup | proc | rss | injected


@dataclass(frozen=True)
class MemoryPressure:
    rss_bytes: int
    swap_bytes: int
    pressure_bytes: int
    signal: str
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
            "swap_bytes": self.swap_bytes,
            "pressure_bytes": self.pressure_bytes,
            "signal": self.signal,
            "soft_bytes": self.soft_bytes,
            "hard_bytes": self.hard_bytes,
            "resume_bytes": self.resume_bytes,
            "level": self.level,
            "pause_bulk": self.pause_bulk,
            "unload_models": self.unload_models,
            "message": self.message,
        }



def note_process_start(*, calm_seconds: float | None = None) -> None:
    """Begin automatic startup calm (bulk seated until mono deadline)."""
    global _startup_calm_until_mono
    seconds = STARTUP_CALM_SECONDS if calm_seconds is None else max(0.0, float(calm_seconds))
    _startup_calm_until_mono = time.monotonic() + seconds
    log.info(
        "worker=memory_pressure event=startup_calm_begin seconds=%.1f",
        seconds,
    )


def startup_calm_remaining() -> float:
    """Seconds of startup calm left (0 when clear)."""
    return max(0.0, _startup_calm_until_mono - time.monotonic())


def startup_calm_active() -> bool:
    return startup_calm_remaining() > 0.0


def set_host_available_reader(reader: Callable[[], int | None] | None) -> None:
    """Inject host MemAvailable reader (tests). ``None`` restores live reads."""
    global _host_available_reader
    _host_available_reader = reader


def read_host_available_bytes() -> int | None:
    """Host free memory (MemAvailable / psutil). ``None`` if unreadable."""
    if _host_available_reader is not None:
        try:
            value = _host_available_reader()
            if value is None:
                return None
            return max(0, int(value))
        except Exception:
            return None
    try:
        import psutil

        return max(0, int(psutil.virtual_memory().available))
    except Exception:
        pass
    try:
        with open("/proc/meminfo", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("MemAvailable:"):
                    return max(0, int(line.split()[1]) * 1024)
    except (OSError, ValueError, IndexError):
        pass
    return None


def set_rss_reader(reader: Callable[[], int] | None) -> None:
    """Inject an RSS/pressure reader (tests). ``None`` restores live reads.

    Legacy contract: the returned int is treated as the *pressure* figure
    (combined), with swap reported as 0 — existing tests pass soft/hard
    thresholds directly.
    """
    global _rss_reader, _memory_reader
    _rss_reader = reader
    if reader is None:
        _memory_reader = None
        return

    def _wrap() -> MemoryReading:
        value = max(0, int(reader()))
        return MemoryReading(
            rss_bytes=value,
            swap_bytes=0,
            pressure_bytes=value,
            source="injected",
        )

    _memory_reader = _wrap


def set_memory_reader(reader: Callable[[], MemoryReading] | None) -> None:
    """Inject a full memory reading (tests). Clears the legacy RSS injector."""
    global _rss_reader, _memory_reader
    _rss_reader = None
    _memory_reader = reader


def reset_for_tests() -> None:
    global _paused, _host_paused, _startup_calm_until_mono
    global _rss_reader, _memory_reader, _host_available_reader
    with _lock:
        _paused = False
        _host_paused = False
        _startup_calm_until_mono = 0.0
        _rss_reader = None
        _memory_reader = None
        _host_available_reader = None


def _read_proc_status_kb(keys: tuple[str, ...]) -> dict[str, int]:
    found = {key: 0 for key in keys}
    try:
        with open("/proc/self/status", encoding="utf-8") as fh:
            for line in fh:
                for key in keys:
                    if line.startswith(f"{key}:"):
                        parts = line.split()
                        found[key] = max(0, int(parts[1]))
                        break
    except (OSError, ValueError, IndexError):
        pass
    return found


def read_rss_bytes() -> int:
    """Process RSS in bytes (legacy helper; prefer ``read_memory``)."""
    return read_memory().rss_bytes


def read_memory() -> MemoryReading:
    """Best available pressure sample (cgroup → proc RSS+swap → RSS)."""
    if _memory_reader is not None:
        try:
            reading = _memory_reader()
            return MemoryReading(
                rss_bytes=max(0, int(reading.rss_bytes)),
                swap_bytes=max(0, int(reading.swap_bytes)),
                pressure_bytes=max(0, int(reading.pressure_bytes)),
                source=str(reading.source or "injected"),
            )
        except Exception:
            return MemoryReading(0, 0, 0, "injected")

    cgroup = _self_cgroup_dir()
    if cgroup is not None:
        try:
            current = int((cgroup / "memory.current").read_text(encoding="utf-8").strip())
            swap_path = cgroup / "memory.swap.current"
            swap = int(swap_path.read_text(encoding="utf-8").strip()) if swap_path.is_file() else 0
            current = max(0, current)
            swap = max(0, swap)
            return MemoryReading(
                rss_bytes=current,
                swap_bytes=swap,
                pressure_bytes=current + swap,
                source="cgroup",
            )
        except (OSError, ValueError):
            pass

    kb = _read_proc_status_kb(("VmRSS", "VmSwap"))
    rss = kb["VmRSS"] * 1024
    swap = kb["VmSwap"] * 1024
    if rss or swap:
        return MemoryReading(
            rss_bytes=rss,
            swap_bytes=swap,
            pressure_bytes=rss + swap,
            source="proc",
        )
    return MemoryReading(0, 0, 0, "rss")


def evaluate_memory_pressure(
    *,
    rss_bytes: int | None = None,
    pressure_bytes: int | None = None,
    swap_bytes: int | None = None,
    host_available_bytes: int | None = None,
) -> MemoryPressure:
    """Classify combined pressure against soft/hard watermarks with hysteresis.

    ``rss_bytes`` remains a test/compat override for the *pressure* figure when
    ``pressure_bytes`` is omitted (existing call sites). Injected rss/pressure
    paths skip host + startup-calm so unit tests stay deterministic.
    """
    global _paused, _host_paused

    injected = pressure_bytes is not None or rss_bytes is not None

    if pressure_bytes is not None:
        reading = MemoryReading(
            rss_bytes=max(0, int(rss_bytes or 0)),
            swap_bytes=max(0, int(swap_bytes or 0)),
            pressure_bytes=max(0, int(pressure_bytes)),
            source="injected",
        )
    elif rss_bytes is not None:
        # Legacy: callers/tests pass the gated figure as rss_bytes.
        value = max(0, int(rss_bytes))
        reading = MemoryReading(
            rss_bytes=value,
            swap_bytes=max(0, int(swap_bytes or 0)),
            pressure_bytes=value,
            source="injected",
        )
    else:
        reading = read_memory()

    soft = max(1, int(SOFT_WATERMARK_BYTES))
    hard = max(soft, int(HARD_WATERMARK_BYTES))
    resume = min(max(0, int(RESUME_WATERMARK_BYTES)), soft)
    pressure_value = reading.pressure_bytes

    with _lock:
        if pressure_value >= hard:
            _paused = True
            level = "hard"
        elif pressure_value >= soft:
            _paused = True
            level = "soft"
        elif _paused and pressure_value > resume:
            level = "soft"
        else:
            _paused = False
            level = "ok"
        cgroup_paused = _paused

    signal = reading.source
    message = ""
    unload = False
    paused = cgroup_paused
    if cgroup_paused:
        unload = True
        message = PAUSE_MESSAGE
        level_out = level
    else:
        level_out = "ok"

    # Startup calm: automatic post-boot/post-heal bulk seat (live path only).
    if not injected and startup_calm_active():
        paused = True
        unload = False
        level_out = "soft"
        signal = "startup_calm"
        message = "Paused: starting up"

    # Host room: roommates count (live path, or explicit host override).
    if not injected or host_available_bytes is not None:
        if host_available_bytes is not None:
            host_avail: int | None = max(0, int(host_available_bytes))
        elif not injected:
            host_avail = read_host_available_bytes()
        else:
            host_avail = None
        host_soft = max(1, int(HOST_SOFT_AVAILABLE_BYTES))
        host_hard = max(1, min(int(HOST_HARD_AVAILABLE_BYTES), host_soft))
        host_resume = max(host_soft, int(HOST_RESUME_AVAILABLE_BYTES))
        if host_avail is not None:
            with _lock:
                if host_avail < host_hard:
                    _host_paused = True
                    host_level = "hard"
                elif host_avail < host_soft:
                    _host_paused = True
                    host_level = "soft"
                elif _host_paused and host_avail < host_resume:
                    host_level = "soft"
                else:
                    _host_paused = False
                    host_level = "ok"
                host_is_paused = _host_paused
            if host_is_paused:
                paused = True
                unload = True
                # Prefer host signal when it is the reason (or worse than cgroup).
                if not cgroup_paused or host_level == "hard":
                    level_out = host_level
                    signal = "host"
                    message = PAUSE_MESSAGE

    return MemoryPressure(
        rss_bytes=reading.rss_bytes,
        swap_bytes=reading.swap_bytes,
        pressure_bytes=pressure_value,
        signal=signal,
        soft_bytes=soft,
        hard_bytes=hard,
        resume_bytes=resume,
        level=level_out if paused else "ok",
        pause_bulk=paused,
        # Soft pause must shed ML residency too — waiting at soft with models
        # still resident is not a stable wait state under a cgroup swap cap.
        # Startup calm is the exception: nothing loaded yet; do not thrash.
        unload_models=unload,
        message=message if paused else "",
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


def request_model_unload(*, force: bool = False) -> list[str]:
    """Shed ModelPool residents (skips pin-while-hot unless ``force``).

    Same contract as before — memory pressure pauses bulk work and drops
    ML residency for a lazy reload later. Pool is the choke point; worker
    unload helpers clear any straggler globals set outside the pool.
    Active search/embed pins block unload so mid-turn stalls never happen.
    """
    unloaded: list[str] = []
    try:
        from core.model_pool import get_model_pool

        unloaded.extend(get_model_pool().unload_all(force=force))
    except Exception:
        log.debug("memory_pressure: model_pool unload_all failed", exc_info=True)

    # Straggler sweep: clear worker globals even if they bypassed the pool
    # (tests, legacy paths). Pool-aware unload helpers are no-ops when empty
    # or when a pin blocks them.
    try:
        import embedding_worker
        from core.model_pool import get_model_pool

        had_model = embedding_worker._model is not None
        embedding_worker._unload_model(force=force)
        if (
            "embeddings" not in unloaded
            and had_model
            and embedding_worker._model is None
            and "embeddings" not in get_model_pool().resident_names()
        ):
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


def gate_bulk_work(*, rss_bytes: int | None = None, pressure_bytes: int | None = None) -> MemoryPressure:
    """Shared bulk gate: pause above soft, shed buffers + unpinned models."""
    pressure = evaluate_memory_pressure(rss_bytes=rss_bytes, pressure_bytes=pressure_bytes)
    if not pressure.pause_bulk:
        return pressure
    before = read_memory() if rss_bytes is None and pressure_bytes is None else None
    before_bytes = (
        pressure.pressure_bytes
        if before is None
        else before.pressure_bytes
    )
    released = release_discardable_buffers()
    unloaded: list[str] = []
    if pressure.unload_models:
        unloaded = request_model_unload(force=False)
    after = read_memory() if before is not None else None
    after_bytes = before_bytes if after is None else after.pressure_bytes
    log.info(
        "worker=memory_pressure event=%s_watermark signal=%s "
        "pressure_before=%s pressure_after=%s rss=%s swap=%s "
        "released=%s unloaded=%s",
        pressure.level,
        pressure.signal,
        before_bytes,
        after_bytes,
        pressure.rss_bytes,
        pressure.swap_bytes,
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

    Full configured size restores only when pressure is at or below resume.
    """
    configured = max(1, int(configured or 1))
    pressure = evaluate_memory_pressure(rss_bytes=rss_bytes)
    if pressure.pressure_bytes > pressure.resume_bytes:
        return max(1, configured // 2)
    return configured
