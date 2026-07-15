"""Persistence, comparison, and console reporting for standing benchmarks."""

from __future__ import annotations

import json
from pathlib import Path


REGRESSION_LIMIT = 0.25


def upper_quartile(values: list[float]) -> float:
    """Return the nearest-rank upper quartile for a small benchmark sample."""
    if not values:
        raise ValueError("upper_quartile requires at least one value")
    ordered = sorted(values)
    return ordered[round((len(ordered) - 1) * 0.75)]


def load(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def deltas(metrics: dict[str, float], reference: dict | None) -> dict[str, float | None]:
    reference_metrics = (reference or {}).get("metrics") or {}
    result: dict[str, float | None] = {}
    for name, value in metrics.items():
        prior = reference_metrics.get(name)
        result[name] = None if prior in (None, 0) else (float(value) - float(prior)) / float(prior)
    return result


def regressions(metrics: dict[str, float], baseline: dict | None) -> list[tuple[str, float]]:
    return [
        (name, change)
        for name, change in deltas(metrics, baseline).items()
        if change is not None and change > REGRESSION_LIMIT
    ]


def previous_run(run_dir: Path, *, current: Path) -> dict | None:
    candidates = sorted(
        (path for path in run_dir.glob("*.json") if path != current),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    return load(candidates[0]) if candidates else None


def _format_delta(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:+.1f}%"


def print_table(result: dict, *, previous: dict | None, baseline: dict | None) -> None:
    metrics = result["metrics"]
    prior_deltas = deltas(metrics, previous)
    baseline_deltas = deltas(metrics, baseline)
    print("\nBenchmark deltas")
    print("| Metric | Current | Previous | Baseline |")
    print("|---|---:|---:|---:|")
    for name, value in metrics.items():
        print(
            f"| {name} | {value:.2f} | {_format_delta(prior_deltas[name])} | "
            f"{_format_delta(baseline_deltas[name])} |"
        )

    print("\nTop measured offenders")
    print("| Metric | Value | Disposition |")
    print("|---|---:|---|")
    for offender in result["top_offenders"]:
        print(f"| {offender['metric']} | {offender['value']:.2f} | {offender['disposition']} |")
