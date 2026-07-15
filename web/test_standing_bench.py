"""Focused contracts for the opt-in standing benchmark gate."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from perf import standing_report


ROOT = Path(__file__).resolve().parents[1]


def test_regression_gate_fails_only_above_twenty_five_percent():
    baseline = {"metrics": {"within": 100.0, "over": 100.0}}
    failures = standing_report.regressions(
        {"within": 125.0, "over": 125.01},
        baseline,
    )

    assert failures == [("over", pytest.approx(0.2501))]


def test_upper_quartile_discards_one_shared_host_outlier():
    assert standing_report.upper_quartile([10.0, 11.0, 12.0, 13.0, 500.0]) == 13.0


@pytest.mark.bench
def test_standing_benchmark_matches_committed_baseline():
    subprocess.run(
        [str(ROOT / "scripts" / "bench.py"), "--check"],
        cwd=ROOT,
        check=True,
    )
