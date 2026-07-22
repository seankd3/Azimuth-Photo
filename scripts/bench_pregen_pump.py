#!/usr/bin/env python3
"""Prove continuous pregen pump keeps decode threads fed vs wave-barrier.

Compares:
  OLD — fill wave of N, wait for ALL to finish, flush, pause 0.25s, re-fetch
  NEW — keep N in flight; as each completes, submit next; overlap fetch; pause 0

Uses synthetic decode work so the measurement isolates orchestration idle,
not disk. Run from repo root:

  web/.venv/bin/python scripts/bench_pregen_pump.py
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "web"))


def _busy_sleep(seconds: float) -> None:
    """CPU-ish wait that shows up as 'active' in stack samples."""
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        pass


async def _old_wave_barrier(
    *,
    items: int,
    workers: int,
    decode_s: float,
    fetch_s: float,
    pause_s: float,
    batch: int,
    slow_every: int = 4,
    slow_factor: float = 3.0,
) -> dict:
    active = 0
    peak = 0
    lock = threading.Lock()
    samples: list[int] = []
    stop = False

    def sample_loop() -> None:
        nonlocal peak
        while not stop:
            with lock:
                n = active
                peak = max(peak, n)
                samples.append(n)
            time.sleep(0.01)

    sampler = threading.Thread(target=sample_loop, daemon=True)
    sampler.start()
    loop = asyncio.get_running_loop()
    completed = 0
    t0 = time.perf_counter()

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="old-decode") as pool:

        def work(i: int) -> int:
            nonlocal active
            with lock:
                active += 1
            try:
                delay = decode_s * slow_factor if slow_every and i % slow_every == 0 else decode_s
                _busy_sleep(delay)
                return 1
            finally:
                with lock:
                    active -= 1

        cursor = 0
        while cursor < items:
            # Serial candidate fetch — decode pool idle.
            await asyncio.sleep(fetch_s)
            batch_end = min(cursor + batch, items)
            while cursor < batch_end:
                wave = list(range(cursor, min(cursor + workers, batch_end)))
                if not wave:
                    break
                cursor = wave[-1] + 1
                tasks = [loop.run_in_executor(pool, work, i) for i in wave]
                # OLD: wait for ALL (head-of-line on slowest).
                results = await asyncio.gather(*tasks)
                completed += sum(results)
            await asyncio.sleep(pause_s)

    stop = True
    sampler.join(timeout=1)
    wall = time.perf_counter() - t0
    busy_frac = (sum(1 for n in samples if n > 0) / len(samples)) if samples else 0.0
    mean_active = statistics.fmean(samples) if samples else 0.0
    return {
        "mode": "old_wave_barrier",
        "wall_s": round(wall, 3),
        "completed": completed,
        "images_per_min": round(completed / wall * 60, 1),
        "peak_active": peak,
        "mean_active": round(mean_active, 2),
        "busy_frac": round(busy_frac, 3),
        "samples": len(samples),
    }


async def _new_continuous_pump(
    *,
    items: int,
    workers: int,
    decode_s: float,
    fetch_s: float,
    slow_every: int = 4,
    slow_factor: float = 3.0,
) -> dict:
    active = 0
    peak = 0
    lock = threading.Lock()
    samples: list[int] = []
    stop = False

    def sample_loop() -> None:
        nonlocal peak
        while not stop:
            with lock:
                n = active
                peak = max(peak, n)
                samples.append(n)
            time.sleep(0.01)

    sampler = threading.Thread(target=sample_loop, daemon=True)
    sampler.start()
    loop = asyncio.get_running_loop()
    completed = 0
    t0 = time.perf_counter()
    pending = list(range(items))
    in_flight: set[asyncio.Future] = set()

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="new-decode") as pool:

        def work(i: int) -> int:
            nonlocal active
            with lock:
                active += 1
            try:
                delay = decode_s * slow_factor if slow_every and i % slow_every == 0 else decode_s
                _busy_sleep(delay)
                return 1
            finally:
                with lock:
                    active -= 1

        top_up_task: asyncio.Task | None = None
        # Seed small, then overlap fetch while decoding.
        seed = pending[:workers]
        pending = pending[workers:]

        async def top_up() -> None:
            await asyncio.sleep(fetch_s)

        # Initial fetch cost once.
        await asyncio.sleep(fetch_s)

        for i in seed:
            in_flight.add(loop.run_in_executor(pool, work, i))

        while in_flight or pending:
            if len(in_flight) < workers and pending:
                if top_up_task is None and len(pending) < workers * 2:
                    top_up_task = asyncio.create_task(top_up())
                i = pending.pop(0)
                in_flight.add(loop.run_in_executor(pool, work, i))
                continue
            if not in_flight:
                if top_up_task is not None and not top_up_task.done():
                    await top_up_task
                    top_up_task = None
                    continue
                break
            done, in_flight = await asyncio.wait(
                in_flight, return_when=asyncio.FIRST_COMPLETED
            )
            for task in done:
                completed += await task
            # Immediately refill (no barrier, no pause).
            while len(in_flight) < workers and pending:
                i = pending.pop(0)
                in_flight.add(loop.run_in_executor(pool, work, i))

    stop = True
    sampler.join(timeout=1)
    wall = time.perf_counter() - t0
    busy_frac = (sum(1 for n in samples if n > 0) / len(samples)) if samples else 0.0
    mean_active = statistics.fmean(samples) if samples else 0.0
    return {
        "mode": "new_continuous_pump",
        "wall_s": round(wall, 3),
        "completed": completed,
        "images_per_min": round(completed / wall * 60, 1),
        "peak_active": peak,
        "mean_active": round(mean_active, 2),
        "busy_frac": round(busy_frac, 3),
        "samples": len(samples),
    }


async def _real_bulk_batch_busy(
    *,
    items: int,
    workers: int,
    decode_s: float,
    fetch_s: float,
) -> dict:
    """Drive the real run_pregen_bulk_batch pump with mocked I/O."""
    from core import memory_pressure
    from thumbnails import pregen_worker
    from thumbnails.decode_budget import bulk_decode_budget

    memory_pressure.reset_for_tests()
    await bulk_decode_budget.reset_and_notify()
    # Tiny per-item charge so the bench measures the pump, not the RAM gate.
    original_estimate = pregen_worker._item_decode_estimate
    pregen_worker._item_decode_estimate = lambda item: 1024 * 1024
    # Host may be under soft pressure from prod — pin worker count for the proof.
    original_effective = memory_pressure.effective_prefetch_workers
    memory_pressure.effective_prefetch_workers = lambda configured, rss_bytes=None: max(
        1, int(configured)
    )

    active = 0
    peak = 0
    lock = threading.Lock()
    samples: list[int] = []
    stop = False
    cursor = {"n": 0}
    rows = [
        {
            "id": i,
            "source_id": 1,
            "filepath": f"/tmp/bench_{i}.CR3",
            "file_size": 25 * 1024 * 1024,
            "file_modified_at": 1.0,
            "width": 6000,
            "height": 4000,
            "content_hash": "x",
            "metadata_scanned_at": 1.0,
            "metadata_version": 99,
        }
        for i in range(1, items + 1)
    ]

    def sample_loop() -> None:
        nonlocal peak
        while not stop:
            with lock:
                n = active
                peak = max(peak, n)
                samples.append(n)
            time.sleep(0.01)

    sampler = threading.Thread(target=sample_loop, daemon=True)
    sampler.start()

    def generate(filepath, image_id, signatures, **kwargs):
        nonlocal active
        with lock:
            active += 1
        try:
            _busy_sleep(decode_s)
            return {"written": 1, "image_id": image_id}
        finally:
            with lock:
                active -= 1

    async def candidates(limit):
        await asyncio.sleep(fetch_s)
        start = cursor["n"]
        end = min(len(rows), start + limit)
        cursor["n"] = end
        return rows[start:end]

    async def priority_empty(limit, processed):
        return [], None

    t0 = time.perf_counter()
    try:
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="bench-prefetch") as pool:
            result = await pregen_worker.run_pregen_bulk_batch(
                generate_batch=items,
                default_generate_batch=items,
                scan_batch=max(8, workers * 2),
                thumb_tiers=("sm",),
                full_tier="full",
                disk_allocations={"sm": 10**12, "full": 0},
                is_prefetching=lambda: True,
                is_manual_paused=lambda: False,
                should_pause_for_priority=lambda: False,
                flush_write_queue=lambda: True,
                cache_metadata_backoff_active=lambda: False,
                bulk_tier_budgets=lambda: {"sm": 10**12},
                bulk_tier_room=lambda budgets: {"sm": 10**12},
                full_tier_room=lambda budget: 0,
                pregen_priority_candidate_batch=priority_empty,
                pregen_bulk_candidate_batch=candidates,
                reset_pregen_bulk_cursor=lambda: None,
                set_priority_scope=lambda label: None,
                bulk_candidate_signatures=lambda row, tier_room, tier_budgets: (
                    {"sm": "sig"},
                    int(row["file_size"]),
                ),
                full_candidate_signature=lambda *args, **kwargs: None,
                prefetch_executor=pool,
                generate_thumbnail_set_sync=generate,
                record_pregen_result=lambda result: int(result.get("written") or 0),
                activity_burst_items=2,
                prefetch_workers=workers,
            )
    finally:
        pregen_worker._item_decode_estimate = original_estimate
        memory_pressure.effective_prefetch_workers = original_effective
        await bulk_decode_budget.reset_and_notify()
    stop = True
    sampler.join(timeout=1)
    wall = time.perf_counter() - t0
    busy_frac = (sum(1 for n in samples if n > 0) / len(samples)) if samples else 0.0
    mean_active = statistics.fmean(samples) if samples else 0.0
    return {
        "mode": "real_run_pregen_bulk_batch",
        "result": result,
        "wall_s": round(wall, 3),
        "completed": result if isinstance(result, int) and result > 0 else 0,
        "images_per_min": round((result if result > 0 else 0) / wall * 60, 1),
        "peak_active": peak,
        "mean_active": round(mean_active, 2),
        "busy_frac": round(busy_frac, 3),
        "samples": len(samples),
    }


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--items", type=int, default=24)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--decode-ms", type=float, default=200.0)
    parser.add_argument("--fetch-ms", type=float, default=80.0)
    parser.add_argument("--pause-ms", type=float, default=250.0)
    args = parser.parse_args()

    decode_s = args.decode_ms / 1000.0
    fetch_s = args.fetch_ms / 1000.0
    pause_s = args.pause_ms / 1000.0

    old = await _old_wave_barrier(
        items=args.items,
        workers=args.workers,
        decode_s=decode_s,
        fetch_s=fetch_s,
        pause_s=pause_s,
        batch=args.items,
    )
    new = await _new_continuous_pump(
        items=args.items,
        workers=args.workers,
        decode_s=decode_s,
        fetch_s=fetch_s,
    )
    real = await _real_bulk_batch_busy(
        items=args.items,
        workers=args.workers,
        decode_s=decode_s,
        fetch_s=fetch_s,
    )

    speedup = (
        new["images_per_min"] / old["images_per_min"] if old["images_per_min"] else 0.0
    )
    out = {
        "params": {
            "items": args.items,
            "workers": args.workers,
            "decode_ms": args.decode_ms,
            "fetch_ms": args.fetch_ms,
            "pause_ms": args.pause_ms,
            "slow_every": 4,
            "slow_factor": 3.0,
        },
        "old": old,
        "new": new,
        "real_pump": real,
        "speedup_new_vs_old": round(speedup, 2),
        "busy_frac_lift": round(new["busy_frac"] - old["busy_frac"], 3),
    }
    print(json.dumps(out, indent=2))
    # Gate: continuous pump must raise mean concurrency and finish faster under
    # mixed (fast/slow) item times — the prod starvation shape.
    if new["mean_active"] < old["mean_active"] * 1.4:
        print(
            f"FAIL: mean_active did not rise enough "
            f"({old['mean_active']} → {new['mean_active']})",
            file=sys.stderr,
        )
        return 1
    if new["images_per_min"] < old["images_per_min"] * 1.25:
        print("FAIL: throughput did not rise enough", file=sys.stderr)
        return 1
    if real["peak_active"] < min(args.workers, args.items):
        print(
            f"FAIL: real pump peak_active={real['peak_active']} < workers",
            file=sys.stderr,
        )
        return 1
    if real["busy_frac"] < 0.80:
        print(f"FAIL: real pump busy_frac={real['busy_frac']}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
