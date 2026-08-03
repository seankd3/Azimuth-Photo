"""Dependency wiring that keeps the app shell out of feature/provider details."""

from features.ai import routes as ai_routes
from features.collections import routes as collection_routes
from features.export import routes as export_routes
from features.media import routes as media_routes
from features.publish import routes as publish_routes
from features.share import routes as share_routes


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


def configure_collection_routes(*, resolve_library_constraints=None) -> None:
    import db
    from features.collections import smart as smart_collections

    async def resolve_smart_detail(query, *, limit=200, offset=0):
        if resolve_library_constraints is None:
            raise RuntimeError("Smart collection routes are not configured")
        return await smart_collections.resolve_detail(
            query,
            limit=limit,
            offset=offset,
            resolve_library_constraints=resolve_library_constraints,
            count_rankings=lambda **kwargs: db.count_rankings(**kwargs),
            get_rankings=lambda **kwargs: db.get_rankings(**kwargs),
        )

    async def resolve_smart_summary(query):
        if resolve_library_constraints is None:
            raise RuntimeError("Smart collection routes are not configured")
        return await smart_collections.resolve_summary(
            query,
            resolve_library_constraints=resolve_library_constraints,
            count_rankings=lambda **kwargs: db.count_rankings(**kwargs),
            get_rankings=lambda **kwargs: db.get_rankings(**kwargs),
            db_signature=lambda: db.DB_PATH,
        )

    async def resolve_smart_image_ids(query):
        if resolve_library_constraints is None:
            raise RuntimeError("Smart collection routes are not configured")
        return await smart_collections.resolve_image_ids(
            query,
            resolve_library_constraints=resolve_library_constraints,
            count_rankings=lambda **kwargs: db.count_rankings(**kwargs),
            get_rankings=lambda **kwargs: db.get_rankings(**kwargs),
        )

    async def resolve_smart_materialized_image_ids(query):
        if resolve_library_constraints is None:
            raise RuntimeError("Smart collection routes are not configured")
        return await smart_collections.resolve_materialized_image_ids(
            query,
            resolve_library_constraints=resolve_library_constraints,
            count_rankings=lambda **kwargs: db.count_rankings(**kwargs),
            get_rankings=lambda **kwargs: db.get_rankings(**kwargs),
        )

    collection_routes.configure(
        resolve_smart_detail=resolve_smart_detail,
        resolve_smart_summary=resolve_smart_summary,
        resolve_smart_image_ids=resolve_smart_image_ids,
        resolve_smart_materialized_image_ids=resolve_smart_materialized_image_ids,
    )


def configure_share_routes(*, templates, resolve_library_constraints=None) -> None:
    import db
    from features.collections import smart as smart_collections

    async def resolve_smart_image_ids(query):
        if resolve_library_constraints is None:
            raise RuntimeError("Smart share routes are not configured")
        return await smart_collections.resolve_image_ids(
            query,
            resolve_library_constraints=resolve_library_constraints,
            count_rankings=lambda **kwargs: db.count_rankings(**kwargs),
            get_rankings=lambda **kwargs: db.get_rankings(**kwargs),
        )

    share_routes.configure(
        templates=templates,
        thumbnail_response=media_routes.thumbnail_response,
        resolve_smart_image_ids=resolve_smart_image_ids,
    )


def configure_publish_routes(*, templates, resolve_library_constraints=None, track_background_task=None) -> None:
    import db
    import thumbnails
    from features.collections import smart as smart_collections

    async def resolve_smart_image_ids(query):
        if resolve_library_constraints is None:
            raise RuntimeError("Smart publish routes are not configured")
        return await smart_collections.resolve_image_ids(
            query,
            resolve_library_constraints=resolve_library_constraints,
            count_rankings=lambda **kwargs: db.count_rankings(**kwargs),
            get_rankings=lambda **kwargs: db.get_rankings(**kwargs),
        )

    publish_routes.configure(
        templates=templates,
        resolve_smart_image_ids=resolve_smart_image_ids,
        thumbnails=thumbnails,
        track_background_task=track_background_task,
    )


def _smart_collection_image_ids_resolver(resolve_library_constraints):
    import db
    from features.collections import smart as smart_collections

    async def resolve(collection_id: int) -> set[int] | None:
        collection = await db.get_collection(collection_id, limit=1)
        if not collection or not collection.get("smart"):
            return None
        image_ids = await smart_collections.resolve_image_ids(
            collection.get("query") or {},
            resolve_library_constraints=resolve_library_constraints,
            count_rankings=lambda **kwargs: db.count_rankings(**kwargs),
            get_rankings=lambda **kwargs: db.get_rankings(**kwargs),
        )
        return set(image_ids)

    return resolve


def configure_library_service(
    *,
    resolve_library_constraints,
    schedule_thumbnail_prefetch,
    schedule_result_thumbnail_memory_warm,
    rankings_response_cache_ttl_seconds,
) -> None:
    from features.library import service as library_service

    library_service.configure(
        resolve_library_constraints=resolve_library_constraints,
        schedule_thumbnail_prefetch=schedule_thumbnail_prefetch,
        schedule_result_thumbnail_memory_warm=schedule_result_thumbnail_memory_warm,
        resolve_smart_collection_image_ids=_smart_collection_image_ids_resolver(resolve_library_constraints),
        rankings_response_cache_ttl_seconds=rankings_response_cache_ttl_seconds,
    )


def configure_compare_service(
    *,
    invalidate_rankings_cache,
    invalidate_interaction_response_cache,
    resolve_library_constraints,
    schedule_thumbnail_prefetch,
    schedule_cached_thumbnail_memory_warm,
    resolve_smart_collection_image_ids=None,
) -> None:
    from features.compare import service as compare_service

    compare_service.configure(
        invalidate_rankings_cache=invalidate_rankings_cache,
        invalidate_interaction_response_cache=invalidate_interaction_response_cache,
        resolve_library_constraints=resolve_library_constraints,
        schedule_thumbnail_prefetch=schedule_thumbnail_prefetch,
        schedule_cached_thumbnail_memory_warm=schedule_cached_thumbnail_memory_warm,
        resolve_smart_collection_image_ids=resolve_smart_collection_image_ids
        or _smart_collection_image_ids_resolver(resolve_library_constraints),
    )


def configure_query_constraints(
    *,
    text_search_resolution_cache_ttl_seconds,
) -> None:
    from core import query_constraints

    query_constraints.configure(
        text_search_resolution_cache_ttl_seconds=text_search_resolution_cache_ttl_seconds,
    )


def configure_export_routes(
    *,
    resolve_library_constraints,
) -> None:

    export_routes.configure(
        resolve_library_constraints=resolve_library_constraints,
    )
