"""Focused contracts for the opt-in standing benchmark gate."""

from __future__ import annotations

import json
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


def test_regression_limit_env_override_widens_the_gate(monkeypatch):
    baseline = {"metrics": {"metric": 100.0}}
    monkeypatch.setenv("AZIMUTH_BENCH_REGRESSION_LIMIT", "2.0")

    assert standing_report.regressions({"metric": 299.0}, baseline) == []
    assert standing_report.regressions({"metric": 301.0}, baseline) == [
        ("metric", pytest.approx(2.01))
    ]


def test_compact_record_appends_one_parseable_line_per_run(tmp_path):
    result = {
        "measured_at": "2026-07-31T00:00:00Z",
        "git_sha": "abcdef123456",
        "details": {"profile": "qa-5000-visible", "active_images": 5003},
        "metrics": {"server_boot_first_200_ms": 3700.0},
        "top_offenders": [],
    }
    record = standing_report.compact_record(result)
    assert record == {
        "measured_at": "2026-07-31T00:00:00Z",
        "git_sha": "abcdef123456",
        "profile": "qa-5000-visible",
        "metrics": {"server_boot_first_200_ms": 3700.0},
    }

    history = tmp_path / "history.jsonl"
    standing_report.append_history(history, record)
    standing_report.append_history(history, record)
    lines = history.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0]) == record


def test_committed_history_rows_are_complete():
    history = ROOT / "web" / "perf" / "history.jsonl"
    rows = [
        json.loads(line)
        for line in history.read_text(encoding="utf-8").splitlines()
        if line
    ]
    assert rows, "web/perf/history.jsonl must hold at least the seeded baseline row"
    for row in rows:
        assert row["git_sha"] and row["measured_at"] and row["profile"]
        assert isinstance(row["metrics"], dict) and row["metrics"]


def test_ci_invokes_the_standing_bench_gate():
    # The gate exists so a perf regression cannot merge; a CI edit that drops
    # the invocation must fail loudly here, not silently stop measuring.
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "scripts/bench.py --check" in ci


def test_upper_quartile_discards_one_shared_host_outlier():
    assert standing_report.upper_quartile([10.0, 11.0, 12.0, 13.0, 500.0]) == 13.0


@pytest.mark.bench
def test_standing_benchmark_matches_committed_baseline():
    subprocess.run(
        [str(ROOT / "scripts" / "bench.py"), "--check"],
        cwd=ROOT,
        check=True,
    )
