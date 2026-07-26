"""Shared ModelPool — single owner of ML model load/unload.

Host reality (RTX 2060 Super 8GB / ~15GB RAM): embeddings (~7.5 GiB VRAM),
captions (~6 GiB VRAM), faces + subject masks (CPU/RAM) must never all sit
resident at once. This pool is the only residency authority.

Policy
------
1. Every loader goes through ``acquire``; every unload through ``unload`` /
   ``unload_all``. A loader that bypasses the pool is a bug.
2. Each model declares approximate ``vram_bytes`` / ``ram_bytes`` costs.
   Budgets come from ``AZIMUTH_MODEL_BUDGET_VRAM_BYTES`` and
   ``AZIMUTH_MODEL_BUDGET_RAM_BYTES``. Empty / unset uses the safe host
   defaults. ``0`` / ``unlimited`` explicitly opts into pass-through.
3. Loading that would exceed a finite budget evicts LRU residents first
   (skipping pin-while-hot), then calls their unload callback +
   ``torch.cuda.empty_cache``.
4. Pin-while-hot: an ``interactive=True`` acquire marks the model exempt
   from eviction for ``PIN_WHILE_HOT_SECONDS`` (default 30). Bulk workers
   (caption sweep, embedding backfill, face scan) must pass
   ``interactive=False`` so they cannot bounce a model an interactive
   request just used.
5. Single-flight: concurrent ``acquire`` of the same name shares one
   in-flight load (threading.Event).
6. ``memory_pressure.request_model_unload`` calls ``unload_all`` — same
   shed contract as before, one choke point.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

log = logging.getLogger(__name__)

_GIB = 1024**3
_MIB = 1024**2

# Budgets and costs come from core.host_profile (detected RAM/VRAM).
# Fallbacks below only apply if profile import fails at import time.
DEFAULT_VRAM_BUDGET_BYTES = 6500 * _MIB
DEFAULT_RAM_BUDGET_BYTES = 10 * _GIB

COST_EMBEDDINGS_VRAM = 6200 * _MIB
COST_EMBEDDINGS_RAM = 512 * _MIB
COST_CAPTIONS_VRAM = 5500 * _MIB
COST_CAPTIONS_RAM = 512 * _MIB
COST_PEOPLE_VRAM = 0
COST_PEOPLE_RAM = 800 * _MIB
COST_SUBJECT_MASK_VRAM = 0
COST_SUBJECT_MASK_RAM = 200 * _MIB


def _host_default_budgets() -> tuple[int | None, int]:
    """VRAM/RAM model budgets from the live host profile."""
    try:
        from core.host_profile import detect_host_profile

        profile = detect_host_profile()
        return profile.model_vram_budget_bytes(), profile.model_ram_budget_bytes()
    except Exception:
        log.debug("model_pool: host_profile unavailable, using static defaults", exc_info=True)
        return DEFAULT_VRAM_BUDGET_BYTES, DEFAULT_RAM_BUDGET_BYTES


def _host_model_costs() -> None:
    """Refresh module-level COST_* from host profile (called on pool create)."""
    global COST_EMBEDDINGS_VRAM, COST_CAPTIONS_VRAM
    global COST_EMBEDDINGS_RAM, COST_CAPTIONS_RAM, COST_PEOPLE_RAM, COST_SUBJECT_MASK_RAM
    try:
        from core.host_profile import (
            COST_CAPTION_7B_VRAM,
            COST_EMBED_8B_VRAM,
            COST_CAPTION_RAM as _CAP_RAM,
            COST_EMBED_RAM as _EMB_RAM,
            COST_PEOPLE_RAM as _PPL_RAM,
            COST_SUBJECT_MASK_RAM as _SUB_RAM,
            detect_host_profile,
        )

        profile = detect_host_profile()
        # Costs follow the *recommended* models for this host so accounting
        # matches what we actually try to load by default.
        COST_EMBEDDINGS_VRAM = profile.embed_vram_cost_bytes(profile.recommended_embed_preset())
        COST_CAPTIONS_VRAM = profile.caption_vram_cost_bytes(profile.recommended_caption_preset())
        COST_EMBEDDINGS_RAM = _EMB_RAM
        COST_CAPTIONS_RAM = _CAP_RAM
        COST_PEOPLE_RAM = _PPL_RAM
        COST_SUBJECT_MASK_RAM = _SUB_RAM
        # Keep 8B estimate available for callers that load it on a big card.
        if "8b" not in profile.recommended_embed_preset():
            # Still export a sane 8B cost if user overrides preset upward.
            pass
        del COST_EMBED_8B_VRAM, COST_CAPTION_7B_VRAM  # silence linters if unused
    except Exception:
        log.debug("model_pool: cost refresh skipped", exc_info=True)

ENV_VRAM_BUDGET = "AZIMUTH_MODEL_BUDGET_VRAM_BYTES"
ENV_RAM_BUDGET = "AZIMUTH_MODEL_BUDGET_RAM_BYTES"
ENV_PIN_SECONDS = "AZIMUTH_MODEL_PIN_SECONDS"

PIN_WHILE_HOT_SECONDS = 30.0

LoadFn = Callable[[], Any]
UnloadFn = Callable[[], None]


def _env_budget_bytes(name: str) -> int | None:
    """Parse a budget env var. None means unlimited (pass-through)."""
    raw = os.environ.get(name, "").strip()
    host_vram, host_ram = _host_default_budgets()
    if not raw:
        if name == ENV_VRAM_BUDGET:
            return host_vram
        if name == ENV_RAM_BUDGET:
            return host_ram
        return None
    lowered = raw.lower()
    if lowered in {"0", "unlimited", "none", "off"}:
        return None
    if lowered in {"auto", "host"}:
        if name == ENV_VRAM_BUDGET:
            return host_vram
        if name == ENV_RAM_BUDGET:
            return host_ram
        return None
    if lowered == "default":
        if name == ENV_VRAM_BUDGET:
            return DEFAULT_VRAM_BUDGET_BYTES
        if name == ENV_RAM_BUDGET:
            return DEFAULT_RAM_BUDGET_BYTES
        return None
    try:
        if lowered.endswith("g"):
            value = int(float(lowered[:-1]) * _GIB)
        elif lowered.endswith("m"):
            value = int(float(lowered[:-1]) * _MIB)
        else:
            value = int(raw)
    except ValueError:
        return None
    if value <= 0:
        return None
    return value


def _env_pin_seconds() -> float:
    raw = os.environ.get(ENV_PIN_SECONDS, "").strip()
    if not raw:
        return PIN_WHILE_HOT_SECONDS
    try:
        return max(0.0, float(raw))
    except ValueError:
        return PIN_WHILE_HOT_SECONDS


@dataclass
class _Resident:
    name: str
    value: Any
    vram_bytes: int
    ram_bytes: int
    unload_fn: UnloadFn
    last_used: float
    pin_until: float = 0.0


@dataclass
class _InFlight:
    event: threading.Event
    error: BaseException | None = None
    value: Any = None


class ModelPool:
    """Process-wide LRU residency for named ML models."""

    def __init__(
        self,
        *,
        vram_budget_bytes: int | None = None,
        ram_budget_bytes: int | None = None,
        pin_seconds: float | None = None,
        empty_cache: Callable[[], None] | None = None,
        clock: Callable[[], float] | None = None,
        exclusive_gpu: bool | None = None,
    ) -> None:
        self._lock = threading.RLock()
        self._residents: dict[str, _Resident] = {}
        self._inflight: dict[str, _InFlight] = {}
        self._vram_budget = vram_budget_bytes
        self._ram_budget = ram_budget_bytes
        self._pin_seconds = PIN_WHILE_HOT_SECONDS if pin_seconds is None else max(0.0, float(pin_seconds))
        self._empty_cache = empty_cache
        self._clock = clock or time.monotonic
        self._eviction_log: list[str] = []
        # Default False so unit tests / explicit small budgets keep classic LRU
        # multi-model accounting. Production get_model_pool() passes True on
        # small GPUs.
        self._exclusive_gpu = bool(exclusive_gpu) if exclusive_gpu is not None else False

    @property
    def vram_budget_bytes(self) -> int | None:
        return self._vram_budget

    @property
    def ram_budget_bytes(self) -> int | None:
        return self._ram_budget

    @property
    def pass_through(self) -> bool:
        """True when both budgets are unlimited — never evicts for budget."""
        return self._vram_budget is None and self._ram_budget is None

    def resident_names(self) -> list[str]:
        with self._lock:
            return list(self._residents)

    def eviction_log(self) -> list[str]:
        with self._lock:
            return list(self._eviction_log)

    def clear_eviction_log(self) -> None:
        with self._lock:
            self._eviction_log.clear()

    def _used_vram(self) -> int:
        return sum(r.vram_bytes for r in self._residents.values())

    def _used_ram(self) -> int:
        return sum(r.ram_bytes for r in self._residents.values())

    def _is_pinned(self, resident: _Resident, now: float) -> bool:
        return resident.pin_until > now

    def _run_empty_cache(self) -> None:
        fn = self._empty_cache
        if fn is None:
            try:
                from core.ml_device import empty_cuda_cache

                fn = empty_cuda_cache
            except Exception:
                return
        try:
            fn()
        except Exception:
            log.debug("model_pool: empty_cache failed", exc_info=True)

    def _evict_one(self, *, exclude: str, need_vram: int, need_ram: int) -> bool:
        """Evict one LRU non-pinned resident that helps the budget. False if none."""
        now = self._clock()
        candidates = [
            r
            for name, r in self._residents.items()
            if name != exclude and not self._is_pinned(r, now)
        ]
        if not candidates:
            return False
        candidates.sort(key=lambda r: r.last_used)
        victim = candidates[0]
        log.info(
            "model_pool event=evict name=%s reason=budget "
            "need_vram=%s need_ram=%s used_vram=%s used_ram=%s",
            victim.name,
            need_vram,
            need_ram,
            self._used_vram(),
            self._used_ram(),
        )
        self._eviction_log.append(victim.name)
        self._drop_locked(victim.name, run_unload=True)
        return True

    def _ensure_budget(self, name: str, vram_bytes: int, ram_bytes: int) -> None:
        if self.pass_through:
            return

        # Small GPUs: only one VRAM-resident model at a time. Evict every other
        # GPU resident before loading so we never "load anyway" into OOM.
        if self._exclusive_gpu and vram_bytes > 0:
            victims = [
                r.name
                for r in list(self._residents.values())
                if r.name != name and r.vram_bytes > 0
            ]
            for victim in victims:
                log.info(
                    "model_pool event=evict name=%s reason=exclusive_gpu new=%s",
                    victim,
                    name,
                )
                self._drop_locked(victim, run_unload=True)
                self._eviction_log.append(victim)

        while True:
            used_vram = self._used_vram()
            used_ram = self._used_ram()
            # Replacing an already-resident name does not double-count it.
            if name in self._residents:
                current = self._residents[name]
                used_vram -= current.vram_bytes
                used_ram -= current.ram_bytes
            over_vram = (
                self._vram_budget is not None
                and used_vram + vram_bytes > self._vram_budget
            )
            over_ram = (
                self._ram_budget is not None
                and used_ram + ram_bytes > self._ram_budget
            )
            if not over_vram and not over_ram:
                return
            # Sole resident of an oversized model: allow exclusive occupancy
            # when nothing else holds VRAM (cost estimate > budget is common
            # on 8GB cards holding a single 6GB 4-bit model).
            if used_vram == 0 and used_ram == 0:
                log.warning(
                    "model_pool event=exclusive_oversized_load name=%s "
                    "vram_cost=%s vram_budget=%s",
                    name,
                    vram_bytes,
                    self._vram_budget,
                )
                return
            if not self._evict_one(exclude=name, need_vram=vram_bytes, need_ram=ram_bytes):
                log.error(
                    "model_pool event=budget_exceeded name=%s "
                    "vram_budget=%s ram_budget=%s used_vram=%s used_ram=%s "
                    "need_vram=%s need_ram=%s (refusing load)",
                    name,
                    self._vram_budget,
                    self._ram_budget,
                    used_vram,
                    used_ram,
                    vram_bytes,
                    ram_bytes,
                )
                raise RuntimeError(
                    f"Insufficient memory to load model '{name}' "
                    f"(need vram={vram_bytes} ram={ram_bytes}, "
                    f"budget vram={self._vram_budget} ram={self._ram_budget}, "
                    f"in use vram={used_vram} ram={used_ram}). "
                    f"Stop other AI work or pick a smaller model preset."
                )

    def _drop_locked(self, name: str, *, run_unload: bool) -> bool:
        resident = self._residents.pop(name, None)
        if resident is None:
            return False
        if run_unload:
            try:
                resident.unload_fn()
            except Exception:
                log.debug("model_pool: unload %s failed", name, exc_info=True)
            # Drop the last strong ref before empty_cache so CUDA blocks return.
            del resident
            try:
                import gc

                gc.collect()
            except Exception:
                pass
            self._run_empty_cache()
        return True

    def acquire(
        self,
        name: str,
        *,
        load_fn: LoadFn,
        unload_fn: UnloadFn,
        vram_bytes: int = 0,
        ram_bytes: int = 0,
        interactive: bool = False,
    ) -> Any:
        """Load ``name`` if needed, evicting LRU peers when over budget.

        ``interactive=True`` extends the pin-while-hot window so bulk work
        cannot immediately evict this model.
        """
        name = str(name or "").strip()
        if not name:
            raise ValueError("model pool name is required")
        vram_bytes = max(0, int(vram_bytes))
        ram_bytes = max(0, int(ram_bytes))

        while True:
            wait_for: _InFlight | None = None
            with self._lock:
                now = self._clock()
                existing = self._residents.get(name)
                if existing is not None:
                    existing.last_used = now
                    if interactive:
                        existing.pin_until = now + self._pin_seconds
                    return existing.value

                inflight = self._inflight.get(name)
                if inflight is not None:
                    wait_for = inflight
                else:
                    wait_for = None
                    self._inflight[name] = _InFlight(event=threading.Event())

            if wait_for is not None:
                wait_for.event.wait()
                if wait_for.error is not None:
                    raise wait_for.error
                # Loop: either resident now, or another failure cleared it.
                with self._lock:
                    existing = self._residents.get(name)
                    if existing is not None:
                        existing.last_used = self._clock()
                        if interactive:
                            existing.pin_until = existing.last_used + self._pin_seconds
                        return existing.value
                continue

            # We own the in-flight slot — load outside the lock so other
            # names can still evict/acquire, but hold the name reservation.
            try:
                with self._lock:
                    self._ensure_budget(name, vram_bytes, ram_bytes)
                value = load_fn()
                with self._lock:
                    now = self._clock()
                    pin_until = (now + self._pin_seconds) if interactive else 0.0
                    # Drop stale entry if a concurrent unload raced.
                    self._residents.pop(name, None)
                    self._residents[name] = _Resident(
                        name=name,
                        value=value,
                        vram_bytes=vram_bytes,
                        ram_bytes=ram_bytes,
                        unload_fn=unload_fn,
                        last_used=now,
                        pin_until=pin_until,
                    )
                    slot = self._inflight.pop(name, None)
                    if slot is not None:
                        slot.value = value
                        slot.event.set()
                    log.info(
                        "model_pool event=load name=%s interactive=%s "
                        "vram=%s ram=%s used_vram=%s used_ram=%s",
                        name,
                        interactive,
                        vram_bytes,
                        ram_bytes,
                        self._used_vram(),
                        self._used_ram(),
                    )
                return value
            except Exception as exc:
                with self._lock:
                    slot = self._inflight.pop(name, None)
                    if slot is not None:
                        slot.error = exc
                        slot.event.set()
                raise

    def touch(self, name: str, *, interactive: bool = False) -> bool:
        """Refresh LRU / pin for an already-resident model. False if absent."""
        with self._lock:
            resident = self._residents.get(name)
            if resident is None:
                return False
            now = self._clock()
            resident.last_used = now
            if interactive:
                resident.pin_until = now + self._pin_seconds
            return True

    def is_pinned(self, name: str) -> bool:
        """True when ``name`` is pin-while-hot (active search/embed turn)."""
        with self._lock:
            resident = self._residents.get(name)
            if resident is None:
                return False
            return self._is_pinned(resident, self._clock())

    def idle_seconds(self, name: str) -> float | None:
        """Seconds since last use, or ``None`` if absent."""
        with self._lock:
            resident = self._residents.get(name)
            if resident is None:
                return None
            return max(0.0, self._clock() - resident.last_used)

    def unload(self, name: str, *, force: bool = False) -> bool:
        """Drop one resident and run its unload callback.

        Pin-while-hot blocks unload unless ``force=True`` (shutdown only).
        Mid-search/embed must never stall from a pressure shed.
        """
        with self._lock:
            resident = self._residents.get(name)
            if resident is None:
                return False
            if not force and self._is_pinned(resident, self._clock()):
                log.info("model_pool event=unload_blocked name=%s reason=pinned", name)
                return False
            dropped = self._drop_locked(name, run_unload=True)
            if dropped:
                log.info("model_pool event=unload name=%s force=%s", name, force)
            return dropped

    def unload_all(self, *, force: bool = False) -> list[str]:
        """Shed residents. Skips pin-while-hot unless ``force=True``.

        Used by memory_pressure (force=False) and process shutdown (force=True).
        """
        with self._lock:
            now = self._clock()
            names = list(self._residents)
            unloaded: list[str] = []
            blocked: list[str] = []
            for name in names:
                resident = self._residents.get(name)
                if resident is None:
                    continue
                if not force and self._is_pinned(resident, now):
                    blocked.append(name)
                    continue
                if self._drop_locked(name, run_unload=True):
                    unloaded.append(name)
            if blocked:
                log.info(
                    "model_pool event=unload_all_blocked names=%s",
                    ",".join(blocked),
                )
            if unloaded:
                log.info(
                    "model_pool event=unload_all names=%s force=%s",
                    ",".join(unloaded),
                    force,
                )
            return unloaded

    def unload_if_idle(self, name: str, *, ttl_seconds: float) -> bool:
        """Unload ``name`` when unpinned and idle past ``ttl_seconds``."""
        ttl_seconds = max(0.0, float(ttl_seconds))
        with self._lock:
            resident = self._residents.get(name)
            if resident is None:
                return False
            now = self._clock()
            if self._is_pinned(resident, now):
                return False
            if (now - resident.last_used) < ttl_seconds:
                return False
            dropped = self._drop_locked(name, run_unload=True)
            if dropped:
                log.info(
                    "model_pool event=unload_idle name=%s idle_s=%.1f ttl_s=%.1f",
                    name,
                    now - resident.last_used,
                    ttl_seconds,
                )
            return dropped

    def drop_tracking(self, name: str) -> bool:
        """Remove residency bookkeeping without calling unload_fn.

        Use when the worker already cleared its globals (e.g. failed load
        cleanup) so the pool does not double-unload.
        """
        with self._lock:
            return self._drop_locked(name, run_unload=False)


_pool: ModelPool | None = None
_pool_lock = threading.Lock()


def get_model_pool() -> ModelPool:
    """Process singleton, budgets from host profile (env overrides still win)."""
    global _pool
    with _pool_lock:
        if _pool is None:
            _host_model_costs()
            vram = _env_budget_bytes(ENV_VRAM_BUDGET)
            ram = _env_budget_bytes(ENV_RAM_BUDGET)
            exclusive = False
            try:
                from core.host_profile import detect_host_profile

                exclusive = detect_host_profile().exclusive_gpu_models()
            except Exception:
                exclusive = bool(vram is not None and vram < 12 * _GIB)
            _pool = ModelPool(
                vram_budget_bytes=vram,
                ram_budget_bytes=ram,
                pin_seconds=_env_pin_seconds(),
                exclusive_gpu=exclusive,
            )
            log.info(
                "model_pool ready vram_budget=%s ram_budget=%s pin_s=%s",
                vram,
                ram,
                _env_pin_seconds(),
            )
        return _pool


def reset_model_pool_for_tests(
    pool: ModelPool | None = None,
    *,
    vram_budget_bytes: int | None = None,
    ram_budget_bytes: int | None = None,
    pin_seconds: float | None = None,
) -> ModelPool:
    """Replace the process singleton (tests only)."""
    global _pool
    with _pool_lock:
        if pool is None:
            pool = ModelPool(
                vram_budget_bytes=vram_budget_bytes,
                ram_budget_bytes=ram_budget_bytes,
                pin_seconds=pin_seconds if pin_seconds is not None else 0.05,
                empty_cache=lambda: None,
                exclusive_gpu=False,
            )
        _pool = pool
        return pool
