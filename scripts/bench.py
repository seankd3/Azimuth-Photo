#!/usr/bin/env python3
"""Run the standing Azimuth Photo performance benchmark and regression gate."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
RUN_DIR = Path(
    os.environ.get(
        "AZIMUTH_BENCH_RUNS",
        str(Path.home() / ".local" / "state" / "azimuth-photo" / "benchmarks"),
    )
)
BASELINE = ROOT / "web" / "perf" / "baseline.json"
REPO_HISTORY = ROOT / "web" / "perf" / "history.jsonl"
SCRATCH = Path(
    os.environ.get(
        "AZIMUTH_BENCH_SCRATCH",
        str(Path(tempfile.gettempdir()) / "azimuth-bench"),
    )
)
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
    parser.add_argument(
        "--write-baseline",
        action="store_true",
        help="replace web/perf/baseline.json with this fixture run",
    )
    parser.add_argument("--iterations", type=int, default=standing.DEFAULT_ITERATIONS)
    parser.add_argument(
        "--record",
        action="store_true",
        help="append a compact metrics row to web/perf/history.jsonl to commit with the change",
    )
    parser.add_argument(
        "--trend",
        action="store_true",
        help="print KPI history from the runtime benchmark directory and exit",
    )
    return parser.parse_args()


def _trend() -> int:
    """Per-metric KPI history across every recorded run, grouped by profile."""
    runs = []
    for path in sorted(RUN_DIR.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        runs.append(data)
    if not runs:
        print("no runs in", RUN_DIR)
        return 1
    by_profile: dict[str, list[dict]] = {}
    for run_data in runs:
        profile = str((run_data.get("details") or {}).get("profile") or "unknown")
        by_profile.setdefault(profile, []).append(run_data)
    for profile, series in by_profile.items():
        print(f"\n== KPI trend: {profile} ({len(series)} runs) ==")
        metric_names = sorted({name for r in series for name in (r.get("metrics") or {})})
        header = f"{'metric':38}" + "".join(
            f"{(r.get('measured_at') or '')[5:16]:>13}" for r in series[-8:]
        )
        print(header)
        print(f"{'':38}" + "".join(f"{(r.get('git_sha') or '')[:9]:>13}" for r in series[-8:]))
        for name in metric_names:
            row = f"{name:38}"
            values = [(r.get("metrics") or {}).get(name) for r in series[-8:]]
            for value in values:
                row += f"{value:>13.1f}" if isinstance(value, (int, float)) else f"{'-':>13}"
            numeric = [v for v in values if isinstance(v, (int, float))]
            if len(numeric) >= 2 and numeric[0]:
                change = (numeric[-1] - numeric[0]) / numeric[0] * 100.0
                row += f"   {change:+6.1f}%"
            print(row)
    return 0


def main() -> int:
    args = _args()
    if args.trend:
        return _trend()
    if args.iterations < 3:
        raise SystemExit("--iterations must be at least 3")
    if args.write_baseline and os.environ.get("AZIMUTH_BENCH_URL"):
        raise SystemExit("web/perf/baseline.json must come from the isolated fixture")

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
    print(f"\nWrote {output}")
    if args.write_baseline:
        print(f"Wrote {BASELINE.relative_to(ROOT)}")
    if args.record:
        report.append_history(REPO_HISTORY, report.compact_record(result))
        print(f"Appended {REPO_HISTORY.relative_to(ROOT)}")

    failures = report.regressions(metrics, baseline)
    if args.check and baseline is None:
        print("\nFAIL: web/perf/baseline.json is missing or invalid", file=sys.stderr)
        return 2
    if args.check and failures:
        print("\nFAIL: >25% benchmark regressions", file=sys.stderr)
        for name, change in failures:
            print(f"- {name}: {change * 100:+.1f}%", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
