"""Settings aggregate status/cache helpers owned by the settings feature."""

import asyncio
import time
from collections.abc import Awaitable, Callable

import ai_models
import settings
from core import responses as response_helpers
from data.repositories import catalog as catalog_repository


AsyncDictBuilder = Callable[..., Awaitable[dict]]
AsyncBoolBuilder = Callable[[], Awaitable[bool]]
BuildSettingsResponse = Callable[[], Awaitable[dict]]
CopySettingsResponse = Callable[[dict], dict]
DbPathProvider = Callable[[], str]
GetRefreshing = Callable[[], bool]
SetRefreshing = Callable[[bool], None]
TrackTask = Callable[[Awaitable], object]

_build_cache_status: AsyncDictBuilder | None = None
_build_ai_status: AsyncDictBuilder | None = None
_people_status_payload: BuildSettingsResponse | None = None
_db_path: DbPathProvider | None = None
_get_catalog_image_counts: BuildSettingsResponse | None = None
_refresh_source_online_states: AsyncBoolBuilder | None = None

_settings_response_cache: dict[str, dict | float | None] = {"data": None, "expires": 0}
_settings_response_refreshing = False
_settings_response_cache_ttl_seconds = 10.0


def configure(
    *,
    build_cache_status: AsyncDictBuilder,
    build_ai_status: AsyncDictBuilder,
    people_status_payload: BuildSettingsResponse,
    db_path: DbPathProvider,
    get_catalog_image_counts: BuildSettingsResponse,
    refresh_source_online_states: AsyncBoolBuilder,
) -> None:
    global _build_cache_status, _build_ai_status, _people_status_payload
    global _db_path, _get_catalog_image_counts, _refresh_source_online_states
    _build_cache_status = build_cache_status
    _build_ai_status = build_ai_status
    _people_status_payload = people_status_payload
    _db_path = db_path
    _get_catalog_image_counts = get_catalog_image_counts
    _refresh_source_online_states = refresh_source_online_states


def _configured() -> tuple[
    AsyncDictBuilder,
    AsyncDictBuilder,
    BuildSettingsResponse,
    DbPathProvider,
    BuildSettingsResponse,
    AsyncBoolBuilder,
]:
    if (
        _build_cache_status is None
        or _build_ai_status is None
        or _people_status_payload is None
        or _db_path is None
        or _get_catalog_image_counts is None
        or _refresh_source_online_states is None
    ):
        raise RuntimeError("Settings status is not configured")
    return (
        _build_cache_status,
        _build_ai_status,
        _people_status_payload,
        _db_path,
        _get_catalog_image_counts,
        _refresh_source_online_states,
    )


async def catalog_light_summary_payload() -> dict:
    (
        _,
        _,
        _,
        db_path,
        get_catalog_image_counts,
        refresh_source_online_states,
    ) = _configured()
    return await catalog_repository.catalog_light_summary_cached(
        db_path(),
        get_catalog_image_counts=get_catalog_image_counts,
        refresh_source_online_states=refresh_source_online_states,
    )


async def build_settings_response() -> dict:
    build_cache_status, build_ai_status, people_status_payload, *_ = _configured()
    model_status = ai_models.get_model_status()
    cache_status_task = asyncio.create_task(build_cache_status(ahead=0))
    ai_status_task = asyncio.create_task(build_ai_status(model_status=model_status))
    catalog_task = asyncio.create_task(catalog_light_summary_payload())
    people_status_task = asyncio.create_task(people_status_payload())
    cache_status, ai_status, catalog, people_status = await asyncio.gather(
        cache_status_task,
        ai_status_task,
        catalog_task,
        people_status_task,
    )
    return {
        "settings": settings.get_settings(),
        "cache_stats": cache_status,
        "model_status": model_status,
        "ai_status": ai_status,
        "people_status": people_status,
        "catalog": catalog,
        **settings.settings_metadata(),
    }


def invalidate_settings_response_cache() -> None:
    global _settings_response_refreshing
    _settings_response_cache["data"] = None
    _settings_response_cache["expires"] = 0
    _settings_response_refreshing = False


def expire_settings_response_cache() -> None:
    global _settings_response_refreshing
    _settings_response_cache["expires"] = 0
    _settings_response_refreshing = False


def copy_settings_response(response: dict) -> dict:
    return response_helpers.copy_settings_response(
        response,
        copy_ai_status=response_helpers.copy_ai_status_response,
        copy_cache_status=response_helpers.copy_cache_status_response,
    )


def get_settings_response_refreshing() -> bool:
    return _settings_response_refreshing


def set_settings_response_refreshing(value: bool) -> None:
    global _settings_response_refreshing
    _settings_response_refreshing = bool(value)


async def cached_settings_response(
    *,
    track_background_task: TrackTask,
    build_response: BuildSettingsResponse | None = None,
    copy_response: CopySettingsResponse | None = None,
    cache_ttl_seconds: Callable[[], float] | None = None,
    get_refreshing: GetRefreshing | None = None,
    set_refreshing: SetRefreshing | None = None,
) -> dict:
    build_response = build_response or build_settings_response
    copy_response = copy_response or copy_settings_response
    cache_ttl_seconds = cache_ttl_seconds or (lambda: _settings_response_cache_ttl_seconds)
    get_refreshing = get_refreshing or get_settings_response_refreshing
    set_refreshing = set_refreshing or set_settings_response_refreshing

    cached = _settings_response_cache.get("data")
    if cached is not None and float(_settings_response_cache.get("expires") or 0) > time.monotonic():
        return copy_response(cached)
    if cached is not None:
        if not get_refreshing():
            set_refreshing(True)

            async def _refresh_settings_response():
                try:
                    response = await build_response()
                    _settings_response_cache["data"] = copy_response(response)
                    _settings_response_cache["expires"] = (
                        time.monotonic() + cache_ttl_seconds()
                    )
                finally:
                    set_refreshing(False)

            track_background_task(_refresh_settings_response())
        return copy_response(cached)

    response = await build_response()
    _settings_response_cache["data"] = copy_response(response)
    _settings_response_cache["expires"] = time.monotonic() + cache_ttl_seconds()
    return response


_build_settings_response = build_settings_response
_invalidate_settings_response_cache = invalidate_settings_response_cache
_expire_settings_response_cache = expire_settings_response_cache
_copy_settings_response = copy_settings_response
_set_settings_response_refreshing = set_settings_response_refreshing
