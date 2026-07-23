"""Persistent process pool for GIL-bound RAW demosaic.

rawpy/libraw ``postprocess`` holds the Python GIL, so ThreadPoolExecutor
cannot parallelize demosaic — it only adds contention. This pool runs the
demosaic→resize→JPEG path in child processes and returns only small JPEG
bytes (never the decoded frame).

HDD reads stay in the parent (single-flight governor). Workers receive a
path (page-cache warm after the parent read) or in-RAM bytes when configured.
"""

from __future__ import annotations

import atexit
import logging
import multiprocessing as mp
import os
import threading
from concurrent.futures import Future, ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from typing import Any

log = logging.getLogger("thumbnails.demosaic_pool")

# One process reserved so a loupe/grid miss is not starved by bulk backfill.
_INTERACTIVE_RESERVED = 1

_lock = threading.Lock()
_executor: ProcessPoolExecutor | None = None
_worker_count = 0
_bulk_slots: threading.Semaphore | None = None
_shutdown = False


def default_demosaic_processes() -> int:
    """Size the pool from host_profile (RAM ceiling + cores)."""
    try:
        from core.host_profile import detect_host_profile

        return detect_host_profile().demosaic_workers()
    except Exception:
        pass
    ncores = max(1, os.cpu_count() or 4)
    try:
        total_bytes = int(os.sysconf("SC_PHYS_PAGES")) * int(os.sysconf("SC_PAGE_SIZE"))
    except (AttributeError, OSError, TypeError, ValueError):
        total_bytes = 16 * 1024**3
    # ~3GB peak per worker; budget half of RAM.
    memory_workers = max(1, int((total_bytes // 2) // (3 * 1024**3)))
    return max(1, min(ncores - 1 if ncores > 1 else 1, memory_workers, 6))


def configured_demosaic_processes() -> int:
    """``PHOTOARCHIVE_DEMOSAIC_PROCESSES`` — 0 disables the pool (in-process)."""
    raw = os.environ.get("PHOTOARCHIVE_DEMOSAIC_PROCESSES", "").strip()
    if not raw:
        return default_demosaic_processes()
    try:
        return max(0, min(16, int(raw)))
    except ValueError:
        return default_demosaic_processes()


def demosaic_ipc_mode() -> str:
    """``path`` (default) or ``bytes``. Path avoids doubling 25–45MB over IPC."""
    mode = os.environ.get("PHOTOARCHIVE_DEMOSAIC_IPC", "path").strip().lower()
    return mode if mode in ("path", "bytes") else "path"


def is_enabled() -> bool:
    return configured_demosaic_processes() > 0 and not _shutdown


def worker_count() -> int:
    return _worker_count if _executor is not None else configured_demosaic_processes()


def _make_executor(workers: int) -> ProcessPoolExecutor:
    ctx = mp.get_context("spawn")
    return ProcessPoolExecutor(
        max_workers=workers,
        mp_context=ctx,
        max_tasks_per_child=64,
    )


def ensure_pool() -> ProcessPoolExecutor | None:
    """Lazily start the persistent pool. Returns None when disabled."""
    global _executor, _worker_count, _bulk_slots
    if not is_enabled():
        return None
    with _lock:
        if _shutdown:
            return None
        if _executor is not None:
            return _executor
        workers = configured_demosaic_processes()
        if workers <= 0:
            return None
        _executor = _make_executor(workers)
        _worker_count = workers
        # Bulk may use all but one slot; interactive skips the bulk gate.
        bulk_limit = max(1, workers - _INTERACTIVE_RESERVED) if workers > 1 else workers
        _bulk_slots = threading.Semaphore(bulk_limit)
        log.info(
            "demosaic process pool started workers=%s ipc=%s bulk_slots=%s",
            workers,
            demosaic_ipc_mode(),
            bulk_limit,
        )
        return _executor


def _reset_pool_locked() -> ProcessPoolExecutor | None:
    """Replace a broken pool. Caller holds ``_lock``."""
    global _executor, _worker_count, _bulk_slots
    old = _executor
    _executor = None
    if old is not None:
        try:
            old.shutdown(wait=False, cancel_futures=True)
        except TypeError:
            try:
                old.shutdown(wait=False)
            except Exception:
                pass
        except Exception:
            pass
    if _shutdown or configured_demosaic_processes() <= 0:
        _worker_count = 0
        _bulk_slots = None
        return None
    workers = configured_demosaic_processes()
    _executor = _make_executor(workers)
    _worker_count = workers
    bulk_limit = max(1, workers - _INTERACTIVE_RESERVED) if workers > 1 else workers
    _bulk_slots = threading.Semaphore(bulk_limit)
    log.warning("demosaic process pool recreated after worker failure workers=%s", workers)
    return _executor


def shutdown_pool(wait: bool = False) -> None:
    """Stop the pool (tests / process exit)."""
    global _executor, _worker_count, _bulk_slots, _shutdown
    with _lock:
        _shutdown = True
        old = _executor
        _executor = None
        _worker_count = 0
        _bulk_slots = None
    if old is not None:
        try:
            old.shutdown(wait=wait, cancel_futures=not wait)
        except TypeError:
            old.shutdown(wait=wait)
        except Exception:
            pass


def _atexit_shutdown() -> None:
    shutdown_pool(wait=False)


atexit.register(_atexit_shutdown)


def reset_for_tests() -> None:
    """Tear down and allow re-enable (pytest)."""
    global _shutdown
    shutdown_pool(wait=True)
    with _lock:
        _shutdown = False


def run_demosaic_tier_jpegs(
    filepath: str,
    uncovered_sizes: list[str],
    *,
    sizes: dict[str, int],
    thumb_quality: int,
    source_data: bytes | None = None,
    interactive: bool = False,
) -> dict[str, Any]:
    """Run demosaic+encode in the process pool (or raise if disabled).

    On ``BrokenProcessPool`` (bad file killing a worker), recreate the pool
    once and re-raise so the caller can mark the image failed and continue.
    """
    if not uncovered_sizes:
        return {"jpegs": {}, "width": 0, "height": 0}

    from raw_thumb_ops import demosaic_tier_jpegs

    pool = ensure_pool()
    if pool is None:
        return demosaic_tier_jpegs(
            filepath,
            list(uncovered_sizes),
            dict(sizes),
            int(thumb_quality),
            source_data=source_data,
        )

    ipc = demosaic_ipc_mode()
    # Prefer path IPC: parent already read under the HDD slot (page cache warm).
    # Bytes IPC doubles 25–45MB over the pipe — use only when requested.
    payload_bytes = source_data if ipc == "bytes" else None

    bulk_slots = _bulk_slots
    acquired = False
    if not interactive and bulk_slots is not None:
        bulk_slots.acquire()
        acquired = True

    try:
        future = _submit(
            pool,
            demosaic_tier_jpegs,
            filepath,
            list(uncovered_sizes),
            dict(sizes),
            int(thumb_quality),
            payload_bytes,
        )
        try:
            return future.result()
        except BrokenProcessPool:
            with _lock:
                pool = _reset_pool_locked()
            if pool is None:
                raise
            # Do not retry the same (possibly corrupt) file in-process here —
            # caller marks failed. Pool is healthy for the next image.
            raise
    finally:
        if acquired and bulk_slots is not None:
            bulk_slots.release()


def _submit(pool: ProcessPoolExecutor, fn, *args) -> Future:
    try:
        return pool.submit(fn, *args)
    except BrokenProcessPool:
        with _lock:
            replacement = _reset_pool_locked()
        if replacement is None:
            raise
        return replacement.submit(fn, *args)
