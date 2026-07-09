"""Personal taste vector construction for Library sorting."""

import asyncio
from collections.abc import Callable
import hashlib
import os

import numpy as np

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

DbPath = Callable[[], str]
DbSignature = Callable[[], str]

_db_path: DbPath | None = None
_db_signature: DbSignature | None = None
_cache: dict[str, object] = {
    "key": None,
    "payload": None,
}
_prediction_cache: dict[str, object] = {
    "key": None,
    "scores": None,
}


def configure(*, db_path: DbPath, db_signature: DbSignature) -> None:
    global _db_path, _db_signature
    _db_path = db_path
    _db_signature = db_signature


def invalidate_taste_cache() -> None:
    _cache["key"] = None
    _cache["payload"] = None
    _prediction_cache["key"] = None
    _prediction_cache["scores"] = None


def _configured(provider, name: str):
    if provider is None:
        raise RuntimeError(f"Library taste service is missing configured dependency: {name}")
    return provider


def _active_model_key() -> str:
    return settings.active_embedding_config()["model_key"]


def _db_file_signature(db_path: str) -> tuple:
    signature = []
    for path in (db_path, f"{db_path}-wal", f"{db_path}-shm"):
        try:
            stat = os.stat(path)
            signature.append((os.path.basename(path), stat.st_size, stat.st_mtime_ns))
        except OSError:
            signature.append((os.path.basename(path), -1, -1))
    return tuple(signature)


def _comparison_summary_sync(db_path: str) -> dict:
    conn = connection.open_sync(db_path)
    try:
        summary = conn.execute(
            "SELECT COUNT(*) AS count, COALESCE(MAX(id), 0) AS max_id FROM comparisons"
        ).fetchone()
        rows = conn.execute(
            "SELECT winner_id, loser_id FROM comparisons ORDER BY id ASC"
        ).fetchall()
        return {
            "count": int(summary["count"] or 0),
            "max_id": int(summary["max_id"] or 0),
            "rows": [(int(row["winner_id"]), int(row["loser_id"])) for row in rows],
        }
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
    norm = float(np.linalg.norm(vector))
    if not np.isfinite(norm) or norm <= 0:
        return None
    return (vector / norm).astype(np.float32)


def taste_vector_signature(taste: dict | None) -> tuple:
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


async def taste_scaled_scores(taste: dict) -> dict[int, float] | None:
    """Return image_id -> taste-scaled Elo prediction for the warm embedding matrix."""
    if not taste.get("available") or taste.get("vector") is None:
        return None
    model_key = str(taste.get("model_key") or "")
    image_ids, matrix = await embed_cache.get_matrix(model_key)
    if not image_ids or matrix is None:
        return None
    signature = taste_vector_signature(taste)
    db_signature = _configured(_db_signature, "db_signature")()
    cache_key = (db_signature, signature, len(image_ids))
    if _prediction_cache.get("key") == cache_key:
        cached = _prediction_cache.get("scores")
        return dict(cached) if isinstance(cached, dict) else None

    vector = np.asarray(taste["vector"], dtype=np.float32)
    row_norms = np.linalg.norm(matrix, axis=1)
    valid = row_norms > 0
    similarities = np.full(len(image_ids), np.nan, dtype=np.float32)
    similarities[valid] = (matrix[valid] @ vector) / row_norms[valid]
    scores = {
        int(image_id): scaled
        for image_id, similarity in zip(image_ids, similarities, strict=False)
        if (scaled := taste_to_elo(float(similarity))) is not None
    }
    _prediction_cache.update({"key": cache_key, "scores": scores})
    return dict(scores)


async def taste_vector() -> dict:
    """Return the learned taste vector and availability metadata."""
    model_key = _active_model_key()
    db_path = _configured(_db_path, "db_path")()
    db_signature = (
        _configured(_db_signature, "db_signature")(),
        _db_file_signature(db_path),
    )
    summary = await _to_thread(_comparison_summary_sync, db_path)
    comparison_count = int(summary["count"] or 0)
    max_id = int(summary["max_id"] or 0)
    if comparison_count < MIN_COMPARISON_ROWS:
        return _unavailable(
            f"Taste needs at least {MIN_COMPARISON_ROWS} direct comparisons.",
            comparison_count=comparison_count,
            model_key=model_key,
        )

    image_ids, matrix = await embed_cache.get_matrix(model_key)
    embedding_count = len(image_ids or [])
    cache_key = (db_signature, model_key, embedding_count, comparison_count, max_id)
    if _cache.get("key") == cache_key:
        return dict(_cache.get("payload") or {})
    if not image_ids or matrix is None:
        payload = _unavailable(
            "Taste needs embeddings for compared photos.",
            comparison_count=comparison_count,
            model_key=model_key,
        )
        _cache.update({"key": cache_key, "payload": payload})
        return dict(payload)

    id_to_idx = embed_cache.get_index(model_key)
    winner_vectors = []
    loser_vectors = []
    for winner_id, loser_id in summary["rows"]:
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
        _cache.update({"key": cache_key, "payload": payload})
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
        _cache.update({"key": cache_key, "payload": payload})
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
    }
    _cache.update({"key": cache_key, "payload": payload})
    return dict(payload)


async def _to_thread(func: Callable, *args):
    return await asyncio.to_thread(func, *args)
