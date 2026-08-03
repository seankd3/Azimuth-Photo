"""
Shared in-memory cache for the embedding matrix.

Used by: search, find similar, duplicates, collections, Elo propagation.
Rebuilt when the embedding count changes (new images embedded).
"""

from __future__ import annotations

from core.catalog_path import catalog_path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np

import asyncio
from collections.abc import Callable
import json
import os
import sqlite3
import time

from core.runtime_paths import resolve_runtime_paths

# Avoid hitting SQLite on every semantic-search request. The embedding worker
# patches new vectors into this cache directly; catalog/source changes call
# invalidate(), so a longer verification window keeps search instant while the
# thumbnail/original cache is writing heavily.
COUNT_CHECK_TTL_SECONDS = 30.0
MATRIX_GROWTH_MIN_ROWS = 512
MATRIX_GROWTH_FACTOR = 1.10
SNAPSHOT_DIR = resolve_runtime_paths().embed_cache_dir

_cache = {
    "image_ids": None,
    "id_to_idx": None,
    "matrix": None,
    "count": 0,
    "checked_at": 0.0,
    "model_key": None,
}
_caches = {}
_rebuild_lock = asyncio.Lock()
DbPath = Callable[[], str]


def _target_model_key(model_key: str | None = None) -> str:
    import db

    return model_key or db.active_embedding_model_key()


def _empty_cache(model_key: str | None = None):
    return {
        "image_ids": None,
        "id_to_idx": None,
        "matrix": None,
        "count": 0,
        "checked_at": 0.0,
        "model_key": model_key,
    }


def _activate_cache(cache: dict):
    global _cache
    _cache = cache


def _matrix_view(cache: dict | None = None):
    cache = cache or _cache
    matrix = cache["matrix"]
    image_ids = cache["image_ids"]
    if matrix is None or image_ids is None:
        return None
    return matrix[:len(image_ids)]


def _rows_to_matrix(rows, *, overallocate: bool = True):
    import numpy as np  # deferred: keeps numpy off boot until the embedding matrix is loaded

    if not rows:
        return [], None

    image_ids = [r[0] for r in rows]
    dim = len(rows[0][1]) // 4
    capacity = len(rows)
    if overallocate:
        capacity = max(
            len(rows),
            int(len(rows) * MATRIX_GROWTH_FACTOR) + MATRIX_GROWTH_MIN_ROWS,
        )
    matrix = np.empty((capacity, dim), dtype=np.float32)
    for i, (_image_id, blob) in enumerate(rows):
        matrix[i] = np.frombuffer(blob, dtype=np.float32, count=dim)
    return image_ids, matrix


def _embedding_source_signature(model_key: str) -> list[str | int]:
    """Stable snapshot identity unaffected by unrelated catalog/cache writes."""
    conn = sqlite3.connect(catalog_path(), timeout=30)
    try:
        row = conn.execute(
            "SELECT COUNT(*), COALESCE(MAX(e.rowid), 0), "
            "COALESCE(MAX(e.created_at), ''), COALESCE(SUM(e.image_id), 0) "
            "FROM embeddings_by_model e "
            "JOIN images i ON e.image_id = i.id "
            "JOIN catalog_sources s ON s.id = i.source_id "
            "WHERE e.model_key = ? AND s.included = 1 AND i.missing_at IS NULL",
            (model_key,),
        ).fetchone()
        return [model_key, int(row[0]), int(row[1]), str(row[2]), int(row[3])]
    finally:
        conn.close()


def _snapshot_paths(model_key: str):
    safe_key = (
        str(model_key or "default")
        .replace("/", "--")
        .replace("\\", "--")
        .replace(":", "-")
        .replace("@", "-")
    )
    return (
        os.path.join(SNAPSHOT_DIR, f"{safe_key}.matrix.npy"),
        os.path.join(SNAPSHOT_DIR, f"{safe_key}.image_ids.npy"),
        os.path.join(SNAPSHOT_DIR, f"{safe_key}.meta.json"),
    )


def _load_snapshot_sync(expected_count: int, model_key: str):
    import numpy as np  # deferred: keeps numpy off boot until an embedding snapshot is loaded

    matrix_path, ids_path, meta_path = _snapshot_paths(model_key)
    try:
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
        if int(meta.get("count", -1)) != expected_count:
            return None
        if meta.get("model_key") != model_key:
            return None
        if meta.get("embedding_signature") != _embedding_source_signature(model_key):
            return None
        # File-backed mmap: taste/search can keep the read-only matrix available
        # without pinning ~729MB anonymous RSS. The OS reclaims pages under
        # pressure; remapping a warm .npy is near-instant. add_vectors() copies
        # to a writable ndarray before mutating.
        ids = np.load(ids_path, mmap_mode="r")
        matrix = np.load(matrix_path, mmap_mode="r")
        if len(ids) != expected_count or matrix.shape[0] != expected_count:
            return None
        return np.asarray(ids, dtype=np.int64).tolist(), matrix
    except Exception:
        return None


def _save_snapshot_sync(image_ids: list[int], matrix: np.ndarray, model_key: str):
    import numpy as np  # deferred: keeps numpy off boot until an embedding snapshot is saved

    matrix_path, ids_path, meta_path = _snapshot_paths(model_key)
    try:
        os.makedirs(SNAPSHOT_DIR, exist_ok=True)
        ids_tmp = f"{ids_path}.tmp"
        matrix_tmp = f"{matrix_path}.tmp"
        meta_tmp = f"{meta_path}.tmp"
        np.save(ids_tmp, np.asarray(image_ids, dtype=np.int64))
        np.save(matrix_tmp, np.asarray(matrix[:len(image_ids)], dtype=np.float32))
        with open(meta_tmp, "w", encoding="utf-8") as f:
            json.dump({
                "model_key": model_key,
                "count": len(image_ids),
                "embedding_signature": _embedding_source_signature(model_key),
                "created_at": time.time(),
            }, f)
        os.replace(f"{ids_tmp}.npy", ids_path)
        os.replace(f"{matrix_tmp}.npy", matrix_path)
        os.replace(meta_tmp, meta_path)
    except Exception:
        pass


def _get_embedding_count_sync(model_key: str) -> int:
    conn = sqlite3.connect(catalog_path(), timeout=30)
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM embeddings_by_model e "
            "JOIN images i ON e.image_id = i.id "
            "JOIN catalog_sources s ON s.id = i.source_id "
            "WHERE e.model_key = ? "
            "AND s.included = 1 AND i.missing_at IS NULL",
            (model_key,),
        ).fetchone()[0]
    finally:
        conn.close()


def _load_embeddings_sync(expected_count: int, model_key: str):
    snapshot = _load_snapshot_sync(expected_count, model_key)
    if snapshot is not None:
        return snapshot

    conn = sqlite3.connect(catalog_path(), timeout=30)
    try:
        rows = conn.execute(
            "SELECT e.image_id, e.embedding FROM embeddings_by_model e "
            "JOIN images i ON e.image_id = i.id "
            "JOIN catalog_sources s ON s.id = i.source_id "
            "WHERE e.model_key = ? "
            "AND s.included = 1 AND i.missing_at IS NULL",
            (model_key,),
        ).fetchall()
    finally:
        conn.close()
    image_ids, matrix = _rows_to_matrix(rows)
    if image_ids and matrix is not None:
        _save_snapshot_sync(image_ids, matrix, model_key)
    return image_ids, matrix


def _remove_snapshot_sync(keep_model_key: str | None = None):
    keep_paths = set(_snapshot_paths(keep_model_key)) if keep_model_key else set()
    try:
        for filename in os.listdir(SNAPSHOT_DIR):
            path = os.path.join(SNAPSHOT_DIR, filename)
            if path in keep_paths:
                continue
            if not filename.endswith((".matrix.npy", ".image_ids.npy", ".meta.json")):
                continue
            try:
                os.remove(path)
            except OSError:
                pass
    except OSError:
        pass


def retain_only(model_key: str):
    """Drop stale in-memory caches and snapshot files for inactive models."""
    global _cache
    target = str(model_key or "")
    for key in list(_caches):
        if key != target:
            _caches.pop(key, None)
    _cache = _caches.get(target) or _empty_cache(target)
    _caches[target] = _cache
    _remove_snapshot_sync(keep_model_key=target)


async def get_matrix(model_key: str | None = None):
    """Return (image_ids, matrix) from cache, rebuilding if needed."""
    model_key = _target_model_key(model_key)
    now = time.monotonic()
    cache = _caches.get(model_key)
    if (
        cache is not None
        and cache["matrix"] is not None
        and now - cache["checked_at"] < COUNT_CHECK_TTL_SECONDS
    ):
        _activate_cache(cache)
        return cache["image_ids"], _matrix_view(cache)

    async with _rebuild_lock:
        now = time.monotonic()
        cache = _caches.get(model_key)
        if (
            cache is not None
            and cache["matrix"] is not None
            and now - cache["checked_at"] < COUNT_CHECK_TTL_SECONDS
        ):
            _activate_cache(cache)
            return cache["image_ids"], _matrix_view(cache)

        current_count = await asyncio.to_thread(_get_embedding_count_sync, model_key)
        if cache is None:
            cache = _empty_cache(model_key)
            _caches[model_key] = cache
        cache["checked_at"] = now
        if (
            cache["matrix"] is not None
            and cache["count"] == current_count
        ):
            _activate_cache(cache)
            return cache["image_ids"], _matrix_view(cache)

        image_ids, matrix = await asyncio.to_thread(_load_embeddings_sync, current_count, model_key)
        if not image_ids or matrix is None:
            cache = {
                "image_ids": None,
                "id_to_idx": None,
                "matrix": None,
                "count": 0,
                "checked_at": now,
                "model_key": model_key,
            }
            _caches[model_key] = cache
            _activate_cache(cache)
            return None, None

        # Build new cache atomically to avoid partial reads from concurrent callers
        new_cache = {
            "image_ids": image_ids,
            "id_to_idx": {img_id: i for i, img_id in enumerate(image_ids)},
            "matrix": matrix,
            "count": len(image_ids),
            "checked_at": now,
            "model_key": model_key,
        }
        _caches[model_key] = new_cache
        _activate_cache(new_cache)

        return image_ids, _matrix_view(new_cache)


def get_warm_matrix(model_key: str | None = None):
    """Return the current in-memory matrix without triggering a rebuild."""
    model_key = _target_model_key(model_key)
    cache = _caches.get(model_key)
    if (
        cache is None
        or cache["matrix"] is None
        or cache["image_ids"] is None
        or int(cache.get("count") or 0) < 0
    ):
        return None, None
    _activate_cache(cache)
    return cache["image_ids"], _matrix_view(cache)


def _writable_matrix(matrix: np.ndarray, *, rows: int | None = None) -> np.ndarray:
    """Copy mmap/read-only matrices before in-place embedding updates."""
    import numpy as np  # deferred

    row_count = int(rows if rows is not None else matrix.shape[0])
    view = matrix[:row_count]
    if isinstance(matrix, np.memmap) or not getattr(matrix, "flags", None) or not matrix.flags.writeable:
        return np.array(view, dtype=np.float32, copy=True)
    return matrix


def add_vectors(rows: list[tuple[int, np.ndarray]], model_key: str | None = None):
    """Append freshly stored vectors to the warm cache without a full DB rebuild."""
    import numpy as np  # deferred: keeps numpy off boot until new embeddings are added

    model_key = _target_model_key(model_key)
    cache = _caches.get(model_key)
    if (
        not rows
        or cache is None
        or cache["matrix"] is None
        or cache["image_ids"] is None
    ):
        return

    id_to_idx = cache["id_to_idx"] or {}
    image_ids = list(cache["image_ids"])
    matrix = _writable_matrix(cache["matrix"], rows=len(image_ids))
    cache["matrix"] = matrix
    new_rows = []
    for image_id, vec in rows:
        idx = id_to_idx.get(image_id)
        if idx is None:
            new_rows.append((image_id, vec))
            continue
        matrix[idx] = np.asarray(vec, dtype=np.float32)
    if not new_rows:
        cache["count"] = len(cache["image_ids"])
        cache["checked_at"] = time.monotonic()
        return

    old_count = len(image_ids)
    dim = matrix.shape[1]
    new_count = old_count + len(new_rows)

    if new_count > matrix.shape[0]:
        new_capacity = max(
            new_count,
            int(new_count * MATRIX_GROWTH_FACTOR) + MATRIX_GROWTH_MIN_ROWS,
        )
        grown = np.empty((new_capacity, dim), dtype=np.float32)
        grown[:old_count] = matrix[:old_count]
        matrix = grown

    for offset, (image_id, vec) in enumerate(new_rows):
        matrix[old_count + offset] = np.asarray(vec, dtype=np.float32)
        id_to_idx[image_id] = old_count + offset
        image_ids.append(image_id)

    cache.update({
        "image_ids": image_ids,
        "id_to_idx": id_to_idx,
        "matrix": matrix,
        "count": new_count,
        "checked_at": time.monotonic(),
    })
    if _cache.get("model_key") == model_key:
        _activate_cache(cache)


def invalidate():
    """Force the next get_matrix() call to verify/rebuild the active set."""
    for cache in _caches.values():
        cache["checked_at"] = 0.0
        cache["count"] = -1
    _cache["checked_at"] = 0.0
    _cache["count"] = -1


def get_index(model_key: str | None = None) -> dict[int, int]:
    target = _target_model_key(model_key)
    cache = _caches.get(target)
    if cache is None:
        return {}
    return cache["id_to_idx"] or {}


def get_vector(image_id: int, model_key: str | None = None):
    """Get a single image's embedding vector from cache. Returns None if not cached."""
    target = _target_model_key(model_key)
    cache = _caches.get(target)
    if cache is None or cache["matrix"] is None or cache["id_to_idx"] is None:
        return None
    idx = cache["id_to_idx"].get(image_id)
    if idx is None:
        return None
    return cache["matrix"][idx]
