"""Putting the library away tidily before the desktop app closes.

The desktop shell stops its engine by killing the process, which is fine for the
process and not fine for the catalog: the shutdown handler never runs, so the
clean-close marker is never written, and the *next* launch treats an ordinary
quit as a crash. Measured on the owner's laptop: a full integrity read of a
2.1GB catalog, 9.1 seconds, before the app would answer anything — on every
single launch, because a normal window close never looked clean.

So the shell asks first. This does the two things worth doing while the library
is still open — fold the write log back into the catalog, and record that it
closed in a known state — and then the shell can stop the process however it
likes.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()

_LOOPBACK = {"127.0.0.1", "::1", "localhost"}
_db_path_provider: Callable[[], str] | None = None


def configure(*, db_path_provider: Callable[[], str]) -> None:
    global _db_path_provider
    _db_path_provider = db_path_provider


def _is_local(request: Request) -> bool:
    client = getattr(request, "client", None)
    return bool(client) and str(client.host) in _LOOPBACK


@router.post("/api/system/prepare-quit")
async def prepare_quit(request: Request):
    """Fold the write log and mark a clean close. Local callers only."""

    if not _is_local(request):
        return JSONResponse({"error": "Only this computer can close the library"}, status_code=403)
    if _db_path_provider is None:
        return JSONResponse({"error": "System routes are not configured"}, status_code=503)

    db_path = _db_path_provider()
    folded = await asyncio.to_thread(_fold_write_log, db_path)

    from features.system import backups

    await asyncio.to_thread(backups.mark_clean_shutdown, db_path)
    return {"ok": True, "write_log_folded": folded}


def _fold_write_log(db_path: str) -> bool:
    """Leave the catalog without a write log for the next launch to read through."""

    import sqlite3

    try:
        conn = sqlite3.connect(db_path, timeout=15)
    except sqlite3.Error:
        return False
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        return True
    except sqlite3.Error:
        return False
    finally:
        conn.close()
