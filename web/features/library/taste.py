"""Personal taste vector construction for Library sorting."""

import asyncio
from collections.abc import Callable
import os
import sqlite3

import numpy as np

import embed_cache
import settings


MIN_COMPARISON_ROWS = 5
MIN_EMBEDDED_WINNERS = 2
MIN_EMBEDDED_LOSERS = 2

DbPath = Callable[[], str]
DbSignature = Callable[[], str]

_db_path: DbPath | None = None
_db_signature: DbSignature | None = None
_cache: dict[str, object] = {
    "key": None,
    "payload": None,
}


def configure(*, db_path: DbPath, db_signature: DbSignature) -> None:
    global _db_path, _db_signature
    _db_path = db_path
    _db_signature = db_signature


def invalidate_taste_cache() -> None:
    _cache["key"] = None
    _cache["payload"] = None


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
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
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
        conn.close()


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
