"""Thumbnail warm scheduling helpers for media and ranking flows."""

import asyncio

import helpers as app_helpers
import resource_governor
import thumbnails


_thumbnail_prefetch_inflight: set[str] = set()
_thumbnail_memory_warm_inflight: set[str] = set()


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

    asyncio.create_task(_run_prefetch())


def schedule_cached_thumbnail_memory_warm(
    rows,
    size: str,
    limit: int,
    *,
    active_min_warm: int | None = None,
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
        min_before_yield = limit if active_min_warm is None else max(1, min(int(active_min_warm or 1), limit))
        for image_id in image_ids:
            if warmed >= limit:
                break
            decision = resource_governor.get_background_decision(thumbnails.get_idle_seconds())
            if decision.pause and warmed >= min_before_yield:
                break
            mode_limit = limit if decision.work_mode == "max" else max(
                min_before_yield,
                min(limit, int(decision.thumbnail_batch_size or 1)),
            )
            if warmed >= mode_limit:
                break
            if thumbnails._memory_get_entry_fast(size, image_id) is not None:
                continue
            if thumbnails.fast_disk_read_entry(size, image_id, populate_memory=True) is not None:
                warmed += 1

    async def _run_warm():
        try:
            await asyncio.to_thread(_warm)
        except Exception:
            pass
        finally:
            _thumbnail_memory_warm_inflight.discard(key)

    asyncio.create_task(_run_warm())


def schedule_result_thumbnail_memory_warm(rows, *, sm_limit: int = 48, md_limit: int = 12, lg_limit: int = 12):
    if not rows:
        return
    row_count = len(rows)
    schedule_cached_thumbnail_memory_warm(
        rows, "sm", limit=min(row_count, sm_limit), active_min_warm=12
    )
    schedule_cached_thumbnail_memory_warm(
        rows, "md", limit=min(row_count, md_limit), active_min_warm=6
    )
    schedule_cached_thumbnail_memory_warm(
        rows, "lg", limit=min(row_count, lg_limit), active_min_warm=6
    )


async def cached_image_ids(image_ids, size: str) -> set[int]:
    return await app_helpers.cached_image_ids(image_ids, size, thumbnails.SSD_CACHE_DIR)
