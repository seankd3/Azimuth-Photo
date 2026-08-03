"""Async background thumbnail/full-original pregeneration batch runners."""

import asyncio
import contextlib
import logging
import os
import time
from collections import deque
from functools import partial

from core import memory_pressure, work_coordination
from thumbnails.config import EMBEDDED_PREVIEW_EXTENSIONS, RAW_EXTENSIONS, SIZES
from thumbnails.decode_budget import bulk_decode_budget, estimate_decode_bytes

try:
    from photo_metadata import METADATA_EXTRACTOR_VERSION
except Exception:  # pragma: no cover
    METADATA_EXTRACTOR_VERSION = 3


log = logging.getLogger("thumbnails.pregen")

PREGEN_YIELDED = -2
PREGEN_PRESSURE = -3
PREGEN_STORAGE_LIMITED = -4

_PREGEN_DIAG = os.environ.get("AZIMUTH_PREGEN_DIAG", "").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}


def _diag(message: str, **fields) -> None:
    if not _PREGEN_DIAG:
        return
    details = " ".join(f"{key}={value!r}" for key, value in fields.items())
    # WARNING so the opt-in trace survives production log filtering (INFO from
    # this module is swallowed by the default logging config).
    log.warning("pregendiag %s %s", message, details)

# Capture the real sleep at import time. Tests often replace `asyncio.sleep`
# (via `thumbnails.asyncio.sleep = …`); the stall watchdog must not share that
# hook or it busy-loops at 100% CPU and starves the worker.
_REAL_ASYNCIO_SLEEP = asyncio.sleep

# state=running with no generation or heartbeat for this long → recover.
PREGEN_STALL_WATCHDOG_SECONDS = float(
    os.environ.get("AZIMUTH_PREGEN_STALL_SECONDS", str(5 * 60))
)
PREGEN_STALL_WATCHDOG_POLL_SECONDS = 15.0
# No-completion ceiling so one stuck demosaic cannot pin the bulk loop forever.
PREGEN_WAVE_TIMEOUT_SECONDS = float(
    os.environ.get("AZIMUTH_PREGEN_WAVE_TIMEOUT_SECONDS", "120")
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


def _item_decode_estimate(item: dict) -> int:
    ext = os.path.splitext(str(item.get("filepath") or ""))[1].lower()
    is_raw = ext in RAW_EXTENSIONS
    # Charge the embedded-JPEG path only for formats that reliably cover lg
    # from the embed. .dng uses the embed for sm when it fits, but lg still
    # demosaics — keep charging demosaic weight.
    estimate = estimate_decode_bytes(
        item.get("source_size"),
        raw=is_raw,
        embedded_preview=ext in EMBEDDED_PREVIEW_EXTENSIONS,
        width=item.get("width"),
        height=item.get("height"),
        max_target=max(SIZES.values()),
    )
    # Whole-file buffers stay in RAM across decode after the HDD slot split —
    # charge source bytes on top of the demosaic peak.
    return estimate + max(0, int(item.get("source_size") or 0))


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
    note_progress=None,
) -> int:
    """Warm previews with a continuously-refilled in-flight decode pump.

    Decode threads stay fed: as each task completes, the next candidate is
    submitted immediately (no wave barrier / head-of-line stall). Candidate
    DB scans double-buffer in the background so the async loop does not leave
    the pool idle while anti-joining the next page.
    """
    set_priority_scope(None)
    generate_batch = generate_batch or default_generate_batch
    if should_pause_for_priority():
        generate_batch = min(generate_batch, max(1, int(activity_burst_items)))
    tier_budgets = bulk_tier_budgets()
    _diag("bulk batch entry", generate_batch=generate_batch, tier_budgets=tier_budgets)
    if all(tier_budgets.get(size, 0) <= 0 for size in thumb_tiers):
        _diag("bulk batch abort: all tier budgets empty")
        return 0

    if not await _flush_off_request_pool(flush_write_queue, prefetch_executor) and cache_metadata_backoff_active():
        _diag("bulk batch abort: flush failed under metadata backoff")
        return 0
    tier_room = bulk_tier_room(tier_budgets)
    # Room, not budget, is what decides whether a photo can be offered — and a
    # tier with no room swallows every row silently, which reads from outside as
    # an endless empty scan. Budgets were already logged and looked healthy
    # while the hub generated nothing for hours, so log the number that matters.
    _diag("bulk batch room", tier_room=tier_room, backoff=cache_metadata_backoff_active())
    if all(room <= 0 for room in tier_room.values()) and cache_metadata_backoff_active():
        _diag("bulk batch abort: no tier room under metadata backoff", tier_room=tier_room)
        return 0
    full_budget = int(disk_allocations.get(full_tier, 0) or 0)
    full_room = {"bytes": full_tier_room(full_budget)} if full_budget > 0 else {"bytes": 0}

    pending: deque[dict] = deque()
    # Limit actionable pages per batch, but tolerate a much wider desert of
    # rows whose requested tiers are already warm.  Treating empty pages as
    # actionable pages was the false-complete loop that stranded backfill.
    max_scan_batches = 4
    max_empty_scan_batches = 64
    candidates_scanned = 0
    empty_scan_batches = 0
    loop = asyncio.get_running_loop()
    priority_processed_ids: set[int] = set()
    priority_scanned_batches = 0
    scanned_batches = 0
    reached_end = False
    # Once priority yields any work, this batch stays on priority (matches prior
    # semantics: do not mix bulk-cursor rows into a priority wave).
    priority_mode = False
    normal_mode = False
    candidates_exhausted = False
    # Cap how many candidates this batch may ever queue (generate_batch).
    queued_total = 0

    def collect_candidates(rows, *, room_left: int) -> list[dict]:
        """Build candidate dicts from rows (off-loop; returns a new list)."""
        out: list[dict] = []
        if room_left <= 0:
            return out
        for row in rows:
            if not is_prefetching() or is_manual_paused():
                break
            if (
                should_pause_for_priority()
                and len(out) >= max(1, int(activity_burst_items))
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
            candidate = {
                "id": int(row["id"]),
                "filepath": row["filepath"],
                "signatures": size_signatures,
                "full": full_item,
                "source_size": source_size,
                "width": _row_dimension(row, "width"),
                "height": _row_dimension(row, "height"),
                "need_hash": need_hash,
                "need_metadata": need_metadata,
            }
            estimate = _item_decode_estimate(candidate)
            if estimate > bulk_decode_budget.max_bytes:
                # The budget clamps an oversized weight to its own ceiling,
                # which admits a frame at a price the machine cannot pay:
                # measured on the hub, one 4.2GB RAW panorama charged 768MB,
                # ran alone, and the kernel OOM-killed the service — four times
                # in an hour. Leave it to the on-demand path, which decodes one
                # photo for one waiting person instead of alongside a batch.
                log.info(
                    "Bulk preview skips %s: needs ~%.1fGB to decode, budget is %.1fGB",
                    row["filepath"],
                    estimate / 1024**3,
                    bulk_decode_budget.max_bytes / 1024**3,
                )
                continue
            out.append(candidate)
            if len(out) >= room_left:
                break
        return out

    fetch_lock = asyncio.Lock()

    async def fetch_more(room_left: int) -> int:
        """Fill ``pending`` with up to ``room_left`` new candidates."""
        nonlocal priority_scanned_batches, scanned_batches, reached_end
        nonlocal candidates_scanned, empty_scan_batches
        nonlocal priority_mode, normal_mode, candidates_exhausted, queued_total
        async with fetch_lock:
            added_total = 0
            room_left = min(room_left, max(0, generate_batch - queued_total))
            while added_total < room_left:
                if not is_prefetching() or is_manual_paused():
                    break
                need = room_left - added_total
                candidate_scan_batch = (
                    min(scan_batch, max(1, int(activity_burst_items)))
                    if should_pause_for_priority()
                    else scan_batch
                )
                # Never over-fetch past remaining room — the bulk cursor advances
                # by the full page, and unconsumed rows would be skipped.
                candidate_scan_batch = max(1, min(candidate_scan_batch, need))

                # Priority first (and exclusively once engaged).
                if not normal_mode and priority_scanned_batches < max_scan_batches:
                    rows, priority_label = await pregen_priority_candidate_batch(
                        candidate_scan_batch,
                        priority_processed_ids,
                    )
                    if rows:
                        set_priority_scope(priority_label)
                        priority_processed_ids.update(int(row["id"]) for row in rows)
                        items = await loop.run_in_executor(
                            prefetch_executor,
                            partial(collect_candidates, rows, room_left=need),
                        )
                        pending.extend(items)
                        added = len(items)
                        priority_scanned_batches += 1
                        if added > 0:
                            # Real priority work — keep this batch exclusive.
                            priority_mode = True
                        added_total += added
                        queued_total += added
                        if added_total >= room_left:
                            break
                        continue
                    if priority_mode:
                        # Priority scope drained mid-batch — stop topping up rather
                        # than slipping bulk-cursor rows into a priority wave.
                        candidates_exhausted = True
                        break
                    # No priority work at all — fall through to bulk cursor.
                    set_priority_scope(None)
                    normal_mode = True

                if priority_mode:
                    candidates_exhausted = True
                    break

                if not normal_mode:
                    set_priority_scope(None)
                    normal_mode = True

                if (
                    scanned_batches >= max_scan_batches
                    or empty_scan_batches >= max_empty_scan_batches
                ):
                    break

                rows = await pregen_bulk_candidate_batch(candidate_scan_batch)
                if not rows:
                    reset_pregen_bulk_cursor()
                    reached_end = True
                    if pending or added_total or queued_total:
                        candidates_exhausted = True
                        break
                    rows = await pregen_bulk_candidate_batch(candidate_scan_batch)
                    if not rows:
                        candidates_exhausted = True
                        break

                candidates_scanned += len(rows)

                items = await loop.run_in_executor(
                    prefetch_executor,
                    partial(collect_candidates, rows, room_left=need),
                )
                pending.extend(items)
                added = len(items)
                if added > 0:
                    scanned_batches += 1
                    empty_scan_batches = 0
                else:
                    empty_scan_batches += 1
                added_total += added
                queued_total += added
                _diag(
                    "bulk_scan_page",
                    rows=len(rows),
                    pending_found_page=added,
                    pending_total=len(pending),
                    empty_scan_batches=empty_scan_batches,
                    scanned_batches=scanned_batches,
                    scan_batch=candidate_scan_batch,
                )
                if len(rows) < candidate_scan_batch:
                    reset_pregen_bulk_cursor()
                    reached_end = True
                    candidates_exhausted = True
                    break
                if reached_end:
                    candidates_exhausted = True
                    break
            return added_total

    # Start decode ASAP: seed just enough for the first in-flight set, then
    # top up in the background while those threads work.
    seed_workers = max(1, memory_pressure.effective_prefetch_workers(prefetch_workers))
    seed_target = min(generate_batch, max(seed_workers * 2, seed_workers))
    await fetch_more(seed_target)

    if not pending:
        if (
            (scanned_batches >= max_scan_batches or empty_scan_batches >= max_empty_scan_batches)
            and not reached_end
        ):
            _diag(
                "bulk_batch",
                decision="no_progress_scan",
                candidates_scanned=candidates_scanned,
                candidates_pending_found=0,
                empty_scan_batches=empty_scan_batches,
                scanned_batches=scanned_batches,
                generated_this_batch=0,
            )
            return -1
        _diag(
            "bulk_batch",
            decision="no_candidates",
            candidates_scanned=candidates_scanned,
            candidates_pending_found=0,
            reached_end=reached_end,
            generated_this_batch=0,
        )
        return 0

    completed = 0
    finished = 0
    submitted = 0
    pressure_abort = False
    wave_timed_out = False
    # Keep the budget epoch with each hold. A watchdog reset invalidates old
    # holds, so their late completion cannot subtract from a newer batch.
    in_flight: dict[asyncio.Future, tuple[int, int]] = {}
    flush_task: asyncio.Task | None = None
    top_up_task: asyncio.Task | None = None
    # Keep ~2× in-flight candidates buffered so DB scans overlap decode.
    top_up_watermark = max(seed_workers * 2, min(generate_batch, seed_workers * 3))

    def _kick_flush() -> None:
        nonlocal flush_task
        if should_pause_for_priority():
            return
        if flush_task is not None and not flush_task.done():
            return
        flush_task = asyncio.create_task(
            _flush_off_request_pool(flush_write_queue, prefetch_executor),
            name="pregen-write-flush",
        )

    def _kick_top_up() -> None:
        nonlocal top_up_task
        if candidates_exhausted or should_pause_for_priority():
            return
        if len(pending) >= top_up_watermark:
            return
        room = generate_batch - queued_total
        if room <= 0:
            return
        if top_up_task is not None and not top_up_task.done():
            return

        async def _fill() -> None:
            await fetch_more(min(room, top_up_watermark))

        top_up_task = asyncio.create_task(_fill(), name="pregen-candidate-topup")

    async def _submit_one(item: dict) -> bool:
        nonlocal submitted, pressure_abort
        if memory_pressure.evaluate_memory_pressure().pause_bulk:
            memory_pressure.gate_bulk_work()
            pressure_abort = True
            return False
        estimate = _item_decode_estimate(item)
        if estimate > bulk_decode_budget.max_bytes:
            # The budget clamps an oversized weight to its own ceiling, which
            # admits the frame at a price it cannot pay: measured on the hub, a
            # single 4.2GB RAW panorama charged 768MB, ran alone, and the kernel
            # OOM-killed the service — four times in one hour. Leave it for the
            # on-demand path, which decodes one photo for one waiting person.
            _diag("pump too big for this machine", item=item["id"], estimate=estimate)
            log.info(
                "Skipping bulk thumbnail for %s: needs ~%.1fGB to decode, budget is %.1fGB",
                item.get("filepath"),
                estimate / 1024**3,
                bulk_decode_budget.max_bytes / 1024**3,
            )
            return False
        _diag("pump acquire", item=item["id"], estimate=estimate, used=bulk_decode_budget.used_bytes, in_flight=len(in_flight))
        if in_flight:
            # Never block on budget while holding in-flight work: releases
            # happen in the completion loop this coroutine still has to
            # reach, so a blocking wait here deadlocks until the watchdog.
            weight = bulk_decode_budget.try_acquire(estimate)
            if weight is None:
                _diag("pump budget full", item=item["id"], in_flight=len(in_flight))
                pending.appendleft(item)
                return False
        else:
            weight = await bulk_decode_budget.acquire(estimate)
        _diag("pump acquired", item=item["id"], weight=weight)
        epoch = bulk_decode_budget.epoch
        if memory_pressure.evaluate_memory_pressure().pause_bulk:
            # Pressure rose while waiting on the decode budget.
            if bulk_decode_budget.release_nowait(weight, epoch=epoch):
                bulk_decode_budget.wake_waiters_soon()
            memory_pressure.gate_bulk_work()
            pressure_abort = True
            return False
        task = loop.run_in_executor(
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
        in_flight[task] = (epoch, weight)
        if note_progress is not None:
            note_progress()
        submitted += 1
        return True

    try:
        while not pressure_abort and not wave_timed_out:
            if not is_prefetching() or is_manual_paused():
                break

            target_n = memory_pressure.effective_prefetch_workers(prefetch_workers)
            _kick_top_up()

            # Refill in-flight immediately — no barrier on the slowest sibling.
            while (
                len(in_flight) < target_n
                and submitted < generate_batch
                and pending
                and not pressure_abort
            ):
                if (
                    should_pause_for_priority()
                    and submitted >= max(1, int(activity_burst_items))
                ):
                    break
                item = pending.popleft()
                if not await _submit_one(item):
                    break

            if in_flight:
                _diag("pump waiting", in_flight=len(in_flight), submitted=submitted, finished=finished)
                done, _still = await asyncio.wait(
                    set(in_flight),
                    timeout=PREGEN_WAVE_TIMEOUT_SECONDS,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                _diag("pump wave done", done=len(done), in_flight=len(in_flight))
                if not done:
                    log.error(
                        "pregen pump timeout after %.0fs with %s tasks still in flight",
                        PREGEN_WAVE_TIMEOUT_SECONDS,
                        len(in_flight),
                    )
                    for task in list(in_flight):
                        task.cancel()
                    wave_timed_out = True
                    break
                for task in done:
                    epoch, weight = in_flight.pop(task)
                    finished += 1
                    try:
                        outcome = await task
                        _diag("pump item result", result=outcome)
                        completed += record_pregen_result(outcome)
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        log.warning("pregen pump item failed: %s", exc)
                    finally:
                        if bulk_decode_budget.release_nowait(weight, epoch=epoch):
                            bulk_decode_budget.wake_waiters_soon()
                    if note_progress is not None:
                        note_progress()
                    if finished % 8 == 0:
                        _kick_flush()
                    if memory_pressure.evaluate_memory_pressure().pause_bulk:
                        memory_pressure.gate_bulk_work()
                        pressure_abort = True
                        for leftover in list(in_flight):
                            leftover.cancel()
                        break
                if pressure_abort:
                    break
                if (
                    should_pause_for_priority()
                    and finished >= max(1, int(activity_burst_items))
                    and not in_flight
                ):
                    break
                continue

            # Nothing in flight — wait on background top-up, or we are done.
            if top_up_task is not None and not top_up_task.done():
                await asyncio.wait({top_up_task}, timeout=1.0)
                continue
            if submitted >= generate_batch:
                break
            if pending:
                continue
            # Last-chance synchronous fill when the queue drained early.
            room = generate_batch - submitted
            if room > 0 and not candidates_exhausted:
                added = await fetch_more(room)
                if added > 0:
                    continue
            break
    finally:
        if top_up_task is not None and not top_up_task.done():
            top_up_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await top_up_task
        # Drain cancelled in-flight so decode budget weights release.
        released_any = False
        for task, (epoch, weight) in list(in_flight.items()):
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
            if bulk_decode_budget.release_nowait(weight, epoch=epoch):
                released_any = True
            in_flight.pop(task, None)
        if released_any:
            bulk_decode_budget.wake_waiters_soon()
        if flush_task is not None:
            with contextlib.suppress(Exception):
                await flush_task
        if not should_pause_for_priority():
            await _flush_off_request_pool(flush_write_queue, prefetch_executor)

    if pressure_abort:
        _diag(
            "bulk_batch",
            decision="memory_pause",
            candidates_scanned=candidates_scanned,
            candidates_pending_found=len(pending),
            generated_this_batch=completed,
        )
        return PREGEN_PRESSURE
    if wave_timed_out and completed <= 0:
        _diag(
            "bulk_batch",
            decision="wave_timeout",
            candidates_scanned=candidates_scanned,
            candidates_pending_found=len(pending),
            generated_this_batch=completed,
        )
        return -1
    if completed <= 0:
        _diag(
            "bulk_batch",
            decision="no_progress_generate",
            candidates_scanned=candidates_scanned,
            candidates_pending_found=len(pending),
            generated_this_batch=completed,
        )
        return -1
    _diag(
        "bulk_batch",
        decision="ran_batch",
        candidates_scanned=candidates_scanned,
        candidates_pending_found=len(pending),
        generated_this_batch=completed,
    )
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
    background_original_cache_allowed,
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
    if not background_original_cache_allowed(cache_root):
        return PREGEN_STORAGE_LIMITED

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
            progress = pregen_status.get("last_progress_at")
            started = pregen_status.get("started_at")
            now_wall = time.time()
            anchor = max(float(last or 0.0), float(progress or 0.0), float(started or 0.0))
            if anchor <= 0:
                continue
            stalled_for = now_wall - anchor
            if stalled_for < stall_limit:
                continue
            leaked = await bulk_decode_budget.reset_and_notify()
            log.error(
                "pregen stall watchdog: state=running with no progress for "
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
                    if decision.reason == memory_pressure.PAUSE_REASON:
                        set_pregen_state("paused", memory_pressure.PAUSE_MESSAGE)
                    else:
                        set_pregen_state("waiting", f"Waiting: {decision.reason}.")
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

                if generated == PREGEN_STORAGE_LIMITED:
                    set_pregen_state(
                        "waiting",
                        "Preview cache remains available; full originals are paused until disk space recovers.",
                    )
                    no_progress_scan_passes = 0
                    await sleep(5)
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
                    # Default pause is 0 — re-enter immediately so the decode
                    # pool never idles between batches. Isolation + priority
                    # yield already protect interactive browsing.
                    pause = max(
                        float(batch_pause_seconds() or 0.0),
                        float(decision.thumbnail_pause_seconds or 0.0),
                    )
                    if pause > 0:
                        await sleep(pause)
                    else:
                        await sleep(0)

            except Exception as e:
                set_pregen_state("error", "Pre-generation worker hit an error.", error=str(e))
                print(f"Prefetch worker error: {e}")
                await sleep(5)
    finally:
        watchdog_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await watchdog_task
