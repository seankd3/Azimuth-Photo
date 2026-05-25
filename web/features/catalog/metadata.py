"""Catalog metadata and orientation backfill workers."""

import asyncio
import time
from collections.abc import Callable

import photo_metadata
from core import work_coordination
from data.repositories import images as image_repository


DbPathProvider = Callable[[], str]
Invalidator = Callable[[], None]

_db_path: DbPathProvider | None = None
_invalidate_filter_options_cache: Invalidator | None = None
_metadata_manual_pause = True
_status = {
    "state": "paused",
    "message": "Catalog metadata is paused until you start it from Background Work.",
    "last_error": "",
    "last_batch_size": 0,
    "last_batch_seconds": 0.0,
    "last_run_at": None,
    "orientation_scanned": 0,
    "metadata_scanned": 0,
}


def configure(
    *,
    db_path: DbPathProvider,
    invalidate_filter_options_cache: Invalidator,
) -> None:
    global _db_path, _invalidate_filter_options_cache
    _db_path = db_path
    _invalidate_filter_options_cache = invalidate_filter_options_cache


def _configured_db_path() -> str:
    if _db_path is None:
        raise RuntimeError("Catalog metadata workers are not configured")
    return _db_path()


def _invalidate_filter_options() -> None:
    if _invalidate_filter_options_cache is None:
        raise RuntimeError("Catalog metadata workers are not configured")
    _invalidate_filter_options_cache()


def pause_catalog_metadata() -> dict:
    global _metadata_manual_pause
    _metadata_manual_pause = True
    _status.update(state="paused", message="Catalog metadata is paused.", last_error="")
    return catalog_metadata_status()


def resume_catalog_metadata() -> dict:
    global _metadata_manual_pause
    _metadata_manual_pause = False
    _status.update(state="waiting", message="Catalog metadata will scan the catalog.", last_error="")
    return catalog_metadata_status()


def catalog_metadata_status() -> dict:
    return {
        "active": not _metadata_manual_pause,
        "manual_pause": _metadata_manual_pause,
        "worker": dict(_status),
    }


async def get_unclassified_images(limit: int = 200):
    return await image_repository.get_unclassified_images(_configured_db_path(), limit)


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


async def classify_orientations_background():
    """Continuously classify unclassified images by reading just the image header."""
    from PIL import Image as PILImage
    loop = asyncio.get_event_loop()

    def _classify_batch(rows):
        results = []
        for row in rows:
            try:
                img = PILImage.open(row["filepath"])
                w, h = img.size
                img.close()
                orient = "landscape" if w >= h else "portrait"
                ar = round(w / h, 4) if h > 0 else 1.5
                results.append((orient, ar, row["id"]))
            except Exception:
                results.append(("landscape", 1.5, row["id"]))
        return results

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
            rows = await get_unclassified_images(limit=batch_limit)
            if not rows:
                _status.update(state="idle", message="Orientations are caught up.", last_error="")
                await asyncio.sleep(5)
                continue
            started = time.perf_counter()
            _status.update(state="running", message=f"Classifying {len(rows)} image orientations.")
            with work_coordination.manual_bulk("catalog_metadata"):
                results = await loop.run_in_executor(None, _classify_batch, rows)
            if results:
                await batch_set_orientations(results)
            _status.update(
                state="running",
                message=f"Classified {len(results)} image orientations.",
                last_batch_size=len(results),
                last_batch_seconds=round(time.perf_counter() - started, 3),
                last_run_at=time.time(),
                orientation_scanned=int(_status.get("orientation_scanned") or 0) + len(results),
            )
            await asyncio.sleep(0.05)
        except Exception as e:
            _status.update(state="error", message="Orientation classifier failed.", last_error=str(e))
            print(f"Orientation classifier error: {e}")
            await asyncio.sleep(5)


def metadata_update_tuple(image_id: int, metadata: dict):
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
        except Exception:
            pass

    return (
        metadata.get("date_taken") or None,
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
    loop = asyncio.get_event_loop()

    def _extract_batch(rows):
        updates = []
        for row in rows:
            metadata = photo_metadata.extract_image_metadata(row["filepath"])
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
                updates = await loop.run_in_executor(None, _extract_batch, rows)
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
        except Exception as e:
            _status.update(state="error", message="Metadata scanner failed.", last_error=str(e))
            print(f"Metadata scanner error: {e}")
            await asyncio.sleep(10)
