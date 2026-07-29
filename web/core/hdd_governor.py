"""App-level gate for bulk reads against the expansion spindle.

Interactive thumb/grid/full serving never acquires this gate — those paths are
already bounded by snappy's on-demand decode semaphore. SSD thumb-cache reads
also stay outside. Only batch workers (pregen, hash backfill, catalog metadata,
scan-time harvest) serialize through here so the app never self-inflicts a
seek storm on the one slow spindle.
"""

from __future__ import annotations

import asyncio
import os
import threading
from contextlib import asynccontextmanager, contextmanager

_CONCURRENCY = max(1, int(os.environ.get("AZIMUTH_BULK_HDD_CONCURRENCY", "1")))
_thread_sem = threading.Semaphore(_CONCURRENCY)
_holds = 0
_holds_lock = threading.Lock()


def bulk_hdd_concurrency() -> int:
    return _CONCURRENCY


def bulk_hdd_holds() -> int:
    with _holds_lock:
        return _holds


def reset_for_tests(limit: int | None = None) -> None:
    """Rebuild the gate after monkeypatching concurrency in tests."""
    global _CONCURRENCY, _thread_sem, _holds
    if limit is not None:
        _CONCURRENCY = max(1, int(limit))
    _thread_sem = threading.Semaphore(_CONCURRENCY)
    with _holds_lock:
        _holds = 0


def _acquire() -> None:
    global _holds
    _thread_sem.acquire()
    with _holds_lock:
        _holds += 1


def _release() -> None:
    global _holds
    with _holds_lock:
        _holds = max(0, _holds - 1)
    _thread_sem.release()


@contextmanager
def bulk_hdd_slot_sync():
    """Serialize one bulk HDD original read on a worker thread."""
    _acquire()
    try:
        yield
    finally:
        _release()


@asynccontextmanager
async def bulk_hdd_slot():
    """Async wrapper around the same thread gate (single spindle flight).

    Cancellation-safe: the acquiring thread cannot be cancelled, so a task
    cancelled while waiting (the stall watchdog does exactly this) must hand
    the eventually-acquired slot straight back — otherwise one cancelled
    waiter starves every later batch permanently.
    """
    future = asyncio.get_running_loop().run_in_executor(None, _acquire)
    try:
        await asyncio.shield(future)
    except BaseException:
        future.add_done_callback(
            lambda f: _release() if not f.cancelled() and f.exception() is None else None
        )
        raise
    try:
        yield
    finally:
        _release()
