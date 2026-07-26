"""Manual bulk work coordination for background builders and warmers."""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from contextlib import asynccontextmanager, contextmanager, suppress


USER_VISIBLE = "user_visible"
MANUAL_BULK = "manual_bulk"
AMBIENT_WARMING = "ambient_warming"
OWNER_LEASE_SECONDS = 15 * 60
LEASE_HEARTBEAT_SECONDS = OWNER_LEASE_SECONDS / 3

log = logging.getLogger(__name__)

_lock = threading.Lock()
_manual_active: dict[str, int] = {}
_manual_updated_at = 0.0
_manual_owner: str | None = None
_manual_owner_updated_at = 0.0
_manual_waiters: set[str] = set()
_gpu_owner: str | None = None
_gpu_owner_updated_at = 0.0
_gpu_waiters: set[str] = set()
_gpu_owner_flag_path = os.environ.get(
    "AZIMUTH_GPU_OWNER_FLAG",
    "/tmp/azimuth-gpu-owner.flag",
)


def _now() -> float:
    return time.time()


def _lease_expired(updated_at: float, *, now: float) -> bool:
    return bool(updated_at and now - updated_at >= OWNER_LEASE_SECONDS)


def _log_stale_owner_steal(
    lane: str,
    previous_owner: str,
    new_owner: str,
    *,
    age_seconds: float,
) -> None:
    log.warning(
        "worker=work_coordination lane=%s event=stale_owner_stolen previous_owner=%s "
        "new_owner=%s age_seconds=%.1f",
        lane,
        previous_owner,
        new_owner,
        age_seconds,
    )


@contextmanager
def manual_bulk(kind: str):
    start_manual_bulk(kind)
    try:
        yield
    finally:
        finish_manual_bulk(kind)


def start_manual_bulk(kind: str) -> None:
    global _manual_updated_at
    name = str(kind or "bulk").strip() or "bulk"
    with _lock:
        _manual_active[name] = int(_manual_active.get(name, 0)) + 1
        _manual_updated_at = _now()


def finish_manual_bulk(kind: str) -> None:
    global _manual_updated_at
    name = str(kind or "bulk").strip() or "bulk"
    with _lock:
        count = int(_manual_active.get(name, 0)) - 1
        if count > 0:
            _manual_active[name] = count
        else:
            _manual_active.pop(name, None)
        _manual_updated_at = _now()


def claim_manual_owner(kind: str) -> str:
    global _manual_owner, _manual_owner_updated_at
    name = str(kind or "bulk").strip() or "bulk"
    now = _now()
    stolen_owner = None
    stolen_age = 0.0
    with _lock:
        if _manual_owner is None or _manual_owner == name or _lease_expired(
            _manual_owner_updated_at,
            now=now,
        ):
            if _manual_owner not in (None, name):
                stolen_owner = _manual_owner
                stolen_age = now - _manual_owner_updated_at
            _manual_owner = name
            _manual_owner_updated_at = now
        owner = _manual_owner
    if stolen_owner is not None:
        _log_stale_owner_steal(
            "manual",
            stolen_owner,
            name,
            age_seconds=stolen_age,
        )
    return owner


def release_manual_owner(kind: str) -> None:
    global _manual_owner, _manual_owner_updated_at
    name = str(kind or "bulk").strip() or "bulk"
    with _lock:
        if _manual_owner == name:
            _manual_owner = None
            _manual_owner_updated_at = _now()


def manual_owner() -> str | None:
    with _lock:
        return _manual_owner


def _write_gpu_owner_flag(owner: str | None) -> None:
    try:
        if owner:
            with open(_gpu_owner_flag_path, "w", encoding="utf-8") as fh:
                fh.write(f"{owner}\n{_now():.6f}\n")
        elif os.path.exists(_gpu_owner_flag_path):
            os.unlink(_gpu_owner_flag_path)
    except OSError:
        pass


def claim_gpu_owner(kind: str) -> str:
    """Claim the single GPU model slot for one worker family.

    The in-process owner prevents two app workers from keeping CUDA models
    resident together. The small flag file makes the ownership visible to
    operators and future out-of-process workers.
    """
    global _gpu_owner, _gpu_owner_updated_at
    name = str(kind or "gpu").strip() or "gpu"
    now = _now()
    stolen_owner = None
    stolen_age = 0.0
    with _lock:
        if _gpu_owner is None or _gpu_owner == name or _lease_expired(
            _gpu_owner_updated_at,
            now=now,
        ):
            if _gpu_owner not in (None, name):
                stolen_owner = _gpu_owner
                stolen_age = now - _gpu_owner_updated_at
            _gpu_owner = name
            _gpu_owner_updated_at = now
            _write_gpu_owner_flag(_gpu_owner)
        owner = _gpu_owner
    if stolen_owner is not None:
        _log_stale_owner_steal(
            "gpu",
            stolen_owner,
            name,
            age_seconds=stolen_age,
        )
    return owner


def release_gpu_owner(kind: str) -> None:
    global _gpu_owner, _gpu_owner_updated_at
    name = str(kind or "gpu").strip() or "gpu"
    with _lock:
        if _gpu_owner == name:
            _gpu_owner = None
            _gpu_owner_updated_at = _now()
            _write_gpu_owner_flag(None)


def gpu_owner() -> str | None:
    with _lock:
        return _gpu_owner


def lost_ownership(kind: str, *, gpu: bool = False) -> bool:
    """Return whether a worker no longer owns every lane it is using."""

    name = str(kind or "bulk").strip() or "bulk"
    with _lock:
        if _manual_owner != name:
            return True
        return gpu and _gpu_owner != name


def manual_turn_blocked(kind: str) -> bool:
    name = str(kind or "bulk").strip() or "bulk"
    with _lock:
        return (
            _manual_owner is not None
            and _manual_owner != name
            and not _lease_expired(_manual_owner_updated_at, now=_now())
        )


def gpu_turn_blocked(kind: str) -> bool:
    name = str(kind or "gpu").strip() or "gpu"
    with _lock:
        return (
            _gpu_owner is not None
            and _gpu_owner != name
            and not _lease_expired(_gpu_owner_updated_at, now=_now())
        )


@asynccontextmanager
async def lease_heartbeat(
    kind: str,
    *,
    gpu: bool = False,
    interval_seconds: float = LEASE_HEARTBEAT_SECONDS,
):
    """Keep leases fresh while one non-interruptible model load is running."""

    name = str(kind or "bulk").strip() or "bulk"

    async def renew_until_cancelled() -> None:
        while True:
            await asyncio.sleep(interval_seconds)
            if manual_owner() == name:
                claim_manual_owner(name)
            if gpu and gpu_owner() == name:
                claim_gpu_owner(name)

    heartbeat = asyncio.create_task(renew_until_cancelled())
    try:
        yield
    finally:
        heartbeat.cancel()
        with suppress(asyncio.CancelledError):
            await heartbeat


async def wait_for_gpu_turn(kind: str, *, poll_seconds: float = 0.5) -> None:
    name = str(kind or "gpu").strip() or "gpu"
    try:
        while True:
            with _lock:
                owner = _gpu_owner
                blocked = owner is not None and owner != name and not _lease_expired(
                    _gpu_owner_updated_at,
                    now=_now(),
                )
                if blocked:
                    _gpu_waiters.add(name)
            if not blocked:
                if claim_gpu_owner(name) == name:
                    return
            await asyncio.sleep(poll_seconds)
    finally:
        with _lock:
            _gpu_waiters.discard(name)


async def wait_for_manual_turn(kind: str, *, poll_seconds: float = 0.25) -> None:
    name = str(kind or "bulk").strip() or "bulk"
    try:
        while True:
            with _lock:
                owner = _manual_owner
                blocked = owner is not None and owner != name and not _lease_expired(
                    _manual_owner_updated_at,
                    now=_now(),
                )
                if blocked:
                    _manual_waiters.add(name)
            if not blocked:
                if claim_manual_owner(name) == name:
                    return
            await asyncio.sleep(poll_seconds)
    finally:
        with _lock:
            _manual_waiters.discard(name)


def manual_bulk_active() -> bool:
    with _lock:
        return bool(_manual_active)


def status() -> dict:
    with _lock:
        active = dict(_manual_active)
        owner = _manual_owner
        manual_owner_updated_at = _manual_owner_updated_at
        manual_waiters = sorted(_manual_waiters)
        gpu_owner_value = _gpu_owner
        updated_at = _manual_updated_at
        gpu_updated_at = _gpu_owner_updated_at
        gpu_waiters = sorted(_gpu_waiters)
    waiting_for_owner = sorted(set(manual_waiters + gpu_waiters))
    return {
        "lanes": {
            "user_visible": {"priority": 0, "state": "ready"},
            "manual_bulk": {
                "priority": 1,
                "state": "running" if active else "idle",
                "active": sorted(active),
            },
            "ambient_warming": {
                "priority": 2,
                "state": "waiting" if active else "ready",
            },
        },
        "manual_active": sorted(active),
        "manual_owner": owner,
        "manual_owner_updated_at": manual_owner_updated_at,
        "manual_waiters": manual_waiters,
        "gpu_owner": gpu_owner_value,
        "gpu_waiters": gpu_waiters,
        "waiting_for_owner": waiting_for_owner,
        "owner_lease_seconds": OWNER_LEASE_SECONDS,
        "gpu_owner_flag_path": _gpu_owner_flag_path,
        "gpu_owner_updated_at": gpu_updated_at,
        "updated_at": updated_at,
    }


async def wait_for_lane(lane: str, *, poll_seconds: float = 0.05) -> None:
    if lane != AMBIENT_WARMING:
        return
    while manual_bulk_active():
        await asyncio.sleep(poll_seconds)


def ambient_warming_allowed() -> bool:
    return not manual_bulk_active()
