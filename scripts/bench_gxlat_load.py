#!/usr/bin/env python3
"""In-process gxlat latency probe (uses BackendTestCase catalog, not prod).

Measures interactive rankings + cached thumb latency while a synthetic bulk
HDD storm occupies the default threadpool and forces slow source lstats.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
sys.path.insert(0, str(WEB))

os.environ.setdefault("AZIMUTH_SMOKE_MODE", "1")


def percentile(samples: list[float], fraction: float) -> float:
    ordered = sorted(samples)
    return ordered[round((len(ordered) - 1) * fraction)]


def summarize(samples_s: list[float]) -> dict:
    ms = [s * 1000.0 for s in samples_s]
    return {
        "p50_ms": round(percentile(ms, 0.50), 2),
        "p95_ms": round(percentile(ms, 0.95), 2),
        "mean_ms": round(statistics.fmean(ms), 2),
        "max_ms": round(max(ms), 2),
        "n": len(ms),
    }


async def main_async(loops: int, label: str) -> dict:
    from unittest import mock

    from PIL import Image
    from anyio import to_thread

    import thumbnails
    from features.library import service as library_service
    from features.media import routes as media_routes
    from test_support import BackendTestCase, HeaderRequest

    case = BackendTestCase()
    await case.asyncSetUp()
    try:
        source = await case._source("gxlat-lib")
        source_path = source["path"] if isinstance(source, dict) and "path" in source else os.path.join(case.tempdir.name, "gxlat-lib")
        image_ids: list[int] = []
        files: list[str] = []
        for i in range(24):
            filename = f"gxlat-{i}.jpg"
            path = os.path.join(source_path, filename)
            Image.new("RGB", (128, 96), color=(i * 3, 20, 90)).save(path, quality=90)
            files.append(path)
            image_ids.append(await case._image(source["id"], filename))

        # Warm sm cache so "cached thumb" is a real SSD hit.
        for image_id, path in zip(image_ids, files):
            await thumbnails.get_thumbnail(path, "sm", image_id)

        slow_stat_seconds = 0.25

        def slow_inspect(path, source_root=""):
            time.sleep(slow_stat_seconds)
            return "available", None

        stop = asyncio.Event()

        async def storm():
            limiter = to_thread.current_default_thread_limiter()
            n = max(8, int(limiter.total_tokens) - 6)

            def burn():
                time.sleep(0.05)
                for path in files:
                    try:
                        os.stat(path)
                    except OSError:
                        pass

            while not stop.is_set():
                await asyncio.gather(
                    *[asyncio.get_running_loop().run_in_executor(None, burn) for _ in range(n)]
                )

        storm_task = asyncio.create_task(storm())
        await asyncio.sleep(0.15)

        rankings_s: list[float] = []
        thumb_s: list[float] = []
        cold_s: list[float] = []

        with mock.patch("features.media.routes.inspect_source_file", side_effect=slow_inspect):
            for i in range(loops):
                t0 = time.perf_counter()
                payload = await library_service.api_rankings_impl(
                    limit=20,
                    offset=0,
                    sort="elo",
                    orientation="",
                    compared="",
                    min_stars=0,
                    folder="",
                    flag="",
                    date_taken="",
                    file_type="",
                    camera="",
                    lens="",
                    tag="",
                    q="",
                    deep=False,
                    people="",
                    import_batch=0,
                    stacks="expanded",
                    ids="",
                    collection_id=0,
                    exclude_sources=[],
                    request=None,
                )
                rankings_s.append(time.perf_counter() - t0)
                assert isinstance(payload, dict)

                image_id = image_ids[i % len(image_ids)]
                t0 = time.perf_counter()
                response = await media_routes.serve_thumbnail(HeaderRequest(), "sm", image_id)
                thumb_s.append(time.perf_counter() - t0)
                assert response.status_code in {200, 304}, response.status_code

            # Cold md while storm + slow inspect are active. Hold the bulk gate
            # so the request path skips interactive lstat (seek contention bypass).
            from core import hdd_governor

            hdd_governor.reset_for_tests(1)
            with hdd_governor.bulk_hdd_slot_sync():
                for i in range(min(8, loops)):
                    image_id = image_ids[i % len(image_ids)]
                    t0 = time.perf_counter()
                    response = await media_routes.serve_thumbnail(HeaderRequest(), "md", image_id)
                    cold_s.append(time.perf_counter() - t0)
                    assert response.status_code in {200, 204, 304, 410}, response.status_code

        stop.set()
        storm_task.cancel()
        try:
            await storm_task
        except asyncio.CancelledError:
            pass

        return {
            "label": label,
            "slow_inspect_s": slow_stat_seconds,
            "under_load": {
                "rankings": summarize(rankings_s),
                "thumb_sm_cached": summarize(thumb_s),
                "thumb_md_cold": summarize(cold_s),
            },
        }
    finally:
        await case.asyncTearDown()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--loops", type=int, default=16)
    parser.add_argument("--label", default="run")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()
    result = asyncio.run(main_async(args.loops, args.label))
    text = json.dumps(result, indent=2)
    print(text)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
