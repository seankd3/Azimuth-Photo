"""Await something from a worker thread — on the app's loop, never a new one.

`asyncio.run()` inside a thread builds a fresh event loop, runs the coroutine,
and destroys the loop. Whatever the coroutine left open goes with it: an
aiosqlite connection is a worker thread plus an open catalog handle, and once
its loop is gone nothing can ever close it. The gallery ZIP builder did this
once per image.

The database that connection belongs to is then held for the life of the
process — which on Windows means a library file that cannot be moved or
deleted, and in the test suite meant a temporary catalog that would not delete,
failing a different test each run.

There is one event loop in this app. Work that starts on a thread and needs to
await something belongs on that loop, not on a copy of it.
"""

from __future__ import annotations

import asyncio

_loop: asyncio.AbstractEventLoop | None = None


def remember_the_loop(loop: asyncio.AbstractEventLoop | None = None) -> None:
    """Record the app's event loop. Called once, as the app starts."""

    global _loop
    _loop = loop or asyncio.get_running_loop()


def run(coro):
    """Await a coroutine from a worker thread and return its result.

    Raises if called from the event loop itself — there the caller should
    simply await, and blocking on a future would deadlock the loop.
    """

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        coro.close()
        raise RuntimeError("already on the event loop — await the coroutine instead")

    if _loop is None or _loop.is_closed():
        # No app loop: a CLI script or an import-time call. A private loop is
        # correct here, because there is no shared state to strand.
        return asyncio.run(coro)
    return asyncio.run_coroutine_threadsafe(coro, _loop).result()
