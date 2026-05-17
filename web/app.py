import asyncio
import copy
import json
import os
import time
import uuid

from fastapi import BackgroundTasks, Request
from fastapi.responses import FileResponse, JSONResponse

import ai_models
import db
import embed_cache
import embedding_worker
import elo_propagation
import face_worker
import helpers as app_helpers
import pairing
import resource_governor
import scanner
import settings
import thumbnails
from core.app_factory import (
    INTERACTION_CACHE_WARMUP_DELAY_SECONDS,
    create_app_shell,
)
from core import background as background_runtime
from core import cache_events
from core import query_constraints
from core import requests as request_helpers
from core import responses as response_helpers
from features.catalog import metadata as catalog_metadata
from features.compare import routes as compare_routes
from features.compare import service as compare_service
from features.export import routes as export_routes
from features.library import routes as library_routes
from features.library import service as library_service
from features.media import warm as media_warm
from features.search import service as search_service


APP_DIR = os.path.dirname(__file__)
_STARTED_AT = time.time()
_app_shell = create_app_shell(base_dir=APP_DIR, started_at=_STARTED_AT)
_runtime_services = _app_shell.runtime_services
if _runtime_services is None:
    raise RuntimeError("App runtime services were not configured")
app = _app_shell.app
templates = _app_shell.templates

_BROWSER_IMAGE_EXTENSIONS = thumbnails.BROWSER_ORIGINAL_EXTENSIONS
_IDLE_ACTIVITY_EXCLUDED_PATHS = _app_shell.idle_activity_excluded_paths
_background_task_tracker = _app_shell.background_task_tracker
_BACKGROUND_TASKS = _app_shell.background_tasks
_track_background_task = _app_shell.track_background_task
_positive_int = request_helpers.positive_int
_clamp_int = request_helpers.clamp_int
_json_object = request_helpers.json_object
_ranking_signal_count = app_helpers.ranking_signal_count
_has_ranking_signal = app_helpers.has_ranking_signal


_static_assets = _app_shell.static_assets
_GIT_COMMIT = _app_shell.git_commit
_static_version = _app_shell.static_version
_template_context = _app_shell.template_context
_warm_templates = _app_shell.warm_templates
_smoke_mode_enabled = background_runtime.smoke_mode_enabled


track_idle_activity = _app_shell.idle_activity_middleware
if track_idle_activity is None:
    raise RuntimeError("App idle activity middleware was not configured")


classify_orientations_background = catalog_metadata.classify_orientations_background
_metadata_update_tuple = catalog_metadata.metadata_update_tuple
scan_metadata_background = catalog_metadata.scan_metadata_background


# --- Mosaic Ranking API ---

# Cache for active pairing images. The default compare/mosaic path uses the
# smaller visible-candidate cache below; this broader cache is for filtered
# paths and is invalidated when ratings change.
_pairing_cache = compare_service._pairing_cache
_matchups_cache = compare_service._matchups_cache
_visible_matchups_cache = compare_service._visible_matchups_cache
_visible_pairing_candidates_cache = compare_service._visible_pairing_candidates_cache
_visible_pairing_candidates_refreshing = compare_service._visible_pairing_candidates_refreshing
_rankings_response_cache = library_service._rankings_response_cache
_text_search_resolution_cache = query_constraints._text_search_resolution_cache
_deep_search_query_record_cache = query_constraints._deep_search_query_record_cache
_interaction_response_cache = compare_service._interaction_response_cache
_visible_pairing_candidates_cache_ttl_seconds = compare_service._visible_pairing_candidates_cache_ttl_seconds
_patched_pairing_candidates_ttl_seconds = compare_service._patched_pairing_candidates_ttl_seconds
# Rankings are invalidated explicitly by rating/flag/catalog changes. Keep the
# idle TTL long so returning to the app does not pay a cold rebuild tax.
_rankings_response_cache_ttl_seconds = library_service._rankings_response_cache_ttl_seconds
_text_search_resolution_cache_ttl_seconds = query_constraints._text_search_resolution_cache_ttl_seconds
_deep_search_query_record_cache_ttl_seconds = query_constraints._deep_search_query_record_cache_ttl_seconds
_interaction_response_cache_ttl_seconds = compare_service._interaction_response_cache_ttl_seconds
_thumbnail_prefetch_inflight = media_warm._thumbnail_prefetch_inflight
_thumbnail_memory_warm_inflight = media_warm._thumbnail_memory_warm_inflight
_SWISS_PAIR_WINDOW = compare_service._SWISS_PAIR_WINDOW
_FILTERED_SWISS_PAIR_WINDOW = compare_service._FILTERED_SWISS_PAIR_WINDOW
_FILTERED_MOSAIC_WINDOW = compare_service._FILTERED_MOSAIC_WINDOW
_MOSAIC_EXPLORE_WINDOW = compare_service._MOSAIC_EXPLORE_WINDOW
_MOSAIC_DIVERSE_WINDOW = compare_service._MOSAIC_DIVERSE_WINDOW

_get_pairing_images = compare_service.get_pairing_images
_invalidate_pairing_cache = _runtime_services.invalidate_pairing_cache

_invalidate_rankings_cache = _runtime_services.invalidate_rankings_cache
_invalidate_image_flag_caches = _runtime_services.invalidate_image_flag_caches
_deep_search_query_embedding_stored = cache_events.deep_search_query_embedding_stored
_invalidate_vector_derived_caches = _runtime_services.invalidate_vector_derived_caches
_embedding_batch_stored = cache_events.embedding_batch_stored
_invalidate_interaction_response_cache = _runtime_services.invalidate_interaction_response_cache

_copy_interaction_response = response_helpers.copy_interaction_response
_copy_rankings_response = library_service.copy_rankings_response
_cache_rankings_response = library_service.cache_rankings_response


_get_past_matchups = compare_service.get_past_matchups
_get_visible_past_matchups = compare_service.get_visible_past_matchups
_get_past_matchups_for_candidate_ids = compare_service.get_past_matchups_for_candidate_ids
_add_past_matchups = compare_service.add_past_matchups
_patch_pairing_cache = compare_service.patch_pairing_cache
_schedule_pairing_propagation = _runtime_services.schedule_pairing_propagation

mosaic_next = compare_routes.mosaic_next
mosaic_pick = compare_routes.mosaic_pick
propagation_last = compare_routes.propagation_last
propagation_predict = compare_routes.propagation_predict
compare_next = compare_routes.compare_next
submit_comparison = compare_routes.submit_comparison
compare_undo = compare_routes.compare_undo


def _top_indices_desc(values, limit: int, exclude_index: int | None = None):
    import numpy as np

    if limit <= 0 or len(values) == 0:
        return []
    if exclude_index is not None:
        values = values.copy()
        values[exclude_index] = -np.inf

    limit = min(limit, len(values))
    if len(values) <= limit:
        return np.argsort(values)[::-1]

    candidates = np.argpartition(values, -limit)[-limit:]
    return candidates[np.argsort(values[candidates])[::-1]]


_camera_label = app_helpers.camera_label
_metadata_payload = app_helpers.metadata_payload
_visibility_counts = response_helpers.visibility_counts
_interaction_pool_stats = response_helpers.interaction_pool_stats


_schedule_thumbnail_prefetch = media_warm.schedule_thumbnail_prefetch
_schedule_cached_thumbnail_memory_warm = media_warm.schedule_cached_thumbnail_memory_warm
_schedule_result_thumbnail_memory_warm = media_warm.schedule_result_thumbnail_memory_warm
_cache_root = _runtime_services.cache_root


_compare_response_rows = response_helpers.compare_response_rows
_chunks = app_helpers._chunks
_cached_image_ids = media_warm.cached_image_ids

_filter_visible_candidates = compare_service.filter_visible_candidates
_hydrate_active_rows = compare_service.hydrate_active_rows
_default_visible_pairing_candidates = compare_service.default_visible_pairing_candidates
_filtered_visible_ranked_candidates = compare_service.filtered_visible_ranked_candidates
_load_filtered_visible_ranked_candidates = compare_service.load_filtered_visible_ranked_candidates
_search_visible_ranked_candidates = compare_service.search_visible_ranked_candidates
_warm_filtered_visible_ranked_candidates = compare_service.warm_filtered_visible_ranked_candidates


async def _visible_ranked_images(ranked_ids: list[int], limit: int, size: str = "sm") -> list[dict]:
    return await app_helpers.visible_ranked_images(ranked_ids, limit, size, _cache_root())


async def _count_visible_ranked_ids(ranked_ids: list[int], size: str = "sm") -> int:
    return await app_helpers.count_visible_ranked_ids(ranked_ids, size, _cache_root())


_visible_embedding_page = search_service.visible_embedding_page


_filter_by_metadata = app_helpers.filter_by_metadata
_sync_query_constraint_compat_globals = query_constraints.sync_configured_ttls
_record_deep_search_query = query_constraints.record_configured_deep_search_query
_normalize_search_query = query_constraints.normalize_search_query


_resolve_cached_deep_search = query_constraints.resolve_cached_deep_search


_encode_text_with_config = query_constraints.encode_text_with_config


_start_search_model_load = query_constraints.start_search_model_load


_apply_metadata_search_ids = query_constraints.apply_configured_metadata_search_ids
_resolve_text_search = _runtime_services.resolve_text_search


_search_constraint_active = query_constraints.search_constraint_active
_intersect_image_id_filters = query_constraints.intersect_image_id_filters


_resolve_library_constraints = _runtime_services.resolve_library_constraints


_metadata_text_match = compare_service.metadata_text_match
_apply_text_search_constraint = compare_service.apply_text_search_constraint
_has_candidate_filters = compare_service.has_candidate_filters


_diverse_sample = compare_service.diverse_sample


_mosaic_next_impl = compare_service.mosaic_next_impl
_compare_next_impl = compare_service.compare_next_impl


# --- Rankings API ---

_api_rankings_impl = library_service.api_rankings_impl


api_rankings = library_routes.api_rankings
api_date_groups = library_routes.api_date_groups
api_map_markers = library_routes.api_map_markers
api_filter_options = library_routes.api_filter_options
api_stats = library_routes.api_stats


_collections_cache = search_service._collections_cache
_duplicates_cache = search_service._duplicates_cache

_lifecycle = _app_shell.lifecycle
if _lifecycle is None:
    raise RuntimeError("App lifecycle was not configured")
startup = _lifecycle.startup
shutdown = _lifecycle.shutdown


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True, access_log=False)
