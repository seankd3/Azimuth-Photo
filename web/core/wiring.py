"""Dependency wiring that keeps the app shell out of feature/provider details."""

from features.ai import routes as ai_routes
from features.cache import routes as cache_routes
from features.captions import routes as caption_routes
from features.catalog import routes as catalog_routes
from features.collections import routes as collection_routes
from features.compare import routes as compare_routes
from features.develop import import_routes
from features.export import routes as export_routes
from features.library import routes as library_routes
from features.media import routes as media_routes
from features.people import routes as people_routes
from features.publish import routes as publish_routes
from features.share import routes as share_routes
from features.shared import routes as shared_routes
from features.stacks import routes as stack_routes
from features.settings import routes as settings_routes
from features.trash import routes as trash_routes


def configure_people_routes() -> None:
    people_routes.reset_for_tests()


def configure_status_media_search_providers() -> None:
    import db
    import thumbnails
    from features.cache import status as cache_status_service
    from features.media import warm as media_warm
    from features.search import service as search_service
    from features.settings import status as settings_status

    cache_status_service.configure(
        cache_root=lambda: thumbnails.SSD_CACHE_DIR,
        get_catalog_image_counts=lambda: db.get_catalog_image_counts(),
        expire_settings_response_cache=settings_status.expire_settings_response_cache,
    )
    search_service.configure(
        cache_root=lambda: thumbnails.SSD_CACHE_DIR,
    )
    media_routes.configure(
        cached_image_ids=media_warm.cached_image_ids,
        schedule_cached_thumbnail_memory_warm=media_warm.schedule_cached_thumbnail_memory_warm,
        mark_image_missing=lambda image_id: db.mark_image_missing(image_id),
    )
    settings_status.configure(
        build_cache_status=cache_status_service.build_cache_status,
        build_ai_status=ai_routes.build_ai_status,
        people_status_payload=lambda: people_routes.people_status_payload(),
        get_catalog_image_counts=lambda: db.get_catalog_image_counts(),
        refresh_source_online_states=lambda: db.refresh_source_online_states(),
    )
    cache_routes.configure(
        build_ai_status=ai_routes.build_ai_status,
    )
    ai_routes.configure(
        invalidate_settings_response_cache=settings_status.invalidate_settings_response_cache,
        get_ai_status_counts=lambda: db.get_ai_status_counts(),
        count_embeddings_for_model=lambda config, **kwargs: db.count_embeddings_for_model(config, **kwargs),
    )
    caption_routes.configure(
        get_caption_status_counts=lambda **kwargs: db.get_caption_status_counts(**kwargs),
        get_image_caption=lambda **kwargs: db.get_image_caption(**kwargs),
        owner_update_caption=lambda **kwargs: db.owner_update_caption(**kwargs),
        get_tags=lambda **kwargs: db.get_tags(**kwargs),
        invalidate_settings_response_cache=settings_status.invalidate_settings_response_cache,
    )


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


def configure_catalog_routes() -> None:
    from core import cache_events
    from features.cache import status as cache_status_service

    catalog_routes.configure(
        invalidate_pairing_cache=lambda **kwargs: cache_events.invalidate_pairing_cache(**kwargs),
        invalidate_cache_status_cache=cache_status_service.invalidate_cache_status_cache,
    )


def configure_develop_import_routes() -> None:
    import thumbnails

    import_routes.configure(
        prefetch_thumbnails=lambda images: thumbnails.prefetch_images(
            images, "sm", limit=len(images)
        ),
    )


def configure_library_routes() -> None:
    import db
    from features.library import service as library_service

    library_routes.configure(
        rankings_handler=lambda **kwargs: library_service.api_rankings_impl(**kwargs),
    )
    library_service.configure_import_batches(
        get_import_batch_image_ids=lambda batch_id: db.get_import_batch_image_ids(batch_id),
    )
    library_service.configure_stacks(
        get_stack_representative_counts=lambda image_ids: db.stack_representative_counts(image_ids),
    )


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


def configure_stack_routes() -> None:
    from core import cache_events

    def invalidate_stack_dependent_caches() -> None:
        cache_events.invalidate_rankings_cache()
        cache_events.invalidate_ranking_count_cache()
        cache_events.invalidate_facet_caches()

    stack_routes.configure(
        invalidate_rankings_cache=invalidate_stack_dependent_caches,
    )


def configure_trash_routes() -> None:
    from core import cache_events
    from features.cache import status as cache_status_service
    from features.settings import status as settings_status

    def invalidate_trash_dependent_caches() -> None:
        cache_events.invalidate_rankings_cache()
        cache_events.invalidate_stats_cache()
        cache_events.invalidate_ranking_count_cache()
        cache_events.invalidate_facet_caches()
        cache_events.invalidate_pairing_cache(matchups=True)
        cache_events.invalidate_cached_image_ids_cache()
        cache_events.invalidate_filter_options_cache()
        cache_status_service.invalidate_cache_status_cache()
        settings_status.invalidate_settings_response_cache()

    trash_routes.configure(
        invalidate=invalidate_trash_dependent_caches,
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


def configure_shared_routes() -> None:
    import db

    shared_routes.configure(
        list_shares=lambda: db.list_active_collection_shares(),
        list_publishes=lambda: db.list_collection_publishes(),
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


def configure_settings_routes(
    *,
    settings_response_cache,
    settings_response_cache_ttl_seconds,
    build_settings_response,
    copy_settings_response,
    track_background_task,
    get_refreshing,
    set_refreshing,
    build_cache_status,
    build_ai_status,
    people_status_payload,
    invalidate_image_flag_caches,
    invalidate_pairing_cache,
    invalidate_cache_status_cache,
    invalidate_ai_status_response_cache,
    invalidate_settings_response_cache,
    invalidate_rankings_cache,
    invalidate_vector_derived_caches,
    get_stats=None,
    refresh_source_online_states=None,
) -> None:
    import db

    settings_routes.configure(
        settings_response_cache=settings_response_cache,
        settings_response_cache_ttl_seconds=settings_response_cache_ttl_seconds,
        build_settings_response=build_settings_response,
        copy_settings_response=copy_settings_response,
        track_background_task=track_background_task,
        get_refreshing=get_refreshing,
        set_refreshing=set_refreshing,
        get_stats=get_stats or (lambda: db.get_stats()),
        refresh_source_online_states=(
            refresh_source_online_states
            or (lambda: db.refresh_source_online_states())
        ),
        build_cache_status=build_cache_status,
        build_ai_status=build_ai_status,
        people_status_payload=people_status_payload,
        invalidate_image_flag_caches=invalidate_image_flag_caches,
        invalidate_pairing_cache=invalidate_pairing_cache,
        invalidate_cache_status_cache=invalidate_cache_status_cache,
        invalidate_ai_status_response_cache=invalidate_ai_status_response_cache,
        invalidate_settings_response_cache=invalidate_settings_response_cache,
        invalidate_rankings_cache=invalidate_rankings_cache,
        invalidate_vector_derived_caches=invalidate_vector_derived_caches,
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


def configure_compare_routes(
    *,
    schedule_pairing_propagation,
    invalidate_pairing_cache,
    patch_pairing_cache=None,
    add_past_matchups=None,
    record_active_mosaic_pick=None,
    record_active_comparison=None,
    undo_last_comparison=None,
    mosaic_next_handler=None,
) -> None:
    import db
    from features.compare import service as compare_service

    compare_routes.configure(
        patch_pairing_cache=patch_pairing_cache or compare_service.patch_pairing_cache,
        add_past_matchups=add_past_matchups or compare_service.add_past_matchups,
        schedule_pairing_propagation=schedule_pairing_propagation,
        invalidate_pairing_cache=invalidate_pairing_cache,
        record_active_mosaic_pick=record_active_mosaic_pick
        or (lambda picked_id, other_ids, action_id: db.record_active_mosaic_pick(picked_id, other_ids, action_id)),
        record_active_comparison=record_active_comparison
        or (
            lambda winner_id, loser_id, mode, **kwargs: db.record_active_comparison(
                winner_id,
                loser_id,
                mode,
                **kwargs,
            )
        ),
        undo_last_comparison=undo_last_comparison or (lambda: db.undo_last_comparison()),
        mosaic_next_handler=mosaic_next_handler,
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
    get_import_batch_image_ids=None,
) -> None:
    from features.library import service as library_service

    export_routes.configure(
        resolve_library_constraints=resolve_library_constraints,
        resolve_collection_scope=library_service._resolve_collection_scope,
        get_import_batch_image_ids=get_import_batch_image_ids,
    )
