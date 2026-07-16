"""Async background thumbnail/full-original pregeneration batch runners."""

import asyncio
from functools import partial

from core import work_coordination


PREGEN_YIELDED = -2


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
    should_pause_for_priority,
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
    activity_burst_items: int,
) -> int:
    generate_batch = generate_batch or default_generate_batch
    if should_pause_for_priority():
        generate_batch = min(generate_batch, max(1, int(activity_burst_items)))
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
        candidate_scan_batch = (
            min(scan_batch, max(1, int(activity_burst_items)))
            if should_pause_for_priority()
            else scan_batch
        )
        rows = await pregen_bulk_candidate_batch(candidate_scan_batch)
        if not rows:
            reset_pregen_bulk_cursor()
            reached_end = True
            if pending:
                break
            rows = await pregen_bulk_candidate_batch(candidate_scan_batch)
            if not rows:
                return 0

        for row in rows:
            if not is_prefetching() or is_manual_paused():
                break
            if (
                should_pause_for_priority()
                and len(pending) >= max(1, int(activity_burst_items))
            ):
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
        if len(rows) < candidate_scan_batch:
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
            if idx % 8 == 0 and not should_pause_for_priority():
                await asyncio.to_thread(flush_write_queue)
        if (
            should_pause_for_priority()
            and idx >= max(1, int(activity_burst_items))
        ):
            break
    if not should_pause_for_priority():
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
    should_pause_for_priority,
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
    activity_burst_items: int,
) -> int:
    generate_batch = generate_batch or default_generate_batch
    if should_pause_for_priority():
        generate_batch = min(generate_batch, max(1, int(activity_burst_items)))
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
        candidate_scan_batch = (
            min(scan_batch, max(1, int(activity_burst_items)))
            if should_pause_for_priority()
            else scan_batch
        )
        rows = await pregen_full_candidate_batch(candidate_scan_batch)
        if not rows:
            reset_pregen_full_cursor()
            reached_end = True
            if pending:
                break
            rows = await pregen_full_candidate_batch(candidate_scan_batch)
            if not rows:
                return 0

        for row in rows:
            if not is_prefetching() or is_manual_paused():
                break
            if (
                should_pause_for_priority()
                and len(pending) >= max(1, int(activity_burst_items))
            ):
                break
            item = full_candidate_signature(row, full_room, full_budget)
            if item is None:
                continue
            pending.append(item)
            if len(pending) >= generate_batch or full_room["bytes"] <= 0:
                break

        scanned_batches += 1
        if len(rows) < candidate_scan_batch:
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
    processed = 0
    yielded_for_activity = False
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
            processed += 1
            if result != item["filepath"] and fast_disk_has(full_tier, item["id"], item["signature"]):
                originals_written += 1
                item_bytes = int(item.get("source_size") or 0)
                pregen_state["last_generated_at"] = current_time()
                pregen_state["generated_this_session"] += 1
                record_pregen_batch(1, thumbnails_written=0, source_bytes=item_bytes)
        if (
            should_pause_for_priority()
            and processed >= max(1, int(activity_burst_items))
        ):
            yielded_for_activity = True
            break
    if yielded_for_activity and originals_written <= 0:
        return PREGEN_YIELDED
    return originals_written


async def run_prefetch_worker_loop(
    *,
    is_prefetching,
    is_manual_paused,
    is_manual_mode,
    pregen_on_idle,
    cache_target_total,
    current_monotonic,
    set_pregen_state,
    sleep,
    flush_write_queue,
    flush_orientation_updates,
    should_pause_for_priority,
    background_decision,
    generate_batch_for_decision,
    pregen_status: dict,
    disk_allocations: dict[str, int],
    full_tier: str,
    background_tier_budget,
    run_pregen_bulk_batch,
    run_full_warm_batch,
    get_pregen_status,
    no_progress_scan_limit,
    batch_pause_seconds,
    preview_phase_order: tuple[str, ...] = ("sm", "md", "lg"),
) -> None:
    target_total_cache = 0
    target_total_at = 0.0
    no_progress_scan_passes = 0

    while is_prefetching():
        try:
            await asyncio.to_thread(flush_write_queue)
            await flush_orientation_updates()

            if is_manual_paused():
                set_pregen_state("paused", "Previews is stopped.")
                no_progress_scan_passes = 0
                await sleep(1)
                continue

            if not is_manual_mode():
                set_pregen_state("paused", "Previews is stopped until you start it from Background Work.")
                no_progress_scan_passes = 0
                await sleep(2)
                continue

            now = current_monotonic()
            if now - target_total_at > 30:
                target_total_cache = await cache_target_total()
                target_total_at = now
            target_total = target_total_cache
            if target_total <= 0:
                set_pregen_state("idle", "No images available to warm.")
                no_progress_scan_passes = 0
                await sleep(5)
                continue

            decision = background_decision()
            generate_batch = generate_batch_for_decision(decision)
            if decision.pause:
                set_pregen_state(
                    "waiting",
                    f"Background work paused: {decision.reason}.",
                )
                no_progress_scan_passes = 0
                await sleep(decision.sleep_seconds)
                continue

            phases = [size for size in preview_phase_order if background_tier_budget(size) > 0]
            full_budget = int(disk_allocations.get(full_tier, 0) or 0)
            if not phases and full_budget <= 0:
                set_pregen_state("idle", "No SSD cache budget is available.")
                no_progress_scan_passes = 0
                await sleep(5)
                continue

            generated = 0
            if phases:
                set_pregen_state(
                    "running",
                    f"Bulk warming preview cache ({decision.mode}: {decision.reason})...",
                    phase="previews",
                )
                with work_coordination.manual_bulk("cache"):
                    generated = await run_pregen_bulk_batch(generate_batch=generate_batch)

                await flush_orientation_updates()

            if generated == PREGEN_YIELDED:
                set_pregen_state(
                    "waiting",
                    "Cache warming yielded to active browsing and will continue shortly.",
                )
                no_progress_scan_passes = 0
                await sleep(max(0.25, batch_pause_seconds()))
                continue

            if generated <= 0 and full_budget > 0:
                set_pregen_state(
                    "running",
                    f"Warming original SSD cache ({decision.mode}: {decision.reason})...",
                    phase=full_tier,
                )
                with work_coordination.manual_bulk("cache"):
                    full_generated = await run_full_warm_batch(
                        generate_batch=max(1, min(8, generate_batch)),
                    )
                generated = full_generated if full_generated != 0 else generated

            if generated == PREGEN_YIELDED:
                set_pregen_state(
                    "waiting",
                    "Cache warming yielded to active browsing and will continue shortly.",
                )
                no_progress_scan_passes = 0
                await sleep(max(0.25, batch_pause_seconds()))
                continue

            if generated == 0:
                no_progress_scan_passes = 0
                status = get_pregen_status(target_total)
                phase_parts = []
                for phase in phases:
                    phase_parts.append(
                        f"{phase}: {status['phases'][phase]['count']}/{target_total}"
                    )
                if full_budget > 0:
                    originals = status["originals"]
                    phase_parts.append(
                        f"full: {originals['count']} cached, {originals['utilization_pct']:.1f}% of budget"
                    )
                set_pregen_state(
                    "complete",
                    "Cache is warm for current budget. " + " · ".join(phase_parts),
                )
                await sleep(5)
            elif generated < 0:
                no_progress_scan_passes += 1
                if no_progress_scan_passes >= no_progress_scan_limit():
                    status = get_pregen_status(target_total)
                    phase_parts = []
                    for phase in phases:
                        phase_status = status["phases"][phase]
                        phase_parts.append(
                            f"{phase}: {phase_status['count']}/{phase_status['total']}"
                        )
                    if full_budget > 0:
                        originals = status["originals"]
                        phase_parts.append(
                            f"full: {originals['count']} cached, {originals['utilization_pct']:.1f}% of budget"
                        )
                    set_pregen_state(
                        "complete",
                        "Cache scan found no more warmable images. " + " · ".join(phase_parts),
                    )
                    no_progress_scan_passes = 0
                    await sleep(5)
                else:
                    set_pregen_state(
                        "running",
                        f"Scanning for remaining cache work ({no_progress_scan_passes}/{no_progress_scan_limit()}).",
                        phase="previews",
                    )
                    await sleep(max(0.25, batch_pause_seconds(), decision.thumbnail_pause_seconds))
            else:
                no_progress_scan_passes = 0
                await sleep(max(batch_pause_seconds(), decision.thumbnail_pause_seconds))

        except Exception as e:
            set_pregen_state("error", "Pre-generation worker hit an error.", error=str(e))
            print(f"Prefetch worker error: {e}")
            await sleep(5)
