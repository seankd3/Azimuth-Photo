"""What a write makes stale — which, for a count, is nothing.

This file held twenty-six invalidators. Seven of them had already become empty
bodies, kept "as a name so its callers need not all change at once"; the rest
swept nine module-global dictionaries holding numbers that cost between 0.01 ms
and 27 ms to read. A count that is cheaper to compute than to remember does not
need remembering, so those went and their call sites with them.

What is left are the two caches that genuinely earn their keep — text-search
results and the embedding matrix, both expensive to rebuild — and the embedding
batch notification, which was never a cache event at all.
"""

import logging

logger = logging.getLogger(__name__)

embedding_batch_listeners = []


def register_embedding_batch_listener(listener) -> None:
    if listener not in embedding_batch_listeners:
        embedding_batch_listeners.append(listener)


def notify_embedding_batch_stored(model_key: str, image_ids: list[int]) -> None:
    for listener in list(embedding_batch_listeners):
        try:
            listener(model_key, image_ids)
        except Exception:
            logger.warning(
                "Embedding batch listener failed for model %r",
                model_key,
                exc_info=True,
            )


def invalidate_rankings_cache() -> None:
    from core import query_constraints

    query_constraints.clear_text_search_caches()


def invalidate_vector_derived_caches(*, invalidate_embedding_matrix: bool = True) -> None:
    from features.search.service import _duplicates_cache as duplicates_cache

    duplicates_cache.update({"key": None, "data": None})
    if not invalidate_embedding_matrix:
        return
    try:
        import embed_cache

        embed_cache.invalidate()
    except Exception:
        pass


def embedding_batch_stored(_model_key: str, _image_ids: list[int]) -> None:
    invalidate_rankings_cache()
    invalidate_vector_derived_caches(invalidate_embedding_matrix=False)


def register_with_db() -> None:
    """Hook the embedding-batch listener up. Called once, as the app starts."""

    import db

    db.register_embedding_batch_listener(embedding_batch_stored)
