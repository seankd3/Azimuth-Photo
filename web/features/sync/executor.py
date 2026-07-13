"""Bounded background executor for satellite sync work.

Starlette/FastAPI and aiosqlite share asyncio's default thread pool. SyncWorker
HTTP + hash + mirror work must not saturate that pool or interactive routes
(`/api/stats`, thumbs) queue behind background fan-out.
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import TypeVar

T = TypeVar("T")

# Keep well below asyncio's default pool (~32) so request threads stay free.
_SYNC_WORKERS = 4
_executor: ThreadPoolExecutor | None = None
_semaphore: asyncio.Semaphore | None = None


def _get_executor() -> ThreadPoolExecutor:
    global _executor
    if _executor is None:
        _executor = ThreadPoolExecutor(max_workers=_SYNC_WORKERS, thread_name_prefix="sync-bg")
    return _executor


def _get_semaphore() -> asyncio.Semaphore:
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(_SYNC_WORKERS)
    return _semaphore


async def run_sync_work(func, /, *args, **kwargs) -> T:
    """Run blocking sync work on the dedicated pool under a concurrency cap."""

    loop = asyncio.get_running_loop()
    semaphore = _get_semaphore()
    async with semaphore:
        return await loop.run_in_executor(_get_executor(), lambda: func(*args, **kwargs))
