"""Settings aggregate status/cache helpers owned by the settings feature."""

from core.catalog_path import catalog_path
import asyncio
import time
from collections.abc import Awaitable, Callable

import ai_models
import settings
from core.background import track_background_task as track_route_background_task
from core import responses as response_helpers
from data.repositories import catalog as catalog_repository
import db
from features.ai import routes as ai_routes
from features.cache import status as cache_status_service
from features.people import routes as people_routes
from features.catalog import metadata as catalog_metadata
from features.sync import satellite


AsyncDictBuilder = Callable[..., Awaitable[dict]]
AsyncBoolBuilder = Callable[[], Awaitable[bool]]
BuildSettingsResponse = Callable[[], Awaitable[dict]]
CopySettingsResponse = Callable[[dict], dict]
DbPathProvider = Callable[[], str]
GetRefreshing = Callable[[], bool]
SetRefreshing = Callable[[bool], None]
TrackTask = Callable[[Awaitable], object]


_settings_response_cache: dict[str, dict | float | None] = {"data": None, "expires": 0}
_settings_response_refreshing = False
_settings_response_cache_ttl_seconds = 10.0
_status_component_timeout_seconds = 1.2




async def catalog_light_summary_payload() -> dict:
    return await catalog_repository.catalog_light_summary_cached(
        catalog_path(),
        get_catalog_image_counts=db.get_catalog_image_counts,
        refresh_source_online_states=db.refresh_source_online_states,
    )


def _stale_cache_status(latency_ms: float) -> dict:
    empty_tiers = {tier: {"count": 0} for tier in ("sm", "md", "lg")}
    return {
        "pregen": {},
        "disk": {"tiers": {tier: dict(values) for tier, values in empty_tiers.items()}},
        "memory": {"tiers": {tier: dict(values) for tier, values in empty_tiers.items()}},
        "counts_stale": True,
        "status_stale": True,
        "latency_ms": latency_ms,
    }


def _stale_ai_status(latency_ms: float) -> dict:
    return {
        "embedding_index": {},
        "worker_state": "stale",
        "counts_stale": True,
        "status_stale": True,
        "latency_ms": latency_ms,
    }


def _stale_people_status(latency_ms: float) -> dict:
    return {
        "active": True,
        "worker": {"state": "stale"},
        "counts": {"pending_cached_images": 0, "scan": {}},
        "counts_stale": True,
        "status_stale": True,
        "latency_ms": latency_ms,
    }


def _stale_caption_status(latency_ms: float) -> dict:
    return {
        "active": False,
        "worker": {"state": "stale"},
        "counts": {"captioned": 0, "pending_cached_images": 0, "scan": {}},
        "counts_stale": True,
        "status_stale": True,
        "latency_ms": latency_ms,
    }


def _stale_catalog_status(latency_ms: float) -> dict:
    return {
        "sources": [],
        "stats": {},
        "counts_stale": True,
        "status_stale": True,
        "latency_ms": latency_ms,
    }


async def _bounded_status(coro, stale_builder, timeout_seconds: float = _status_component_timeout_seconds) -> dict:
    started = time.perf_counter()
    task = track_route_background_task(coro)
    try:
        done, _ = await asyncio.wait(
            {task},
            timeout=max(0.05, float(timeout_seconds)),
        )
        if task not in done:
            return stale_builder(round((time.perf_counter() - started) * 1000, 1))
        return task.result()
    except Exception:
        return stale_builder(round((time.perf_counter() - started) * 1000, 1))


async def build_settings_response() -> dict:
    model_status = ai_models.get_model_status()
    cache_status_task = asyncio.create_task(_bounded_status(
        cache_status_service.build_cache_status(ahead=0),
        _stale_cache_status,
    ))
    ai_status_task = asyncio.create_task(_bounded_status(
        ai_routes.build_ai_status(model_status=model_status),
        _stale_ai_status,
    ))
    catalog_task = asyncio.create_task(_bounded_status(
        catalog_light_summary_payload(),
        _stale_catalog_status,
    ))
    people_status_task = asyncio.create_task(_bounded_status(
        people_routes.people_status_payload(),
        _stale_people_status,
    ))
    cache_status, ai_status, catalog, people_status = await asyncio.gather(
        cache_status_task,
        ai_status_task,
        catalog_task,
        people_status_task,
    )
    return {
        "settings": settings.public_settings(),
        "cache_stats": cache_status,
        "model_status": model_status,
        "ai_status": ai_status,
        "people_status": people_status,
        "metadata_status": catalog_metadata.catalog_metadata_status(),
        "catalog": catalog,
        "sync": satellite.bootstrap_payload(),
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
