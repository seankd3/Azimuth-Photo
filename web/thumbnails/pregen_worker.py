"""Async background thumbnail/full-original pregeneration batch runners."""

import asyncio
from functools import partial


async def run_pregen_bulk_batch(
    generate_batch: int | None = None,
    *,
    default_generate_batch: int,
    scan_batch: int,
    thumb_tiers: tuple[str, ...],
    full_tier: str,
    disk_allocations: dict[str, int],
    is_prefetching,
    is_manual_paused,
    should_yield_to_foreground,
    flush_write_queue,
    cache_metadata_backoff_active,
    bulk_tier_budgets,
    bulk_tier_room,
    full_tier_room,
    pregen_bulk_candidate_batch,
    reset_pregen_bulk_cursor,
    bulk_candidate_signatures,
    full_candidate_signature,
    prefetch_executor,
    generate_thumbnail_set_sync,
    record_pregen_result,
) -> int:
    generate_batch = generate_batch or default_generate_batch
    tier_budgets = bulk_tier_budgets()
    if all(tier_budgets.get(size, 0) <= 0 for size in thumb_tiers):
        return 0

    if not await asyncio.to_thread(flush_write_queue) and cache_metadata_backoff_active():
        return 0
    tier_room = bulk_tier_room(tier_budgets)
    if all(room <= 0 for room in tier_room.values()) and cache_metadata_backoff_active():
        return 0
    full_budget = int(disk_allocations.get(full_tier, 0) or 0)
    full_room = {"bytes": full_tier_room(full_budget)} if full_budget > 0 else {"bytes": 0}
    pending = []
    scanned_batches = 0
    max_scan_batches = 4
    reached_end = False

    while len(pending) < generate_batch and scanned_batches < max_scan_batches:
        if not is_prefetching() or is_manual_paused():
            break
        if should_yield_to_foreground():
            break

        rows = await pregen_bulk_candidate_batch(scan_batch)
        if not rows:
            reset_pregen_bulk_cursor()
            reached_end = True
            if pending:
                break
            rows = await pregen_bulk_candidate_batch(scan_batch)
            if not rows:
                return 0

        for row in rows:
            if not is_prefetching() or is_manual_paused():
                break
            if should_yield_to_foreground():
                break
            size_signatures, source_size = bulk_candidate_signatures(row, tier_room, tier_budgets)
            full_item = (
                full_candidate_signature(row, full_room, full_budget)
                if full_room["bytes"] > 0
                else None
            )
            if not size_signatures and full_item is None:
                continue
            pending.append({
                "id": int(row["id"]),
                "filepath": row["filepath"],
                "signatures": size_signatures,
                "full": full_item,
                "source_size": source_size,
            })
            if len(pending) >= generate_batch:
                break

        scanned_batches += 1
        if len(rows) < scan_batch:
            reset_pregen_bulk_cursor()
            reached_end = True
            break
        if reached_end:
            break

    if not pending:
        if scanned_batches >= max_scan_batches and not reached_end:
            return -1
        return 0

    loop = asyncio.get_running_loop()
    completed = 0
    idx = 0
    wave_size = 1
    for start in range(0, len(pending), wave_size):
        wave = pending[start:start + wave_size]
        tasks = [
            loop.run_in_executor(
                prefetch_executor,
                partial(
                    generate_thumbnail_set_sync,
                    item["filepath"],
                    item["id"],
                    item["signatures"],
                    source_bytes=item["source_size"],
                    full_item=item.get("full"),
                    hot=False,
                ),
            )
            for item in wave
        ]
        for task in asyncio.as_completed(tasks):
            idx += 1
            completed += record_pregen_result(await task)
            if idx % 8 == 0 and not should_yield_to_foreground():
                await asyncio.to_thread(flush_write_queue)
        if should_yield_to_foreground():
            break
    if not should_yield_to_foreground():
        await asyncio.to_thread(flush_write_queue)
    if completed <= 0:
        return -1
    return completed


async def run_full_warm_batch(
    generate_batch: int | None = None,
    *,
    default_generate_batch: int,
    scan_batch: int,
    cache_root: str,
    full_tier: str,
    disk_allocations: dict[str, int],
    is_prefetching,
    is_manual_paused,
    should_yield_to_foreground,
    flush_write_queue,
    cache_metadata_backoff_active,
    full_tier_room,
    pregen_full_candidate_batch,
    reset_pregen_full_cursor,
    full_candidate_signature,
    prefetch_executor,
    cache_full_image_sync,
    fast_disk_has,
    pregen_state: dict,
    current_time,
    record_pregen_batch,
) -> int:
    generate_batch = generate_batch or default_generate_batch
    full_budget = int(disk_allocations.get(full_tier, 0) or 0)
    if full_budget <= 0 or not cache_root:
        return 0

    if not await asyncio.to_thread(flush_write_queue) and cache_metadata_backoff_active():
        return 0
    full_room = {"bytes": full_tier_room(full_budget)}
    if full_room["bytes"] <= 0:
        return 0

    pending = []
    scanned_batches = 0
    max_scan_batches = 4
    reached_end = False

    while len(pending) < generate_batch and scanned_batches < max_scan_batches:
        if not is_prefetching() or is_manual_paused():
            break
        if should_yield_to_foreground():
            break

        rows = await pregen_full_candidate_batch(scan_batch)
        if not rows:
            reset_pregen_full_cursor()
            reached_end = True
            if pending:
                break
            rows = await pregen_full_candidate_batch(scan_batch)
            if not rows:
                return 0

        for row in rows:
            if not is_prefetching() or is_manual_paused():
                break
            if should_yield_to_foreground():
                break
            item = full_candidate_signature(row, full_room, full_budget)
            if item is None:
                continue
            pending.append(item)
            if len(pending) >= generate_batch or full_room["bytes"] <= 0:
                break

        scanned_batches += 1
        if len(rows) < scan_batch:
            reset_pregen_full_cursor()
            reached_end = True
            break
        if reached_end or full_room["bytes"] <= 0:
            break

    if not pending:
        if scanned_batches >= max_scan_batches and not reached_end:
            return -1
        return 0

    loop = asyncio.get_running_loop()
    originals_written = 0
    wave_size = 1

    async def cache_full_item(item: dict) -> tuple[dict, str]:
        result = await loop.run_in_executor(
            prefetch_executor,
            cache_full_image_sync,
            item["filepath"],
            item["id"],
            item["signature"],
            False,
        )
        return item, result

    for start in range(0, len(pending), wave_size):
        wave = pending[start:start + wave_size]
        tasks = [asyncio.create_task(cache_full_item(item)) for item in wave]
        for task in asyncio.as_completed(tasks):
            item, result = await task
            if result != item["filepath"] and fast_disk_has(full_tier, item["id"], item["signature"]):
                originals_written += 1
                item_bytes = int(item.get("source_size") or 0)
                pregen_state["last_generated_at"] = current_time()
                pregen_state["generated_this_session"] += 1
                record_pregen_batch(1, thumbnails_written=0, source_bytes=item_bytes)
        if should_yield_to_foreground():
            break
    return originals_written
