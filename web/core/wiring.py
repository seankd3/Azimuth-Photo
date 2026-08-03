"""Dependency wiring that keeps the app shell out of feature/provider details."""

from features.ai import routes as ai_routes


def configure_cache_events() -> None:
    import db
    import elo_propagation
    from core import cache_events, query_constraints
    from features.compare import service as compare_service
    from features.library import service as library_service
    from features.search import service as search_service

    cache_events.configure(
        compare_service=compare_service,
        library_service=library_service,
        query_constraints=query_constraints,
        invalidate_ai_status_response_cache=ai_routes.invalidate_ai_status_response_cache,
        duplicates_cache=search_service._duplicates_cache,
        elo_propagation=elo_propagation,
    )
    db.register_embedding_batch_listener(cache_events.embedding_batch_stored)


