"""HTTP surface for System Health aggregation."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter

from features.system import health


router = APIRouter()
DbPathProvider = Callable[[], str]


def configure(*, db_path: DbPathProvider) -> None:
    health.configure(db_path=db_path)


@router.get("/api/health/details")
async def api_health_details() -> dict[str, Any]:
    return await asyncio.to_thread(health.collect_health)
