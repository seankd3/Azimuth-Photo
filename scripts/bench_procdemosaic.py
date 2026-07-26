#!/usr/bin/env python3
"""Isolated RAW demosaic bench: thread path vs process pool (GIL bypass).

Usage (from web/):
  .venv/bin/python ../scripts/bench_procdemosaic.py
"""

from __future__ import annotations

import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
sys.path.insert(0, str(WEB))

SIZES = {"md": 1920, "lg": 3840}
QUALITY = 92


def find_r5_dngs(n: int = 12) -> list[Path]:
    roots = [Path(os.environ.get("AZIMUTH_BENCH_RAW_ROOT", Path.home() / "Pictures"))]
    found: list[Path] = []
    for root in roots:
        if not root.is_dir():
            continue
        found.extend(sorted(root.glob("*R5*.DNG")))
        if len(found) >= n:
            break
    return found[:n]


def demosaic_one_thread(path: Path) -> dict:
    from raw_thumb_ops import demosaic_tier_jpegs

    return demosaic_tier_jpegs(str(path), ["md", "lg"], SIZES, QUALITY)


def warm_page_cache(paths: list[Path]) -> None:
    for path in paths:
        with open(path, "rb") as handle:
            handle.read()


def run_serial(paths: list[Path]) -> float:
    started = time.perf_counter()
    for path in paths:
        demosaic_one_thread(path)
    return time.perf_counter() - started


def run_thread_pool(paths: list[Path], workers: int) -> float:
    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(demosaic_one_thread, paths))
    return time.perf_counter() - started


def run_process_pool(paths: list[Path], workers: int, ipc: str) -> tuple[float, int]:
    os.environ["AZIMUTH_DEMOSAIC_PROCESSES"] = str(workers)
    os.environ["AZIMUTH_DEMOSAIC_IPC"] = ipc
    from thumbnails import demosaic_pool

    demosaic_pool.reset_for_tests()
    demosaic_pool.ensure_pool()

    peak = {"n": 0}
    lock = threading.Lock()
    in_flight = {"n": 0}

    def one(path: Path) -> None:
        nonlocal in_flight
        with lock:
            in_flight["n"] += 1
            peak["n"] = max(peak["n"], in_flight["n"])
        try:
            data = None
            if ipc == "bytes":
                with open(path, "rb") as handle:
                    data = handle.read()
            demosaic_pool.run_demosaic_tier_jpegs(
                str(path),
                ["md", "lg"],
                sizes=SIZES,
                thumb_quality=QUALITY,
                source_data=data,
                interactive=False,
            )
        finally:
            with lock:
                in_flight["n"] -= 1

    # Oversubscribe submitters so the process pool stays full (mirrors prefetch threads).
    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=workers) as submitters:
        list(submitters.map(one, paths))
    wall = time.perf_counter() - started
    demosaic_pool.shutdown_pool(wait=True)
    return wall, peak["n"]


def rate(n: int, wall: float) -> float:
    return (n / wall) * 60.0 if wall > 0 else 0.0


def main() -> int:
    n = int(os.environ.get("BENCH_N", "12"))
    workers = int(os.environ.get("BENCH_WORKERS", "6"))
    paths = find_r5_dngs(n)
    if len(paths) < n:
        print(f"ERROR: need {n} R5 DNGs, found {len(paths)}", file=sys.stderr)
        return 1

    print(f"n={len(paths)} workers={workers}")
    print(f"sample[0]={paths[0]} size_mb={paths[0].stat().st_size / 1e6:.1f}")
    print("warming page cache…")
    warm_page_cache(paths)

    print("serial (in-process)…")
    serial_wall = run_serial(paths)
    print(f"  wall={serial_wall:.2f}s  rate={rate(len(paths), serial_wall):.1f} DNGs/min")

    print(f"thread pool ({workers})…")
    thread_wall = run_thread_pool(paths, workers)
    print(
        f"  wall={thread_wall:.2f}s  rate={rate(len(paths), thread_wall):.1f} DNGs/min  "
        f"speedup_vs_serial={serial_wall / thread_wall:.2f}×"
    )

    print(f"process pool ({workers}, ipc=path)…")
    proc_path_wall, peak_path = run_process_pool(paths, workers, "path")
    print(
        f"  wall={proc_path_wall:.2f}s  rate={rate(len(paths), proc_path_wall):.1f} DNGs/min  "
        f"speedup_vs_serial={serial_wall / proc_path_wall:.2f}×  "
        f"speedup_vs_thread={thread_wall / proc_path_wall:.2f}×  peak_inflight={peak_path}"
    )

    print(f"process pool ({workers}, ipc=bytes)…")
    proc_bytes_wall, peak_bytes = run_process_pool(paths, workers, "bytes")
    print(
        f"  wall={proc_bytes_wall:.2f}s  rate={rate(len(paths), proc_bytes_wall):.1f} DNGs/min  "
        f"speedup_vs_serial={serial_wall / proc_bytes_wall:.2f}×  "
        f"speedup_vs_path={proc_path_wall / proc_bytes_wall:.2f}×  peak_inflight={peak_bytes}"
    )

    # Correctness spot-check: first file path-pool vs in-process
    from raw_thumb_ops import demosaic_tier_jpegs
    from thumbnails import demosaic_pool

    os.environ["AZIMUTH_DEMOSAIC_PROCESSES"] = "2"
    os.environ["AZIMUTH_DEMOSAIC_IPC"] = "path"
    demosaic_pool.reset_for_tests()
    local = demosaic_tier_jpegs(str(paths[0]), ["md", "lg"], SIZES, QUALITY)
    pooled = demosaic_pool.run_demosaic_tier_jpegs(
        str(paths[0]),
        ["md", "lg"],
        sizes=SIZES,
        thumb_quality=QUALITY,
        interactive=True,
    )
    match = local["jpegs"]["md"] == pooled["jpegs"]["md"] and local["jpegs"]["lg"] == pooled["jpegs"]["lg"]
    print(f"parity md+lg bytes match: {match}")
    demosaic_pool.shutdown_pool(wait=True)

    # Emit a compact JSON summary for benchmark history.
    print("---")
    print(
        f"SUMMARY serial_rate={rate(len(paths), serial_wall):.1f} "
        f"thread_rate={rate(len(paths), thread_wall):.1f} "
        f"proc_path_rate={rate(len(paths), proc_path_wall):.1f} "
        f"proc_bytes_rate={rate(len(paths), proc_bytes_wall):.1f} "
        f"thread_vs_serial={serial_wall / thread_wall:.2f} "
        f"proc_vs_serial={serial_wall / proc_path_wall:.2f} "
        f"proc_vs_thread={thread_wall / proc_path_wall:.2f}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
