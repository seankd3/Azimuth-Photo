#!/usr/bin/env python3
"""Pure rawpy.postprocess: threads vs processes (GIL / OpenMP interaction)."""

from __future__ import annotations

import os
import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from pathlib import Path
import multiprocessing as mp


def postprocess(path: str):
    import rawpy

    with rawpy.imread(path) as raw:
        rgb = raw.postprocess(
            use_camera_wb=True,
            no_auto_bright=True,
            half_size=True,
            demosaic_algorithm=rawpy.DemosaicAlgorithm.LINEAR,
        )
    return rgb.shape


def main() -> None:
    root = Path(os.environ.get("AZIMUTH_BENCH_RAW_ROOT", Path.home() / "Pictures"))
    paths = [
        str(p)
        for p in sorted(root.rglob("*.DNG"))[:12]
    ]
    for path in paths:
        with open(path, "rb") as handle:
            handle.read()

    print("OMP_NUM_THREADS", os.environ.get("OMP_NUM_THREADS", "<unset>"))
    print("n", len(paths))

    started = time.perf_counter()
    for path in paths:
        postprocess(path)
    serial = time.perf_counter() - started
    print(f"serial wall={serial:.2f}s rate={len(paths) / serial * 60:.1f}/min")

    started = time.perf_counter()
    with ThreadPoolExecutor(6) as pool:
        list(pool.map(postprocess, paths))
    threaded = time.perf_counter() - started
    print(
        f"thread6 wall={threaded:.2f}s rate={len(paths) / threaded * 60:.1f}/min "
        f"speedup={serial / threaded:.2f}x"
    )

    ctx = mp.get_context("spawn")
    started = time.perf_counter()
    with ProcessPoolExecutor(6, mp_context=ctx) as pool:
        list(pool.map(postprocess, paths))
    processed = time.perf_counter() - started
    print(
        f"process6 wall={processed:.2f}s rate={len(paths) / processed * 60:.1f}/min "
        f"speedup={serial / processed:.2f}x vs_thread={threaded / processed:.2f}x"
    )


if __name__ == "__main__":
    main()
