import logging
from collections.abc import Awaitable, Callable

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

import ai_models
import face_worker
import settings
import thumbnails
from core.requests import json_object
from data.repositories import catalog as catalog_repository
from data.repositories import images as image_repository
from features.catalog import metadata as catalog_metadata
from features.settings import status as settings_status
from features.sync import oplog


logger = logging.getLogger(__name__)

router = APIRouter()
MAX_BATCH_IMAGE_IDS = 10_000


def _batch_image_ids_too_large(image_ids) -> bool:
    return isinstance(image_ids, list) and len(image_ids) > MAX_BATCH_IMAGE_IDS
BuildResponse = Callable[[], Awaitable[dict]]
CopyResponse = Callable[[dict], dict]
TrackTask = Callable[[Awaitable], object]
GetRefreshing = Callable[[], bool]
SetRefreshing = Callable[[bool], None]
AsyncDictBuilder = Callable[..., Awaitable[dict]]
AsyncBoolBuilder = Callable[[], Awaitable[bool]]
DbPathProvider = Callable[[], str]
Invalidator = Callable[[], None]
InvalidatePairing = Callable[..., None]

_settings_response_cache: dict | None = None
_settings_response_cache_ttl_seconds: Callable[[], float] | None = None
_build_settings_response: BuildResponse | None = None
_copy_settings_response: CopyResponse | None = None
_track_background_task: TrackTask | None = None
_get_refreshing: GetRefreshing | None = None
_set_refreshing: SetRefreshing | None = None
_db_path: DbPathProvider | None = None
_get_stats: BuildResponse | None = None
_refresh_source_online_states: AsyncBoolBuilder | None = None
_build_cache_status: AsyncDictBuilder | None = None
_build_ai_status: AsyncDictBuilder | None = None
_people_status_payload: BuildResponse | None = None
_invalidate_image_flag_caches: Invalidator | None = None
_invalidate_pairing_cache: InvalidatePairing | None = None
_invalidate_cache_status_cache: Invalidator | None = None
_invalidate_ai_status_response_cache: Invalidator | None = None
_invalidate_settings_response_cache: Invalidator | None = None
_invalidate_rankings_cache: Invalidator | None = None
_invalidate_vector_derived_caches: Invalidator | None = None


def configure(
    *,
    settings_response_cache: dict,
    settings_response_cache_ttl_seconds: Callable[[], float],
    build_settings_response: BuildResponse,
    copy_settings_response: CopyResponse,
    track_background_task: TrackTask,
    get_refreshing: GetRefreshing,
    set_refreshing: SetRefreshing,
    db_path: DbPathProvider,
    get_stats: BuildResponse,
    refresh_source_online_states: AsyncBoolBuilder,
    build_cache_status: AsyncDictBuilder,
    build_ai_status: AsyncDictBuilder,
    people_status_payload: BuildResponse,
    invalidate_image_flag_caches: Invalidator,
    invalidate_pairing_cache: InvalidatePairing,
    invalidate_cache_status_cache: Invalidator,
    invalidate_ai_status_response_cache: Invalidator,
    invalidate_settings_response_cache: Invalidator,
    invalidate_rankings_cache: Invalidator,
    invalidate_vector_derived_caches: Invalidator,
) -> None:
    global _settings_response_cache, _settings_response_cache_ttl_seconds
    global _build_settings_response, _copy_settings_response, _track_background_task
    global _get_refreshing, _set_refreshing, _db_path, _get_stats, _refresh_source_online_states
    global _build_cache_status, _build_ai_status, _people_status_payload
    global _invalidate_image_flag_caches, _invalidate_pairing_cache, _invalidate_cache_status_cache
    global _invalidate_ai_status_response_cache, _invalidate_settings_response_cache
    global _invalidate_rankings_cache, _invalidate_vector_derived_caches
    _settings_response_cache = settings_response_cache
    _settings_response_cache_ttl_seconds = settings_response_cache_ttl_seconds
    _build_settings_response = build_settings_response
    _copy_settings_response = copy_settings_response
    _track_background_task = track_background_task
    _get_refreshing = get_refreshing
    _set_refreshing = set_refreshing
    _db_path = db_path
    _get_stats = get_stats
    _refresh_source_online_states = refresh_source_online_states
    _build_cache_status = build_cache_status
    _build_ai_status = build_ai_status
    _people_status_payload = people_status_payload
    _invalidate_image_flag_caches = invalidate_image_flag_caches
    _invalidate_pairing_cache = invalidate_pairing_cache
    _invalidate_cache_status_cache = invalidate_cache_status_cache
    _invalidate_ai_status_response_cache = invalidate_ai_status_response_cache
    _invalidate_settings_response_cache = invalidate_settings_response_cache
    _invalidate_rankings_cache = invalidate_rankings_cache
    _invalidate_vector_derived_caches = invalidate_vector_derived_caches


def _configured():
    values = (
        _settings_response_cache,
        _settings_response_cache_ttl_seconds,
        _build_settings_response,
        _copy_settings_response,
        _track_background_task,
        _get_refreshing,
        _set_refreshing,
        _db_path,
        _get_stats,
        _refresh_source_online_states,
        _build_cache_status,
        _build_ai_status,
        _people_status_payload,
        _invalidate_image_flag_caches,
        _invalidate_pairing_cache,
        _invalidate_cache_status_cache,
        _invalidate_ai_status_response_cache,
        _invalidate_settings_response_cache,
        _invalidate_rankings_cache,
        _invalidate_vector_derived_caches,
    )
    if any(value is None for value in values):
        raise RuntimeError("Settings routes are not configured")


def _configured_db_path() -> str:
    if _db_path is None:
        raise RuntimeError("Settings routes are not configured")
    return _db_path()


async def _catalog_summary_payload() -> dict:
    _configured()
    return await catalog_repository.catalog_summary_cached(
        _configured_db_path(),
        get_stats=_get_stats,
        refresh_source_online_states=_refresh_source_online_states,
    )


@router.get("/api/settings")
async def api_settings():
    _configured()
    return await settings_status.cached_settings_response(
        track_background_task=_track_background_task,
        build_response=_build_settings_response,
        copy_response=_copy_settings_response,
        cache_ttl_seconds=_settings_response_cache_ttl_seconds,
        get_refreshing=_get_refreshing,
        set_refreshing=_set_refreshing,
    )


@router.get("/api/ui/settings")
async def api_ui_settings():
    config = settings.get_settings()
    return {
        "settings": {
            "show_loupe_cache_status": bool(config.get("show_loupe_cache_status", True)),
        }
    }


@router.post("/api/image/{image_id}/flag")
async def api_set_image_flag(image_id: int, request: Request):
    _configured()
    body, error = await json_object(request)
    if error:
        return error
    flag = body.get("flag", "unflagged")
    if flag not in ("picked", "unflagged", "rejected"):
        return JSONResponse({"error": "Invalid flag"}, status_code=400)

    image = await image_repository.get_image_by_id(_configured_db_path(), image_id)
    if not image:
        return JSONResponse({"error": "Image not found"}, status_code=404)

    await image_repository.set_image_flag(_configured_db_path(), image_id, flag)
    await oplog.append_flags(_configured_db_path(), [image_id], flag)
    _invalidate_image_flag_caches()
    _invalidate_pairing_cache()
    return {"ok": True, "id": image_id, "flag": flag}


@router.post("/api/images/flag")
async def api_batch_set_flag(request: Request):
    _configured()
    body, error = await json_object(request)
    if error:
        return error
    flag = body.get("flag", "unflagged")
    image_ids = body.get("image_ids", [])
    if flag not in ("picked", "unflagged", "rejected"):
        return JSONResponse({"error": "Invalid flag"}, status_code=400)
    if not image_ids or not isinstance(image_ids, list):
        return JSONResponse({"error": "image_ids must be a non-empty list"}, status_code=400)
    if _batch_image_ids_too_large(image_ids):
        return JSONResponse(
            {"error": f"image_ids is limited to {MAX_BATCH_IMAGE_IDS} entries"},
            status_code=413,
        )

    normalized_ids = []
    seen_ids = set()
    for value in image_ids:
        try:
            image_id = int(value)
        except (TypeError, ValueError):
            continue
        if image_id <= 0 or image_id in seen_ids:
            continue
        seen_ids.add(image_id)
        normalized_ids.append(image_id)

    if not normalized_ids:
        return JSONResponse({"error": "No valid image ids"}, status_code=400)

    count = await image_repository.batch_set_image_flags(_configured_db_path(), normalized_ids, flag)
    await oplog.append_flags(_configured_db_path(), normalized_ids, flag)
    if count:
        _invalidate_image_flag_caches()
    _invalidate_pairing_cache()
    return {"ok": True, "count": count, "flag": flag}


@router.post("/api/settings")
async def api_save_settings(request: Request):
    _configured()
    body, error = await json_object(request)
    if error:
        return error
    body = {key: value for key, value in body.items() if key not in settings.PRIVATE_SETTING_KEYS}
    if str(body.pop("publish_hook", "") or "").strip():
        # The hook executes shell on publish; it is never API-writable (AUTH_SPEC).
        return JSONResponse(
            {
                "error": "publish_hook is server-side configuration. Set the "
                "PHOTOARCHIVE_PUBLISH_HOOK environment variable or edit the "
                "settings file on the server."
            },
            status_code=400,
        )
    current = settings.get_settings()
    saved = settings.save_settings({**current, **body})
    model_changed = any(
        current.get(field) != saved.get(field)
        for field in ("embed_model_id", "embed_model_revision", "embed_model_dir", "embed_model_dim")
    )
    search_runtime_changed = (
        model_changed
        or float(current.get("search_similarity_threshold", 0.35))
        != float(saved.get("search_similarity_threshold", 0.35))
    )
    people_runtime_changed = any(
        current.get(field) != saved.get(field)
        for field in (
            "face_model_id",
            "face_model_dir",
            "face_detection_size",
            "face_similarity_threshold",
            "face_merge_suggestion_threshold",
        )
    )
    thumbnail_changed = any(
        int(current.get(field, 0)) != int(saved.get(field, 0))
        for field in ("thumb_size_sm", "thumb_size_md", "thumb_size_lg", "thumb_quality")
    )
    replace_thumbnail_cache = (
        thumbnail_changed
        and str(body.get("thumbnail_cache_policy", "keep")).strip().lower() == "replace"
    )
    thumbnails.configure({**saved, "_replace_thumbnail_cache": replace_thumbnail_cache})
    if model_changed:
        try:
            import embedding_worker
            embedding_worker._text_cache.clear()
        except Exception:
            pass
        try:
            import db
            await db.purge_retired_embedding_data()
        except Exception:
            logger.warning(
                "Failed to purge retired embedding data after model change",
                exc_info=True,
            )
        _invalidate_vector_derived_caches()
    if search_runtime_changed:
        _invalidate_rankings_cache()
    if people_runtime_changed:
        face_worker.request_scan_now()
    _invalidate_cache_status_cache()
    _invalidate_ai_status_response_cache()
    _invalidate_settings_response_cache()
    return {
        "ok": True,
        "settings": settings.public_settings(saved),
        "cache_stats": await _build_cache_status(ahead=0, force=True),
        "model_status": ai_models.get_model_status(),
        "ai_status": await _build_ai_status(),
        "people_status": await _people_status_payload(),
        "metadata_status": catalog_metadata.catalog_metadata_status(),
        "catalog": await _catalog_summary_payload(),
    }


@router.post("/api/settings/reset")
async def api_reset_settings():
    _configured()
    current = settings.get_settings()
    saved = settings.reset_settings()
    thumbnails.configure(saved)
    try:
        import embed_cache
        import embedding_worker
        embed_cache.invalidate()
        embedding_worker._text_cache.clear()
    except Exception:
        pass
    if any(
        current.get(field) != saved.get(field)
        for field in ("embed_model_id", "embed_model_revision", "embed_model_dir", "embed_model_dim")
    ):
        try:
            import db
            await db.purge_retired_embedding_data()
        except Exception:
            logger.warning(
                "Failed to purge retired embedding data after settings reset",
                exc_info=True,
            )
    _invalidate_rankings_cache()
    _invalidate_cache_status_cache()
    _invalidate_ai_status_response_cache()
    _invalidate_settings_response_cache()
    face_worker.request_scan_now()
    return {
        "ok": True,
        "settings": settings.public_settings(saved),
        "cache_stats": await _build_cache_status(ahead=0, force=True),
        "model_status": ai_models.get_model_status(),
        "ai_status": await _build_ai_status(),
        "people_status": await _people_status_payload(),
        "metadata_status": catalog_metadata.catalog_metadata_status(),
        "catalog": await _catalog_summary_payload(),
    }
