import logging

from core.numbers import increment_cached_int  # noqa: F401  (re-exported)
import time as _time

from data.repositories import cache_entries as cache_entry_repository
from data.repositories import catalog as catalog_repository
from data.repositories import embeddings as embedding_repository
from data.repositories import filter_options as filter_options_repository
from data.repositories import ratings as rating_repository
from data.repositories import rankings as ranking_repository
from data.repositories import stats as stats_repository


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




def invalidate_pairing_cache(*, matchups: bool = False) -> int:
    from features.compare import service as compare_service

    compare_service.invalidate_pairing_cache(matchups=matchups)
    return compare_service._visible_pairing_candidates_generation


def invalidate_rankings_cache() -> None:
    from core import query_constraints
    from features.library import service as library_service

    library_service.invalidate_rankings_response_cache()
    query_constraints.clear_text_search_caches()
    _invalidate_smart_collection_cache()


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
    from features.ai.routes import invalidate_ai_status_response_cache

    invalidate_rankings_cache()
    invalidate_ai_status_response_cache()
    invalidate_vector_derived_caches(invalidate_embedding_matrix=False)


def invalidate_interaction_response_cache() -> None:
    from features.compare import service as compare_service

    compare_service.invalidate_interaction_response_cache()


def invalidate_stats_cache() -> None:
    stats_repository.invalidate_full_stats_cache()
    invalidate_past_matchups_cache()
    stats_repository.invalidate_catalog_image_counts_cache()
    invalidate_catalog_cache()
    invalidate_facet_caches()
    invalidate_ranking_count_cache()
    invalidate_rankable_image_ids_cache()
    invalidate_embedding_count_cache()
    invalidate_active_source_ids_cache()


def invalidate_ai_status_counts_cache() -> None:
    stats_repository.invalidate_ai_status_counts_cache()


def invalidate_catalog_summary_cache() -> None:
    catalog_repository.invalidate_catalog_summary_cache()


def invalidate_rating_stats_cache() -> None:
    stats_repository.invalidate_full_stats_cache()
    invalidate_past_matchups_cache()
    invalidate_catalog_summary_cache()
    invalidate_rating_facet_caches()
    invalidate_rating_ranking_count_cache()
    invalidate_ai_status_counts_cache()
    _schedule_stored_star_refresh()


def invalidate_people_dependent_caches(*, filter_options_invalidator=None) -> None:
    invalidate_ranking_count_cache()
    invalidate_facet_caches()
    if filter_options_invalidator is None:
        invalidate_filter_options_cache()
    else:
        filter_options_invalidator()


def patch_direct_rating_stats_cache(pair_delta: int, rated_image_delta: int) -> None:
    pair_delta = int(pair_delta or 0)
    rated_image_delta = int(rated_image_delta or 0)
    active_cap = None
    stats_cache = stats_repository._stats_cache
    if stats_cache["data"] and _time.time() < stats_cache["expires"]:
        stats = stats_cache["data"]
        active_cap = int(stats.get("active_images") or stats.get("total_images") or 0)
        for key in (
            "total_comparisons",
            "total_catalog_comparisons",
            "direct_comparison_rows",
            "direct_catalog_comparison_rows",
            "ranking_signal_count",
            "catalog_ranking_signal_count",
        ):
            increment_cached_int(stats, key, pair_delta)
        increment_cached_int(stats, "rated_images", rated_image_delta, cap=active_cap)
        invalidate_catalog_summary_cache()
    else:
        stats_repository.invalidate_full_stats_cache()
        invalidate_catalog_summary_cache()

    stats_repository.patch_ai_status_direct_rating_counts(
        pair_delta,
        rated_image_delta,
        active_cap=active_cap,
    )

    invalidate_rating_facet_caches()
    invalidate_rating_ranking_count_cache()
    _schedule_stored_star_refresh()


def _schedule_stored_star_refresh() -> None:
    """Stars are a stored projection of Elo — re-persist after rankings move."""
    try:
        import db
        from features.sync import elo_stars

        elo_stars.schedule_stored_stars_refresh(db.DB_PATH)
    except Exception:
        logger.debug("Stored star refresh scheduling skipped", exc_info=True)


def invalidate_filter_options_cache() -> None:
    filter_options_repository.invalidate_filter_options_cache()


def clear_filter_options_cache() -> None:
    filter_options_repository.clear_filter_options_cache()


def invalidate_catalog_cache() -> None:
    catalog_repository.invalidate_catalog_cache()


def invalidate_facet_caches() -> None:
    ranking_repository.invalidate_facet_caches()


def invalidate_visible_facet_caches(cache_root: str | None = None, size: str | None = None) -> None:
    ranking_repository.invalidate_visible_facet_caches(cache_root, size)


def invalidate_rating_facet_caches() -> None:
    ranking_repository.invalidate_rating_facet_caches()


def invalidate_ranking_count_cache() -> None:
    ranking_repository.invalidate_ranking_count_cache()
    rating_repository.invalidate_visible_pairing_pool_counts_cache()
    _invalidate_smart_collection_cache()


cache_scope_matches = ranking_repository.cache_scope_matches


def invalidate_visible_cache_dependent_counts(
    cache_root: str | None = None,
    size: str | None = None,
) -> None:
    ranking_repository.invalidate_visible_cache_dependent_counts(cache_root, size)
    rating_repository.invalidate_visible_pairing_pool_counts_cache(cache_root, size)


def invalidate_rating_ranking_count_cache() -> None:
    ranking_repository.invalidate_rating_ranking_count_cache()


def invalidate_cached_image_ids_cache(
    cache_root: str | None = None,
    size: str | None = None,
) -> None:
    invalidate_visible_cache_dependent_counts(cache_root, size)
    invalidate_visible_facet_caches(cache_root, size)
    cache_entry_repository.invalidate_cached_image_ids_cache(cache_root=cache_root, size=size)


def note_cached_image_ids_added(cache_root: str, size: str, image_ids) -> None:
    added_ids = tuple(image_ids or ())
    cache_entry_repository.note_cached_image_ids_added(cache_root, size, added_ids)
    if not added_ids:
        return
    invalidate_visible_cache_dependent_counts(cache_root, size)
    invalidate_visible_facet_caches(cache_root, size)
    if size == "sm":
        # Preview readiness on cards changed — drop response payloads only.
        # Taste/Elo ordered-id caches do not depend on thumbnail rows; clearing
        # them here made every taste request rebuild (18–30s) during pregen.
        from features.library import service as library_service

        library_service.invalidate_rankings_response_cache(order_caches=False)


def invalidate_rankable_image_ids_cache() -> None:
    ranking_repository.invalidate_rankable_image_ids_cache()


def _invalidate_smart_collection_cache() -> None:
    try:
        from features.collections import smart as smart_collections
        smart_collections.invalidate_smart_collection_cache()
    except Exception:
        logger.debug("Smart collection cache invalidation skipped", exc_info=True)


def invalidate_embedding_count_cache() -> None:
    embedding_repository.invalidate_embedding_count_cache()
    invalidate_ai_status_counts_cache()


def invalidate_active_source_ids_cache() -> None:
    catalog_repository.invalidate_active_source_ids_cache()


def invalidate_past_matchups_cache() -> None:
    rating_repository.invalidate_past_matchups_cache()


def invalidate_image_flag_caches() -> None:
    """Everything a flag change makes stale."""

    import db  # deferred: db imports this module

    invalidate_rankings_cache()
    db._invalidate_ranking_count_cache()
    db._invalidate_filter_options_cache()


def register_with_db() -> None:
    """Hook the embedding-batch listener up. Called once, as the app starts."""

    import db

    db.register_embedding_batch_listener(embedding_batch_stored)
