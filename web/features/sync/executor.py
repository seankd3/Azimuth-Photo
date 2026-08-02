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

# Keep both pools well below asyncio's default pool (~32). Background mirror,
# upload, and predictive prefetch work must never occupy the threads reserved
# for an interactive hub media read-through.
_SYNC_WORKERS = 4
_FOREGROUND_WORKERS = 4
_executor: ThreadPoolExecutor | None = None
_foreground_executor: ThreadPoolExecutor | None = None
_semaphore: asyncio.Semaphore | None = None
_foreground_semaphore: asyncio.Semaphore | None = None


def _get_executor() -> ThreadPoolExecutor:
    global _executor
    if _executor is None:
        _executor = ThreadPoolExecutor(max_workers=_SYNC_WORKERS, thread_name_prefix="sync-bg")
    return _executor


def _get_foreground_executor() -> ThreadPoolExecutor:
    global _foreground_executor
    if _foreground_executor is None:
        _foreground_executor = ThreadPoolExecutor(
            max_workers=_FOREGROUND_WORKERS,
            thread_name_prefix="hub-fg",
        )
    return _foreground_executor


def _get_semaphore() -> asyncio.Semaphore:
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(_SYNC_WORKERS)
    return _semaphore


def _get_foreground_semaphore() -> asyncio.Semaphore:
    global _foreground_semaphore
    if _foreground_semaphore is None:
        _foreground_semaphore = asyncio.Semaphore(_FOREGROUND_WORKERS)
    return _foreground_semaphore


async def run_sync_work(func, /, *args, **kwargs) -> T:
    """Run blocking background sync work under its concurrency cap."""

    loop = asyncio.get_running_loop()
    semaphore = _get_semaphore()
    async with semaphore:
        return await loop.run_in_executor(_get_executor(), lambda: func(*args, **kwargs))


async def run_foreground_sync_work(func, /, *args, **kwargs) -> T:
    """Run request-path hub work without queueing behind bulk sync jobs."""

    loop = asyncio.get_running_loop()
    semaphore = _get_foreground_semaphore()
    async with semaphore:
        return await loop.run_in_executor(
            _get_foreground_executor(),
            lambda: func(*args, **kwargs),
        )


async def hub_request(
    method: str,
    url: str,
    *,
    body: bytes | None = None,
    headers: dict | None = None,
    timeout: float = 30,
    runner=None,
) -> tuple[int, dict[str, str], bytes]:
    """Talk to the hub, on a background thread, as this satellite.

    One place. The mirror, the preview catch-up and the sync worker each had
    their own copy of these ten lines, differing only in a default timeout —
    and one of them had already generalised it, which the other two never
    learned. An HTTP error is a reply, not an exception: the caller decides
    what a 404 or a 422 means.
    """

    import urllib.error
    import urllib.request

    from features.sync import satellite

    def request() -> tuple[int, dict[str, str], bytes]:
        request_headers = dict(headers or {})
        request_headers.update(satellite.hub_request_headers())
        outbound = urllib.request.Request(url, data=body, headers=request_headers, method=method)
        try:
            with urllib.request.urlopen(outbound, timeout=timeout) as response:  # noqa: S310 - configured tailnet hub.
                return response.status, dict(response.headers), response.read()
        except urllib.error.HTTPError as error:
            return error.code, dict(error.headers or {}), error.read()

    return await (runner or run_sync_work)(request)
