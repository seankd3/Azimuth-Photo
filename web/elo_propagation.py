"""Elo propagation via embedding similarity.

When a comparison is recorded, propagate scaled Elo adjustments to
visually similar images. This dramatically accelerates ranking for
archives with many similar shots (same scene, same shoot, etc.).

Direct comparisons are always source of truth — propagation only
nudges images that haven't been extensively compared yet.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import logging
import sqlite3
from typing import Any

import embed_cache

log = logging.getLogger("elo_propagation")

# Last propagation result (read by /api/propagation/last)
last_propagation_count = 0
_prediction_cache_key = None
_prediction_cache_counts = None
ActiveEmbeddingModelKey = Callable[[], str]
GetActiveImagesByIds = Callable[[list[int]], Awaitable[dict[int, dict]]]
GetDb = Callable[[], Awaitable[Any]]
InvalidateRatingStatsCache = Callable[[], None]
_active_embedding_model_key: ActiveEmbeddingModelKey | None = None
_get_active_images_by_ids: GetActiveImagesByIds | None = None
_get_db: GetDb | None = None
_invalidate_rating_stats_cache: InvalidateRatingStatsCache | None = None

# Tuning parameters
SIMILARITY_THRESHOLD = 0.70   # minimum cosine similarity to propagate
MAX_NEIGHBORS = 100           # long tail — cubic scaling makes weak matches near-zero anyway
PROPAGATION_DECAY = 0.3       # scale factor (0.3 = propagated change is 30% of direct)
MAX_DIRECT_COMPARISONS = 50   # allow propagation to well-compared images (cubic scaling keeps it safe)
PROPAGATION_LOCK_RETRIES = 3
PROPAGATION_LOCK_RETRY_SECONDS = 1.0


def configure(
    *,
    active_embedding_model_key: ActiveEmbeddingModelKey | None = None,
    get_active_images_by_ids: GetActiveImagesByIds | None = None,
    get_db: GetDb | None = None,
    invalidate_rating_stats_cache: InvalidateRatingStatsCache | None = None,
) -> None:
    global _active_embedding_model_key, _get_active_images_by_ids
    global _get_db, _invalidate_rating_stats_cache
    if active_embedding_model_key is not None:
        _active_embedding_model_key = active_embedding_model_key
    if get_active_images_by_ids is not None:
        _get_active_images_by_ids = get_active_images_by_ids
    if get_db is not None:
        _get_db = get_db
    if invalidate_rating_stats_cache is not None:
        _invalidate_rating_stats_cache = invalidate_rating_stats_cache


def _configured(provider, name: str):
    if provider is None:
        raise RuntimeError(f"elo_propagation is missing configured dependency: {name}")
    return provider


def _is_sqlite_locked(exc: BaseException) -> bool:
    text = str(exc).lower()
    return (
        isinstance(exc, sqlite3.OperationalError)
        or "sqlite" in type(exc).__module__.lower()
    ) and (
        "database is locked" in text
        or "database table is locked" in text
        or "database schema is locked" in text
    )


async def _run_with_lock_retries(label: str, operation):
    for attempt in range(PROPAGATION_LOCK_RETRIES + 1):
        try:
            return await operation()
        except Exception as exc:
            if _is_sqlite_locked(exc) and attempt < PROPAGATION_LOCK_RETRIES:
                await asyncio.sleep(PROPAGATION_LOCK_RETRY_SECONDS * (attempt + 1))
                continue
            log.warning("%s propagation error: %s", label, exc)
            return None


def invalidate_prediction_cache():
    global _prediction_cache_key, _prediction_cache_counts
    _prediction_cache_key = None
    _prediction_cache_counts = None


def compare_embedding_model_key() -> str:
    """Return the embedding surface Compare uses for vector propagation."""
    return _configured(_active_embedding_model_key, "active_embedding_model_key")()


async def _get_compare_matrix(required_ids=()):
    """Load the active embedding matrix used by Compare vector math."""
    model_key = compare_embedding_model_key()
    image_ids, matrix = await embed_cache.get_matrix(model_key)
    if image_ids is None or matrix is None:
        return model_key, None, None, {}
    id_to_idx = embed_cache.get_index(model_key)
    return model_key, image_ids, matrix, id_to_idx


def _nonlinear_weight(similarity: float) -> float:
    """Remap similarity to a cubic curve so near-identical images (0.99)
    get strong propagation while barely-qualifying ones (0.75) get almost none.

    Linear:  0.75→0.75, 0.90→0.90, 0.99→0.99  (flat, everything gets a lot)
    Cubic:   0.75→0.00, 0.90→0.22, 0.99→0.89  (steep falloff for weak matches)
    """
    t = (similarity - SIMILARITY_THRESHOLD) / (1.0 - SIMILARITY_THRESHOLD)
    return t * t * t  # cubic



def _find_similar(image_id, image_ids, matrix, id_to_idx, threshold, max_n):
    """Find the most similar images above threshold. Returns [(id, similarity), ...]."""
    idx = id_to_idx.get(image_id)
    if idx is None:
        return []

    similarities = matrix @ matrix[idx]  # cosine sim (already L2-normalized)
    return _rank_similar_from_scores(image_id, image_ids, similarities, threshold, max_n)


def _rank_similar_from_scores(image_id, image_ids, similarities, threshold, max_n):
    """Rank precomputed similarity scores. Returns [(id, similarity), ...]."""
    import numpy as np  # deferred: keeps numpy off boot until Elo propagation ranks similar images

    candidate_count = min(len(image_ids), max_n + 1)
    if candidate_count <= 0:
        return []
    if len(image_ids) <= candidate_count:
        ranked = np.argsort(similarities)[::-1]
    else:
        candidates = np.argpartition(similarities, -candidate_count)[-candidate_count:]
        ranked = candidates[np.argsort(similarities[candidates])[::-1]]

    results = []
    for i in ranked:
        if image_ids[i] == image_id:
            continue
        sim = float(similarities[i])
        if sim < threshold:
            break
        results.append((image_ids[i], sim))
        if len(results) >= max_n:
            break
    return results


def _find_similar_batch(image_ids_to_find, image_ids, matrix, id_to_idx, threshold, max_n):
    """Find similar images for many source IDs using one matrix multiply."""
    valid_ids = []
    valid_indices = []
    for image_id in image_ids_to_find:
        idx = id_to_idx.get(image_id)
        if idx is not None:
            valid_ids.append(image_id)
            valid_indices.append(idx)

    results_by_id = {image_id: [] for image_id in image_ids_to_find}
    if not valid_indices:
        return results_by_id

    grid_matrix = matrix[valid_indices]
    similarity_rows = grid_matrix @ matrix.T  # cosine sim (already L2-normalized)

    for image_id, similarities in zip(valid_ids, similarity_rows):
        results_by_id[image_id] = _rank_similar_from_scores(
            image_id, image_ids, similarities, threshold, max_n
        )
    return results_by_id


async def _apply_propagation_deltas(
    conn,
    neighbors: dict[int, dict],
    deltas: dict[int, float],
    *,
    action_id: str | None,
) -> list[tuple[int, float]]:
    """Apply the deltas and return the (image_id, delta) rows written.

    Callers report those ids so caches can patch the touched rows instead of
    dropping every reservoir the originating pick just patched. The delta, not
    the resulting Elo, is what the writes below apply, so a patch built from it
    survives a pick that landed between the read and the write.
    """
    updates = []
    history_rows = []
    applied: list[tuple[int, float]] = []
    for neighbor_id, delta in deltas.items():
        neighbor = neighbors.get(neighbor_id)
        if not neighbor or neighbor["comparisons"] >= MAX_DIRECT_COMPARISONS:
            continue
        before_elo = float(neighbor["elo"])
        before_count = int(neighbor.get("propagated_updates") or 0)
        after_elo = before_elo + float(delta)
        updates.append((float(delta), neighbor_id))
        applied.append((int(neighbor_id), float(delta)))
        if action_id:
            history_rows.append((
                action_id,
                neighbor_id,
                before_elo,
                before_count,
                after_elo,
                float(delta),
            ))

    if not updates:
        return []

    await conn.execute("BEGIN IMMEDIATE")
    if action_id:
        cursor = await conn.execute(
            "SELECT 1 FROM comparisons WHERE action_id = ? LIMIT 1",
            (action_id,),
        )
        if await cursor.fetchone() is None:
            await conn.rollback()
            return []
        await conn.executemany(
            "INSERT INTO propagation_updates "
            "(action_id, image_id, elo_before, propagated_updates_before, elo_after, delta) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            history_rows,
        )

    # Relative update: a compare pick committed between our stale read and this
    # write must not be reverted by an absolute `elo = before + delta` value.
    await conn.executemany(
        "UPDATE images SET elo = COALESCE(elo, 0) + ?, "
        "propagated_updates = COALESCE(propagated_updates, 0) + 1 WHERE id = ?",
        updates,
    )
    return applied


async def predict_propagation(grid_ids: list[int]) -> dict[int, int]:
    """Precompute how many images would be affected if each grid image were the winner.
    Returns {image_id: predicted_count} for each image in grid_ids."""
    global _prediction_cache_key, _prediction_cache_counts

    try:
        model_key, image_ids, matrix, id_to_idx = await _get_compare_matrix(grid_ids)
        if image_ids is None:
            return {gid: 0 for gid in grid_ids}
        cache_key = (model_key, len(image_ids), tuple(grid_ids))
        if _prediction_cache_key == cache_key and _prediction_cache_counts is not None:
            return dict(_prediction_cache_counts)

        grid_set = set(grid_ids)
        # Precompute neighbors for every grid image. The big matmul is
        # CPU-bound numpy work; keep it off the event loop.
        neighbors_by_id = await asyncio.to_thread(
            _find_similar_batch,
            grid_ids,
            image_ids,
            matrix,
            id_to_idx,
            SIMILARITY_THRESHOLD,
            MAX_NEIGHBORS,
        )

        # For filtering: fetch comparison counts for all potential neighbors
        all_neighbor_ids = set()
        for nlist in neighbors_by_id.values():
            for nid, _ in nlist:
                if nid not in grid_set:
                    all_neighbor_ids.add(nid)
        neighbor_data = (
            await _configured(_get_active_images_by_ids, "get_active_images_by_ids")(list(all_neighbor_ids))
            if all_neighbor_ids
            else {}
        )

        result = {}
        for winner_id in grid_ids:
            affected = set()
            # Winner neighbors
            for nid, _ in neighbors_by_id[winner_id]:
                if nid in grid_set:
                    continue
                n = neighbor_data.get(nid)
                if n and n["comparisons"] < MAX_DIRECT_COMPARISONS:
                    affected.add(nid)
            # Loser neighbors (everyone else on the grid)
            for loser_id in grid_ids:
                if loser_id == winner_id:
                    continue
                for nid, _ in neighbors_by_id[loser_id]:
                    if nid in grid_set:
                        continue
                    n = neighbor_data.get(nid)
                    if n and n["comparisons"] < MAX_DIRECT_COMPARISONS:
                        affected.add(nid)
            result[winner_id] = len(affected)
        _prediction_cache_key = cache_key
        _prediction_cache_counts = dict(result)
        return result
    except Exception as e:
        log.warning(f"Propagation prediction error: {e}")
        return {gid: 0 for gid in grid_ids}


async def _propagate_comparison_once(winner_id: int, loser_id: int, k: float, action_id: str | None = None):
    """
    After a direct comparison, propagate scaled Elo changes to similar images.
    Called as a fire-and-forget background task.
    """
    _, image_ids, matrix, id_to_idx = await _get_compare_matrix((winner_id, loser_id))
    if image_ids is None:
        return []  # no embeddings available yet

    # Find similar images for winner and loser (one batched matmul; off-loop)
    neighbors_by_id = await asyncio.to_thread(
        _find_similar_batch,
        [winner_id, loser_id],
        image_ids,
        matrix,
        id_to_idx,
        SIMILARITY_THRESHOLD,
        MAX_NEIGHBORS,
    )
    winner_neighbors = neighbors_by_id.get(winner_id) or []
    loser_neighbors = neighbors_by_id.get(loser_id) or []

    if not winner_neighbors and not loser_neighbors:
        return []

    # Collect all neighbor IDs to fetch their current state
    all_neighbor_ids = list({nid for nid, _ in winner_neighbors + loser_neighbors})
    neighbors = await _configured(_get_active_images_by_ids, "get_active_images_by_ids")(all_neighbor_ids)

    conn = await _configured(_get_db, "get_db")()
    try:
        deltas = {}

        # Boost images similar to the winner
        for neighbor_id, similarity in winner_neighbors:
            if neighbor_id == loser_id:
                continue
            weight = _nonlinear_weight(similarity)
            boost = k * weight * PROPAGATION_DECAY
            deltas[neighbor_id] = deltas.get(neighbor_id, 0.0) + boost

        # Penalize images similar to the loser
        for neighbor_id, similarity in loser_neighbors:
            if neighbor_id == winner_id:
                continue
            weight = _nonlinear_weight(similarity)
            penalty = k * weight * PROPAGATION_DECAY
            deltas[neighbor_id] = deltas.get(neighbor_id, 0.0) - penalty

        global last_propagation_count
        applied = await _apply_propagation_deltas(
            conn,
            neighbors,
            deltas,
            action_id=action_id,
        )
        if applied:
            await conn.commit()
            _configured(_invalidate_rating_stats_cache, "invalidate_rating_stats_cache")()
            last_propagation_count = len(applied)
            log.debug(f"Propagated Elo to {len(applied)} neighbors "
                     f"(winner={winner_id}, loser={loser_id})")
        else:
            last_propagation_count = 0
        return applied
    finally:
        await conn.close()


async def propagate_comparison(winner_id: int, loser_id: int, k: float, action_id: str | None = None):
    """
    After a direct comparison, propagate scaled Elo changes to similar images.
    Called through the write-behind queue in normal request handling.
    """
    return await _run_with_lock_retries(
        "Elo",
        lambda: _propagate_comparison_once(winner_id, loser_id, k, action_id=action_id),
    )


async def _propagate_mosaic_once(winner_id: int, loser_ids: list[int], k: float, action_id: str | None = None):
    """
    Propagate after a mosaic pick. Boost images similar to the winner,
    and penalize images similar to the losers. This makes each mosaic
    pick dramatically more powerful by also affecting look-alikes of
    every image on the grid.
    """
    _, image_ids, matrix, id_to_idx = await _get_compare_matrix((winner_id, *loser_ids))
    if image_ids is None:
        return []

    involved = {winner_id} | set(loser_ids)

    # Find neighbors for winner AND all losers in one batched matmul, instead of
    # one sequential matvec per grid image (CPU-bound numpy; off-loop).
    neighbors_by_id = await asyncio.to_thread(
        _find_similar_batch,
        [winner_id, *loser_ids],
        image_ids,
        matrix,
        id_to_idx,
        SIMILARITY_THRESHOLD,
        MAX_NEIGHBORS,
    )
    winner_neighbors = neighbors_by_id.get(winner_id) or []
    loser_neighbor_lists = [neighbors_by_id.get(lid) or [] for lid in loser_ids]

    all_neighbor_ids = set()
    for nid, _ in winner_neighbors:
        all_neighbor_ids.add(nid)
    for ln in loser_neighbor_lists:
        for nid, _ in ln:
            all_neighbor_ids.add(nid)

    if not all_neighbor_ids:
        return []

    neighbors = await _configured(_get_active_images_by_ids, "get_active_images_by_ids")(list(all_neighbor_ids))

    conn = await _configured(_get_db, "get_db")()
    try:
        deltas = {}

        # Boost images similar to the winner
        for neighbor_id, similarity in winner_neighbors:
            if neighbor_id in involved:
                continue
            weight = _nonlinear_weight(similarity)
            boost = k * weight * PROPAGATION_DECAY
            deltas[neighbor_id] = deltas.get(neighbor_id, 0.0) + boost

        # Penalize images similar to losers (scaled down since each
        # loser only lost to the winner, not to each other)
        loser_scale = 1.0 / max(len(loser_ids), 1)
        for loser_neighbors in loser_neighbor_lists:
            for neighbor_id, similarity in loser_neighbors:
                if neighbor_id in involved:
                    continue
                weight = _nonlinear_weight(similarity)
                penalty = k * weight * PROPAGATION_DECAY * loser_scale
                deltas[neighbor_id] = deltas.get(neighbor_id, 0.0) - penalty

        global last_propagation_count
        applied = await _apply_propagation_deltas(
            conn,
            neighbors,
            deltas,
            action_id=action_id,
        )
        if applied:
            await conn.commit()
            _configured(_invalidate_rating_stats_cache, "invalidate_rating_stats_cache")()
            last_propagation_count = len(applied)
            log.debug(f"Propagated mosaic to {len(applied)} neighbors "
                     f"(winner={winner_id}, {len(loser_ids)} losers)")
        else:
            last_propagation_count = 0
        return applied
    finally:
        await conn.close()


async def propagate_mosaic(winner_id: int, loser_ids: list[int], k: float, action_id: str | None = None):
    """
    Propagate after a mosaic pick. Boost images similar to the winner,
    and penalize images similar to the losers.
    """
    return await _run_with_lock_retries(
        "Mosaic",
        lambda: _propagate_mosaic_once(winner_id, loser_ids, k, action_id=action_id),
    )
