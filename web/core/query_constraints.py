import asyncio
import logging

from core.search_fusion import FUSED_CANDIDATE_LIMIT, candidate_evidence, fused_candidate_scores
# The one thing the ranking repository was still consulted for: which typed
# words mean "a file extension" rather than a search term.
IMAGE_EXTENSION_SEARCH_TERMS = frozenset(
    {"jpg", "jpeg", "png", "tif", "tiff", "webp", "heic", "heif",
     "cr2", "cr3", "arw", "dng", "nef", "orf", "raf", "rw2",
     "mp4", "mov", "avi", "m4v"}
)


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


async def resolve_configured_library_constraints(q: str = "", *, people: str = "", deep: bool = False) -> dict:
    """A query, as a set of image ids for a caller that narrows by one.

    This used to reach `resolve_text_search` — 192 lines that fused lexical
    search, a caption index, a metadata index, an embedding warm-up and a
    fallback ladder, and which nothing else had called since `search.search`
    replaced it. Export was the last caller of the last surviving entry point,
    so it asks the same search the search box asks and gets the same answer.

    `people` and `deep` are still accepted because the UI sends them. Neither
    narrows anything: `people` and `face_detections` hold zero rows, and depth
    was a parameter of the ladder that is gone. Answering honestly and doing
    nothing beats an argument that quietly means something else.
    """

    del people, deep
    import search as search_module

    from core.catalog_path import catalog_path
    from data import connection

    query = (q or "").strip()
    if not query:
        return {"id_filter": None, "text_query": "", "people_ids": [], "people_active": False}
    rows = search_module.search(connection.reading(catalog_path()), query, limit=100_000)
    return {
        "id_filter": {int(row["id"]) for row in rows},
        "text_query": query,
        "people_ids": [],
        "people_active": False,
    }
