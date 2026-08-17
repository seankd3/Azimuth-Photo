from core.catalog_path import catalog_path
import asyncio
import logging
from collections.abc import Awaitable, Callable

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

import ai_models
import settings
import tiles
from core.requests import json_object
from data.repositories import stats as stats_repository
from data.repositories import images as image_repository
from features.catalog import metadata as catalog_metadata
from features.settings import status as settings_status
import judgements


logger = logging.getLogger(__name__)

import db
from core import background, cache_events
from features.ai import routes as ai_routes
import api as core_api


router = APIRouter()
MAX_BATCH_IMAGE_IDS = 10_000
ACTIVITY_STATUS_INITIAL_WAIT_SECONDS = 0.15


def _batch_image_ids_too_large(image_ids) -> bool:
    return isinstance(image_ids, list) and len(image_ids) > MAX_BATCH_IMAGE_IDS
TrackTask = Callable[[Awaitable], object]
GetRefreshing = Callable[[], bool]
SetRefreshing = Callable[[bool], None]
AsyncDictBuilder = Callable[..., Awaitable[dict]]
AsyncBoolBuilder = Callable[[], Awaitable[bool]]
Invalidator = Callable[[], None]
InvalidatePairing = Callable[..., None]


async def _catalog_summary_payload() -> dict:
    return await stats_repository.catalog_summary(catalog_path())


@router.get("/api/settings")
async def api_settings():
    return await settings_status.cached_settings_response(
        track_background_task=background.track_background_task,
        build_response=settings_status.build_settings_response,
        copy_response=settings_status.copy_settings_response,
        cache_ttl_seconds=(lambda: settings_status._settings_response_cache_ttl_seconds),
        get_refreshing=settings_status.get_settings_response_refreshing,
        set_refreshing=settings_status.set_settings_response_refreshing,
    )


@router.get("/api/background-work/status")
async def api_background_work_status():
    """One bounded snapshot for the desktop activity widget."""

    ai_status, cache_status, people_status, captions = await asyncio.gather(
        settings_status._bounded_status(
            ai_routes.build_ai_status(),
            settings_status._stale_ai_status,
            ACTIVITY_STATUS_INITIAL_WAIT_SECONDS,
        ),
        settings_status._bounded_status(
            core_api.cache_status(),
            settings_status._stale_cache_status,
            ACTIVITY_STATUS_INITIAL_WAIT_SECONDS,
        ),
        settings_status._bounded_status(
            core_api.people_status(),
            settings_status._stale_people_status,
            ACTIVITY_STATUS_INITIAL_WAIT_SECONDS,
        ),
        settings_status._bounded_status(
            core_api.captions_status(),
            settings_status._stale_caption_status,
            ACTIVITY_STATUS_INITIAL_WAIT_SECONDS,
        ),
    )
    return {
        "ai": ai_status,
        "cache": cache_status,
        "people": people_status,
        "captions": captions,
        "metadata": catalog_metadata.catalog_metadata_status(),
    }




@router.get("/api/image/{image_id}/rating")
async def api_get_image_rating(image_id: int):
    """Read-only star projection. Stars are computed from Elo — there is no
    manual star write anywhere; Refine is the only way to change a star."""
    image = await image_repository.get_image_by_id(catalog_path(), image_id)
    if not image:
        return JSONResponse({"error": "Image not found"}, status_code=404)
    import elo_stars as elo_stars_mod
    import shoot_rank as shoot_rank_mod

    row = dict(image) if not isinstance(image, dict) else image
    stars = int(row.get("stars") or 0)
    content_hash = str(row.get("content_hash") or "")
    projected = 0
    if content_hash:
        by_hash = await elo_stars_mod.elo_stars_for_hashes(catalog_path(), [content_hash])
        projected = int(by_hash.get(content_hash) or 0)
    display = stars or projected
    base_whisper = elo_stars_mod.whisper_for_stars(display) if display else None
    rank_in_shoot = None
    filepath = str(row.get("filepath") or "")
    if base_whisper and filepath:
        rank_in_shoot = await shoot_rank_mod.rank_in_shoot_for_filepath(catalog_path(), filepath)
    whisper = shoot_rank_mod.compose_star_whisper(base_whisper, rank_in_shoot)
    return {
        "ok": True,
        "id": image_id,
        "stars": display,
        # Compatibility alias for clients that predate the stored projection.
        "rating": display,
        "elo_stars": display,
        "elo_stars_whisper": whisper,
        "rank_in_shoot": rank_in_shoot,
    }


@router.post("/api/images/flag")
async def api_batch_set_flag(request: Request):
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

    count = await image_repository.batch_set_image_flags(catalog_path(), normalized_ids, flag)
    await judgements.flag(catalog_path(), normalized_ids, flag)
    if count:
        cache_events.invalidate_rankings_cache()
    return {"ok": True, "count": count, "flag": flag}


@router.post("/api/settings")
async def api_save_settings(request: Request):
    body, error = await json_object(request)
    if error:
        return error
    body = {key: value for key, value in body.items() if key not in settings.PRIVATE_SETTING_KEYS}
    if str(body.pop("publish_hook", "") or "").strip():
        # The hook executes shell on publish; it is never API-writable (AUTH_SPEC).
        return JSONResponse(
            {
                "error": "publish_hook is server-side configuration. Set the "
                "AZIMUTH_PUBLISH_HOOK environment variable or edit the "
                "settings file on the server."
            },
            status_code=400,
        )
    current = settings.get_settings()
    saved = settings.save_settings({**current, **body})
    # `apply_saved_worker_intent(current, saved)` stood here and started or
    # stopped four workers when their toggles changed. `core/intelligence_campaign`
    # went with those workers in 10e29ebd, and this import is not guarded — so
    # **saving settings has raised ModuleNotFoundError ever since**.
    #
    # Nothing replaces it, deliberately. There is one loop and its queue is a
    # query, so it reads the settings on the next pass: a preference that takes
    # effect by being true does not need anyone told about it.
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
    tiles.configure({**saved, "_replace_thumbnail_cache": replace_thumbnail_cache})
    if model_changed:
        # Nothing to invalidate by hand any more. `search.space` keys its memo
        # on the model's recipe, so a changed model misses it by construction
        # -- which is the point of naming the model in the recipe. This used to
        # reach for `embedding_worker._text_cache`, in a module deleted in
        # 6fc7e31c, inside a bare `except: pass` that made the failure silent.
        try:
            import db
            await db.purge_retired_embedding_data()
        except Exception:
            logger.warning(
                "Failed to purge retired embedding data after model change",
                exc_info=True,
            )
        cache_events.invalidate_vector_derived_caches()
    if search_runtime_changed:
        cache_events.invalidate_rankings_cache()
    settings_status.invalidate_settings_response_cache()
    return {
        "ok": True,
        "settings": settings.public_settings(saved),
        "cache_stats": await core_api.cache_status(),
        "model_status": ai_models.get_model_status(),
        "ai_status": await ai_routes.build_ai_status(),
        "people_status": await core_api.people_status(),
        "metadata_status": catalog_metadata.catalog_metadata_status(),
        "catalog": await _catalog_summary_payload(),
    }


@router.post("/api/settings/reset")
async def api_reset_settings():
    current = settings.get_settings()
    saved = settings.reset_settings()
    tiles.configure(saved)
    try:
        import embed_cache
        embed_cache.invalidate()
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
    cache_events.invalidate_rankings_cache()
    settings_status.invalidate_settings_response_cache()
    return {
        "ok": True,
        "settings": settings.public_settings(saved),
        "cache_stats": await core_api.cache_status(),
        "model_status": ai_models.get_model_status(),
        "ai_status": await ai_routes.build_ai_status(),
        "people_status": await core_api.people_status(),
        "metadata_status": catalog_metadata.catalog_metadata_status(),
        "catalog": await _catalog_summary_payload(),
    }
