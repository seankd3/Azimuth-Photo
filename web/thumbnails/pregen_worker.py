"""Async background thumbnail/full-original pregeneration batch runners."""

import asyncio
import contextlib
import logging
import os
import time
from functools import partial

from core import memory_pressure, work_coordination
from thumbnails.config import EMBEDDED_PREVIEW_EXTENSIONS, RAW_EXTENSIONS
from thumbnails.decode_budget import bulk_decode_budget, estimate_decode_bytes

try:
    from photo_metadata import METADATA_EXTRACTOR_VERSION
except Exception:  # pragma: no cover
    METADATA_EXTRACTOR_VERSION = 3


log = logging.getLogger("thumbnails.pregen")

PREGEN_YIELDED = -2
PREGEN_PRESSURE = -3

# Capture the real sleep at import time. Tests often replace `asyncio.sleep`
# (via `thumbnails.asyncio.sleep = …`); the stall watchdog must not share that
# hook or it busy-loops at 100% CPU and starves the worker.
_REAL_ASYNCIO_SLEEP = asyncio.sleep

# state=running with no generation for this long → ERROR + reset iteration.
PREGEN_STALL_WATCHDOG_SECONDS = float(
    os.environ.get("PHOTOARCHIVE_PREGEN_STALL_SECONDS", str(5 * 60))
)
PREGEN_STALL_WATCHDOG_POLL_SECONDS = 15.0
# Per-wave ceiling so one stuck demosaic cannot pin the bulk loop forever.
PREGEN_WAVE_TIMEOUT_SECONDS = float(
    os.environ.get("PHOTOARCHIVE_PREGEN_WAVE_TIMEOUT_SECONDS", "120")
)


async def _flush_off_request_pool(flush_write_queue, prefetch_executor=None):
    """Flush on the prefetch pool — never steal default-executor slots from HTTP."""
    if prefetch_executor is None:
        return await asyncio.to_thread(flush_write_queue)
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(prefetch_executor, flush_write_queue)


def _row_dimension(row, key: str) -> int | None:
    try:
        value = row[key]
    except (KeyError, IndexError, TypeError):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


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
    pregen_priority_candidate_batch,
    pregen_bulk_candidate_batch,
    reset_pregen_bulk_cursor,
    set_priority_scope,
    bulk_candidate_signatures,
    full_candidate_signature,
    prefetch_executor,
    generate_thumbnail_set_sync,
    record_pregen_result,
    activity_burst_items: int,
    prefetch_workers: int = 1,
) -> int:
    set_priority_scope(None)
    generate_batch = generate_batch or default_generate_batch
    if should_pause_for_priority():
        generate_batch = min(generate_batch, max(1, int(activity_burst_items)))
    tier_budgets = bulk_tier_budgets()
    if all(tier_budgets.get(size, 0) <= 0 for size in thumb_tiers):
        return 0

    if not await _flush_off_request_pool(flush_write_queue, prefetch_executor) and cache_metadata_backoff_active():
        return 0
    tier_room = bulk_tier_room(tier_budgets)
    if all(room <= 0 for room in tier_room.values()) and cache_metadata_backoff_active():
        return 0
    full_budget = int(disk_allocations.get(full_tier, 0) or 0)
    full_room = {"bytes": full_tier_room(full_budget)} if full_budget > 0 else {"bytes": 0}
    pending = []
    max_scan_batches = 4

    def collect_candidates(rows) -> None:
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
            try:
                need_hash = not row["content_hash"]
            except (KeyError, IndexError, TypeError):
                need_hash = False
            try:
                meta_version = row["metadata_version"]
                need_metadata = (
                    row["metadata_scanned_at"] is None
                    or meta_version is None
                    or int(meta_version) < int(METADATA_EXTRACTOR_VERSION)
                )
            except (KeyError, IndexError, TypeError, ValueError):
                need_metadata = False
            pending.append({
                "id": int(row["id"]),
                "filepath": row["filepath"],
                "signatures": size_signatures,
                "full": full_item,
                "source_size": source_size,
                "width": _row_dimension(row, "width"),
                "height": _row_dimension(row, "height"),
                "need_hash": need_hash,
                "need_metadata": need_metadata,
            })
            if len(pending) >= generate_batch:
                break

    loop = asyncio.get_running_loop()
    priority_processed_ids: set[int] = set()
    priority_scanned_batches = 0
    while len(pending) < generate_batch and priority_scanned_batches < max_scan_batches:
        if not is_prefetching() or is_manual_paused():
            break
        candidate_scan_batch = (
            min(scan_batch, max(1, int(activity_burst_items)))
            if should_pause_for_priority()
            else scan_batch
        )
        rows, priority_label = await pregen_priority_candidate_batch(
            candidate_scan_batch,
            priority_processed_ids,
        )
        if not rows:
            break
        set_priority_scope(priority_label)
        priority_processed_ids.update(int(row["id"]) for row in rows)
        await loop.run_in_executor(prefetch_executor, collect_candidates, rows)
        priority_scanned_batches += 1

    scanned_batches = 0
    reached_end = False
    use_normal_candidates = not pending
    if use_normal_candidates:
        set_priority_scope(None)
    while (
        use_normal_candidates
        and len(pending) < generate_batch
        and scanned_batches < max_scan_batches
    ):
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

        await loop.run_in_executor(prefetch_executor, collect_candidates, rows)

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

    completed = 0
    idx = 0
    cursor = 0
    pressure_abort = False
    wave_timed_out = False
    while cursor < len(pending) and not pressure_abort and not wave_timed_out:
        # Consult RSS per wave — a single demosaic can balloon multi-GB before
        # the next between-batch gate would fire.
        if memory_pressure.evaluate_memory_pressure().pause_bulk:
            memory_pressure.gate_bulk_work()
            pressure_abort = True
            break

        wave_size = memory_pressure.effective_prefetch_workers(prefetch_workers)
        wave: list[dict] = []
        held_weights: list[int] = []
        try:
            while len(wave) < wave_size and cursor < len(pending):
                if memory_pressure.evaluate_memory_pressure().pause_bulk:
                    memory_pressure.gate_bulk_work()
                    pressure_abort = True
                    break
                item = pending[cursor]
                cursor += 1
                ext = os.path.splitext(str(item.get("filepath") or ""))[1].lower()
                is_raw = ext in RAW_EXTENSIONS
                # Charge the embedded-JPEG path only for formats that reliably
                # cover lg from the embed. .dng uses the embed for sm when it
                # fits, but lg still demosaics — keep charging demosaic weight.
                estimate = estimate_decode_bytes(
                    item.get("source_size"),
                    raw=is_raw,
                    embedded_preview=ext in EMBEDDED_PREVIEW_EXTENSIONS,
                    width=item.get("width"),
                    height=item.get("height"),
                )
                held_weights.append(await bulk_decode_budget.acquire(estimate))
                wave.append(item)

            if pressure_abort or not wave:
                break

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
                        need_hash=bool(item.get("need_hash")),
                        need_metadata=bool(item.get("need_metadata")),
                    ),
                )
                for item in wave
            ]
            pending_tasks = set(tasks)
            while pending_tasks:
                done, pending_tasks = await asyncio.wait(
                    pending_tasks,
                    timeout=PREGEN_WAVE_TIMEOUT_SECONDS,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if not done:
                    log.error(
                        "pregen wave timeout after %.0fs with %s tasks still in flight",
                        PREGEN_WAVE_TIMEOUT_SECONDS,
                        len(pending_tasks),
                    )
                    for task in pending_tasks:
                        task.cancel()
                    wave_timed_out = True
                    pending_tasks.clear()
                    break
                for task in done:
                    idx += 1
                    try:
                        completed += record_pregen_result(await task)
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        log.warning("pregen wave item failed: %s", exc)
                    if idx % 8 == 0 and not should_pause_for_priority():
                        await _flush_off_request_pool(flush_write_queue, prefetch_executor)
                    if (
                        not pressure_abort
                        and memory_pressure.evaluate_memory_pressure().pause_bulk
                    ):
                        memory_pressure.gate_bulk_work()
                        pressure_abort = True
                if pressure_abort:
                    for task in pending_tasks:
                        task.cancel()
                    break
        finally:
            for weight in held_weights:
                await bulk_decode_budget.release(weight)

        if (
            should_pause_for_priority()
            and idx >= max(1, int(activity_burst_items))
        ):
            break

    if not should_pause_for_priority():
        await _flush_off_request_pool(flush_write_queue, prefetch_executor)
    if pressure_abort:
        return PREGEN_PRESSURE
    if wave_timed_out and completed <= 0:
        return -1
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

    if not await _flush_off_request_pool(flush_write_queue, prefetch_executor) and cache_metadata_backoff_active():
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
    stall_watchdog_seconds: float | None = None,
    reset_prefetch_executor=None,
) -> None:
    target_total_cache = 0
    target_total_at = 0.0
    no_progress_scan_passes = 0
    stall_limit = float(
        PREGEN_STALL_WATCHDOG_SECONDS if stall_watchdog_seconds is None else stall_watchdog_seconds
    )
    active_batch: asyncio.Task | None = None
    stall_reset = asyncio.Event()
    watchdog_enabled = stall_limit > 0

    def _cancel_active_batch(reason: str) -> None:
        batch = active_batch
        if batch is not None and not batch.done():
            log.warning("pregen cancelling active batch reason=%s", reason)
            batch.cancel()

    def _recover_stuck_executor() -> None:
        """Abandon wedged prefetch threads so the next wave can schedule."""
        if reset_prefetch_executor is None:
            return
        try:
            reset_prefetch_executor()
            log.warning("pregen replaced prefetch executor after stall recovery")
        except Exception as exc:
            log.error("pregen failed to replace prefetch executor: %s", exc)

    async def _await_batch(batch: asyncio.Task):
        """Await a bulk/full batch, honouring pause/stop without waiting forever."""
        while not batch.done():
            if is_manual_paused() or not is_manual_mode():
                stall_reset.set()
                _cancel_active_batch("pause_during_batch")
                break
            # Poll so stop/pause can interrupt a stuck decode-budget wait.
            await _REAL_ASYNCIO_SLEEP(0.25)
        try:
            return await batch
        except asyncio.CancelledError:
            if stall_reset.is_set() or is_manual_paused() or not is_manual_mode():
                return PREGEN_YIELDED
            raise

    async def _stall_watchdog() -> None:
        """Detect state=running with no generation — cancel stuck batch + reset budget."""
        while is_prefetching():
            # Real asyncio.sleep captured at import — not the injectable `sleep`
            # hook and not a late-bound asyncio.sleep that tests may replace.
            await _REAL_ASYNCIO_SLEEP(PREGEN_STALL_WATCHDOG_POLL_SECONDS)
            if not watchdog_enabled or pregen_status.get("state") != "running":
                continue
            last = pregen_status.get("last_generated_at")
            started = pregen_status.get("started_at")
            now_wall = time.time()
            anchor = float(last or started or 0.0)
            if anchor <= 0:
                continue
            stalled_for = now_wall - anchor
            if stalled_for < stall_limit:
                continue
            leaked = await bulk_decode_budget.reset_and_notify()
            log.error(
                "pregen stall watchdog: state=running with no generation for "
                "%.0fs (limit=%.0fs); resetting decode budget (leaked=%s bytes) "
                "and cancelling stuck batch",
                stalled_for,
                stall_limit,
                leaked,
            )
            # Arm recovery before cancel so the awaiter sees stall_reset set.
            pregen_status["last_generated_at"] = time.time()
            stall_reset.set()
            _cancel_active_batch("stall_watchdog")
            _recover_stuck_executor()
            set_pregen_state(
                "waiting",
                "Cache warming recovered from a stalled batch and will continue shortly.",
            )

    watchdog_task = asyncio.create_task(_stall_watchdog(), name="pregen-stall-watchdog")
    try:
        while is_prefetching():
            try:
                stall_reset.clear()
                await _flush_off_request_pool(flush_write_queue)
                await flush_orientation_updates()

                if is_manual_paused():
                    _cancel_active_batch("manual_pause")
                    set_pregen_state("paused", "Previews is stopped.")
                    no_progress_scan_passes = 0
                    await sleep(1)
                    continue

                if not is_manual_mode():
                    _cancel_active_batch("manual_mode_off")
                    set_pregen_state(
                        "paused",
                        "Previews is stopped until you start it from Background Work.",
                    )
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
                    if decision.reason == "memory pressure":
                        set_pregen_state("paused", "Paused: memory pressure")
                    else:
                        set_pregen_state(
                            "waiting",
                            f"Background work paused: {decision.reason}.",
                        )
                    no_progress_scan_passes = 0
                    await sleep(max(2.0, float(decision.sleep_seconds or 0.0)))
                    continue

                phases = [
                    size for size in preview_phase_order if background_tier_budget(size) > 0
                ]
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

                    async def _run_bulk():
                        with work_coordination.manual_bulk("cache"):
                            return await run_pregen_bulk_batch(generate_batch=generate_batch)

                    active_batch = asyncio.create_task(_run_bulk(), name="pregen-bulk-batch")
                    try:
                        generated = await _await_batch(active_batch)
                    finally:
                        active_batch = None

                    await flush_orientation_updates()

                if generated == PREGEN_PRESSURE:
                    set_pregen_state("paused", memory_pressure.PAUSE_MESSAGE)
                    no_progress_scan_passes = 0
                    await sleep(max(2.0, float(decision.sleep_seconds or 0.0)))
                    continue

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

                    async def _run_full():
                        with work_coordination.manual_bulk("cache"):
                            return await run_full_warm_batch(
                                generate_batch=max(1, min(8, generate_batch)),
                            )

                    active_batch = asyncio.create_task(_run_full(), name="pregen-full-batch")
                    try:
                        full_generated = await _await_batch(active_batch)
                    finally:
                        active_batch = None
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
                                f"full: {originals['count']} cached, "
                                f"{originals['utilization_pct']:.1f}% of budget"
                            )
                        set_pregen_state(
                            "complete",
                            "Cache scan found no more warmable images. "
                            + " · ".join(phase_parts),
                        )
                        no_progress_scan_passes = 0
                        await sleep(5)
                    else:
                        set_pregen_state(
                            "running",
                            f"Scanning for remaining cache work "
                            f"({no_progress_scan_passes}/{no_progress_scan_limit()}).",
                            phase="previews",
                        )
                        await sleep(
                            max(0.25, batch_pause_seconds(), decision.thumbnail_pause_seconds)
                        )
                else:
                    no_progress_scan_passes = 0
                    await sleep(max(batch_pause_seconds(), decision.thumbnail_pause_seconds))

            except Exception as e:
                set_pregen_state("error", "Pre-generation worker hit an error.", error=str(e))
                print(f"Prefetch worker error: {e}")
                await sleep(5)
    finally:
        watchdog_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await watchdog_task

