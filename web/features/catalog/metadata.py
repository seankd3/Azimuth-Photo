"""Catalog metadata and orientation backfill workers."""

import asyncio
import time
from collections.abc import Callable

import photo_metadata
import resource_governor
import thumbnails
from data.repositories import images as image_repository


DbPathProvider = Callable[[], str]
Invalidator = Callable[[], None]

_db_path: DbPathProvider | None = None
_invalidate_filter_options_cache: Invalidator | None = None


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
            decision = resource_governor.get_background_decision(thumbnails.get_idle_seconds())
            if decision.pause:
                await asyncio.sleep(decision.sleep_seconds)
                continue

            batch_limit = max(10, min(200, int(200 * max(decision.intensity, 0.1))))
            rows = await get_unclassified_images(limit=batch_limit)
            if not rows:
                await asyncio.sleep(5)
                continue
            results = await loop.run_in_executor(None, _classify_batch, rows)
            if results:
                await batch_set_orientations(results)
            await asyncio.sleep(max(0.05, decision.embedding_pause_seconds))
        except Exception as e:
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
            decision = resource_governor.get_background_decision(thumbnails.get_idle_seconds())
            if decision.pause:
                await asyncio.sleep(decision.sleep_seconds)
                continue

            batch_limit = max(10, min(100, int(100 * max(decision.intensity, 0.1))))
            rows = await get_images_needing_metadata(
                limit=batch_limit,
                metadata_version=photo_metadata.METADATA_EXTRACTOR_VERSION,
            )
            if not rows:
                await asyncio.sleep(10)
                continue
            updates = await loop.run_in_executor(None, _extract_batch, rows)
            await batch_update_metadata(updates)
            await asyncio.sleep(max(0.05, decision.embedding_pause_seconds))
        except Exception as e:
            print(f"Metadata scanner error: {e}")
            await asyncio.sleep(10)
