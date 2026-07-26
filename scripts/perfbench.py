#!/usr/bin/env python3
"""Azimuth performance benchmark — reproducible hot-path timings for the bottleneck loop.

Runs against the live app (default http://100.102.150.104:8000) and its
configured preview cache. Emits a one-line summary and appends a JSONL row
outside the checkout. With ``--md``, it also updates a Markdown report beside
that history file. Pass ``--label "what changed"`` to tag the run.

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
from pathlib import Path
import sqlite3
import statistics
import subprocess
import sys
import time
import urllib.request

HUB = os.environ.get("PERFBENCH_HUB", "http://100.102.150.104:8000")
REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "web"))
from core.runtime_paths import resolve_runtime_paths  # noqa: E402

RUNTIME = resolve_runtime_paths()
THUMBCACHE = Path(RUNTIME.thumb_cache_dir)
DB = Path(RUNTIME.catalog_db)
HISTORY = Path(
    os.environ.get(
        "AZIMUTH_PERFBENCH_HISTORY",
        str(Path(RUNTIME.state_dir) / "benchmarks" / "perfbench.jsonl"),
    )
)


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
        uri = f"{DB.resolve().as_uri()}?mode=ro"
        with sqlite3.connect(uri, uri=True, timeout=10) as conn:
            row = conn.execute(
                "SELECT image_id FROM cache_entries WHERE size='sm' LIMIT 1"
            ).fetchone()
        return int(row[0]) if row else None
    except (OSError, sqlite3.Error, TypeError, ValueError):
        return None


def _count_thumbs() -> int:
    n = 0
    for tier in ("sm", "md", "lg"):
        directory = THUMBCACHE / tier
        if directory.is_dir():
            for _root, _dirs, files in os.walk(directory):
                n += len(files)
    return n


def _preview_rate(window_s: int) -> int:
    t0 = _count_thumbs()
    time.sleep(window_s)
    t1 = _count_thumbs()
    return round((t1 - t0) * 60 / window_s)


def _git_commit() -> str:
    try:
        return subprocess.run(["git", "-C", str(REPO), "rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception:
        return "?"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", default="")
    ap.add_argument("--samples", type=int, default=20)
    ap.add_argument("--rate-window", type=int, default=60)
    ap.add_argument("--md", action="store_true", help="update the external Markdown history")
    ap.add_argument("--epoch", type=float, default=None, help="override the Unix timestamp")
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

    ts = args.epoch if args.epoch is not None else time.time()
    commit = _git_commit()
    try:
        load1 = round(os.getloadavg()[0],1)
    except Exception:
        load1 = None
    row = {
        "epoch": ts, "commit": commit, "label": args.label,
        "preview_files_per_min": preview_rate,
        "metrics_ms": metrics,
        "load1": load1,
    }

    HISTORY.parent.mkdir(parents=True, exist_ok=True)
    with HISTORY.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row) + "\n")

    def cell(m):
        v = metrics.get(m)
        return f"{v['p50']}/{v['p95']}" if v and v["p50"] is not None else "—"

    line = (f"commit={commit} label={args.label!r} | preview={preview_rate}/min | "
            f"rankings={cell('rankings')} counts={cell('counts')} "
            f"folders={cell('folders_tree')} warmthumb={cell('warm_thumb_sm')} (p50/p95 ms) load={load1}")
    print(line)

    if args.md:
        md = HISTORY.with_suffix(".md")
        header = ("# Azimuth Benchmarks\n\n"
                  "Reproducible hot-path timings tracked over time (newest first). "
                  "Run `scripts/perfbench.py --label \"...\" --md`. "
                  "p50/p95 in ms; preview = files/min.\n\n"
                  "| commit | label | preview/min | rankings | counts | folders | warm thumb |\n"
                  "|--------|-------|------------:|---------:|-------:|--------:|-----------:|\n")
        newrow = (f"| `{commit}` | {args.label} | {preview_rate} | {cell('rankings')} | "
                  f"{cell('counts')} | {cell('folders_tree')} | {cell('warm_thumb_sm')} |\n")
        if md.exists():
            content = md.read_text(encoding="utf-8")
            if content.startswith("# Azimuth Benchmarks"):
                idx = content.find("|--------|")
                nl = content.find("\n", idx)
                content = content[:nl + 1] + newrow + content[nl + 1:]
            else:
                content = header + newrow
        else:
            content = header + newrow
        md.write_text(content, encoding="utf-8")
        print(f"appended row to {md}")


if __name__ == "__main__":
    main()
