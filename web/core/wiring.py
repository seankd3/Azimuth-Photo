"""Dependency wiring that keeps the app shell out of feature/provider details."""

from features.ai import routes as ai_routes
from features.cache import routes as cache_routes
from features.catalog import routes as catalog_routes
from features.compare import routes as compare_routes
from features.library import routes as library_routes
from features.media import routes as media_routes
from features.people import routes as people_routes
from features.search import routes as search_routes


def configure_database_backed_providers() -> None:
    import db
    import embed_cache
    import embedding_worker
    import elo_propagation
    import face_worker
    import helpers as app_helpers
    import scanner
    import thumbnails
    from features.catalog import metadata as catalog_metadata

    embed_cache.configure(
        active_embedding_model_key=lambda: db.active_embedding_model_key(),
        db_path=lambda: db.DB_PATH,
    )
    embedding_worker.configure(
        get_deep_search_cache_status=lambda config, terms=None: db.get_deep_search_cache_status(config, terms),
        get_catalog_image_counts=lambda: db.get_catalog_image_counts(),
        count_embeddings_for_model=lambda config, **kwargs: db.count_embeddings_for_model(config, **kwargs),
        get_unembedded_images=lambda **kwargs: db.get_unembedded_images(**kwargs),
        get_pending_deep_search_queries=lambda config, terms=None, **kwargs: db.get_pending_deep_search_queries(
            config,
            terms,
            **kwargs,
        ),
        store_deep_search_query_embedding=lambda config, query, blob: db.store_deep_search_query_embedding(
            config,
            query,
            blob,
        ),
        store_embeddings_batch=lambda rows, **kwargs: db.store_embeddings_batch(rows, **kwargs),
        get_embedding_count=lambda: db.get_embedding_count(),
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
        get_deep_search_cache_status=lambda config, terms=None: db.get_deep_search_cache_status(config, terms),
        list_deep_search_queries=lambda config: db.list_deep_search_queries(config),
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
    db.register_deep_search_query_embedding_listener(cache_events.deep_search_query_embedding_stored)


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
    from features.library import service as library_service

    library_routes.configure(
        rankings_handler=lambda **kwargs: library_service.api_rankings_impl(**kwargs),
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
