from collections.abc import Callable


class CacheInvalidationBus:
    """Small fanout helper for cross-feature cache invalidation callbacks."""

    def __init__(self):
        self._listeners: list = []

    def register(self, listener) -> None:
        if listener not in self._listeners:
            self._listeners.append(listener)

    def invalidate(self, *args, **kwargs) -> None:
        for listener in list(self._listeners):
            listener(*args, **kwargs)


_compare_service = None
_library_service = None
_query_constraints = None
_invalidate_ai_status_response_cache: Callable[[], None] | None = None
_duplicates_cache: dict | None = None
_collections_cache: dict | None = None
_elo_propagation = None


def configure(
    *,
    compare_service,
    library_service,
    query_constraints,
    invalidate_ai_status_response_cache: Callable[[], None],
    duplicates_cache: dict,
    collections_cache: dict,
    elo_propagation,
) -> None:
    global _compare_service, _library_service, _query_constraints
    global _invalidate_ai_status_response_cache, _duplicates_cache
    global _collections_cache, _elo_propagation
    _compare_service = compare_service
    _library_service = library_service
    _query_constraints = query_constraints
    _invalidate_ai_status_response_cache = invalidate_ai_status_response_cache
    _duplicates_cache = duplicates_cache
    _collections_cache = collections_cache
    _elo_propagation = elo_propagation


def _configured():
    if (
        _compare_service is None
        or _library_service is None
        or _query_constraints is None
        or _invalidate_ai_status_response_cache is None
        or _duplicates_cache is None
        or _collections_cache is None
        or _elo_propagation is None
    ):
        raise RuntimeError("Cache event coordinator is not configured")
    return (
        _compare_service,
        _library_service,
        _query_constraints,
        _invalidate_ai_status_response_cache,
        _duplicates_cache,
        _collections_cache,
        _elo_propagation,
    )


def invalidate_pairing_cache(*, matchups: bool = False) -> int:
    compare_service, *_ = _configured()
    compare_service.invalidate_pairing_cache(matchups=matchups)
    return compare_service._visible_pairing_candidates_generation


def invalidate_rankings_cache() -> None:
    _, library_service, query_constraints, *_ = _configured()
    library_service.invalidate_rankings_response_cache()
    query_constraints.clear_text_search_caches()


def deep_search_query_embedding_stored(_model_key: str, _query: str) -> None:
    _, _, _, invalidate_ai_status_response_cache, _, _, _ = _configured()
    invalidate_rankings_cache()
    invalidate_ai_status_response_cache()


def invalidate_vector_derived_caches() -> None:
    *_, duplicates_cache, collections_cache, elo_propagation = _configured()
    duplicates_cache.update({"key": None, "data": None})
    collections_cache.update({"key": None, "data": None})
    elo_propagation.invalidate_prediction_cache()
    try:
        import embed_cache
        embed_cache.invalidate()
    except Exception:
        pass


def embedding_batch_stored(_model_key: str, _image_ids: list[int]) -> None:
    _, _, _, invalidate_ai_status_response_cache, _, _, _ = _configured()
    invalidate_rankings_cache()
    invalidate_ai_status_response_cache()
    invalidate_vector_derived_caches()


def invalidate_interaction_response_cache() -> None:
    compare_service, *_ = _configured()
    compare_service.invalidate_interaction_response_cache()
