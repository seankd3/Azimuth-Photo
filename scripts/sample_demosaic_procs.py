#!/usr/bin/env python3
"""Sample demosaic worker processes while the pool is busy."""

from __future__ import annotations

import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "web"))

os.environ["AZIMUTH_DEMOSAIC_PROCESSES"] = "6"
os.environ["AZIMUTH_DEMOSAIC_IPC"] = "path"


def _child_rss_mb(parent_pid: int) -> list[tuple[int, float]]:
    rows: list[tuple[int, float]] = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            status = (entry / "status").read_text()
            ppid = int(
                next(
                    line.split(":", 1)[1]
                    for line in status.splitlines()
                    if line.startswith("PPid:")
                ).strip()
            )
            if ppid != parent_pid:
                continue
            rss_kb = int(
                next(
                    line.split(":", 1)[1].split()[0]
                    for line in status.splitlines()
                    if line.startswith("VmRSS:")
                ).strip()
            )
            rows.append((int(entry.name), rss_kb / 1024.0))
        except (OSError, StopIteration, ValueError):
            continue
    return rows


def main() -> int:
    from thumbnails import demosaic_pool

    demosaic_pool.reset_for_tests()
    demosaic_pool.ensure_pool()
    parent = os.getpid()
    paths = sorted(Path("/mnt/expansion/Photos/RAWS/2022/2022-05-09").glob("*R5*.DNG"))[:12]

    def one(path: Path) -> None:
        demosaic_pool.run_demosaic_tier_jpegs(
            str(path),
            ["md", "lg"],
            sizes={"md": 1920, "lg": 3840},
            thumb_quality=92,
            interactive=True,
        )

    peak_n = 0
    peak_rss = 0.0
    samples: list[str] = []

    def sampler() -> None:
        nonlocal peak_n, peak_rss
        deadline = time.time() + 12
        while time.time() < deadline:
            direct = _child_rss_mb(parent)
            nested: list[tuple[int, float]] = []
            for pid, _rss in direct:
                nested.extend(_child_rss_mb(pid))
            by_pid = {pid: rss for pid, rss in (direct + nested)}
            n = len(by_pid)
            total = sum(by_pid.values())
            peak_n = max(peak_n, n)
            peak_rss = max(peak_rss, total)
            if n:
                top = sorted(by_pid.items(), key=lambda item: item[1], reverse=True)[:6]
                samples.append(
                    f"{time.strftime('%H:%M:%S')} children={n} total_rss_mb={total:.0f} "
                    + ", ".join(f"{pid}:{rss:.0f}MB" for pid, rss in top)
                )
            time.sleep(0.25)

    import threading

    thread = threading.Thread(target=sampler, daemon=True)
    thread.start()
    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(one, paths))
    wall = time.perf_counter() - started
    demosaic_pool.shutdown_pool(wait=True)
    print(f"wall={wall:.2f}s rate={len(paths) / wall * 60:.1f}/min")
    print(f"peak_child_procs={peak_n} peak_child_rss_mb={peak_rss:.0f}")
    print("samples:")
    for line in samples[:15]:
        print(" ", line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
