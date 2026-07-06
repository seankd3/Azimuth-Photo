import asyncio
import inspect
import importlib.util
import logging
import time

import settings


logger = logging.getLogger(__name__)


_text_search_resolution_cache: dict[tuple, dict] = {}
_text_search_resolution_cache_ttl_seconds = 300.0
_CONFIG: dict[str, object] = {}


def configure(**dependencies) -> None:
    """Register app-specific dependencies for configured query resolution."""
    _CONFIG.update({
        key: value
        for key, value in dependencies.items()
        if value is not None
    })


def _dependency(name: str):
    value = _CONFIG.get(name)
    if value is None:
        raise RuntimeError(f"core.query_constraints is missing configured dependency: {name}")
    return value


def _optional_dependency(name: str):
    return _CONFIG.get(name)


def _call_dependency(name: str, *args, **kwargs):
    value = _dependency(name)
    if not callable(value):
        return value
    return value(*args, **kwargs)


def sync_configured_ttls() -> None:
    global _text_search_resolution_cache_ttl_seconds

    text_ttl = _CONFIG.get("text_search_resolution_cache_ttl_seconds")
    if callable(text_ttl):
        _text_search_resolution_cache_ttl_seconds = float(text_ttl())
    elif text_ttl is not None:
        _text_search_resolution_cache_ttl_seconds = float(text_ttl)


def clear_text_search_caches() -> None:
    _text_search_resolution_cache.clear()


def normalize_search_query(query: str) -> str:
    normalized = " ".join(str(query or "").split())
    max_length = 160
    return normalized[:max_length].strip()


def encode_text_with_config(encoder, query: str, config: dict):
    try:
        if len(inspect.signature(encoder).parameters) < 2:
            return encoder(query)
    except (TypeError, ValueError):
        pass
    return encoder(query, config)


def start_search_model_load(embedding_worker) -> bool:
    start = getattr(embedding_worker, "start_search_model_load", None)
    if not callable(start):
        return False
    try:
        return bool(start())
    except Exception:
        return False


async def apply_metadata_search_ids(result: dict, normalized_query: str, *, metadata_search_image_ids) -> None:
    metadata_ids = await metadata_search_image_ids(normalized_query)
    if metadata_ids is not None:
        result["id_filter"] = metadata_ids
        if not metadata_ids:
            result["text_query"] = ""


async def apply_configured_metadata_search_ids(result: dict, normalized_query: str) -> None:
    await apply_metadata_search_ids(
        result,
        normalized_query,
        metadata_search_image_ids=_dependency("metadata_search_image_ids"),
    )


async def resolve_text_search(
    q: str,
    *,
    deep: bool = False,
    normalize_query=normalize_search_query,
    encode_text=encode_text_with_config,
    start_model_load=start_search_model_load,
    apply_metadata_ids=None,
    get_search_query_embedding=None,
    store_search_query_embedding=None,
    extension_search_terms: set[str],
    get_settings,
    active_embedding_config=None,
    fast_search_embedding_config=None,
) -> dict:
    """Resolve a text query into either embedding IDs or metadata fallback text."""
    del deep
    normalized_query = normalize_query(q)
    result = {
        "active": bool(normalized_query),
        "id_filter": None,
        "scores": {},
        "text_query": "",
        "search_mode": "",
        "ai_unavailable": False,
        "fallback_reason": "",
    }
    if not normalized_query:
        return result

    extension_query = normalized_query.lower().lstrip(".")
    config_provider = active_embedding_config or fast_search_embedding_config
    if config_provider is None:
        raise RuntimeError("resolve_text_search requires active_embedding_config")
    active_config = config_provider()
    threshold = get_settings().get("search_similarity_threshold", 0.35)
    cache_key = (
        normalized_query.casefold(),
        active_config["model_key"],
        f"{float(threshold or 0.0):.6f}",
    )
    cached = _text_search_resolution_cache.get(cache_key)
    if cached and cached["expires"] > time.monotonic():
        return dict(cached["data"])

    if extension_query in extension_search_terms:
        result.update({
            "text_query": normalized_query,
            "search_mode": "metadata",
        })
        _text_search_resolution_cache[cache_key] = {
            "data": dict(result),
            "expires": time.monotonic() + _text_search_resolution_cache_ttl_seconds,
        }
        return result

    try:
        import embedding_worker
        import embed_cache
        import numpy as np

        default_loader = (
            getattr(embedding_worker.ensure_model_loaded_for_search, "__module__", "")
            == "embedding_worker"
        )
        if default_loader and importlib.util.find_spec("torch") is None:
            raise RuntimeError("torch is not installed")

        text_vec = None
        if get_search_query_embedding is not None:
            cached_blob = await get_search_query_embedding(active_config, normalized_query)
            if cached_blob:
                cached_vec = np.frombuffer(cached_blob, dtype=np.float32).copy()
                if cached_vec.shape[0] == int(active_config["dimension"]):
                    text_vec = cached_vec

        if text_vec is None:
            text_vec = await asyncio.get_event_loop().run_in_executor(
                None,
                encode_text,
                embedding_worker.encode_text,
                normalized_query,
                active_config,
            )
            if text_vec is not None and store_search_query_embedding is not None:
                text_arr = np.asarray(text_vec, dtype=np.float32)
                if text_arr.shape[0] == int(active_config["dimension"]):
                    await store_search_query_embedding(
                        active_config,
                        normalized_query,
                        text_arr.tobytes(),
                    )
                    text_vec = text_arr
        if text_vec is None and start_model_load(embedding_worker):
            result.update({
                "text_query": normalized_query,
                "search_mode": "metadata",
                "ai_unavailable": True,
                "fallback_reason": "model_loading",
            })
            if extension_query not in extension_search_terms and apply_metadata_ids is not None:
                await apply_metadata_ids(result, normalized_query)
            return result
        if text_vec is not None:
            image_ids, matrix = await embed_cache.get_matrix(active_config["model_key"])
            if image_ids is not None and matrix is not None and matrix.shape[1] == text_vec.shape[0]:
                similarities = matrix @ text_vec
                matching_indices = np.flatnonzero(similarities >= threshold)
                scores = {
                    int(image_ids[int(i)]): float(similarities[int(i)])
                    for i in matching_indices
                }
                # Hybrid search: exact metadata matches (filename, camera,
                # lens, date, folder) always count and rank ahead of
                # semantic-only matches, so specific multi-word queries keep
                # working even when the embedding match is weak.
                metadata_ids = None
                if apply_metadata_ids is not None:
                    metadata_probe = {"id_filter": None, "text_query": ""}
                    try:
                        await apply_metadata_ids(metadata_probe, normalized_query)
                        metadata_ids = metadata_probe.get("id_filter")
                    except Exception:
                        metadata_ids = None
                if metadata_ids:
                    top_score = max(scores.values(), default=0.0)
                    for image_id in metadata_ids:
                        image_id = int(image_id)
                        scores[image_id] = max(scores.get(image_id, 0.0), top_score + 1.0)
                result.update({
                    "id_filter": set(scores.keys()),
                    "scores": scores,
                    "search_mode": "hybrid" if metadata_ids else "embedding",
                })
                _text_search_resolution_cache[cache_key] = {
                    "data": dict(result),
                    "expires": time.monotonic() + _text_search_resolution_cache_ttl_seconds,
                }
                return result
    except Exception:
        logger.warning(
            "Semantic search failed for %r; falling back to metadata search",
            normalized_query,
            exc_info=True,
        )

    result.update({
        "text_query": normalized_query,
        "search_mode": "metadata",
        "ai_unavailable": True,
    })
    if extension_query not in extension_search_terms and apply_metadata_ids is not None:
        await apply_metadata_ids(result, normalized_query)
    _text_search_resolution_cache[cache_key] = {
        "data": dict(result),
        "expires": time.monotonic() + _text_search_resolution_cache_ttl_seconds,
    }
    return result


async def resolve_configured_text_search(
    q: str,
    *,
    deep: bool = False,
    encode_text=None,
    start_model_load=None,
    apply_metadata_ids=None,
) -> dict:
    sync_configured_ttls()
    return await resolve_text_search(
        q,
        deep=deep,
        normalize_query=normalize_search_query,
        encode_text=encode_text or encode_text_with_config,
        start_model_load=start_model_load or start_search_model_load,
        apply_metadata_ids=apply_metadata_ids or apply_configured_metadata_search_ids,
        get_search_query_embedding=(
            _optional_dependency("get_search_query_embedding")
        ),
        store_search_query_embedding=(
            _optional_dependency("store_search_query_embedding")
        ),
        extension_search_terms=_dependency("extension_search_terms"),
        active_embedding_config=(
            _optional_dependency("active_embedding_config")
            or _dependency("fast_search_embedding_config")
        ),
        get_settings=_dependency("get_settings"),
    )


def search_constraint_active(search: dict | None) -> bool:
    return bool(
        search
        and (
            search.get("active")
            or search.get("id_filter") is not None
            or search.get("people_active")
        )
    )


def intersect_image_id_filters(left, right):
    if left is None:
        return None if right is None else {int(image_id) for image_id in right}
    if right is None:
        return {int(image_id) for image_id in left}
    return {int(image_id) for image_id in left}.intersection(int(image_id) for image_id in right)


async def resolve_library_constraints(
    *,
    q: str = "",
    people: str = "",
    deep: bool = False,
    resolve_text_search,
    parse_people_ids,
    get_people_image_id_filter,
) -> dict:
    """Resolve whole-image text search plus People membership as image ID constraints."""

    search = await resolve_text_search(q, deep=deep)
    people_ids = parse_people_ids(people)
    if not people_ids:
        search["people_ids"] = []
        search["people_active"] = False
        return search

    people_filter = await get_people_image_id_filter(people_ids)
    search["id_filter"] = intersect_image_id_filters(search.get("id_filter"), people_filter)
    search["people_ids"] = list(people_ids)
    search["people_active"] = True
    return search


async def resolve_configured_library_constraints(
    q: str = "",
    *,
    people: str = "",
    deep: bool = False,
    resolve_text_search=None,
) -> dict:
    return await resolve_library_constraints(
        q=q,
        people=people,
        deep=deep,
        resolve_text_search=resolve_text_search or resolve_configured_text_search,
        parse_people_ids=_dependency("parse_people_ids"),
        get_people_image_id_filter=_dependency("get_people_image_id_filter"),
    )
