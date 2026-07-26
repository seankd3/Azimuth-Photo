#!/usr/bin/env python3
"""Print archived QA scenario reliability for the latest retained runs."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from collections import defaultdict
from pathlib import Path


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Summarize archived Azimuth Photo QA reliability")
    parser.add_argument("--runs-root", type=Path, help="override the QA runs directory")
    parser.add_argument("--window", type=int, default=20, help="number of latest runs to inspect (default: 20)")
    return parser


def _default_runs_root() -> Path:
    scratch = Path(
        os.environ.get("AZIMUTH_QA_SCRATCH", Path(tempfile.gettempdir()) / "azimuth-photo" / "qa-harness")
    )
    return scratch.resolve() / "runs"


def _reports(root: Path, window: int) -> list[tuple[Path, dict]]:
    reports = []
    for path in sorted(root.glob("*/report.json"))[-window:]:
        try:
            reports.append((path, json.loads(path.read_text(encoding="utf-8"))))
        except json.JSONDecodeError:
            print(f"Skipping invalid report: {path}")
    return reports


def main() -> int:
    args = _parser().parse_args()
    root = args.runs_root or _default_runs_root()
    reports = _reports(root, args.window)
    if not reports:
        print(f"No archived QA reports in {root}")
        return 0

    totals: dict[str, dict[str, int]] = defaultdict(lambda: {"runs": 0, "failures": 0, "flakes": 0})
    for _path, report in reports:
        for scenario in report.get("scenarios", []):
            row = totals[scenario["name"]]
            row["runs"] += 1
            row["failures"] += scenario.get("status") == "FAIL"
            row["flakes"] += scenario.get("status") == "FLAKY"

    print(f"QA flake trend: {len(reports)} archived run(s), latest window {args.window}")
    print(f"{'scenario':<32} {'runs':>4} {'failures':>8} {'flakes':>6} {'flake rate':>11}")
    for name, row in sorted(totals.items()):
        rate = row["flakes"] / row["runs"]
        print(f"{name:<32} {row['runs']:>4} {row['failures']:>8} {row['flakes']:>6} {rate:>10.1%}")
        if row["flakes"] >= 3:
            print(f"QUARANTINE: {name} was flaky {row['flakes']} times in the last {len(reports)} runs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
