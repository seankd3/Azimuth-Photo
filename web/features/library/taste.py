"""Personal taste vector construction for Library sorting."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import hashlib
import time

import embed_cache
import settings
from data import connection


MIN_COMPARISON_ROWS = 5
MIN_EMBEDDED_WINNERS = 2
MIN_EMBEDDED_LOSERS = 2
TASTE_ELO_CENTER = 1200.0
TASTE_ELO_RANGE = 400.0
ELO_CONFIDENCE_COMPARISONS = 10
RANK_BASIS_PREDICTED_MAX = 0.2
RANK_BASIS_MEASURED_MIN = 0.8
TASTE_SOURCE_VERIFY_TTL_SECONDS = 5.0

DbPath = Callable[[], str]
DbSignature = Callable[[], str]

_db_path: DbPath | None = None
_db_signature: DbSignature | None = None
_cache: dict[str, object] = {
    "key": None,
    "payload": None,
    "verified_at": 0.0,
}
_prediction_cache: dict[str, object] = {
    "key": None,
    "scores": None,
}
_cache_generation = 0


def configure(*, db_path: DbPath, db_signature: DbSignature) -> None:
    global _db_path, _db_signature
    _db_path = db_path
    _db_signature = db_signature
    from core import cache_events

    cache_events.register_embedding_batch_listener(_embedding_batch_stored)


def invalidate_taste_cache() -> None:
    global _cache_generation
    _cache_generation += 1
    _cache["key"] = None
    _cache["payload"] = None
    _cache["verified_at"] = 0.0
    _prediction_cache["key"] = None
    _prediction_cache["scores"] = None


def _embedding_batch_stored(model_key: str, _image_ids: list[int]) -> None:
    if str(model_key or "") == _active_model_key():
        invalidate_taste_cache()


def _configured(provider, name: str):
    if provider is None:
        raise RuntimeError(f"Library taste service is missing configured dependency: {name}")
    return provider


def _active_model_key() -> str:
    return settings.active_embedding_config()["model_key"]


def _taste_source_signature_sync(db_path: str, model_key: str) -> dict:
    conn = connection.open_sync(db_path)
    try:
        comparisons = conn.execute(
            "SELECT COUNT(*) AS count, COALESCE(MAX(id), 0) AS max_id FROM comparisons"
        ).fetchone()
        embeddings = conn.execute(
            "SELECT COUNT(*) AS count, COALESCE(MAX(rowid), 0) AS max_rowid "
            "FROM embeddings_by_model WHERE model_key = ?",
            (model_key,),
        ).fetchone()
        return {
            "comparison_count": int(comparisons["count"] or 0),
            "comparison_max_id": int(comparisons["max_id"] or 0),
            "embedding_count": int(embeddings["count"] or 0),
            "embedding_max_rowid": int(embeddings["max_rowid"] or 0),
        }
    finally:
        connection.close_sync(conn, db_path=db_path)


def _comparison_rows_sync(db_path: str) -> list[tuple[int, int]]:
    conn = connection.open_sync(db_path)
    try:
        rows = conn.execute(
            "SELECT winner_id, loser_id FROM comparisons ORDER BY id ASC"
        ).fetchall()
        return [(int(row["winner_id"]), int(row["loser_id"])) for row in rows]
    finally:
        connection.close_sync(conn, db_path=db_path)


def _unavailable(reason: str, *, comparison_count: int = 0, winner_count: int = 0,
                 loser_count: int = 0, model_key: str = "") -> dict:
    return {
        "available": False,
        "vector": None,
        "model_key": model_key,
        "comparison_count": int(comparison_count or 0),
        "embedded_winner_count": int(winner_count or 0),
        "embedded_loser_count": int(loser_count or 0),
        "signal_count": min(int(winner_count or 0), int(loser_count or 0)),
        "fallback_reason": reason,
    }


def _normalize(vector: np.ndarray) -> np.ndarray | None:
    import numpy as np  # deferred: keeps numpy off boot until a taste vector is computed

    norm = float(np.linalg.norm(vector))
    if not np.isfinite(norm) or norm <= 0:
        return None
    return (vector / norm).astype(np.float32)


def taste_vector_signature(taste: dict | None) -> tuple:
    import numpy as np  # deferred: keeps numpy off boot until a taste vector is compared

    if not taste or not taste.get("available"):
        return (
            False,
            str((taste or {}).get("model_key") or ""),
            int((taste or {}).get("comparison_count") or 0),
            int((taste or {}).get("signal_count") or 0),
            str((taste or {}).get("fallback_reason") or ""),
        )
    vector = taste.get("vector")
    if vector is None:
        digest = ""
    else:
        digest = hashlib.blake2b(np.asarray(vector, dtype=np.float32).tobytes(), digest_size=12).hexdigest()
    return (
        True,
        str(taste.get("model_key") or ""),
        int(taste.get("comparison_count") or 0),
        int(taste.get("signal_count") or 0),
        digest,
    )


def elo_confidence(comparisons: int, *, scale: int = ELO_CONFIDENCE_COMPARISONS) -> float:
    """Saturating confidence from direct comparison count.

    The existing confident ranking filter is comparisons >= 10, so ten direct
    comparisons is the point where stored Elo fully owns the display score.
    """
    try:
        count = max(0, int(comparisons or 0))
    except (TypeError, ValueError):
        count = 0
    scale = max(1, int(scale or ELO_CONFIDENCE_COMPARISONS))
    return min(1.0, count / scale)


def taste_to_elo(similarity: float | None) -> float | None:
    import numpy as np  # deferred: keeps numpy off boot until a taste score is rendered

    if similarity is None:
        return None
    try:
        value = float(similarity)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(value):
        return None
    value = max(-1.0, min(1.0, value))
    return TASTE_ELO_CENTER + (value * TASTE_ELO_RANGE)


def blend_display_score(stored_elo: float, taste_scaled: float | None, comparisons: int) -> tuple[float, float]:
    confidence = elo_confidence(comparisons)
    if taste_scaled is None:
        return float(stored_elo), confidence
    display_score = (confidence * float(stored_elo)) + ((1.0 - confidence) * float(taste_scaled))
    return display_score, confidence


def rank_basis(confidence: float) -> str:
    if confidence <= RANK_BASIS_PREDICTED_MAX:
        return "predicted"
    if confidence >= RANK_BASIS_MEASURED_MIN:
        return "measured"
    return "blended"


def _matrix_identity(matrix: np.ndarray | None) -> int:
    if matrix is None:
        return 0
    return int(matrix.__array_interface__["data"][0])


async def taste_scaled_scores(taste: dict) -> dict[int, float] | None:
    """Return image_id -> taste-scaled Elo prediction for the warm embedding matrix."""
    if not taste.get("available") or taste.get("vector") is None:
        return None
    model_key = str(taste.get("model_key") or "")
    image_ids, matrix = await embed_cache.get_matrix(model_key)
    if not image_ids or matrix is None:
        return None
    signature = taste_vector_signature(taste)
    cache_key = (taste.get("_cache_key"), signature, len(image_ids), _matrix_identity(matrix))
    if _prediction_cache.get("key") == cache_key:
        cached = _prediction_cache.get("scores")
        return cached if isinstance(cached, dict) else None

    scores = _compute_scaled_scores(image_ids, matrix, taste["vector"])
    _prediction_cache.update({"key": cache_key, "scores": scores})
    return scores


def _compute_scaled_scores(image_ids, matrix: np.ndarray, vector) -> dict[int, float]:
    import numpy as np  # deferred: keeps numpy off boot until taste scores are computed

    vector = np.asarray(vector, dtype=np.float32)
    row_norms = np.linalg.norm(matrix, axis=1)
    valid = row_norms > 0
    similarities = np.full(len(image_ids), np.nan, dtype=np.float32)
    similarities[valid] = (matrix[valid] @ vector) / row_norms[valid]
    scores = {
        int(image_id): scaled
        for image_id, similarity in zip(image_ids, similarities, strict=False)
        if (scaled := taste_to_elo(float(similarity))) is not None
    }
    return scores


async def taste_vector() -> dict:
    """Return the learned taste vector and availability metadata."""
    import numpy as np  # deferred: keeps numpy off boot until a taste vector is requested

    model_key = _active_model_key()
    db_path = _configured(_db_path, "db_path")()
    db_signature = _configured(_db_signature, "db_signature")()
    now = time.monotonic()
    cached_payload = _cache.get("payload")
    cached_source_key = cached_payload.get("_cache_key") if isinstance(cached_payload, dict) else None
    if (
        isinstance(cached_payload, dict)
        and cached_payload.get("model_key") == model_key
        and cached_source_key
        and cached_source_key[0] == db_signature
        and now - float(_cache.get("verified_at") or 0.0) < TASTE_SOURCE_VERIFY_TTL_SECONDS
    ):
        return dict(cached_payload)

    generation = _cache_generation
    source = await _to_thread(_taste_source_signature_sync, db_path, model_key)
    comparison_count = int(source["comparison_count"] or 0)
    max_id = int(source["comparison_max_id"] or 0)
    embedding_count = int(source["embedding_count"] or 0)
    if comparison_count >= MIN_COMPARISON_ROWS:
        image_ids, matrix = await embed_cache.get_matrix(model_key)
    else:
        image_ids, matrix = None, None
    active_embedding_count = len(image_ids or [])
    cache_key = (
        db_signature,
        model_key,
        active_embedding_count,
        comparison_count,
        max_id,
        int(source["embedding_max_rowid"] or 0),
        embedding_count,
        _matrix_identity(matrix),
        generation,
    )
    if _cache.get("key") == cache_key and isinstance(cached_payload, dict):
        _cache["verified_at"] = now
        return dict(cached_payload)
    if comparison_count < MIN_COMPARISON_ROWS:
        payload = _unavailable(
            f"Taste needs at least {MIN_COMPARISON_ROWS} direct comparisons.",
            comparison_count=comparison_count,
            model_key=model_key,
        )
        payload["_cache_key"] = cache_key
        _cache.update({"key": cache_key, "payload": payload, "verified_at": now})
        return dict(payload)

    if not image_ids or matrix is None:
        payload = _unavailable(
            "Taste needs embeddings for compared photos.",
            comparison_count=comparison_count,
            model_key=model_key,
        )
        payload["_cache_key"] = cache_key
        _cache.update({"key": cache_key, "payload": payload, "verified_at": now})
        return dict(payload)

    comparison_rows = await _to_thread(_comparison_rows_sync, db_path)
    id_to_idx = embed_cache.get_index(model_key)
    winner_vectors = []
    loser_vectors = []
    for winner_id, loser_id in comparison_rows:
        winner_idx = id_to_idx.get(winner_id)
        if winner_idx is not None:
            winner_vectors.append(matrix[winner_idx])
        loser_idx = id_to_idx.get(loser_id)
        if loser_idx is not None:
            loser_vectors.append(matrix[loser_idx])

    winner_count = len(winner_vectors)
    loser_count = len(loser_vectors)
    if winner_count < MIN_EMBEDDED_WINNERS or loser_count < MIN_EMBEDDED_LOSERS:
        payload = _unavailable(
            (
                f"Taste needs embeddings for at least {MIN_EMBEDDED_WINNERS} winners "
                f"and {MIN_EMBEDDED_LOSERS} losers from direct comparisons."
            ),
            comparison_count=comparison_count,
            winner_count=winner_count,
            loser_count=loser_count,
            model_key=model_key,
        )
        payload["_cache_key"] = cache_key
        _cache.update({"key": cache_key, "payload": payload, "verified_at": now})
        return dict(payload)

    vector = np.mean(winner_vectors, axis=0) - np.mean(loser_vectors, axis=0)
    normalized = _normalize(vector)
    if normalized is None:
        payload = _unavailable(
            "Taste comparisons do not yet form a directional preference.",
            comparison_count=comparison_count,
            winner_count=winner_count,
            loser_count=loser_count,
            model_key=model_key,
        )
        payload["_cache_key"] = cache_key
        _cache.update({"key": cache_key, "payload": payload, "verified_at": now})
        return dict(payload)

    payload = {
        "available": True,
        "vector": normalized,
        "model_key": model_key,
        "comparison_count": comparison_count,
        "embedded_winner_count": winner_count,
        "embedded_loser_count": loser_count,
        "signal_count": min(winner_count, loser_count),
        "fallback_reason": "",
        "_cache_key": cache_key,
    }
    if generation == _cache_generation:
        _cache.update({"key": cache_key, "payload": payload, "verified_at": now})
    return dict(payload)


async def _to_thread(func: Callable, *args):
    return await asyncio.to_thread(func, *args)
