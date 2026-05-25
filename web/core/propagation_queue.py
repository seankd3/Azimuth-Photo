"""Deferred write-behind queue for Elo propagation work."""

from __future__ import annotations

import asyncio
import inspect
import time
from collections import deque
from collections.abc import Awaitable, Callable


_queue: deque[Awaitable] = deque()
_worker_task: asyncio.Task | None = None
_last_error = ""
_last_completed_at: float | None = None
_last_invalidated_at: float | None = None
_pending_invalidations = 0


def status() -> dict:
    return {
        "queued": len(_queue),
        "running": bool(_worker_task and not _worker_task.done()),
        "last_error": _last_error,
        "last_completed_at": _last_completed_at,
        "last_invalidated_at": _last_invalidated_at,
        "pending_invalidations": _pending_invalidations,
    }


def schedule(coro: Awaitable, *, invalidate_callback: Callable[[], None] | None = None) -> None:
    """Queue a propagation coroutine and coalesce cache invalidation after it drains."""

    global _worker_task
    if not inspect.isawaitable(coro):
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        close = getattr(coro, "close", None)
        if close:
            close()
        return
    _queue.append(coro)
    if _worker_task is None or _worker_task.done():
        _worker_task = loop.create_task(_run_queue(invalidate_callback))


async def _run_queue(invalidate_callback: Callable[[], None] | None) -> None:
    global _last_error, _last_completed_at, _last_invalidated_at, _pending_invalidations
    while True:
        while _queue:
            coro = _queue.popleft()
            try:
                await coro
                _last_error = ""
            except asyncio.CancelledError:
                close = getattr(coro, "close", None)
                if close:
                    close()
                raise
            except Exception as exc:
                _last_error = f"{type(exc).__name__}: {exc}"
            finally:
                _last_completed_at = time.time()
                _pending_invalidations += 1

        if invalidate_callback is not None and _pending_invalidations > 0:
            try:
                invalidate_callback()
                _last_invalidated_at = time.time()
                _pending_invalidations = 0
            except Exception as exc:
                _last_error = f"{type(exc).__name__}: {exc}"
                if not _queue:
                    break

        if not _queue:
            break
