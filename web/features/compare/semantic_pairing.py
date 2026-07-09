"""Embedding affinity helpers for refine candidate pairing."""

from __future__ import annotations

from dataclasses import dataclass


SEMANTIC_DUEL_EXPLORATION_RATE = 0.2
MOSAIC_NEIGHBOR_THRESHOLD = 0.35
MOSAIC_DIVERSE_SPREAD_THRESHOLD = 0.8
MOSAIC_DIVERSE_NEIGHBOR_CELL_LIMIT = 2


@dataclass(frozen=True)
class SemanticContext:
    matrix: object
    index_by_id: dict[int, int]

    def has(self, image_id: int) -> bool:
        return int(image_id) in self.index_by_id

    def cosine(self, left_id: int, right_id: int) -> float | None:
        try:
            import numpy as np

            left_idx = self.index_by_id[int(left_id)]
            right_idx = self.index_by_id[int(right_id)]
            left = self.matrix[left_idx]
            right = self.matrix[right_idx]
            denom = float(np.linalg.norm(left) * np.linalg.norm(right))
            if denom <= 0:
                return None
            return max(-1.0, min(1.0, float(np.dot(left, right) / denom)))
        except Exception:
            return None


def _candidate_id(candidate) -> int | None:
    try:
        return int(candidate["id"])
    except (KeyError, IndexError, TypeError, ValueError):
        return None


async def load_context(candidates: list[dict], model_key: str | None) -> SemanticContext | None:
    try:
        import embed_cache

        image_ids, matrix = await embed_cache.get_matrix(model_key)
        if image_ids is None or matrix is None:
            return None
        id_to_idx = embed_cache.get_index(model_key)
        if not id_to_idx:
            id_to_idx = {int(image_id): idx for idx, image_id in enumerate(image_ids)}
        candidate_ids = {
            image_id
            for image_id in (_candidate_id(candidate) for candidate in candidates)
            if image_id is not None
        }
        index_by_id = {
            int(image_id): int(idx)
            for image_id, idx in id_to_idx.items()
            if int(image_id) in candidate_ids
        }
        if len(index_by_id) < 2:
            return None
        return SemanticContext(matrix=matrix, index_by_id=index_by_id)
    except Exception:
        return None


def blended_score(strategy_score: float, cosine_sim: float) -> float:
    return max(0.0, float(strategy_score)) * (0.5 + 0.5 * max(-1.0, min(1.0, float(cosine_sim))))


def best_partner(
    seed: dict,
    candidates: list[dict],
    strategy_scores: dict[int, float],
    context: SemanticContext,
    *,
    minimum_cosine: float | None = None,
    used_ids: set[int] | None = None,
    past_matchups: set[tuple[int, int]] | None = None,
    allow_past: bool = True,
) -> dict | None:
    seed_id = int(seed["id"])
    used_ids = used_ids or set()
    past_matchups = past_matchups or set()
    ranked = []
    for candidate in candidates:
        candidate_id = int(candidate["id"])
        if candidate_id == seed_id or candidate_id in used_ids:
            continue
        if not allow_past:
            pair_key = (min(seed_id, candidate_id), max(seed_id, candidate_id))
            if pair_key in past_matchups:
                continue
        cosine = context.cosine(seed_id, candidate_id)
        if cosine is None:
            continue
        if minimum_cosine is not None and cosine <= minimum_cosine:
            continue
        score = blended_score(strategy_scores.get(candidate_id, 1.0), cosine)
        if score <= 0:
            continue
        ranked.append((score, cosine, -candidate_id, candidate))
    if not ranked:
        return None
    ranked.sort(reverse=True)
    return ranked[0][3]
