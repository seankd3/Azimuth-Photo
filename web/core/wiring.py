"""Dependency wiring that keeps the app shell out of feature/provider details."""

from features.ai import routes as ai_routes
from features.cache import routes as cache_routes
from features.captions import routes as caption_routes
from features.catalog import routes as catalog_routes
from features.collections import routes as collection_routes
from features.compare import routes as compare_routes
from features.export import routes as export_routes
from features.library import routes as library_routes
from features.media import routes as media_routes
from features.people import routes as people_routes
from features.publish import routes as publish_routes
from features.search import routes as search_routes
from features.share import routes as share_routes
from features.stacks import routes as stack_routes
from features.settings import routes as settings_routes
from features.trash import routes as trash_routes


def configure_database_backed_providers() -> None:
    import db
    import embed_cache
    import embedding_worker
    import caption_worker
    import elo_propagation
    import face_worker
    import helpers as app_helpers
    import scanner
    import thumbnails
    from core import cache_events
    from features.catalog import metadata as catalog_metadata

    embed_cache.configure(
        active_embedding_model_key=lambda: db.active_embedding_model_key(),
        db_path=lambda: db.DB_PATH,
    )
    embedding_worker.configure(
        get_catalog_image_counts=lambda: db.get_catalog_image_counts(),
        count_embeddings_for_model=lambda config, **kwargs: db.count_embeddings_for_model(config, **kwargs),
        get_unembedded_images=lambda **kwargs: db.get_unembedded_images(**kwargs),
        store_embeddings_batch=lambda rows, **kwargs: db.store_embeddings_batch(rows, **kwargs),
        get_embedding_count=lambda: db.get_embedding_count(),
    )
    caption_worker.configure(
        count_images_needing_captions=lambda **kwargs: db.count_images_needing_captions(**kwargs),
        get_images_needing_captions=lambda **kwargs: db.get_images_needing_captions(**kwargs),
        store_caption_result=lambda **kwargs: db.store_caption_result(**kwargs),
    )
    elo_propagation.configure(
        active_embedding_model_key=lambda: db.active_embedding_model_key(),
        get_active_images_by_ids=lambda image_ids: db.get_active_images_by_ids(image_ids),
        get_db=lambda: db.get_db(),
        invalidate_rating_stats_cache=lambda: db.invalidate_rating_stats_cache(),
    )
    face_worker.configure(
        count_images_needing_faces=lambda **kwargs: db.count_images_needing_faces(**kwargs),
        get_images_needing_faces=lambda **kwargs: db.get_images_needing_faces(**kwargs),
        store_face_scan_result=lambda **kwargs: db.store_face_scan_result(**kwargs),
        cluster_unassigned_faces=lambda **kwargs: db.cluster_unassigned_faces(**kwargs),
    )
    thumbnails.configure_data_providers(
        db_path=lambda: db.DB_PATH,
        get_db=lambda: db.get_db(),
        batch_set_orientations=lambda updates: db.batch_set_orientations(updates),
        mark_image_missing_sync=lambda image_id: db.mark_image_missing_sync(image_id),
        invalidate_cached_image_ids_cache=lambda cache_root=None, size=None: db.invalidate_cached_image_ids_cache(
            cache_root=cache_root,
            size=size,
        ),
        note_cached_image_ids_added=lambda cache_root, size, image_ids: db.note_cached_image_ids_added(
            cache_root,
            size,
            image_ids,
        ),
    )
    app_helpers.configure(
        cached_image_ids=lambda image_ids, size, cache_root: db.get_cached_image_ids(image_ids, size, cache_root),
        get_active_images_by_ids=lambda image_ids: db.get_active_images_by_ids(image_ids),
        star_thresholds=db.STAR_THRESHOLDS,
    )
    catalog_metadata.configure(
        db_path=lambda: db.DB_PATH,
        invalidate_filter_options_cache=db._invalidate_filter_options_cache,
        invalidate_rankings_cache=cache_events.invalidate_rankings_cache,
    )
    scanner.configure(
        mark_source_scan_started=lambda source_id: db.mark_source_scan_started(source_id),
        insert_images_batch=lambda rows, **kwargs: db.insert_images_batch(rows, **kwargs),
        mark_source_scan_finished=lambda source_id, **kwargs: db.mark_source_scan_finished(source_id, **kwargs),
    )


def configure_people_routes() -> None:
    import db

    people_routes.configure(
        get_people_review=lambda **kwargs: db.get_people_review(**kwargs),
        get_people_status_counts=lambda **kwargs: db.get_people_status_counts(**kwargs),
        get_face_thumbnail_context=lambda face_id: db.get_face_thumbnail_context(face_id),
        label_person=lambda person_id, name: db.label_person(person_id, name),
        merge_people=lambda source_person_id, target_person_id: db.merge_people(
            source_person_id,
            target_person_id,
        ),
        reject_merge_suggestion=lambda suggestion_id: db.reject_merge_suggestion(suggestion_id),
        assign_face=lambda face_id, **kwargs: db.assign_face(face_id, **kwargs),
        ignore_face=lambda face_id: db.ignore_face(face_id),
        ignore_person=lambda person_id: db.ignore_person(person_id),
    )


def configure_status_media_search_providers() -> None:
    import db
    import thumbnails
    from features.cache import status as cache_status_service
    from features.media import warm as media_warm
    from features.search import service as search_service
    from features.settings import status as settings_status

    cache_status_service.configure(
        cache_root=lambda: thumbnails.SSD_CACHE_DIR,
        db_path=lambda: db.DB_PATH,
        get_catalog_image_counts=lambda: db.get_catalog_image_counts(),
        expire_settings_response_cache=settings_status.expire_settings_response_cache,
    )
    search_service.configure(
        cache_root=lambda: thumbnails.SSD_CACHE_DIR,
        db_path=lambda: db.DB_PATH,
    )
    media_routes.configure(
        cached_image_ids=media_warm.cached_image_ids,
        schedule_cached_thumbnail_memory_warm=media_warm.schedule_cached_thumbnail_memory_warm,
        db_path=lambda: db.DB_PATH,
    )
    settings_status.configure(
        build_cache_status=cache_status_service.build_cache_status,
        build_ai_status=ai_routes.build_ai_status,
        people_status_payload=lambda: people_routes.people_status_payload(),
        db_path=lambda: db.DB_PATH,
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
        collections_cache=search_service._collections_cache,
        elo_propagation=elo_propagation,
    )
    db.register_embedding_batch_listener(cache_events.embedding_batch_stored)


def configure_catalog_routes() -> None:
    import db
    from core import cache_events
    from features.cache import status as cache_status_service

    catalog_routes.configure(
        invalidate_pairing_cache=lambda **kwargs: cache_events.invalidate_pairing_cache(**kwargs),
        invalidate_cache_status_cache=cache_status_service.invalidate_cache_status_cache,
        db_path=lambda: db.DB_PATH,
        get_recent_active_images=lambda **kwargs: db.get_recent_active_images(**kwargs),
        add_or_restore_source=lambda path: db.add_or_restore_source(path),
        get_scan_folder=lambda: db.get_scan_folder(),
        get_catalog_summary=lambda: db.get_catalog_summary(),
        get_source=lambda source_id: db.get_source(source_id),
        remove_source_keep_data=lambda source_id: db.remove_source_keep_data(source_id),
        get_source_image_ids=lambda source_id: db.get_source_image_ids(source_id),
        purge_source_catalog_data=lambda source_id: db.purge_source_catalog_data(source_id),
        get_catalog_image_counts=lambda: db.get_catalog_image_counts(),
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


def configure_search_routes() -> None:
    import db
    from core import cache_events, query_constraints
    from core import requests as request_helpers
    from core import responses as response_helpers
    from features.catalog import metadata as catalog_metadata
    from features.media import warm as media_warm
    from features.search import service as search_service

    search_routes.configure(
        api_rankings=lambda **kwargs: library_routes.api_rankings(**kwargs),
        visible_embedding_page=search_service.visible_embedding_page,
        cached_image_ids=media_warm.cached_image_ids,
        metadata_update_tuple=catalog_metadata.metadata_update_tuple,
        invalidate_pairing_cache=lambda **kwargs: cache_events.invalidate_pairing_cache(**kwargs),
        normalize_search_query=query_constraints.normalize_search_query,
        clamp_int=request_helpers.clamp_int,
        visibility_counts=response_helpers.visibility_counts,
        active_embedding_model_key=lambda: db.active_embedding_model_key(),
        db_signature=lambda: db.DB_PATH,
        get_active_source_id_set=lambda: db.get_active_source_id_set(),
        get_active_images_by_ids=lambda image_ids: db.get_active_images_by_ids(image_ids),
        get_image_by_id=lambda image_id: db.get_image_by_id(image_id),
        batch_update_metadata=lambda updates: db.batch_update_metadata(updates),
        duplicates_cache=search_service._duplicates_cache,
        collections_cache=search_service._collections_cache,
    )


def configure_collection_routes(*, resolve_library_constraints=None) -> None:
    import db
    from features.collections import suggestions as collection_suggestions
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

    collection_routes.configure(
        create_collection=lambda **kwargs: db.create_collection(**kwargs),
        list_collections=lambda: db.list_collections(),
        get_collection=lambda collection_id, **kwargs: db.get_collection(collection_id, **kwargs),
        rename_collection=lambda collection_id, **kwargs: db.rename_collection(collection_id, **kwargs),
        delete_collection=lambda collection_id: db.delete_collection(collection_id),
        add_collection_images=lambda collection_id, image_ids: db.add_collection_images(collection_id, image_ids),
        remove_collection_images=lambda collection_id, image_ids: db.remove_collection_images(collection_id, image_ids),
        collection_is_smart=lambda collection_id: db.collection_is_smart(collection_id),
        resolve_smart_detail=resolve_smart_detail,
        resolve_smart_summary=resolve_smart_summary,
        resolve_smart_image_ids=resolve_smart_image_ids,
        get_suggestions=lambda: collection_suggestions.collection_suggestions(
            db.DB_PATH,
            db_signature=db.DB_PATH,
        ),
    )


def configure_stack_routes() -> None:
    import db
    from core import cache_events

    def invalidate_stack_dependent_caches() -> None:
        cache_events.invalidate_rankings_cache()
        cache_events.invalidate_ranking_count_cache()
        cache_events.invalidate_facet_caches()

    stack_routes.configure(
        db_path=lambda: db.DB_PATH,
        invalidate_rankings_cache=invalidate_stack_dependent_caches,
    )


def configure_trash_routes() -> None:
    import db
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
        db_path=lambda: db.DB_PATH,
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
        create_or_rotate_share=lambda collection_id, **kwargs: db.create_or_rotate_share(collection_id, **kwargs),
        get_share=lambda collection_id: db.get_collection_share(collection_id),
        revoke_share=lambda collection_id: db.revoke_collection_share(collection_id),
        set_share_password=lambda collection_id, password_hash: db.set_collection_share_password(
            collection_id,
            password_hash,
        ),
        record_share_view=lambda token: db.record_share_view(token),
        resolve_token=lambda token: db.resolve_share_token(token),
        token_allows_image=lambda token, image_id: db.share_token_allows_image(token, image_id),
        set_favorite=lambda share_id, image_id, on, client_name=None: db.set_share_favorite(
            share_id,
            image_id,
            on,
            client_name=client_name,
        ),
        list_favorites=lambda share_id: db.list_share_favorites(share_id),
        favorites_for_collection=lambda collection_id: db.favorites_for_collection(collection_id),
        thumbnail_response=media_routes.thumbnail_response,
        get_collection=lambda collection_id, **kwargs: db.get_collection(collection_id, **kwargs),
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
        get_collection=lambda collection_id, **kwargs: db.get_collection(collection_id, **kwargs),
        get_images_by_ids=lambda image_ids: db.get_images_by_ids(image_ids),
        collection_image_ids=lambda collection_id: db.collection_image_ids(collection_id),
        resolve_smart_image_ids=resolve_smart_image_ids,
        get_publish=lambda collection_id: db.get_collection_publish(collection_id),
        list_publishes=lambda: db.list_collection_publishes(),
        upsert_publish=lambda **kwargs: db.upsert_collection_publish(**kwargs),
        delete_publish=lambda collection_id: db.delete_collection_publish(collection_id),
        slug_available=lambda slug, **kwargs: db.collection_publish_slug_available(slug, **kwargs),
        thumbnails=thumbnails,
        track_background_task=track_background_task,
    )


def configure_library_service(
    *,
    resolve_library_constraints,
    cache_root,
    clamp_int,
    normalize_search_query,
    schedule_thumbnail_prefetch,
    schedule_result_thumbnail_memory_warm,
    rankings_response_cache_ttl_seconds,
) -> None:
    import db
    from features.library import service as library_service
    from features.library import taste as taste_service

    taste_service.configure(
        db_path=lambda: db.DB_PATH,
        db_signature=lambda: db.DB_PATH,
    )
    library_service.configure(
        resolve_library_constraints=resolve_library_constraints,
        cache_root=cache_root,
        clamp_int=clamp_int,
        normalize_search_query=normalize_search_query,
        schedule_thumbnail_prefetch=schedule_thumbnail_prefetch,
        schedule_result_thumbnail_memory_warm=schedule_result_thumbnail_memory_warm,
        extension_search_terms=lambda: db.IMAGE_EXTENSION_SEARCH_TERMS,
        db_signature=lambda: db.DB_PATH,
        get_date_groups=lambda **kwargs: db.get_date_groups(**kwargs),
        get_map_markers=lambda **kwargs: db.get_map_markers(**kwargs),
        get_filter_options=lambda: db.get_filter_options(),
        get_stats=lambda: db.get_stats(),
        count_rankings=lambda **kwargs: db.count_rankings(**kwargs),
        get_rankings=lambda **kwargs: db.get_rankings(**kwargs),
        get_rank_quality=lambda **kwargs: db.rank_quality(**kwargs),
        get_date_histogram=lambda **kwargs: db.date_histogram(**kwargs),
        get_scope_counts=lambda **kwargs: db.scope_counts(**kwargs),
        get_visible_pairing_pool_counts=lambda size, cache_root: db.get_visible_pairing_pool_counts(
            size,
            cache_root,
        ),
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
    db_path=None,
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
        db_path=db_path or (lambda: db.DB_PATH),
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
    cache_root,
    resolve_library_constraints,
    schedule_thumbnail_prefetch,
    schedule_cached_thumbnail_memory_warm,
    db_signature=None,
    get_active_images_for_pairing=None,
    get_past_matchups=None,
    get_visible_past_matchups=None,
    get_past_matchups_for_image_ids=None,
    get_active_images_by_ids=None,
    get_visible_images_for_pairing=None,
    get_visible_orientation_pairing_pool_counts=None,
    count_rankings=None,
    get_rankings=None,
    get_visible_pairing_pool_counts=None,
    get_top_images=None,
    get_collection_image_ids=None,
) -> None:
    import db
    from features.compare import service as compare_service

    compare_service.configure(
        invalidate_rankings_cache=invalidate_rankings_cache,
        invalidate_interaction_response_cache=invalidate_interaction_response_cache,
        cache_root=cache_root,
        resolve_library_constraints=resolve_library_constraints,
        schedule_thumbnail_prefetch=schedule_thumbnail_prefetch,
        schedule_cached_thumbnail_memory_warm=schedule_cached_thumbnail_memory_warm,
        db_signature=db_signature or (lambda: db.DB_PATH),
        get_active_images_for_pairing=get_active_images_for_pairing or (lambda: db.get_active_images_for_pairing()),
        get_past_matchups=get_past_matchups or (lambda: db.get_past_matchups()),
        get_visible_past_matchups=get_visible_past_matchups
        or (lambda size, cache_root: db.get_visible_past_matchups(size, cache_root)),
        get_past_matchups_for_image_ids=get_past_matchups_for_image_ids
        or (lambda image_ids: db.get_past_matchups_for_image_ids(image_ids)),
        get_active_images_by_ids=get_active_images_by_ids or (lambda image_ids: db.get_active_images_by_ids(image_ids)),
        get_visible_images_for_pairing=get_visible_images_for_pairing
        or (lambda size, cache_root, **kwargs: db.get_visible_images_for_pairing(size, cache_root, **kwargs)),
        get_visible_orientation_pairing_pool_counts=get_visible_orientation_pairing_pool_counts
        or (
            lambda size, cache_root, orientation: db.get_visible_orientation_pairing_pool_counts(
                size,
                cache_root,
                orientation,
            )
        ),
        count_rankings=count_rankings or (lambda **kwargs: db.count_rankings(**kwargs)),
        get_rankings=get_rankings or (lambda **kwargs: db.get_rankings(**kwargs)),
        get_visible_pairing_pool_counts=get_visible_pairing_pool_counts
        or (lambda size, cache_root: db.get_visible_pairing_pool_counts(size, cache_root)),
        get_top_images=get_top_images or (lambda **kwargs: db.get_top_images(**kwargs)),
        get_collection_image_ids=get_collection_image_ids
        or (lambda collection_id: db.collection_image_ids(collection_id)),
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
    compare_next_handler=None,
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
        compare_next_handler=compare_next_handler,
    )


def configure_query_constraints(
    *,
    text_search_resolution_cache_ttl_seconds,
) -> None:
    import db
    import settings
    from core import query_constraints

    query_constraints.configure(
        extension_search_terms=db.IMAGE_EXTENSION_SEARCH_TERMS,
        metadata_search_image_ids=lambda query: db.metadata_search_image_ids(query),
        metadata_search_ranked_image_ids=lambda query: db.metadata_search_ranked_image_ids(query),
        caption_search_ranked_image_ids=lambda query: db.caption_search_ranked_image_ids(query),
        get_active_images_by_ids=lambda image_ids: db.get_active_images_by_ids(image_ids),
        caption_count_for_signature=lambda: db.caption_count_for_signature(),
        active_embedding_config=settings.active_embedding_config,
        fast_search_embedding_config=settings.fast_search_embedding_config,
        get_settings=settings.get_settings,
        parse_people_ids=db.parse_people_ids,
        get_people_image_id_filter=lambda people_ids: db.get_people_image_id_filter(people_ids),
        text_search_resolution_cache_ttl_seconds=text_search_resolution_cache_ttl_seconds,
        get_search_query_embedding=db.get_search_query_embedding,
        store_search_query_embedding=db.store_search_query_embedding,
    )


def configure_export_routes(
    *,
    resolve_library_constraints,
    db_path,
    get_import_batch_image_ids=None,
) -> None:
    export_routes.configure(
        resolve_library_constraints=resolve_library_constraints,
        db_path=db_path,
        get_import_batch_image_ids=get_import_batch_image_ids,
    )
