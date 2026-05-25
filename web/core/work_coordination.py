"""Manual bulk work coordination for background builders and warmers."""

from __future__ import annotations

import asyncio
import threading
import time
from contextlib import contextmanager


USER_VISIBLE = "user_visible"
MANUAL_BULK = "manual_bulk"
AMBIENT_WARMING = "ambient_warming"

_lock = threading.Lock()
_manual_active: dict[str, int] = {}
_manual_updated_at = 0.0
_manual_owner: str | None = None


def _now() -> float:
    return time.time()


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
    global _manual_owner, _manual_updated_at
    name = str(kind or "bulk").strip() or "bulk"
    with _lock:
        if _manual_owner is None:
            _manual_owner = name
            _manual_updated_at = _now()
        return _manual_owner


def release_manual_owner(kind: str) -> None:
    global _manual_owner, _manual_updated_at
    name = str(kind or "bulk").strip() or "bulk"
    with _lock:
        if _manual_owner == name:
            _manual_owner = None
            _manual_updated_at = _now()


def manual_owner() -> str | None:
    with _lock:
        return _manual_owner


async def wait_for_manual_turn(kind: str, *, poll_seconds: float = 0.25) -> None:
    global _manual_owner, _manual_updated_at
    name = str(kind or "bulk").strip() or "bulk"
    while True:
        with _lock:
            owner = _manual_owner
            if owner is None:
                claim = True
            else:
                claim = False
            if owner is None or owner == name:
                if claim:
                    _manual_owner = name
                    _manual_updated_at = _now()
                return
        await asyncio.sleep(poll_seconds)


def manual_bulk_active() -> bool:
    with _lock:
        return bool(_manual_active)


def status() -> dict:
    with _lock:
        active = dict(_manual_active)
        owner = _manual_owner
        updated_at = _manual_updated_at
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
        "updated_at": updated_at,
    }


async def wait_for_lane(lane: str, *, poll_seconds: float = 0.05) -> None:
    if lane != AMBIENT_WARMING:
        return
    while manual_bulk_active():
        await asyncio.sleep(poll_seconds)


def ambient_warming_allowed() -> bool:
    return not manual_bulk_active()
