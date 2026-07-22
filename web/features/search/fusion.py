"""Rank fusion helpers for text search retrieval channels."""

from __future__ import annotations

import math

from core.dates import parse_taken_timestamp


RRF_K = 60
FUSED_CANDIDATE_LIMIT = 400
RERANK_RRF_WEIGHT = 0.75
RERANK_EMBEDDING_WEIGHT = 0.25


def reciprocal_rank_fusion(
    ranked_sources: dict[str, list[int]],
    *,
    k: int = RRF_K,
    source_weights: dict[str, float] | None = None,
) -> dict[int, float]:
    scores: dict[int, float] = {}
    for source, image_ids in ranked_sources.items():
        weight = float((source_weights or {}).get(source, 1.0))
        for rank, image_id in enumerate(image_ids, start=1):
            image_id = int(image_id)
            scores[image_id] = scores.get(image_id, 0.0) + weight / (k + rank)
    return scores


def normalize_scores(scores: dict[int, float]) -> dict[int, float]:
    if not scores:
        return {}
    values = list(scores.values())
    low = min(values)
    high = max(values)
    if math.isclose(low, high):
        return {int(image_id): 1.0 for image_id in scores}
    scale = high - low
    return {
        int(image_id): (float(score) - low) / scale
        for image_id, score in scores.items()
    }


def _date_value(row: dict) -> float:
    return parse_taken_timestamp(row.get("date_taken")) or 0.0


def recency_priors(rows_by_id: dict[int, dict]) -> dict[int, float]:
    dated = {int(image_id): _date_value(row) for image_id, row in rows_by_id.items()}
    return normalize_scores({image_id: value for image_id, value in dated.items() if value > 0.0})


def fused_candidate_scores(
    *,
    ranked_sources: dict[str, list[int]],
    embedding_scores: dict[int, float] | None = None,
    rows_by_id: dict[int, dict] | None = None,
    source_weights: dict[str, float] | None = None,
    recency_weight: float = 0.0,
    limit: int = FUSED_CANDIDATE_LIMIT,
) -> tuple[dict[int, float], list[str]]:
    ranked_sources = {
        source: [int(image_id) for image_id in image_ids]
        for source, image_ids in ranked_sources.items()
        if image_ids
    }
    rrf_scores = reciprocal_rank_fusion(ranked_sources, source_weights=source_weights)
    if not rrf_scores:
        return {}, []

    rrf_norm = normalize_scores(rrf_scores)
    embedding_norm = normalize_scores(embedding_scores or {})
    recency_norm = recency_priors(rows_by_id or {})
    final: dict[int, float] = {}
    recency_weight = max(0.0, min(float(recency_weight), 0.2))
    relevance_scale = 1.0 - recency_weight
    for image_id in rrf_scores:
        final[image_id] = (
            relevance_scale * (
                RERANK_RRF_WEIGHT * rrf_norm.get(image_id, 0.0)
                + RERANK_EMBEDDING_WEIGHT * embedding_norm.get(image_id, 0.0)
            )
            + recency_weight * recency_norm.get(image_id, 0.0)
        )

    top = sorted(final.items(), key=lambda item: item[1], reverse=True)[: max(1, int(limit))]
    return dict(top), sorted(ranked_sources)


def candidate_evidence(
    ranked_sources: dict[str, list[int]],
    final_scores: dict[int, float],
) -> dict[int, dict]:
    """Describe why each candidate survived, without exposing model internals."""

    ranks = {
        source: {int(image_id): rank for rank, image_id in enumerate(ids, start=1)}
        for source, ids in ranked_sources.items()
    }
    evidence: dict[int, dict] = {}
    for image_id, score in final_scores.items():
        matched = [source for source in sorted(ranks) if image_id in ranks[source]]
        evidence[int(image_id)] = {
            "signals": matched,
            "ranks": {source: ranks[source][image_id] for source in matched},
            "score": round(float(score), 6),
        }
    return evidence
