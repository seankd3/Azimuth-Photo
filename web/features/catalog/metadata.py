"""Catalog metadata and orientation backfill workers."""

import asyncio
import logging
import os
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from date_inference import infer_image_date
import image_headers
from core import work_coordination
from core import hdd_governor
from data.repositories import catalog as catalog_repository
from data.repositories import images as image_repository


log = logging.getLogger(__name__)
ORIENTATION_RETRY_SECONDS = 15 * 60
ORIENTATION_POISON_THRESHOLD = 3

# Keep catalog bulk IO off asyncio's default pool so interactive to_thread
# (thumb cache probes, source inspect on real misses) is not starved.
_CATALOG_WORKERS = max(1, int(os.environ.get("PHOTOARCHIVE_CATALOG_METADATA_WORKERS", "2")))
_catalog_executor: ThreadPoolExecutor | None = None


def _get_catalog_executor() -> ThreadPoolExecutor:
    global _catalog_executor
    if _catalog_executor is None:
        _catalog_executor = ThreadPoolExecutor(
            max_workers=_CATALOG_WORKERS,
            thread_name_prefix="catalog-meta",
        )
    return _catalog_executor


async def _run_catalog_work(func, /, *args, **kwargs):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_get_catalog_executor(), lambda: func(*args, **kwargs))


DbPathProvider = Callable[[], str]
Invalidator = Callable[[], None]

_db_path: DbPathProvider | None = None
_invalidate_filter_options_cache: Invalidator | None = None
_invalidate_rankings_cache: Invalidator | None = None
_metadata_manual_pause = True
_orientation_retry_ledger: dict[int, dict] = {}
_status = {
    "state": "paused",
    "message": "Catalog metadata is paused until you start it from Background Work.",
    "last_error": "",
    "last_batch_size": 0,
    "last_batch_seconds": 0.0,
    "last_run_at": None,
    "orientation_scanned": 0,
    "metadata_scanned": 0,
    "orientation_retry_count": 0,
    "orientation_poisoned_count": 0,
    "next_retry_at": None,
}


def configure(
    *,
    db_path: DbPathProvider,
    invalidate_filter_options_cache: Invalidator,
    invalidate_rankings_cache: Invalidator,
) -> None:
    global _db_path, _invalidate_filter_options_cache, _invalidate_rankings_cache
    _db_path = db_path
    _invalidate_filter_options_cache = invalidate_filter_options_cache
    _invalidate_rankings_cache = invalidate_rankings_cache


def _configured_db_path() -> str:
    if _db_path is None:
        raise RuntimeError("Catalog metadata workers are not configured")
    return _db_path()


def _invalidate_filter_options() -> None:
    if _invalidate_filter_options_cache is None or _invalidate_rankings_cache is None:
        raise RuntimeError("Catalog metadata workers are not configured")
    _invalidate_filter_options_cache()
    _invalidate_rankings_cache()


def pause_catalog_metadata() -> dict:
    global _metadata_manual_pause
    _metadata_manual_pause = True
    _status.update(state="paused", message="Catalog metadata is paused.", last_error="")
    return catalog_metadata_status()


def resume_catalog_metadata() -> dict:
    global _metadata_manual_pause
    _metadata_manual_pause = False
    _orientation_retry_ledger.clear()
    _status.update(state="waiting", message="Catalog metadata will scan the catalog.", last_error="")
    return catalog_metadata_status()


def catalog_metadata_status() -> dict:
    return {
        "active": not _metadata_manual_pause,
        "manual_pause": _metadata_manual_pause,
        "worker": dict(_status),
    }


async def get_unclassified_images(limit: int = 200):
    import photo_metadata  # deferred: keeps Pillow off boot until catalog metadata work runs

    return await image_repository.get_unclassified_images(
        _configured_db_path(),
        limit,
        file_extensions=photo_metadata.PILLOW_METADATA_EXTENSIONS,
    )


async def batch_set_orientations(updates: list[tuple[str, float, int]]):
    await image_repository.batch_set_orientations(_configured_db_path(), updates)
    _invalidate_filter_options()


async def get_images_needing_metadata(limit: int = 100, metadata_version: int = 1):
    return await image_repository.get_images_needing_metadata(
        _configured_db_path(),
        limit=limit,
        metadata_version=metadata_version,
    )


async def batch_update_metadata(updates: list[tuple]):
    if not updates:
        return
    await image_repository.batch_update_metadata(_configured_db_path(), updates)
    _invalidate_filter_options()


def _note_orientation_failure(
    image_id: int,
    error: str,
    *,
    now: float | None = None,
    source_root: str = "",
) -> float:
    now = time.time() if now is None else float(now)
    image_id = int(image_id)
    previous = _orientation_retry_ledger.get(image_id, {})
    attempts = int(previous.get("attempts") or 0) + 1
    poisoned = attempts >= ORIENTATION_POISON_THRESHOLD
    retry_at = float("inf") if poisoned else now + ORIENTATION_RETRY_SECONDS
    _orientation_retry_ledger[image_id] = {
        "attempts": attempts,
        "retry_at": retry_at,
        "error": str(error or ""),
        "poisoned": poisoned,
        "source_root": str(source_root or ""),
    }
    return retry_at


def _clear_restored_source_failures(rows) -> None:
    restored_roots = set()
    for row in rows:
        try:
            source_root = str(row["source_root"] or "")
        except (KeyError, TypeError):
            continue
        if source_root and os.path.isdir(source_root):
            restored_roots.add(source_root)
    if not restored_roots:
        return
    for image_id, record in list(_orientation_retry_ledger.items()):
        if str(record.get("source_root") or "") in restored_roots:
            _orientation_retry_ledger.pop(image_id, None)


def _ready_orientation_rows(rows, *, now: float | None = None):
    now = time.time() if now is None else float(now)
    _clear_restored_source_failures(rows)
    ready = []
    cooled_down = 0
    next_retry_at = None
    for row in rows:
        record = _orientation_retry_ledger.get(int(row["id"]))
        if not record:
            ready.append(row)
            continue
        retry_at = float(record.get("retry_at") or 0.0)
        if record.get("poisoned") or retry_at > now:
            cooled_down += 1
            if not record.get("poisoned") and (
                next_retry_at is None or retry_at < next_retry_at
            ):
                next_retry_at = retry_at
            continue
        ready.append(row)
    return ready, cooled_down, next_retry_at


def _orientation_retry_summary() -> dict[str, int]:
    return {
        "retrying": sum(
            1 for record in _orientation_retry_ledger.values()
            if not record.get("poisoned")
        ),
        "poisoned": sum(
            1 for record in _orientation_retry_ledger.values()
            if record.get("poisoned")
        ),
    }


async def classify_orientations_background():
    """Continuously classify unclassified images by reading just the image header."""

    def _classify_batch(rows):
        results = []
        failures = []
        for row in rows:
            try:
                with hdd_governor.bulk_hdd_slot_sync():
                    dimensions = image_headers.read_header_dimensions(
                        row["filepath"],
                        budget_seconds=None,
                    )
                if dimensions is None:
                    raise ValueError("unsupported or unreadable image header")
                w, h = dimensions
                orient = "landscape" if w >= h else "portrait"
                ar = round(w / h, 4) if h > 0 else 1.5
                results.append((orient, ar, row["id"]))
            except (FileNotFoundError, OSError, ValueError) as exc:
                failures.append(
                    (
                        int(row["id"]),
                        str(row["filepath"]),
                        type(exc).__name__,
                        str(row["source_root"] or ""),
                        os.path.isdir(str(row["source_root"] or "")),
                    )
                )
        return results, failures

    while True:
        try:
            if _metadata_manual_pause:
                _status.update(
                    state="paused",
                    message="Catalog metadata is paused.",
                    last_error="",
                )
                await asyncio.sleep(10)
                continue

            batch_limit = 200
            candidate_limit = min(5000, batch_limit + len(_orientation_retry_ledger))
            candidates = await get_unclassified_images(limit=candidate_limit)
            rows, cooled_down, next_retry_at = _ready_orientation_rows(candidates)
            retry_summary = _orientation_retry_summary()
            _status.update(
                orientation_retry_count=retry_summary["retrying"],
                orientation_poisoned_count=retry_summary["poisoned"],
                next_retry_at=next_retry_at,
            )
            if candidates and not rows:
                wait_for = max(1, int(next_retry_at - time.time())) if next_retry_at else 60
                _status.update(
                    state="waiting_retry",
                    message=(
                        f"Waiting to retry {cooled_down} unreadable images."
                        if next_retry_at
                        else f"Skipping {cooled_down} repeatedly unreadable images."
                    ),
                )
                await asyncio.sleep(min(wait_for, 60))
                continue
            if not rows:
                _status.update(state="idle", message="Orientations are caught up.", last_error="")
                await asyncio.sleep(5)
                continue
            started = time.perf_counter()
            _status.update(state="running", message=f"Classifying {len(rows)} image orientations.")
            with work_coordination.manual_bulk("catalog_metadata"):
                results, failures = await _run_catalog_work(_classify_batch, rows)
            for image_id, filepath, reason, source_root, source_online in failures:
                if not source_online or os.path.exists(filepath):
                    _note_orientation_failure(
                        image_id,
                        reason,
                        source_root=source_root,
                    )
                    continue
                _orientation_retry_ledger.pop(image_id, None)
                changed = await catalog_repository.mark_image_missing(
                    _configured_db_path(),
                    image_id,
                )
                if changed:
                    log.warning(
                        "worker=orientation_classifier image_id=%s skipped unreadable image reason=%s path=%r",
                        image_id,
                        reason,
                        filepath,
                    )
            if results:
                for _orientation, _aspect_ratio, image_id in results:
                    _orientation_retry_ledger.pop(int(image_id), None)
                await batch_set_orientations(results)
            retry_summary = _orientation_retry_summary()
            _status.update(
                state="running",
                message=f"Classified {len(results)} image orientations.",
                last_batch_size=len(results),
                last_batch_seconds=round(time.perf_counter() - started, 3),
                last_run_at=time.time(),
                orientation_scanned=int(_status.get("orientation_scanned") or 0) + len(results),
                orientation_retry_count=retry_summary["retrying"],
                orientation_poisoned_count=retry_summary["poisoned"],
            )
            await asyncio.sleep(0.05)
        except Exception:
            _status.update(
                state="error",
                message="Orientation classifier failed.",
                last_error="See the server log for details.",
            )
            log.exception("worker=orientation_classifier failed")
            await asyncio.sleep(5)


def metadata_update_tuple(image_id: int, metadata: dict):
    import photo_metadata  # deferred: keeps Pillow off boot until catalog metadata work runs

    width = metadata.get("width")
    height = metadata.get("height")
    orientation = None
    aspect_ratio = None
    if width and height:
        try:
            width_num = int(width)
            height_num = int(height)
            if height_num > 0:
                orientation = "landscape" if width_num >= height_num else "portrait"
                aspect_ratio = round(width_num / height_num, 4)
        except (TypeError, ValueError, ZeroDivisionError):
            pass

    date_taken = metadata.get("date_taken") or None
    date_source = metadata.get("date_source") or ("exif" if date_taken else None)
    if not date_taken:
        inferred = infer_image_date(
            filename=metadata.get("filename") or "",
            filepath=metadata.get("filepath") or "",
            file_modified_at=metadata.get("file_modified_at"),
            source_root=metadata.get("source_root"),
        )
        if inferred:
            date_taken = inferred.date_taken
            date_source = inferred.date_source

    return (
        date_source,
        date_taken,
        date_taken,
        date_taken,
        date_source,
        date_taken,
        date_taken,
        date_source,
        metadata.get("camera_make") or None,
        metadata.get("camera_model") or None,
        metadata.get("lens") or None,
        metadata.get("file_ext") or None,
        metadata.get("file_size"),
        metadata.get("file_modified_at"),
        width,
        height,
        time.time(),
        photo_metadata.METADATA_EXTRACTOR_VERSION,
        orientation,
        aspect_ratio,
        metadata.get("latitude"),
        metadata.get("longitude"),
        image_id,
    )


async def scan_metadata_background():
    """Backfill EXIF/file metadata used for library filters and sorts."""
    import photo_metadata  # deferred: keeps Pillow off boot until catalog metadata work runs

    def _extract_batch(rows):
        updates = []
        for row in rows:
            with hdd_governor.bulk_hdd_slot_sync():
                metadata = photo_metadata.extract_image_metadata(row["filepath"])
            metadata["source_root"] = row["source_root"]
            updates.append(metadata_update_tuple(row["id"], metadata))
        return updates

    while True:
        try:
            if _metadata_manual_pause:
                _status.update(
                    state="paused",
                    message="Catalog metadata is paused.",
                    last_error="",
                )
                await asyncio.sleep(10)
                continue

            batch_limit = 100
            rows = await get_images_needing_metadata(
                limit=batch_limit,
                metadata_version=photo_metadata.METADATA_EXTRACTOR_VERSION,
            )
            if not rows:
                _status.update(state="idle", message="Catalog metadata is caught up.", last_error="")
                await asyncio.sleep(10)
                continue
            started = time.perf_counter()
            _status.update(state="running", message=f"Scanning metadata for {len(rows)} images.")
            with work_coordination.manual_bulk("catalog_metadata"):
                updates = await _run_catalog_work(_extract_batch, rows)
            await batch_update_metadata(updates)
            _status.update(
                state="running",
                message=f"Scanned metadata for {len(updates)} images.",
                last_batch_size=len(updates),
                last_batch_seconds=round(time.perf_counter() - started, 3),
                last_run_at=time.time(),
                metadata_scanned=int(_status.get("metadata_scanned") or 0) + len(updates),
            )
            await asyncio.sleep(0.05)
        except Exception:
            _status.update(
                state="error",
                message="Metadata scanner failed.",
                last_error="See the server log for details.",
            )
            log.exception("worker=catalog_metadata failed")
            await asyncio.sleep(10)
