"""Ground-truth metrics for Azimuth Photo search quality."""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
from statistics import mean
from typing import Iterable


DEFAULT_CUTOFFS = (5, 10, 20, 50)
MAX_RELEVANCE = 3


@dataclass(frozen=True)
class EvaluationQuery:
    query: str
    category: str
    judgments: dict[int, int]
    notes: str = ""

    @property
    def judged(self) -> bool:
        return any(relevance > 0 for relevance in self.judgments.values())


def _without_line_comments(text: str) -> str:
    return "\n".join(
        line for line in text.splitlines()
        if not line.lstrip().startswith("//")
    )


def _judgments(raw: object) -> dict[int, int]:
    if raw is None:
        return {}
    if isinstance(raw, list):
        raw = {image_id: MAX_RELEVANCE for image_id in raw}
    if not isinstance(raw, dict):
        raise ValueError("judgments must map image IDs to relevance grades")

    parsed: dict[int, int] = {}
    for raw_image_id, raw_relevance in raw.items():
        image_id = int(raw_image_id)
        relevance = int(raw_relevance)
        if image_id <= 0:
            raise ValueError("judgment image IDs must be positive")
        if relevance < 0 or relevance > MAX_RELEVANCE:
            raise ValueError(f"relevance must be between 0 and {MAX_RELEVANCE}")
        parsed[image_id] = relevance
    return parsed


def parse_evaluation_set(raw: object) -> list[EvaluationQuery]:
    if isinstance(raw, dict):
        version = int(raw.get("version") or 1)
        if version != 1:
            raise ValueError(f"unsupported search evaluation version: {version}")
        raw_queries = raw.get("queries")
    else:
        # Legacy draft lists remain useful as unjudged query prompts. Folder and
        # filename hints are deliberately not treated as relevance truth.
        raw_queries = raw
    if not isinstance(raw_queries, list):
        raise ValueError("search evaluation set must contain a queries list")

    queries: list[EvaluationQuery] = []
    seen: set[str] = set()
    for raw_query in raw_queries:
        if not isinstance(raw_query, dict):
            raise ValueError("each search evaluation query must be an object")
        query = " ".join(str(raw_query.get("query") or "").split())
        if not query:
            raise ValueError("search evaluation queries cannot be empty")
        key = query.casefold()
        if key in seen:
            raise ValueError(f"duplicate search evaluation query: {query}")
        seen.add(key)
        queries.append(EvaluationQuery(
            query=query,
            category=str(raw_query.get("category") or "uncategorized").strip() or "uncategorized",
            judgments=_judgments(
                raw_query.get("judgments", raw_query.get("relevant_image_ids"))
            ),
            notes=str(raw_query.get("notes") or "").strip(),
        ))
    return queries


def load_evaluation_set(path: str | Path) -> list[EvaluationQuery]:
    payload = json.loads(_without_line_comments(Path(path).read_text(encoding="utf-8")))
    return parse_evaluation_set(payload)


def _dcg(relevances: Iterable[int]) -> float:
    return sum(
        ((2 ** int(relevance)) - 1) / math.log2(rank + 2)
        for rank, relevance in enumerate(relevances)
    )


def ndcg_at(ranked_ids: list[int], judgments: dict[int, int], cutoff: int) -> float:
    cutoff = max(1, int(cutoff))
    actual = [judgments.get(int(image_id), 0) for image_id in ranked_ids[:cutoff]]
    ideal = sorted(judgments.values(), reverse=True)[:cutoff]
    denominator = _dcg(ideal)
    return _dcg(actual) / denominator if denominator > 0 else 0.0


def recall_at(ranked_ids: list[int], judgments: dict[int, int], cutoff: int) -> float:
    relevant = {image_id for image_id, grade in judgments.items() if grade > 0}
    if not relevant:
        return 0.0
    returned = {int(image_id) for image_id in ranked_ids[:max(1, int(cutoff))]}
    return len(relevant & returned) / len(relevant)


def reciprocal_rank(ranked_ids: list[int], judgments: dict[int, int]) -> float:
    for rank, image_id in enumerate(ranked_ids, start=1):
        if judgments.get(int(image_id), 0) > 0:
            return 1.0 / rank
    return 0.0


def evaluate_query(
    spec: EvaluationQuery,
    ranked_ids: list[int],
    *,
    latency_ms: float,
    search_mode: str,
    search_sources: list[str] | tuple[str, ...],
    cutoffs: tuple[int, ...] = DEFAULT_CUTOFFS,
) -> dict:
    normalized_ids = [int(image_id) for image_id in ranked_ids]
    row = {
        "query": spec.query,
        "category": spec.category,
        "judged": spec.judged,
        "judgment_count": len(spec.judgments),
        "positive_judgment_count": sum(1 for grade in spec.judgments.values() if grade > 0),
        "result_count": len(normalized_ids),
        "latency_ms": round(max(0.0, float(latency_ms)), 3),
        "search_mode": str(search_mode or ""),
        "search_sources": sorted({str(source) for source in search_sources if source}),
        "reciprocal_rank": reciprocal_rank(normalized_ids, spec.judgments) if spec.judged else None,
        "metrics": {},
    }
    for cutoff in cutoffs:
        row["metrics"][f"ndcg@{cutoff}"] = (
            ndcg_at(normalized_ids, spec.judgments, cutoff) if spec.judged else None
        )
        row["metrics"][f"recall@{cutoff}"] = (
            recall_at(normalized_ids, spec.judgments, cutoff) if spec.judged else None
        )
    return row


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(float(value) for value in values)
    position = (len(ordered) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def aggregate_evaluations(rows: list[dict]) -> dict:
    judged = [row for row in rows if row.get("judged")]
    metric_names = sorted({
        name
        for row in judged
        for name, value in (row.get("metrics") or {}).items()
        if value is not None
    })
    latencies = [float(row.get("latency_ms") or 0.0) for row in rows]
    modes: dict[str, int] = {}
    for row in rows:
        mode = str(row.get("search_mode") or "unknown")
        modes[mode] = modes.get(mode, 0) + 1

    def summarize(subset: list[dict]) -> dict:
        return {
            "query_count": len(subset),
            "reciprocal_rank": round(mean(
                float(row["reciprocal_rank"])
                for row in subset
                if row.get("reciprocal_rank") is not None
            ), 6) if subset else 0.0,
            **{
                name: round(mean(float(row["metrics"][name]) for row in subset), 6)
                for name in metric_names
            },
        }

    categories = sorted({str(row.get("category") or "uncategorized") for row in judged})
    return {
        "query_count": len(rows),
        "judged_query_count": len(judged),
        "unjudged_query_count": len(rows) - len(judged),
        "latency_ms": {
            "mean": round(mean(latencies), 3) if latencies else 0.0,
            "p50": round(_percentile(latencies, 0.50), 3),
            "p95": round(_percentile(latencies, 0.95), 3),
            "max": round(max(latencies), 3) if latencies else 0.0,
        },
        "search_modes": modes,
        "overall": summarize(judged),
        "categories": {
            category: summarize([row for row in judged if row.get("category") == category])
            for category in categories
        },
    }
