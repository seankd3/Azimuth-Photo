import asyncio
import db
import settings
import inspect
import importlib.util
import logging
import time
from functools import partial

from core.catalog_path import catalog_path
from core.search_fusion import FUSED_CANDIDATE_LIMIT, candidate_evidence, fused_candidate_scores
from core.search_planning import plan_search
from data.repositories import embeddings as embedding_repository
from data.repositories import images as image_repository
from data.repositories import rankings as ranking_repository


logger = logging.getLogger(__name__)


_text_search_resolution_cache: dict[tuple, dict] = {}
_text_search_resolution_cache_ttl_seconds = 300.0
# As-you-type response budget for the query encode. A warm small encode / a
# cache hit / the test mock lands well under this; a cold 8B encode blows it
# and we fall back to metadata while the embedding warms in the background.


def clear_text_search_caches() -> None:
    _text_search_resolution_cache.clear()


def normalize_search_query(query: str) -> str:
    normalized = " ".join(str(query or "").split())
    max_length = 160
    return normalized[:max_length].strip()


def _embedding_ranked_scores(matrix, text_vec, image_ids, threshold, max_results: int) -> dict[int, float]:
    import numpy as np

    similarities = matrix @ text_vec
    if similarities.size == 0:
        return {}
    matching_indices = np.flatnonzero(similarities >= threshold)
    if matching_indices.size == 0:
        return {}
    if matching_indices.size > max_results:
        local_scores = similarities[matching_indices]
        top_local = np.argpartition(local_scores, -max_results)[-max_results:]
        matching_indices = matching_indices[top_local]
    ordered = sorted(
        (
            (int(image_ids[int(i)]), float(similarities[int(i)]))
            for i in matching_indices
        ),
        key=lambda item: item[1],
        reverse=True,
    )
    return dict(ordered)


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
        metadata_search_image_ids=db.metadata_search_image_ids,
    )


async def get_catalog_search_query_embedding(config: dict, query: str) -> bytes | None:
    return await embedding_repository.get_search_query_embedding(
        catalog_path(), config=config, query=query
    )


async def store_catalog_search_query_embedding(config: dict, query: str, blob: bytes):
    return await embedding_repository.store_search_query_embedding(
        catalog_path(), config=config, query=query, blob=blob
    )


async def _apply_lexical_search(
    result: dict,
    normalized_query: str,
    *,
    query_plan,
    metadata_ranked_image_ids,
    caption_ranked_image_ids,
    get_active_images_by_ids,
) -> bool:
    """Return caption/metadata intelligence immediately while embeddings warm."""

    async def ranked(provider):
        if provider is None:
            return []
        try:
            return await provider(normalized_query)
        except Exception:
            return []

    metadata_ranked, caption_ranked = await asyncio.gather(
        ranked(metadata_ranked_image_ids),
        ranked(caption_ranked_image_ids),
    )
    ranked_sources = {}
    if metadata_ranked:
        ranked_sources["metadata"] = [int(image_id) for image_id, _score in metadata_ranked]
    if caption_ranked:
        ranked_sources["captions"] = [int(image_id) for image_id, _score in caption_ranked]
    if not ranked_sources:
        return False
    source_union = {
        image_id
        for image_ids in ranked_sources.values()
        for image_id in image_ids[:FUSED_CANDIDATE_LIMIT]
    }
    rows_by_id = (
        await get_active_images_by_ids(list(source_union))
        if source_union and get_active_images_by_ids is not None
        else {}
    )
    scores, sources = fused_candidate_scores(
        ranked_sources=ranked_sources,
        rows_by_id=rows_by_id,
        source_weights=query_plan.source_weights,
        recency_weight=query_plan.recency_weight,
        limit=FUSED_CANDIDATE_LIMIT,
    )
    result.update({
        "id_filter": set(scores),
        "scores": scores,
        "search_mode": "fused" if len(sources) > 1 else sources[0],
        "search_sources": sources,
        "evidence_by_id": candidate_evidence(ranked_sources, scores),
        "ai_unavailable": True,
        "fallback_reason": "embedding_warming",
    })
    return True


async def resolve_text_search(
    q: str,
    *,
    deep: bool = False,
    normalize_query=normalize_search_query,
    apply_metadata_ids=None,
    get_search_query_embedding=None,
    store_search_query_embedding=None,
    metadata_ranked_image_ids=None,
    caption_ranked_image_ids=None,
    get_active_images_by_ids=None,
    caption_count_for_signature=None,
    extension_search_terms: set[str],
    get_settings,
    active_embedding_config=None,
    fast_search_embedding_config=None,
) -> dict:
    """Resolve a text query into either embedding IDs or metadata fallback text."""
    normalized_query = normalize_query(q)
    result = {
        "active": bool(normalized_query),
        "id_filter": None,
        "scores": {},
        "text_query": "",
        "search_mode": "",
        "search_sources": [],
        "ai_unavailable": False,
        "fallback_reason": "",
        "query_plan": {},
        "evidence_by_id": {},
    }
    if not normalized_query:
        return result

    extension_query = normalized_query.lower().lstrip(".")
    config_provider = active_embedding_config if deep else (fast_search_embedding_config or active_embedding_config)
    if config_provider is None:
        raise RuntimeError("resolve_text_search requires active_embedding_config")
    active_config = config_provider()
    query_plan = plan_search(normalized_query)
    result["query_plan"] = query_plan.as_dict()
    threshold = get_settings().get("search_similarity_threshold", 0.35)
    caption_signature = None
    if caption_count_for_signature is not None:
        try:
            caption_signature = await caption_count_for_signature()
        except Exception:
            caption_signature = None
    cache_key = (
        normalized_query.casefold(),
        bool(deep),
        active_config["model_key"],
        caption_signature,
        query_plan.version,
        query_plan.intent,
        f"{float(threshold or 0.0):.6f}",
    )
    cached = _text_search_resolution_cache.get(cache_key)
    if cached and cached["expires"] > time.monotonic():
        return dict(cached["data"])

    if extension_query in extension_search_terms:
        result.update({
            "text_query": normalized_query,
            "search_mode": "metadata",
            "search_sources": ["metadata"],
        })
        _text_search_resolution_cache[cache_key] = {
            "data": dict(result),
            "expires": time.monotonic() + _text_search_resolution_cache_ttl_seconds,
        }
        return result

    try:
        import embed_cache
        import numpy as np

        # The embedding model lived with the derivation fleet, which is gone
        # (2026-08-14 gutting order). A query still searches semantically when
        # its vector was cached by an earlier deep search; otherwise metadata,
        # filenames, and captions carry it — the same graceful floor the app
        # already stood on whenever the model was absent.
        text_vec = None
        if get_search_query_embedding is not None:
            cached_blob = await get_search_query_embedding(active_config, normalized_query)
            if cached_blob:
                cached_vec = np.frombuffer(cached_blob, dtype=np.float32).copy()
                if cached_vec.shape[0] == int(active_config["dimension"]):
                    text_vec = cached_vec

        if text_vec is None:
            if await _apply_lexical_search(
                result,
                normalized_query,
                query_plan=query_plan,
                metadata_ranked_image_ids=metadata_ranked_image_ids,
                caption_ranked_image_ids=caption_ranked_image_ids,
                get_active_images_by_ids=get_active_images_by_ids,
            ):
                return result
            result.update({
                "text_query": normalized_query,
                "search_mode": "metadata",
                "search_sources": ["metadata"],
            })
            if extension_query not in extension_search_terms and apply_metadata_ids is not None:
                await apply_metadata_ids(result, normalized_query)
            return result
        if text_vec is not None:
            image_ids, matrix = await embed_cache.get_matrix(active_config["model_key"])
            if image_ids is not None and matrix is not None and matrix.shape[1] == text_vec.shape[0]:
                # The matvec + score extraction is CPU-bound numpy work that
                # can take a while on a large archive; keep it off the loop.
                scores = await asyncio.get_event_loop().run_in_executor(
                    None,
                    _embedding_ranked_scores,
                    matrix,
                    text_vec,
                    image_ids,
                    threshold,
                    5000,
                )
                metadata_ranked = []
                caption_ranked = []
                if metadata_ranked_image_ids is not None:
                    try:
                        metadata_ranked = await metadata_ranked_image_ids(normalized_query)
                    except Exception:
                        metadata_ranked = []
                if caption_ranked_image_ids is not None:
                    try:
                        caption_ranked = await caption_ranked_image_ids(normalized_query)
                    except Exception:
                        caption_ranked = []
                ranked_sources = {}
                if scores:
                    ranked_sources["embedding"] = list(scores.keys())
                if metadata_ranked:
                    ranked_sources["metadata"] = [image_id for image_id, _score in metadata_ranked]
                if caption_ranked:
                    ranked_sources["captions"] = [image_id for image_id, _score in caption_ranked]
                rows_by_id = {}
                source_union = {
                    int(image_id)
                    for image_ids in ranked_sources.values()
                    for image_id in image_ids[:FUSED_CANDIDATE_LIMIT]
                }
                if source_union and get_active_images_by_ids is not None:
                    rows_by_id = await get_active_images_by_ids(list(source_union))
                fused_scores, sources = fused_candidate_scores(
                    ranked_sources=ranked_sources,
                    embedding_scores=scores,
                    rows_by_id=rows_by_id,
                    source_weights=query_plan.source_weights,
                    recency_weight=query_plan.recency_weight,
                    limit=FUSED_CANDIDATE_LIMIT,
                )
                if not fused_scores and scores:
                    fused_scores = scores
                    sources = ["embedding"]
                result.update({
                    "id_filter": set(fused_scores.keys()),
                    "scores": fused_scores,
                    "search_mode": "fused" if len(sources) > 1 else (sources[0] if sources else "embedding"),
                    "search_sources": sources,
                    "evidence_by_id": candidate_evidence(ranked_sources, fused_scores),
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
        "search_sources": ["metadata"],
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
    apply_metadata_ids=None,
) -> dict:
    return await resolve_text_search(
        q,
        deep=deep,
        normalize_query=normalize_search_query,
        apply_metadata_ids=apply_metadata_ids or apply_configured_metadata_search_ids,
        get_search_query_embedding=get_catalog_search_query_embedding,
        store_search_query_embedding=store_catalog_search_query_embedding,
        metadata_ranked_image_ids=db.metadata_search_ranked_image_ids,
        caption_ranked_image_ids=db.caption_search_ranked_image_ids,
        get_active_images_by_ids=partial(
            image_repository.get_active_images_by_ids, catalog_path()
        ),
        caption_count_for_signature=db.caption_count_for_signature,
        extension_search_terms=ranking_repository.IMAGE_EXTENSION_SEARCH_TERMS,
        active_embedding_config=(
            settings.active_embedding_config
            or settings.fast_search_embedding_config
        ),
        get_settings=settings.get_settings,
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
) -> dict:
    """Resolve whole-image text search as an image ID constraint.

    People used to be the other half of this, threaded in as two injected
    functions so that this module would not import the people repository. With
    `people` and `face_detections` holding zero rows the branch could only ever
    return early, so both the injection and the thing it was avoiding are gone.

    `people` is still accepted and still answered, because the UI sends it. It
    simply never narrows anything, which is the truth.
    """

    search = await resolve_text_search(q, deep=deep)
    search["people_ids"] = []
    search["people_active"] = False
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
    )
