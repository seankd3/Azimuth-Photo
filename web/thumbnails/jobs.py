"""Thumbnail request and background scheduling helpers."""

import asyncio
from collections.abc import Callable
from functools import partial
import inspect
import os

from core import work_coordination


# Cap concurrent user-facing cold decodes (heavy TIFF/RAW demosaic). Queues
# excess work on an async semaphore so status/health stay responsive.
_ON_DEMAND_LIMIT = max(
    1,
    int(os.environ.get("AZIMUTH_ON_DEMAND_DECODE_LIMIT", "2")),
)
_on_demand_decode_sem: asyncio.Semaphore | None = None


def _on_demand_semaphore() -> asyncio.Semaphore:
    global _on_demand_decode_sem
    if _on_demand_decode_sem is None:
        _on_demand_decode_sem = asyncio.Semaphore(_ON_DEMAND_LIMIT)
    return _on_demand_decode_sem


def reset_on_demand_semaphore_for_tests(limit: int | None = None) -> None:
    """Test helper — rebuild the semaphore after monkeypatching the limit."""
    global _on_demand_decode_sem, _ON_DEMAND_LIMIT
    if limit is not None:
        _ON_DEMAND_LIMIT = max(1, int(limit))
    _on_demand_decode_sem = asyncio.Semaphore(_ON_DEMAND_LIMIT)


def has_cached(
    size: str,
    filepath: str,
    image_id: int,
    *,
    thumb_tiers: tuple[str, ...],
    build_source_signature: Callable[[str, str, int], str],
    memory_get: Callable[[str, int, str], bytes | None],
    fast_disk_has: Callable[..., bool],
    get_disk_entry: Callable[..., object | None],
) -> bool:
    source_signature = build_source_signature(filepath, size, image_id)
    if size in thumb_tiers and memory_get(size, image_id, source_signature) is not None:
        return True
    if fast_disk_has(size, image_id, source_signature):
        return True
    return get_disk_entry(size, image_id, source_signature, touch=False) is not None


def has_cached_fast(
    size: str,
    image_id: int,
    *,
    memory_get_entry_fast: Callable[[str, int], object | None],
    fast_disk_has: Callable[..., bool],
) -> bool:
    return memory_get_entry_fast(size, image_id) is not None or fast_disk_has(size, image_id)


async def run_thumbnail_job(
    filepath: str,
    size: str,
    image_id: int,
    executor,
    include_smaller_tiers: bool,
    hot: bool,
    allow_stale_fallback: bool,
    *,
    generate_missing_thumbnails_sync: Callable[..., object],
) -> object:
    loop = asyncio.get_running_loop()
    # An accessor may be passed instead of a pool so submits always target the
    # CURRENT executor — the stall watchdog swaps pools, and tasks queued
    # against the old one would die with "cannot schedule after shutdown".
    resolve = executor if callable(executor) and not hasattr(executor, "submit") else None
    job = partial(
        generate_missing_thumbnails_sync,
        filepath,
        size,
        image_id,
        include_smaller_tiers=include_smaller_tiers,
        hot=hot,
        allow_stale_fallback=allow_stale_fallback,
    )
    try:
        return await loop.run_in_executor(resolve() if resolve else executor, job)
    except RuntimeError as exc:
        if resolve is None or "after shutdown" not in str(exc):
            raise
        # Pool was swapped between resolve and submit — retry on the new one.
        return await loop.run_in_executor(resolve(), job)


def _probe_cached_sync(
    size: str,
    image_id: int,
    source_signature: str,
    allow_stale_fallback: bool,
    memory_get: Callable[[str, int, str], bytes | None],
    fast_disk_read_entry: Callable[..., tuple[str, bytes] | None],
    read_disk_thumbnail: Callable[[str, int, str], bytes | None],
) -> bytes | None:
    """Sync cache probe (memory, disk index, disk DB); run via to_thread."""
    cached = memory_get(size, image_id, source_signature)
    if cached is not None:
        return cached
    disk_entry = fast_disk_read_entry(
        size,
        image_id,
        None if allow_stale_fallback else source_signature,
    )
    if disk_entry is not None:
        return disk_entry[1]
    return read_disk_thumbnail(size, image_id, source_signature)


def _probe_before_generate_sync(
    filepath: str,
    size: str,
    image_id: int,
    allow_stale_fallback: bool,
    build_source_signature: Callable[[str, str, int], str],
    memory_get: Callable[[str, int, str], bytes | None],
    fast_disk_read_entry: Callable[..., tuple[str, bytes] | None],
    read_disk_thumbnail: Callable[[str, int, str], bytes | None],
    source_missing: Callable[[str], bool],
) -> tuple[str, bytes | None, bool]:
    """Signature build + cache probe + source stat, bundled off the loop."""
    source_signature = build_source_signature(filepath, size, image_id)
    cached = _probe_cached_sync(
        size,
        image_id,
        source_signature,
        allow_stale_fallback,
        memory_get,
        fast_disk_read_entry,
        read_disk_thumbnail,
    )
    if cached is not None:
        return source_signature, cached, False
    return source_signature, None, source_missing(filepath)


async def ensure_thumbnail_with_executor(
    filepath: str,
    size: str,
    image_id: int,
    executor,
    *,
    note_activity: bool,
    include_smaller_tiers: bool = False,
    allow_stale_fallback: bool = True,
    note_user_activity: Callable[[], object],
    build_source_signature: Callable[[str, str, int], str],
    memory_get: Callable[[str, int, str], bytes | None],
    fast_disk_read_entry: Callable[..., tuple[str, bytes] | None],
    read_disk_thumbnail: Callable[[str, int, str], bytes | None],
    source_missing: Callable[[str], bool],
    inflight: dict,
    run_thumbnail_job: Callable[..., object],
    create_task: Callable[[object], object] = asyncio.create_task,
) -> bytes:
    if note_activity:
        note_user_activity()

    # Signature build, cache probe, and source stat are sync stat/file/SQLite
    # work; keep them off the event loop.
    source_signature, cached, missing = await asyncio.to_thread(
        _probe_before_generate_sync,
        filepath,
        size,
        image_id,
        allow_stale_fallback,
        build_source_signature,
        memory_get,
        fast_disk_read_entry,
        read_disk_thumbnail,
        source_missing,
    )
    if cached is not None:
        return cached
    if missing:
        return b""

    inflight_key = ("thumb", image_id, source_signature)
    task = inflight.get(inflight_key)
    if task is None:

        async def _decode_job():
            # User-facing cold decodes share a bounded semaphore so a burst of
            # cold TIFFs queues instead of saturating the pool.
            if note_activity:
                async with _on_demand_semaphore():
                    return await run_thumbnail_job(
                        filepath,
                        size,
                        image_id,
                        executor,
                        include_smaller_tiers,
                        note_activity,
                        allow_stale_fallback,
                    )
            return await run_thumbnail_job(
                filepath,
                size,
                image_id,
                executor,
                include_smaller_tiers,
                note_activity,
                allow_stale_fallback,
            )

        task = create_task(_decode_job())
        inflight[inflight_key] = task

    try:
        generated = await task
    finally:
        if inflight.get(inflight_key) is task and task.done():
            inflight.pop(inflight_key, None)
    if generated:
        return generated

    cached = await asyncio.to_thread(
        _probe_cached_sync,
        size,
        image_id,
        source_signature,
        allow_stale_fallback,
        memory_get,
        fast_disk_read_entry,
        read_disk_thumbnail,
    )
    return cached or b""


async def get_thumbnail(
    filepath: str,
    size: str,
    image_id: int,
    *,
    executor,
    ensure_thumbnail_with_executor: Callable[..., object],
) -> bytes:
    return await ensure_thumbnail_with_executor(
        filepath,
        size,
        image_id,
        executor,
        note_activity=True,
        include_smaller_tiers=False,
    )


async def prefetch_images(
    images: list[dict],
    size: str,
    limit: int | None = None,
    *,
    hot: bool = False,
    sizes: dict[str, int],
    replace_stale_thumbnails: Callable[[], bool],
    memory_get_entry_fast: Callable[[str, int], object | None],
    memory_get: Callable[[str, int, str], bytes | None],
    fast_disk_has: Callable[..., bool],
    touch_cached_signature: Callable[[str, int, str | None], bool],
    touch_cached: Callable[[str, str, int], bool],
    build_source_signature: Callable[[str, str, int], str],
    has_cached: Callable[[str, str, int], bool],
    ensure_thumbnail_with_executor: Callable[..., object],
    prefetch_executor,
    create_task: Callable[[object], object] = asyncio.create_task,
) -> int:
    if size not in sizes or not images:
        return 0

    async def schedule_ambient_ensure(*args, **kwargs):
        await work_coordination.wait_for_lane(work_coordination.AMBIENT_WARMING)
        result = ensure_thumbnail_with_executor(*args, **kwargs)
        if inspect.isawaitable(result):
            return await result
        return result

    scheduled = 0
    for img in images:
        if limit is not None and scheduled >= limit:
            break
        image_id = img.get("id")
        filepath = img.get("filepath")
        if image_id is None or not filepath:
            continue
        require_current = replace_stale_thumbnails()
        if not require_current and memory_get_entry_fast(size, image_id) is not None:
            continue
        if not require_current and fast_disk_has(size, image_id):
            if hot:
                # Sync SQLite write; keep it off the event loop.
                await asyncio.to_thread(touch_cached_signature, size, image_id, None)
            continue

        if not require_current:
            create_task(
                schedule_ambient_ensure(
                    filepath,
                    size,
                    image_id,
                    prefetch_executor,
                    note_activity=hot,
                    include_smaller_tiers=True,
                    allow_stale_fallback=True,
                )
            )
            scheduled += 1
            continue

        source_signature = build_source_signature(filepath, size, image_id)
        if memory_get(size, image_id, source_signature) is not None:
            continue
        if fast_disk_has(size, image_id, source_signature):
            if hot:
                # Sync SQLite write; keep it off the event loop.
                await asyncio.to_thread(touch_cached_signature, size, image_id, source_signature)
            continue
        if has_cached(size, filepath, image_id):
            if hot:
                touch_cached(size, filepath, image_id)
            continue
        create_task(
            schedule_ambient_ensure(
                filepath,
                size,
                image_id,
                prefetch_executor,
                note_activity=hot,
                include_smaller_tiers=True,
                allow_stale_fallback=False,
            )
        )
        scheduled += 1
    return scheduled
