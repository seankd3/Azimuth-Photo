"""Interactive latency under load — the number the speed doctrine gets held to.

Read-only GET probes only. Safe to point at prod.

Load policy
-----------
`--with-load` does **not** POST to pregen/caption start endpoints (those mutate
worker state and are unsafe on a shared/prod instance). Instead the second phase
runs the identical browse mix while polling GET `/api/cache/pregen/status` and
GET `/api/captions/status`, recording whatever bulk work is naturally active.
"""

from __future__ import annotations

import json
import statistics
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Any

import httpx


# Initial LAN budgets — to be ratified. See docs/PERF_BUDGETS.md.
BUDGETS_MS = {
    "grid": {"p95": 150.0},
    "thumb_sm": {"p95": 80.0},
}

DEFAULT_LOOPS = 10
DEFAULT_WARMUP = 2
THUMBS_PER_CYCLE = 20
SEARCH_QUERY = "sunset"
HISTORY_SCHEMA = 1

# One browse cycle: grid page, 20 sm thumbs, one md preview, one search, one rankings.
CYCLE_WEIGHTS = (
    ("grid", 1),
    ("thumb_sm", THUMBS_PER_CYCLE),
    ("thumb_md", 1),
    ("search", 1),
    ("rankings", 1),
)


def percentile(samples: list[float], fraction: float) -> float:
    """Nearest-rank percentile on a non-empty sample list (fraction in [0, 1])."""

    if not samples:
        raise ValueError("percentile requires at least one sample")
    if not 0.0 <= fraction <= 1.0:
        raise ValueError("fraction must be between 0 and 1")
    ordered = sorted(samples)
    index = round((len(ordered) - 1) * fraction)
    return ordered[index]


def summarize(samples: list[float]) -> dict[str, float]:
    """p50/p95/p99 in milliseconds from raw second timings."""

    if not samples:
        return {"p50_ms": 0.0, "p95_ms": 0.0, "p99_ms": 0.0, "n": 0}
    ms = [value * 1000.0 for value in samples]
    return {
        "p50_ms": round(percentile(ms, 0.50), 2),
        "p95_ms": round(percentile(ms, 0.95), 2),
        "p99_ms": round(percentile(ms, 0.99), 2),
        "n": len(ms),
        "mean_ms": round(statistics.fmean(ms), 2),
    }


def exclude_warmup(samples: list[float], warmup: int) -> list[float]:
    """Drop the first ``warmup`` samples; warm-up never enters reported stats."""

    if warmup < 0:
        raise ValueError("warmup must be >= 0")
    if warmup >= len(samples):
        return []
    return samples[warmup:]


@dataclass
class PhaseResult:
    name: str
    classes: dict[str, dict[str, float]]
    bulk_state: dict[str, Any] = field(default_factory=dict)
    budget_ok: bool = True
    budget_failures: list[str] = field(default_factory=list)


class InteractiveClient:
    """GET-only HTTP session for interactive browse probes."""

    def __init__(self, base_url: str, *, timeout: float = 60.0) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = httpx.Client(base_url=self.base_url, timeout=timeout)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "InteractiveClient":
        return self

    def __exit__(self, *_args) -> None:
        self.close()

    def get(self, path: str) -> tuple[int, bytes, float]:
        started = time.perf_counter()
        response = self._client.get(path)
        return response.status_code, response.content, time.perf_counter() - started

    def get_json(self, path: str) -> tuple[int, dict[str, Any], float]:
        status, body, elapsed = self.get(path)
        try:
            payload = json.loads(body or b"{}")
        except json.JSONDecodeError as error:
            raise RuntimeError(f"{path} returned invalid JSON ({status})") from error
        if not isinstance(payload, dict):
            raise RuntimeError(f"{path} returned non-object JSON ({status})")
        return status, payload, elapsed


def resolve_image_ids(client: InteractiveClient, *, limit: int = 40) -> list[int]:
    status, payload, _elapsed = client.get_json("/api/rankings?limit=100&sort=elo")
    if status != 200:
        raise RuntimeError(f"grid probe for image ids returned {status}")
    ids = [int(image["id"]) for image in (payload.get("images") or []) if "id" in image]
    if len(ids) < 2:
        raise RuntimeError("catalog returned fewer than 2 images; need a browsable library")
    return ids[: max(limit, THUMBS_PER_CYCLE + 1)]


def sample_bulk_state(client: InteractiveClient) -> dict[str, Any]:
    """Read-only snapshot of bulk workers (pregen + captions)."""

    state: dict[str, Any] = {"sampled_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    try:
        status, pregen, _elapsed = client.get_json("/api/cache/pregen/status")
        if status == 200:
            state["pregen"] = {
                "state": pregen.get("state"),
                "message": pregen.get("message"),
                "generated_this_session": pregen.get("generated_this_session"),
                "priority_scope": pregen.get("priority_scope"),
            }
        else:
            state["pregen"] = {"error": f"http {status}"}
    except (OSError, RuntimeError, httpx.HTTPError) as error:
        state["pregen"] = {"error": str(error)}
    try:
        status, captions, _elapsed = client.get_json("/api/captions/status")
        if status == 200:
            worker = captions.get("worker") or {}
            state["captions"] = {
                "state": worker.get("state") or captions.get("state"),
                "message": worker.get("message") or captions.get("message"),
                "counts": captions.get("counts"),
            }
        else:
            state["captions"] = {"error": f"http {status}"}
    except (OSError, RuntimeError, httpx.HTTPError) as error:
        state["captions"] = {"error": str(error)}
    return state


def _bulk_active(snapshot: dict[str, Any]) -> bool:
    pregen_state = str(((snapshot.get("pregen") or {}).get("state") or "")).lower()
    caption_state = str(((snapshot.get("captions") or {}).get("state") or "")).lower()
    active = {"running", "working", "generating", "scanning", "active", "busy"}
    return pregen_state in active or caption_state in active


def run_cycle(
    client: InteractiveClient,
    image_ids: list[int],
    *,
    thumb_cursor: int,
) -> tuple[dict[str, list[float]], int]:
    """Execute one weighted browse cycle; return per-class timings (seconds)."""

    samples: dict[str, list[float]] = {name: [] for name, _weight in CYCLE_WEIGHTS}
    cursor = thumb_cursor

    status, _payload, elapsed = client.get_json("/api/rankings?limit=100&sort=date_taken")
    if status != 200:
        raise RuntimeError(f"grid returned {status}")
    samples["grid"].append(elapsed)

    for _ in range(THUMBS_PER_CYCLE):
        image_id = image_ids[cursor % len(image_ids)]
        cursor += 1
        status, _body, elapsed = client.get(f"/api/thumb/sm/{image_id}")
        if status not in (200, 204):
            raise RuntimeError(f"sm thumb {image_id} returned {status}")
        samples["thumb_sm"].append(elapsed)

    md_id = image_ids[cursor % len(image_ids)]
    cursor += 1
    status, _body, elapsed = client.get(f"/api/thumb/md/{md_id}")
    if status not in (200, 204):
        raise RuntimeError(f"md thumb {md_id} returned {status}")
    samples["thumb_md"].append(elapsed)

    search_path = "/api/search?" + urllib.parse.urlencode({"q": SEARCH_QUERY, "limit": 50})
    status, _payload, elapsed = client.get_json(search_path)
    if status != 200:
        raise RuntimeError(f"search returned {status}")
    samples["search"].append(elapsed)

    status, _payload, elapsed = client.get_json("/api/rankings?limit=100&sort=elo")
    if status != 200:
        raise RuntimeError(f"rankings returned {status}")
    samples["rankings"].append(elapsed)

    return samples, cursor


def run_phase(
    client: InteractiveClient,
    *,
    name: str,
    loops: int,
    warmup: int,
    observe_bulk: bool,
) -> PhaseResult:
    if loops < 1:
        raise ValueError("loops must be at least 1")
    image_ids = resolve_image_ids(client)
    accumulated: dict[str, list[float]] = {name_: [] for name_, _ in CYCLE_WEIGHTS}
    bulk_snapshots: list[dict[str, Any]] = []
    cursor = 0

    total_cycles = warmup + loops
    for cycle_index in range(total_cycles):
        if observe_bulk:
            bulk_snapshots.append(sample_bulk_state(client))
        cycle_samples, cursor = run_cycle(client, image_ids, thumb_cursor=cursor)
        # Warm-up cycles are executed but discarded from reported stats.
        if cycle_index < warmup:
            continue
        for class_name, values in cycle_samples.items():
            accumulated[class_name].extend(values)

    classes = {class_name: summarize(values) for class_name, values in accumulated.items()}
    bulk_state: dict[str, Any] = {}
    if observe_bulk:
        active_count = sum(1 for snap in bulk_snapshots if _bulk_active(snap))
        bulk_state = {
            "mode": "observe-natural",
            "note": (
                "No load injection: GET-only observation of /api/cache/pregen/status "
                "and /api/captions/status while the identical mix ran."
            ),
            "snapshots": len(bulk_snapshots),
            "active_snapshots": active_count,
            "any_bulk_active": active_count > 0,
            "first": bulk_snapshots[0] if bulk_snapshots else None,
            "last": bulk_snapshots[-1] if bulk_snapshots else None,
        }

    failures: list[str] = []
    for class_name, limits in BUDGETS_MS.items():
        stats = classes.get(class_name) or {}
        for percentile_name, limit_ms in limits.items():
            key = f"{percentile_name}_ms"
            measured = float(stats.get(key) or 0.0)
            if stats.get("n", 0) and measured > limit_ms:
                failures.append(
                    f"{class_name} {percentile_name} {measured:.1f}ms > {limit_ms:.0f}ms"
                )

    return PhaseResult(
        name=name,
        classes=classes,
        bulk_state=bulk_state,
        budget_ok=not failures,
        budget_failures=failures,
    )


def run_benchmark(
    base_url: str,
    *,
    loops: int = DEFAULT_LOOPS,
    warmup: int = DEFAULT_WARMUP,
    with_load: bool = False,
) -> dict[str, Any]:
    with InteractiveClient(base_url) as client:
        phases = [
            run_phase(
                client,
                name="baseline",
                loops=loops,
                warmup=warmup,
                observe_bulk=False,
            )
        ]
        if with_load:
            phases.append(
                run_phase(
                    client,
                    name="under_natural_bulk",
                    loops=loops,
                    warmup=warmup,
                    observe_bulk=True,
                )
            )

    return {
        "schema_version": HISTORY_SCHEMA,
        "base_url": base_url.rstrip("/"),
        "loops": loops,
        "warmup_cycles": warmup,
        "thumbs_per_cycle": THUMBS_PER_CYCLE,
        "cycle_weights": {name: weight for name, weight in CYCLE_WEIGHTS},
        "budgets_ms": BUDGETS_MS,
        "load_policy": (
            "observe-natural"
            if with_load
            else "baseline-only"
        ),
        "phases": [
            {
                "name": phase.name,
                "classes": phase.classes,
                "bulk_state": phase.bulk_state,
                "budget_ok": phase.budget_ok,
                "budget_failures": phase.budget_failures,
            }
            for phase in phases
        ],
        "budget_ok": all(phase.budget_ok for phase in phases),
    }


def format_summary_table(result: dict[str, Any]) -> str:
    lines = [
        f"Interactive latency @ {result.get('base_url')}",
        f"loops={result.get('loops')} warmup_cycles={result.get('warmup_cycles')} "
        f"load_policy={result.get('load_policy')}",
        "",
        f"{'phase':22} {'class':12} {'n':>5} {'p50':>8} {'p95':>8} {'p99':>8} {'budget':>10}",
        "-" * 78,
    ]
    for phase in result.get("phases") or []:
        for class_name, stats in (phase.get("classes") or {}).items():
            budget = BUDGETS_MS.get(class_name, {})
            budget_label = ""
            if "p95" in budget:
                ok = float(stats.get("p95_ms") or 0) <= budget["p95"]
                budget_label = f"p95<{budget['p95']:.0f}{' ✓' if ok else ' ✗'}"
            lines.append(
                f"{phase.get('name', ''):22} {class_name:12} {int(stats.get('n') or 0):5d} "
                f"{float(stats.get('p50_ms') or 0):8.1f} "
                f"{float(stats.get('p95_ms') or 0):8.1f} "
                f"{float(stats.get('p99_ms') or 0):8.1f} "
                f"{budget_label:>10}"
            )
        bulk = phase.get("bulk_state") or {}
        if bulk:
            lines.append(
                f"  bulk observe: active_snapshots="
                f"{bulk.get('active_snapshots')}/{bulk.get('snapshots')} "
                f"any_active={bulk.get('any_bulk_active')}"
            )
    lines.append("")
    lines.append("BUDGET OK" if result.get("budget_ok") else "BUDGET MISS")
    for phase in result.get("phases") or []:
        for failure in phase.get("budget_failures") or []:
            lines.append(f"  - {phase.get('name')}: {failure}")
    return "\n".join(lines)


def append_history(path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, separators=(",", ":")) + "\n")
