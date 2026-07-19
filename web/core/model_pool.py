"""Shared ModelPool — single owner of ML model load/unload.

Host reality (RTX 2060 Super 8GB / ~15GB RAM): embeddings (~7.5 GiB VRAM),
captions (~6 GiB VRAM), faces + subject masks (CPU/RAM) must never all sit
resident at once. This pool is the only residency authority.

Policy
------
1. Every loader goes through ``acquire``; every unload through ``unload`` /
   ``unload_all``. A loader that bypasses the pool is a bug.
2. Each model declares approximate ``vram_bytes`` / ``ram_bytes`` costs.
   Budgets come from ``PHOTOARCHIVE_MODEL_BUDGET_VRAM_BYTES`` and
   ``PHOTOARCHIVE_MODEL_BUDGET_RAM_BYTES``. Empty / unset / ``0`` /
   ``unlimited`` → unlimited budget (pass-through: no eviction — today's
   behavior). Recommended host defaults live in ``DEFAULT_*_BUDGET_BYTES``.
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

# Recommended budgets for this host (8GB VRAM / ~15GB RAM). One large
# GPU model at a time; RAM room for faces + subject mask beside it.
DEFAULT_VRAM_BUDGET_BYTES = 6500 * _MIB
DEFAULT_RAM_BUDGET_BYTES = 10 * _GIB

# Approx costs from gpuaudit peak measurements (2026-07-19).
COST_EMBEDDINGS_VRAM = 7500 * _MIB
COST_EMBEDDINGS_RAM = 512 * _MIB
COST_CAPTIONS_VRAM = 6000 * _MIB
COST_CAPTIONS_RAM = 512 * _MIB
COST_PEOPLE_VRAM = 0
COST_PEOPLE_RAM = 800 * _MIB
COST_SUBJECT_MASK_VRAM = 0
COST_SUBJECT_MASK_RAM = 200 * _MIB

ENV_VRAM_BUDGET = "PHOTOARCHIVE_MODEL_BUDGET_VRAM_BYTES"
ENV_RAM_BUDGET = "PHOTOARCHIVE_MODEL_BUDGET_RAM_BYTES"
ENV_PIN_SECONDS = "PHOTOARCHIVE_MODEL_PIN_SECONDS"

PIN_WHILE_HOT_SECONDS = 30.0

LoadFn = Callable[[], Any]
UnloadFn = Callable[[], None]


def _env_budget_bytes(name: str) -> int | None:
    """Parse a budget env var. None means unlimited (pass-through)."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return None
    lowered = raw.lower()
    if lowered in {"0", "unlimited", "none", "off"}:
        return None
    if lowered in {"default", "auto", "host"}:
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
            if not self._evict_one(exclude=name, need_vram=vram_bytes, need_ram=ram_bytes):
                log.warning(
                    "model_pool event=budget_exceeded name=%s "
                    "vram_budget=%s ram_budget=%s used_vram=%s used_ram=%s "
                    "(no evictable resident; loading anyway)",
                    name,
                    self._vram_budget,
                    self._ram_budget,
                    used_vram,
                    used_ram,
                )
                return

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

    def unload(self, name: str) -> bool:
        """Drop one resident and run its unload callback."""
        with self._lock:
            dropped = self._drop_locked(name, run_unload=True)
            if dropped:
                log.info("model_pool event=unload name=%s", name)
            return dropped

    def unload_all(self) -> list[str]:
        """Shed every resident. Used by memory_pressure."""
        with self._lock:
            names = list(self._residents)
            for name in names:
                self._drop_locked(name, run_unload=True)
            if names:
                log.info("model_pool event=unload_all names=%s", ",".join(names))
            return names

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
    """Process singleton, budgets read from env on first use."""
    global _pool
    with _pool_lock:
        if _pool is None:
            _pool = ModelPool(
                vram_budget_bytes=_env_budget_bytes(ENV_VRAM_BUDGET),
                ram_budget_bytes=_env_budget_bytes(ENV_RAM_BUDGET),
                pin_seconds=_env_pin_seconds(),
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
            )
        _pool = pool
        return pool
