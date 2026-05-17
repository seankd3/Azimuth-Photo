import asyncio
import inspect
import importlib.util
import time

import settings


_text_search_resolution_cache: dict[tuple, dict] = {}
_deep_search_query_record_cache: dict[str, float] = {}
_text_search_resolution_cache_ttl_seconds = 300.0
_deep_search_query_record_cache_ttl_seconds = 300.0
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


def _call_dependency(name: str, *args, **kwargs):
    value = _dependency(name)
    if not callable(value):
        return value
    return value(*args, **kwargs)


def sync_configured_ttls() -> None:
    global _text_search_resolution_cache_ttl_seconds
    global _deep_search_query_record_cache_ttl_seconds

    text_ttl = _CONFIG.get("text_search_resolution_cache_ttl_seconds")
    if callable(text_ttl):
        _text_search_resolution_cache_ttl_seconds = float(text_ttl())
    elif text_ttl is not None:
        _text_search_resolution_cache_ttl_seconds = float(text_ttl)

    record_ttl = _CONFIG.get("deep_search_query_record_cache_ttl_seconds")
    if callable(record_ttl):
        _deep_search_query_record_cache_ttl_seconds = float(record_ttl())
    elif record_ttl is not None:
        _deep_search_query_record_cache_ttl_seconds = float(record_ttl)


def clear_text_search_caches() -> None:
    _text_search_resolution_cache.clear()
    _deep_search_query_record_cache.clear()


def normalize_search_query(query: str) -> str:
    normalized = " ".join(str(query or "").split())
    max_length = int(getattr(settings, "MAX_DEEP_SEARCH_TERM_LENGTH", 160))
    return normalized[:max_length].strip()


async def resolve_cached_deep_search(
    query: str,
    *,
    allow_cold_load: bool = True,
    get_deep_search_query_embedding=None,
) -> dict | None:
    normalized_query = normalize_search_query(query)
    if not normalized_query:
        return None
    try:
        deep_config = settings.deep_search_embedding_config()
        embedding_provider = get_deep_search_query_embedding or _dependency("get_deep_search_query_embedding")
        blob = await embedding_provider(
            normalized_query,
            deep_config["model_key"],
        )
        if blob is None:
            return None
        import embed_cache
        import embedding_worker
        import numpy as np

        text_vec = embedding_worker.blob_to_vec(blob)
        if allow_cold_load:
            image_ids, matrix = await embed_cache.get_matrix(deep_config["model_key"])
        else:
            image_ids, matrix = embed_cache.get_warm_matrix(deep_config["model_key"])
        if image_ids is None or matrix is None or matrix.shape[1] != text_vec.shape[0]:
            return None
        similarities = matrix @ text_vec
        threshold = settings.get_settings().get("search_similarity_threshold", 0.35)
        matching_indices = np.flatnonzero(similarities >= threshold)
        scores = {
            int(image_ids[int(i)]): float(similarities[int(i)])
            for i in matching_indices
        }
        return {
            "id_filter": set(scores.keys()),
            "scores": scores,
            "image_ids": image_ids,
            "similarities": similarities,
            "search_mode": "deep_embedding",
            "deep_model_key": deep_config["model_key"],
        }
    except Exception:
        return None


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


async def record_deep_search_query(
    query: str,
    *,
    record_query,
    extension_search_terms: set[str],
    invalidate_ai_status_response_cache,
    invalidate_settings_response_cache,
    normalize_query=normalize_search_query,
) -> None:
    normalized_query = normalize_query(query)
    if not normalized_query:
        return
    extension_query = normalized_query.lower().lstrip(".")
    if extension_query in extension_search_terms:
        return
    cache_key = normalized_query.casefold()
    now = time.monotonic()
    if _deep_search_query_record_cache.get(cache_key, 0.0) > now:
        return
    try:
        await record_query(normalized_query)
        _deep_search_query_record_cache[cache_key] = (
            now + _deep_search_query_record_cache_ttl_seconds
        )
        invalidate_ai_status_response_cache()
        invalidate_settings_response_cache()
    except Exception:
        pass


async def apply_metadata_search_ids(result: dict, normalized_query: str, *, metadata_search_image_ids) -> None:
    metadata_ids = await metadata_search_image_ids(normalized_query)
    if metadata_ids is not None:
        result["id_filter"] = metadata_ids
        if not metadata_ids:
            result["text_query"] = ""


async def record_configured_deep_search_query(query: str) -> None:
    sync_configured_ttls()
    await record_deep_search_query(
        query,
        record_query=_dependency("record_deep_search_query"),
        extension_search_terms=_dependency("extension_search_terms"),
        invalidate_ai_status_response_cache=_dependency("invalidate_ai_status_response_cache"),
        invalidate_settings_response_cache=_dependency("invalidate_settings_response_cache"),
        normalize_query=normalize_search_query,
    )


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
    record_query=None,
    resolve_deep_search=resolve_cached_deep_search,
    encode_text=encode_text_with_config,
    start_model_load=start_search_model_load,
    apply_metadata_ids=None,
    extension_search_terms: set[str],
    fast_search_embedding_config,
    get_settings,
) -> dict:
    """Resolve a text query into either embedding IDs or metadata fallback text."""
    normalized_query = normalize_query(q)
    deep_requested = bool(deep)
    cache_key = (normalized_query.casefold(), deep_requested)
    result = {
        "active": bool(normalized_query),
        "id_filter": None,
        "scores": {},
        "text_query": "",
        "search_mode": "",
        "ai_unavailable": False,
        "deep_requested": deep_requested,
        "deep_search_cached": False,
        "fallback_reason": "",
    }
    if not normalized_query:
        return result

    extension_query = normalized_query.lower().lstrip(".")
    cached = _text_search_resolution_cache.get(cache_key)
    if cached and cached["expires"] > time.monotonic():
        return dict(cached["data"])

    if extension_query not in extension_search_terms and record_query is not None:
        await record_query(normalized_query)

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

    deep_search = await resolve_deep_search(
        normalized_query,
        allow_cold_load=deep_requested,
    )
    if deep_search is not None:
        result.update({
            "id_filter": deep_search["id_filter"],
            "scores": deep_search["scores"],
            "search_mode": deep_search["search_mode"],
            "deep_search_cached": True,
        })
        _text_search_resolution_cache[cache_key] = {
            "data": dict(result),
            "expires": time.monotonic() + _text_search_resolution_cache_ttl_seconds,
        }
        return result

    if deep_requested:
        result.update({
            "text_query": normalized_query,
            "search_mode": "metadata",
            "ai_unavailable": True,
            "fallback_reason": "deep_search_not_cached",
        })
        _text_search_resolution_cache[cache_key] = {
            "data": dict(result),
            "expires": time.monotonic() + _text_search_resolution_cache_ttl_seconds,
        }
        return result

    try:
        import embedding_worker
        import embed_cache

        default_loader = (
            getattr(embedding_worker.ensure_model_loaded_for_search, "__module__", "")
            == "embedding_worker"
        )
        if default_loader and importlib.util.find_spec("torch") is None:
            raise RuntimeError("torch is not installed")

        fast_config = fast_search_embedding_config()
        text_vec = await asyncio.get_event_loop().run_in_executor(
            None,
            encode_text,
            embedding_worker.encode_text,
            normalized_query,
            fast_config,
        )
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
            image_ids, matrix = await embed_cache.get_matrix()
            if image_ids is not None:
                config = get_settings()
                threshold = config.get("search_similarity_threshold", 0.35)
                similarities = matrix @ text_vec
                import numpy as np
                matching_indices = np.flatnonzero(similarities >= threshold)
                scores = {
                    int(image_ids[int(i)]): float(similarities[int(i)])
                    for i in matching_indices
                }
                result.update({
                    "id_filter": set(scores.keys()),
                    "scores": scores,
                    "search_mode": "embedding",
                })
                _text_search_resolution_cache[cache_key] = {
                    "data": dict(result),
                    "expires": time.monotonic() + _text_search_resolution_cache_ttl_seconds,
                }
                return result
    except Exception:
        pass

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
    record_query=None,
    resolve_deep_search=None,
    encode_text=None,
    start_model_load=None,
    apply_metadata_ids=None,
) -> dict:
    sync_configured_ttls()
    return await resolve_text_search(
        q,
        deep=deep,
        normalize_query=normalize_search_query,
        record_query=record_query or record_configured_deep_search_query,
        resolve_deep_search=resolve_deep_search or resolve_cached_deep_search,
        encode_text=encode_text or encode_text_with_config,
        start_model_load=start_model_load or start_search_model_load,
        apply_metadata_ids=apply_metadata_ids or apply_configured_metadata_search_ids,
        extension_search_terms=_dependency("extension_search_terms"),
        fast_search_embedding_config=_dependency("fast_search_embedding_config"),
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
