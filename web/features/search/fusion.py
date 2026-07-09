"""Rank fusion helpers for text search retrieval channels."""

from __future__ import annotations

import math
from datetime import datetime


RRF_K = 60
FUSED_CANDIDATE_LIMIT = 400
RERANK_RRF_WEIGHT = 0.70
RERANK_EMBEDDING_WEIGHT = 0.25
RERANK_RECENCY_WEIGHT = 0.05


def reciprocal_rank_fusion(
    ranked_sources: dict[str, list[int]],
    *,
    k: int = RRF_K,
) -> dict[int, float]:
    scores: dict[int, float] = {}
    for image_ids in ranked_sources.values():
        for rank, image_id in enumerate(image_ids, start=1):
            image_id = int(image_id)
            scores[image_id] = scores.get(image_id, 0.0) + 1.0 / (k + rank)
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
    text = str(row.get("date_taken") or "").strip()
    if not text:
        return 0.0
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except Exception:
        try:
            return datetime.strptime(text[:10], "%Y-%m-%d").timestamp()
        except Exception:
            return 0.0


def recency_priors(rows_by_id: dict[int, dict]) -> dict[int, float]:
    dated = {int(image_id): _date_value(row) for image_id, row in rows_by_id.items()}
    return normalize_scores({image_id: value for image_id, value in dated.items() if value > 0.0})


def fused_candidate_scores(
    *,
    ranked_sources: dict[str, list[int]],
    embedding_scores: dict[int, float] | None = None,
    rows_by_id: dict[int, dict] | None = None,
    limit: int = FUSED_CANDIDATE_LIMIT,
) -> tuple[dict[int, float], list[str]]:
    ranked_sources = {
        source: [int(image_id) for image_id in image_ids]
        for source, image_ids in ranked_sources.items()
        if image_ids
    }
    rrf_scores = reciprocal_rank_fusion(ranked_sources)
    if not rrf_scores:
        return {}, []

    rrf_norm = normalize_scores(rrf_scores)
    embedding_norm = normalize_scores(embedding_scores or {})
    recency_norm = recency_priors(rows_by_id or {})
    final: dict[int, float] = {}
    for image_id in rrf_scores:
        final[image_id] = (
            RERANK_RRF_WEIGHT * rrf_norm.get(image_id, 0.0)
            + RERANK_EMBEDDING_WEIGHT * embedding_norm.get(image_id, 0.0)
            + RERANK_RECENCY_WEIGHT * recency_norm.get(image_id, 0.0)
        )

    top = sorted(final.items(), key=lambda item: item[1], reverse=True)[: max(1, int(limit))]
    return dict(top), sorted(ranked_sources)
