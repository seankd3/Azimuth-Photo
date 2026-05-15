import inspect

import db
import settings


def normalize_search_query(query: str) -> str:
    normalized = " ".join(str(query or "").split())
    max_length = int(getattr(settings, "MAX_DEEP_SEARCH_TERM_LENGTH", 160))
    return normalized[:max_length].strip()


def encode_text_with_config(encoder, query: str, config: dict):
    try:
        if len(inspect.signature(encoder).parameters) < 2:
            return encoder(query)
    except (TypeError, ValueError):
        pass
    return encoder(query, config)


async def resolve_cached_deep_search(query: str, *, allow_cold_load: bool = True) -> dict | None:
    normalized_query = normalize_search_query(query)
    if not normalized_query:
        return None
    try:
        import embed_cache
        import embedding_worker
        import numpy as np

        deep_config = settings.deep_search_embedding_config()
        blob = await db.get_deep_search_query_embedding(
            normalized_query,
            deep_config["model_key"],
        )
        if blob is None:
            return None
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
