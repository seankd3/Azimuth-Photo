"""Thumbnail warm scheduling helpers for media and ranking flows."""

import asyncio

import helpers as app_helpers
import thumbnails
from core import work_coordination
from thumbnails import cache_entries


_thumbnail_prefetch_inflight: set[str] = set()
_thumbnail_memory_warm_inflight: set[str] = set()
_background_tasks: set[asyncio.Task] = set()


def _track_background_task(coro) -> asyncio.Task:
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return task


async def cancel_background_tasks() -> None:
    tasks = list(_background_tasks)
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    _background_tasks.clear()
    _thumbnail_prefetch_inflight.clear()
    _thumbnail_memory_warm_inflight.clear()


def schedule_thumbnail_prefetch(rows, size: str, limit: int):
    if not rows or limit <= 0:
        return
    if size in _thumbnail_prefetch_inflight:
        return
    _thumbnail_prefetch_inflight.add(size)

    async def _run_prefetch():
        try:
            await thumbnails.prefetch_images(rows, size, limit=limit)
        except Exception:
            pass
        finally:
            _thumbnail_prefetch_inflight.discard(size)

    _track_background_task(_run_prefetch())


def schedule_cached_thumbnail_memory_warm(
    rows,
    size: str,
    limit: int,
):
    if size not in ("sm", "md", "lg") or not rows or limit <= 0:
        return
    image_ids = []
    for row in rows[:limit]:
        try:
            image_ids.append(int(row["id"]))
        except (KeyError, TypeError, ValueError):
            continue
    if not image_ids:
        return
    key = f"{size}:{','.join(str(image_id) for image_id in image_ids)}"
    if key in _thumbnail_memory_warm_inflight:
        return
    _thumbnail_memory_warm_inflight.add(key)

    def _warm():
        warmed = 0
        for image_id in image_ids:
            if warmed >= limit:
                break
            if thumbnails._memory_get_entry_fast(size, image_id) is not None:
                continue
            if cache_entries.fast_disk_read_entry(size, image_id, populate_memory=True) is not None:
                warmed += 1

    async def _run_warm():
        try:
            await work_coordination.wait_for_lane(work_coordination.AMBIENT_WARMING)
            await asyncio.to_thread(_warm)
        except Exception:
            pass
        finally:
            _thumbnail_memory_warm_inflight.discard(key)

    _track_background_task(_run_warm())


def schedule_result_thumbnail_memory_warm(rows, *, sm_limit: int = 48, md_limit: int = 12, lg_limit: int = 12):
    if not rows:
        return
    row_count = len(rows)
    schedule_cached_thumbnail_memory_warm(
        rows, "sm", limit=min(row_count, sm_limit)
    )
    schedule_cached_thumbnail_memory_warm(
        rows, "md", limit=min(row_count, md_limit)
    )
    schedule_cached_thumbnail_memory_warm(
        rows, "lg", limit=min(row_count, lg_limit)
    )


async def cached_image_ids(image_ids, size: str) -> set[int]:
    return await app_helpers.cached_image_ids(image_ids, size, thumbnails.SSD_CACHE_DIR)
