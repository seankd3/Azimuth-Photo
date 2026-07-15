#!/usr/bin/env python3
"""Run the standing Azimuth Photo performance benchmark and regression gate."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
RUN_DIR = ROOT / "bench-runs"
BASELINE = ROOT / "baseline.json"
SCRATCH = Path("/mnt/expansion/tmp/azimuth-bench")
VENV_PYTHON = WEB / ".venv" / "bin" / "python"
BASELINE_SAMPLE_RUNS = 5

if VENV_PYTHON.is_file() and Path(sys.executable).resolve() != VENV_PYTHON.resolve():
    os.execv(str(VENV_PYTHON), [str(VENV_PYTHON), __file__, *sys.argv[1:]])

sys.path.insert(0, str(WEB))

from perf import standing  # noqa: E402
from perf import standing_report as report  # noqa: E402


def _sha() -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "--short=12", "HEAD"], cwd=ROOT, text=True
    ).strip()


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail when a baseline metric regresses by more than 25%%")
    parser.add_argument("--write-baseline", action="store_true", help="replace baseline.json with this fixture run")
    parser.add_argument("--iterations", type=int, default=standing.DEFAULT_ITERATIONS)
    return parser.parse_args()


def main() -> int:
    args = _args()
    if args.iterations < 3:
        raise SystemExit("--iterations must be at least 3")
    if args.write_baseline and os.environ.get("PHOTOARCHIVE_BENCH_URL"):
        raise SystemExit("baseline.json must come from the isolated fixture, not a real URL")

    measured_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    sha = _sha()
    samples = [standing.run(scratch=SCRATCH, iterations=args.iterations)]
    if args.write_baseline:
        samples.extend(
            standing.run(scratch=SCRATCH, iterations=args.iterations)
            for _ in range(BASELINE_SAMPLE_RUNS - 1)
        )
    metrics = {
        name: report.upper_quartile([
            sample_metrics[name] for sample_metrics, _details in samples
        ])
        for name in samples[0][0]
    }
    details = dict(samples[-1][1])
    if args.write_baseline:
        details.update({
            "baseline_sample_runs": BASELINE_SAMPLE_RUNS,
            "baseline_strategy": "per-metric upper quartile",
        })
    latency_metrics = [(name, value) for name, value in metrics.items() if name.endswith("_ms")]
    top = sorted(latency_metrics, key=lambda item: item[1], reverse=True)[:3]
    result = {
        "schema_version": 1,
        "measured_at": measured_at,
        "git_sha": sha,
        "details": details,
        "metrics": metrics,
        "top_offenders": [
            {
                "metric": name,
                "value": value,
                "disposition": (
                    "optimized in Q3" if name == "collection_suggestions_cold_ms" else "profile before changing"
                ),
            }
            for name, value in top
        ],
    }

    RUN_DIR.mkdir(parents=True, exist_ok=True)
    output = RUN_DIR / f"{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{sha}.json"
    previous = report.previous_run(RUN_DIR, current=output)
    baseline = report.load(BASELINE)
    output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    if args.write_baseline:
        BASELINE.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        baseline = result
    report.print_table(result, previous=previous, baseline=baseline)
    print(f"\nWrote {output.relative_to(ROOT)}")
    if args.write_baseline:
        print(f"Wrote {BASELINE.relative_to(ROOT)}")

    failures = report.regressions(metrics, baseline)
    if args.check and baseline is None:
        print("\nFAIL: baseline.json is missing or invalid", file=sys.stderr)
        return 2
    if args.check and failures:
        print("\nFAIL: >25% benchmark regressions", file=sys.stderr)
        for name, change in failures:
            print(f"- {name}: {change * 100:+.1f}%", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
