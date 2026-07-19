"""Bare contracts for interactive-latency stats math."""

from __future__ import annotations

import pytest

from perf import interactive


def test_percentile_nearest_rank_on_known_series():
    samples = [10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0, 100.0]
    assert interactive.percentile(samples, 0.50) == 50.0
    assert interactive.percentile(samples, 0.95) == 100.0
    assert interactive.percentile(samples, 0.99) == 100.0
    assert interactive.percentile(samples, 0.0) == 10.0
    assert interactive.percentile(samples, 1.0) == 100.0


def test_percentile_rejects_empty():
    with pytest.raises(ValueError):
        interactive.percentile([], 0.5)


def test_summarize_reports_p50_p95_p99_in_ms():
    # One second each → 1000 ms; percentiles collapse on a flat series.
    summary = interactive.summarize([1.0, 1.0, 1.0, 1.0])
    assert summary["n"] == 4
    assert summary["p50_ms"] == 1000.0
    assert summary["p95_ms"] == 1000.0
    assert summary["p99_ms"] == 1000.0


def test_warmup_exclusion_drops_leading_samples():
    samples = [0.01, 0.02, 0.03, 0.04, 0.05]
    kept = interactive.exclude_warmup(samples, warmup=2)
    assert kept == [0.03, 0.04, 0.05]
    assert interactive.exclude_warmup(samples, warmup=0) == samples
    assert interactive.exclude_warmup(samples, warmup=5) == []
    assert interactive.exclude_warmup(samples, warmup=99) == []


def test_warmup_exclusion_rejects_negative():
    with pytest.raises(ValueError):
        interactive.exclude_warmup([1.0], warmup=-1)


def test_initial_budgets_are_documented_targets():
    assert interactive.BUDGETS_MS["grid"]["p95"] == 150.0
    assert interactive.BUDGETS_MS["thumb_sm"]["p95"] == 80.0
