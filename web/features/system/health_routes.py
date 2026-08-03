"""HTTP surface for System Health aggregation."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter

from core.background import track_background_task
from features.system import health


router = APIRouter()
DbPathProvider = Callable[[], str]
_health_cache: dict[str, Any] | None = None
_health_cache_at = 0.0
_health_refresh_task: asyncio.Task | None = None
_HEALTH_CACHE_SECONDS = 30.0


async def _refresh_health() -> dict[str, Any]:
    global _health_cache, _health_cache_at
    result = await asyncio.to_thread(health.collect_health)
    _health_cache = result
    _health_cache_at = time.monotonic()
    return result


async def _health_snapshot(*, initial_wait_seconds: float) -> dict[str, Any]:
    """Return cached health immediately while one uncancelled refresh runs."""
    global _health_refresh_task
    now = time.monotonic()
    if _health_cache is not None and now - _health_cache_at < _HEALTH_CACHE_SECONDS:
        return _health_cache
    if _health_refresh_task is None or _health_refresh_task.done():
        _health_refresh_task = track_background_task(_refresh_health())
    done, _ = await asyncio.wait(
        {_health_refresh_task},
        timeout=max(0.05, float(initial_wait_seconds)),
    )
    if _health_refresh_task in done:
        try:
            return _health_refresh_task.result()
        except Exception:
            pass
    if _health_cache is not None:
        return _health_cache
    return {
        "overall": "warn",
        "checked_at": time.time(),
        "checks": [],
        "status_stale": True,
    }




@router.get("/api/health")
async def api_health() -> dict[str, Any]:
    """Small fleet-health summary for paired satellites and local monitors."""

    details = await _health_snapshot(initial_wait_seconds=0.15)
    return {
        "status": details["overall"],
        "checked_at": details["checked_at"],
        "checks": {
            str(item["id"]): str(item["status"])
            for item in details.get("checks", [])
        },
    }


@router.get("/api/health/details")
async def api_health_details() -> dict[str, Any]:
    return await _health_snapshot(initial_wait_seconds=1.0)
