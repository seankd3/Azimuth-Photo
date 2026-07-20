#!/usr/bin/env python3
"""Azimuth performance benchmark — reproducible hot-path timings for the bottleneck loop.

Runs against the live app (default http://100.102.150.104:8000) and the .thumbcache.
Emits a one-line summary, appends a JSONL row to bench/history.jsonl, and (with --md)
a row to docs/BENCHMARKS.md. Pass --label "what changed" to tag the run.

Usage:
  scripts/perfbench.py --label "baseline post mem-flap fix" --md
  scripts/perfbench.py --label "before X"   # capture, note the numbers
  # ...make change, deploy...
  scripts/perfbench.py --label "after X" --md

Metrics are interactive p50/p95 (ms) over N samples + background preview rate.
Interactive latency is the comparable-over-time signal; preview rate depends on load.
"""
import argparse
import json
import os
import statistics
import subprocess
import time
import urllib.request

HUB = os.environ.get("PERFBENCH_HUB", "http://100.102.150.104:8000")
THUMBCACHE = os.path.expanduser("~/Projects/photo-archive/web/.thumbcache")
DB = os.path.expanduser("~/Projects/photo-archive/web/photoarchive.db")
REPO = os.path.expanduser("~/Projects/photo-archive")


def _time_get(path: str, timeout: float = 15.0) -> float | None:
    url = HUB + path
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            r.read()
        return (time.perf_counter() - t0) * 1000.0
    except Exception:
        return None


def _stat(path: str, samples: int = 20, warmup: int = 3) -> dict:
    for _ in range(warmup):
        _time_get(path)
    xs = [t for _ in range(samples) if (t := _time_get(path)) is not None]
    if not xs:
        return {"p50": None, "p95": None, "n": 0}
    xs.sort()
    p95 = xs[min(len(xs) - 1, int(round(0.95 * (len(xs) - 1))))]
    return {"p50": round(statistics.median(xs), 1), "p95": round(p95, 1), "n": len(xs)}


def _sample_thumb_id() -> int | None:
    try:
        out = subprocess.run(
            ["sqlite3", "-readonly", DB,
             "SELECT image_id FROM cache_entries WHERE size='sm' LIMIT 1"],
            capture_output=True, text=True, timeout=10,
        ).stdout.strip()
        return int(out) if out else None
    except Exception:
        return None


def _count_thumbs() -> int:
    n = 0
    for tier in ("sm", "md", "lg"):
        d = os.path.join(THUMBCACHE, tier)
        if os.path.isdir(d):
            for _root, _dirs, files in os.walk(d):
                n += len(files)
    return n


def _preview_rate(window_s: int) -> int:
    t0 = _count_thumbs()
    time.sleep(window_s)
    t1 = _count_thumbs()
    return round((t1 - t0) * 60 / window_s)


def _git_commit() -> str:
    try:
        return subprocess.run(["git", "-C", REPO, "rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception:
        return "?"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", default="")
    ap.add_argument("--samples", type=int, default=20)
    ap.add_argument("--rate-window", type=int, default=60)
    ap.add_argument("--md", action="store_true", help="append a row to docs/BENCHMARKS.md")
    ap.add_argument("--epoch", type=float, default=None, help="unix ts (scripts have no clock)")
    args = ap.parse_args()

    tid = _sample_thumb_id()
    metrics = {
        "rankings": _stat("/api/rankings?limit=100", args.samples),
        "counts": _stat("/api/counts", args.samples),
        "folders_tree": _stat("/api/folders/tree", args.samples),
    }
    if tid is not None:
        metrics["warm_thumb_sm"] = _stat(f"/api/thumb/sm/{tid}", args.samples)
    preview_rate = _preview_rate(args.rate_window)

    ts = args.epoch if args.epoch is not None else 0
    commit = _git_commit()
    row = {
        "epoch": ts, "commit": commit, "label": args.label,
        "preview_files_per_min": preview_rate,
        "metrics_ms": metrics,
    }

    os.makedirs(os.path.join(REPO, "bench"), exist_ok=True)
    with open(os.path.join(REPO, "bench", "history.jsonl"), "a") as fh:
        fh.write(json.dumps(row) + "\n")

    def cell(m):
        v = metrics.get(m)
        return f"{v['p50']}/{v['p95']}" if v and v["p50"] is not None else "—"

    line = (f"commit={commit} label={args.label!r} | preview={preview_rate}/min | "
            f"rankings={cell('rankings')} counts={cell('counts')} "
            f"folders={cell('folders_tree')} warmthumb={cell('warm_thumb_sm')} (p50/p95 ms)")
    print(line)

    if args.md:
        md = os.path.join(REPO, "docs", "BENCHMARKS.md")
        os.makedirs(os.path.dirname(md), exist_ok=True)
        header = ("# Azimuth Benchmarks\n\n"
                  "Reproducible hot-path timings tracked over time (newest first). "
                  "Run `scripts/perfbench.py --label \"...\" --md`. p50/p95 in ms; preview = files/min.\n\n"
                  "| commit | label | preview/min | rankings | counts | folders | warm thumb |\n"
                  "|--------|-------|------------:|---------:|-------:|--------:|-----------:|\n")
        newrow = (f"| `{commit}` | {args.label} | {preview_rate} | {cell('rankings')} | "
                  f"{cell('counts')} | {cell('folders_tree')} | {cell('warm_thumb_sm')} |\n")
        if os.path.exists(md):
            with open(md) as fh:
                content = fh.read()
            if content.startswith("# Azimuth Benchmarks"):
                idx = content.find("|--------|")
                nl = content.find("\n", idx)
                content = content[:nl + 1] + newrow + content[nl + 1:]
            else:
                content = header + newrow
        else:
            content = header + newrow
        with open(md, "w") as fh:
            fh.write(content)
        print(f"appended row to {md}")


if __name__ == "__main__":
    main()
