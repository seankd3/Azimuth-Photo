"""Thumbnail request and background scheduling helpers."""

import asyncio
from collections.abc import Callable


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
                touch_cached_signature(size, image_id, None)
            continue

        if not require_current:
            create_task(
                ensure_thumbnail_with_executor(
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
                touch_cached_signature(size, image_id, source_signature)
            continue
        if has_cached(size, filepath, image_id):
            if hot:
                touch_cached(size, filepath, image_id)
            continue
        create_task(
            ensure_thumbnail_with_executor(
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
