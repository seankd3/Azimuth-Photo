import asyncio
import os
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.gzip import GZipMiddleware

from core import background as background_runtime
from core.static_assets import StaticAssetContext, warm_templates
from features.ai import routes as ai_routes
from features.cache import routes as cache_routes
from features.catalog import routes as catalog_routes
from features.compare import routes as compare_routes
from features.dev import routes as dev_routes
from features.export import routes as export_routes
from features.library import routes as library_routes
from features.media import routes as media_routes
from features.pages import routes as page_routes
from features.people import routes as people_routes
from features.search import routes as search_routes
from features.settings import routes as settings_routes


DEFAULT_TEMPLATE_WARMUP = ("settings.html", "library.html", "compare.html", "people.html")
INTERACTION_CACHE_WARMUP_DELAY_SECONDS = 0.05


def _app_compat_value(name: str, default):
    module = sys.modules.get("app")
    if module is not None and hasattr(module, name):
        return getattr(module, name)
    return default


def _app_compat_callable(name: str, default):
    value = _app_compat_value(name, default)
    return value if callable(value) else default


@dataclass(frozen=True)
class AppRuntimeServices:
    cache_root: Callable[[], str]
    invalidate_pairing_cache: Callable[..., None]
    invalidate_rankings_cache: Callable[[], None]
    invalidate_image_flag_caches: Callable[[], None]
    invalidate_vector_derived_caches: Callable[[], None]
    invalidate_interaction_response_cache: Callable[[], None]
    schedule_pairing_propagation: Callable[[Awaitable[Any]], None]
    resolve_text_search: Callable[..., Awaitable[dict]]
    resolve_library_constraints: Callable[..., Awaitable[dict]]


@dataclass(frozen=True)
class AppLifecycleHandlers:
    startup: Callable[[], Awaitable[None]]
    shutdown: Callable[[], Awaitable[None]]


@dataclass(frozen=True)
class AppShell:
    app: FastAPI
    templates: Jinja2Templates
    static_assets: StaticAssetContext
    background_task_tracker: background_runtime.BackgroundTaskTracker = field(
        default_factory=background_runtime.BackgroundTaskTracker,
    )
    runtime_services: AppRuntimeServices | None = None
    lifecycle: AppLifecycleHandlers | None = None
    idle_activity_middleware: Callable | None = None

    @property
    def git_commit(self) -> str | None:
        return self.static_assets.git_commit

    def static_version(self) -> str:
        return self.static_assets.static_version()

    def template_context(self, request) -> dict:
        return self.static_assets.template_context(request)

    def warm_templates(self, template_names: tuple[str, ...] = DEFAULT_TEMPLATE_WARMUP) -> None:
        warm_templates(self.templates, template_names)

    @property
    def idle_activity_excluded_paths(self):
        return background_runtime.IDLE_ACTIVITY_EXCLUDED_PATHS

    @property
    def background_tasks(self):
        return self.background_task_tracker.tasks

    def track_background_task(self, coro):
        return self.background_task_tracker.track(coro)

    def install_idle_activity_middleware(self, *, thumbnails, excluded_paths=None):
        return background_runtime.install_idle_activity_middleware(
            self.app,
            thumbnails=thumbnails,
            excluded_paths=excluded_paths or self.idle_activity_excluded_paths,
        )


@dataclass(frozen=True)
class AppLifecycleDependencies:
    smoke_mode_enabled: Callable[[], bool]
    warm_templates: Callable[[], Any]
    thumbnails: Any
    settings: Any
    resource_governor: Any
    face_worker: Any
    init_db: Callable[[], Awaitable[Any]]
    get_filter_options: Callable[[], Awaitable[dict]]
    get_date_groups: Callable[..., Awaitable[Any]]
    get_catalog_image_counts: Callable[[], Awaitable[Any]]
    get_stats: Callable[[], Awaitable[Any]]
    get_ai_status_counts: Callable[[], Awaitable[Any]]
    get_visible_orientation_pairing_pool_counts: Callable[..., Awaitable[Any]]
    get_catalog_summary: Callable[[], Awaitable[Any]]
    cache_root: Callable[[], str]
    build_ai_status: Callable[..., Awaitable[Any]]
    build_cache_status: Callable[..., Awaitable[Any]]
    api_rankings: Callable[..., Awaitable[Any]]
    api_folders: Callable[..., Awaitable[Any]]
    api_map_markers: Callable[..., Awaitable[Any]]
    api_date_groups: Callable[..., Awaitable[Any]]
    api_settings: Callable[..., Awaitable[Any]]
    mosaic_next: Callable[..., Awaitable[Any]]
    compare_next: Callable[..., Awaitable[Any]]
    default_visible_pairing_candidates: Callable[..., Awaitable[Any]]
    warm_filtered_visible_ranked_candidates: Callable[..., Awaitable[Any]]
    get_visible_past_matchups: Callable[..., Awaitable[Any]]
    classify_orientations_background: Callable[[], Awaitable[Any]]
    scan_metadata_background: Callable[[], Awaitable[Any]]
    swiss_pair_window: int
    filtered_swiss_pair_window: int
    filtered_mosaic_window: int
    mosaic_explore_window: int
    mosaic_diverse_window: int
    interaction_cache_warmup_delay_seconds: float


class SelectiveGZipMiddleware:
    """Compress text/JSON responses without spending CPU on image streams."""

    def __init__(self, app, minimum_size: int = 1000):
        self.app = app
        self.gzip = GZipMiddleware(app, minimum_size=minimum_size)

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http":
            path = scope.get("path") or ""
            if (
                path.startswith("/api/thumb/")
                or path.startswith("/api/full/")
                or path.startswith("/api/people/faces/")
            ):
                await self.app(scope, receive, send)
                return
        await self.gzip(scope, receive, send)


class StaticCacheHeadersMiddleware:
    """Let browsers reuse static JS/CSS briefly while still revalidating soon."""

    def __init__(self, app, max_age: int = 300):
        self.app = app
        self.cache_control = f"public, max-age={max_age}, stale-while-revalidate=3600"

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or not (scope.get("path") or "").startswith("/static/"):
            await self.app(scope, receive, send)
            return

        async def send_with_cache_headers(message):
            if message.get("type") == "http.response.start":
                headers = list(message.get("headers") or [])
                if not any(name.lower() == b"cache-control" for name, _value in headers):
                    headers.append((b"cache-control", self.cache_control.encode("ascii")))
                    message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_cache_headers)


def create_base_app(*, base_dir: str | None = None, title: str = "photoArchive") -> FastAPI:
    """Create the bare FastAPI app with middleware and static mounting only."""

    root = base_dir or os.path.dirname(os.path.dirname(__file__))
    app = FastAPI(title=title)
    app.add_middleware(SelectiveGZipMiddleware, minimum_size=1000)
    app.add_middleware(StaticCacheHeadersMiddleware, max_age=86400)
    app.mount("/static", StaticFiles(directory=os.path.join(root, "static")), name="static")
    return app


def create_app(*, base_dir: str | None = None, title: str = "photoArchive") -> FastAPI:
    """Create the routed app shell with runtime and lifecycle wiring."""

    return create_app_shell(base_dir=base_dir, title=title).app


def create_templates(*, base_dir: str | None = None) -> Jinja2Templates:
    root = base_dir or os.path.dirname(os.path.dirname(__file__))
    return Jinja2Templates(directory=os.path.join(root, "templates"))


def register_app_lifecycle(shell: AppShell, dependencies: AppLifecycleDependencies) -> AppLifecycleHandlers:
    async def startup() -> None:
        await background_runtime.run_startup(
            smoke_mode_enabled=dependencies.smoke_mode_enabled,
            warm_templates=dependencies.warm_templates,
            thumbnails=dependencies.thumbnails,
            settings=dependencies.settings,
            resource_governor=dependencies.resource_governor,
            face_worker=dependencies.face_worker,
            track_background_task=shell.track_background_task,
            init_db=dependencies.init_db,
            get_filter_options=dependencies.get_filter_options,
            get_date_groups=dependencies.get_date_groups,
            get_catalog_image_counts=dependencies.get_catalog_image_counts,
            get_stats=dependencies.get_stats,
            get_ai_status_counts=dependencies.get_ai_status_counts,
            get_visible_orientation_pairing_pool_counts=dependencies.get_visible_orientation_pairing_pool_counts,
            get_catalog_summary=dependencies.get_catalog_summary,
            cache_root=dependencies.cache_root,
            build_ai_status=dependencies.build_ai_status,
            build_cache_status=dependencies.build_cache_status,
            api_rankings=dependencies.api_rankings,
            api_folders=dependencies.api_folders,
            api_map_markers=dependencies.api_map_markers,
            api_date_groups=dependencies.api_date_groups,
            api_settings=dependencies.api_settings,
            mosaic_next=dependencies.mosaic_next,
            compare_next=dependencies.compare_next,
            default_visible_pairing_candidates=dependencies.default_visible_pairing_candidates,
            warm_filtered_visible_ranked_candidates=dependencies.warm_filtered_visible_ranked_candidates,
            get_visible_past_matchups=dependencies.get_visible_past_matchups,
            classify_orientations_background=dependencies.classify_orientations_background,
            scan_metadata_background=dependencies.scan_metadata_background,
            swiss_pair_window=dependencies.swiss_pair_window,
            filtered_swiss_pair_window=dependencies.filtered_swiss_pair_window,
            filtered_mosaic_window=dependencies.filtered_mosaic_window,
            mosaic_explore_window=dependencies.mosaic_explore_window,
            mosaic_diverse_window=dependencies.mosaic_diverse_window,
            interaction_cache_warmup_delay_seconds=dependencies.interaction_cache_warmup_delay_seconds,
        )

    async def shutdown() -> None:
        await background_runtime.run_shutdown(
            thumbnails=dependencies.thumbnails,
            background_task_tracker=shell.background_task_tracker,
        )

    shell.app.router.on_startup.append(startup)
    shell.app.router.on_shutdown.append(shutdown)
    return AppLifecycleHandlers(startup=startup, shutdown=shutdown)


def configure_library_service(
    *,
    resolve_library_constraints: Callable[..., Awaitable[dict]],
    cache_root: Callable[[], str],
    clamp_int: Callable[[object, int, int, int], int],
    normalize_search_query: Callable[[str], str],
    schedule_thumbnail_prefetch: Callable[..., None],
    schedule_result_thumbnail_memory_warm: Callable[[list], None],
    rankings_response_cache_ttl_seconds: Callable[[], float],
) -> None:
    import db
    from features.library import service as library_service

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
        get_visible_pairing_pool_counts=lambda size, cache_root: db.get_visible_pairing_pool_counts(
            size,
            cache_root,
        ),
        rankings_response_cache_ttl_seconds=rankings_response_cache_ttl_seconds,
    )


def configure_settings_routes(
    *,
    settings_response_cache: dict,
    settings_response_cache_ttl_seconds: Callable[[], float],
    build_settings_response: settings_routes.BuildResponse,
    copy_settings_response: settings_routes.CopyResponse,
    track_background_task: settings_routes.TrackTask,
    get_refreshing: settings_routes.GetRefreshing,
    set_refreshing: settings_routes.SetRefreshing,
    build_cache_status: settings_routes.AsyncDictBuilder,
    build_ai_status: settings_routes.AsyncDictBuilder,
    people_status_payload: settings_routes.BuildResponse,
    invalidate_image_flag_caches: settings_routes.Invalidator,
    invalidate_pairing_cache: settings_routes.InvalidatePairing,
    invalidate_cache_status_cache: settings_routes.Invalidator,
    invalidate_ai_status_response_cache: settings_routes.Invalidator,
    invalidate_settings_response_cache: settings_routes.Invalidator,
    invalidate_rankings_cache: settings_routes.Invalidator,
    invalidate_vector_derived_caches: settings_routes.Invalidator,
    db_path: settings_routes.DbPathProvider | None = None,
    get_stats: settings_routes.BuildResponse | None = None,
    refresh_source_online_states: settings_routes.AsyncBoolBuilder | None = None,
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
    invalidate_rankings_cache: Callable[[], None],
    invalidate_interaction_response_cache: Callable[[], None],
    cache_root: Callable[[], str],
    resolve_library_constraints: Callable[..., Awaitable[dict]],
    schedule_thumbnail_prefetch: Callable[..., None],
    schedule_cached_thumbnail_memory_warm: Callable[..., None],
    db_signature: Callable[[], str] | None = None,
    get_active_images_for_pairing: Callable[[], Awaitable[list]] | None = None,
    get_past_matchups: Callable[[], Awaitable[set]] | None = None,
    get_visible_past_matchups: Callable[..., Awaitable[set]] | None = None,
    get_past_matchups_for_image_ids: Callable[[list[int]], Awaitable[set]] | None = None,
    get_active_images_by_ids: Callable[[list[int]], Awaitable[dict[int, dict]]] | None = None,
    get_visible_images_for_pairing: Callable[..., Awaitable[list]] | None = None,
    get_visible_orientation_pairing_pool_counts: Callable[..., Awaitable[dict]] | None = None,
    count_rankings: Callable[..., Awaitable[int]] | None = None,
    get_rankings: Callable[..., Awaitable[list]] | None = None,
    get_visible_pairing_pool_counts: Callable[..., Awaitable[dict]] | None = None,
    get_top_images: Callable[..., Awaitable[list]] | None = None,
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
    )


def configure_compare_routes(
    *,
    schedule_pairing_propagation: compare_routes.SchedulePropagation,
    invalidate_pairing_cache: compare_routes.InvalidatePairing,
    patch_pairing_cache: compare_routes.PatchPairingCache | None = None,
    add_past_matchups: compare_routes.AddPastMatchups | None = None,
    record_active_mosaic_pick: compare_routes.RecordMosaicPick | None = None,
    record_active_comparison: compare_routes.RecordComparison | None = None,
    undo_last_comparison: compare_routes.UndoComparison | None = None,
    mosaic_next_handler: compare_routes.NextHandler | None = None,
    compare_next_handler: compare_routes.NextHandler | None = None,
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
    text_search_resolution_cache_ttl_seconds: Callable[[], float],
    deep_search_query_record_cache_ttl_seconds: Callable[[], float],
    invalidate_ai_status_response_cache: Callable[[], None] | None = None,
    invalidate_settings_response_cache: Callable[[], None] | None = None,
) -> None:
    import db
    import settings
    from core import query_constraints
    from features.settings import status as settings_status

    query_constraints.configure(
        record_deep_search_query=lambda query: db.record_deep_search_query(query),
        extension_search_terms=db.IMAGE_EXTENSION_SEARCH_TERMS,
        invalidate_ai_status_response_cache=(
            invalidate_ai_status_response_cache or ai_routes.invalidate_ai_status_response_cache
        ),
        invalidate_settings_response_cache=(
            invalidate_settings_response_cache or settings_status.invalidate_settings_response_cache
        ),
        metadata_search_image_ids=lambda query: db.metadata_search_image_ids(query),
        get_deep_search_query_embedding=lambda query, model_key: db.get_deep_search_query_embedding(query, model_key),
        fast_search_embedding_config=settings.fast_search_embedding_config,
        get_settings=settings.get_settings,
        parse_people_ids=db.parse_people_ids,
        get_people_image_id_filter=lambda people_ids: db.get_people_image_id_filter(people_ids),
        text_search_resolution_cache_ttl_seconds=text_search_resolution_cache_ttl_seconds,
        deep_search_query_record_cache_ttl_seconds=deep_search_query_record_cache_ttl_seconds,
    )


def configure_export_routes(
    *,
    resolve_library_constraints: export_routes.ResolveLibraryConstraints,
    db_path: export_routes.DbPathProvider,
) -> None:
    export_routes.configure(
        resolve_library_constraints=resolve_library_constraints,
        db_path=db_path,
    )


def configure_app_runtime_services(shell: AppShell) -> AppRuntimeServices:
    import db
    import thumbnails
    from core import cache_events, query_constraints
    from core import requests as request_helpers
    from features.cache import status as cache_status_service
    from features.compare import service as compare_service
    from features.library import service as library_service
    from features.media import warm as media_warm
    from features.settings import status as settings_status

    def cache_root() -> str:
        return thumbnails.SSD_CACHE_DIR

    def invalidate_pairing_cache(*, matchups: bool = False) -> None:
        cache_events.invalidate_pairing_cache(matchups=matchups)

    def invalidate_rankings_cache() -> None:
        cache_events.invalidate_rankings_cache()

    def invalidate_image_flag_caches() -> None:
        db._invalidate_ranking_count_cache()
        db._invalidate_filter_options_cache()

    def invalidate_vector_derived_caches() -> None:
        cache_events.invalidate_vector_derived_caches()

    def invalidate_interaction_response_cache() -> None:
        cache_events.invalidate_interaction_response_cache()

    def schedule_pairing_propagation(coro) -> None:
        async def _runner():
            try:
                await coro
            except Exception as exc:
                print(f"Elo propagation error: {exc}")
            finally:
                invalidate_pairing_cache()

        asyncio.create_task(_runner())

    async def resolve_text_search(q: str, *, deep: bool = False) -> dict:
        return await query_constraints.resolve_configured_text_search(
            q,
            deep=deep,
            record_query=_app_compat_callable(
                "_record_deep_search_query",
                query_constraints.record_configured_deep_search_query,
            ),
            resolve_deep_search=_app_compat_callable(
                "_resolve_cached_deep_search",
                query_constraints.resolve_cached_deep_search,
            ),
            encode_text=_app_compat_callable(
                "_encode_text_with_config",
                query_constraints.encode_text_with_config,
            ),
            start_model_load=_app_compat_callable(
                "_start_search_model_load",
                query_constraints.start_search_model_load,
            ),
            apply_metadata_ids=_app_compat_callable(
                "_apply_metadata_search_ids",
                query_constraints.apply_configured_metadata_search_ids,
            ),
        )

    async def resolve_library_constraints(q: str = "", *, people: str = "", deep: bool = False) -> dict:
        return await query_constraints.resolve_configured_library_constraints(
            q=q,
            people=people,
            deep=deep,
            resolve_text_search=_app_compat_callable("_resolve_text_search", resolve_text_search),
        )

    configure_compare_service(
        invalidate_rankings_cache=invalidate_rankings_cache,
        invalidate_interaction_response_cache=invalidate_interaction_response_cache,
        cache_root=cache_root,
        resolve_library_constraints=resolve_library_constraints,
        schedule_thumbnail_prefetch=media_warm.schedule_thumbnail_prefetch,
        schedule_cached_thumbnail_memory_warm=media_warm.schedule_cached_thumbnail_memory_warm,
    )
    configure_query_constraints(
        text_search_resolution_cache_ttl_seconds=lambda: _app_compat_value(
            "_text_search_resolution_cache_ttl_seconds",
            query_constraints._text_search_resolution_cache_ttl_seconds,
        ),
        deep_search_query_record_cache_ttl_seconds=lambda: _app_compat_value(
            "_deep_search_query_record_cache_ttl_seconds",
            query_constraints._deep_search_query_record_cache_ttl_seconds,
        ),
        invalidate_ai_status_response_cache=ai_routes.invalidate_ai_status_response_cache,
        invalidate_settings_response_cache=settings_status.invalidate_settings_response_cache,
    )
    configure_compare_routes(
        schedule_pairing_propagation=lambda coro: _app_compat_callable(
            "_schedule_pairing_propagation",
            schedule_pairing_propagation,
        )(coro),
        invalidate_pairing_cache=invalidate_pairing_cache,
        mosaic_next_handler=lambda **kwargs: _app_compat_callable(
            "_mosaic_next_impl",
            compare_service.mosaic_next_impl,
        )(**kwargs),
        compare_next_handler=lambda **kwargs: _app_compat_callable(
            "_compare_next_impl",
            compare_service.compare_next_impl,
        )(**kwargs),
    )
    configure_library_service(
        resolve_library_constraints=resolve_library_constraints,
        cache_root=cache_root,
        clamp_int=request_helpers.clamp_int,
        normalize_search_query=query_constraints.normalize_search_query,
        schedule_thumbnail_prefetch=media_warm.schedule_thumbnail_prefetch,
        schedule_result_thumbnail_memory_warm=media_warm.schedule_result_thumbnail_memory_warm,
        rankings_response_cache_ttl_seconds=lambda: _app_compat_value(
            "_rankings_response_cache_ttl_seconds",
            library_service._rankings_response_cache_ttl_seconds,
        ),
    )
    configure_export_routes(
        resolve_library_constraints=resolve_library_constraints,
        db_path=lambda: db.DB_PATH,
    )
    configure_settings_routes(
        settings_response_cache=settings_status._settings_response_cache,
        settings_response_cache_ttl_seconds=lambda: _app_compat_value(
            "_settings_response_cache_ttl_seconds",
            settings_status._settings_response_cache_ttl_seconds,
        ),
        build_settings_response=lambda: _app_compat_callable(
            "_build_settings_response",
            settings_status.build_settings_response,
        )(),
        copy_settings_response=settings_status.copy_settings_response,
        track_background_task=shell.track_background_task,
        get_refreshing=settings_status.get_settings_response_refreshing,
        set_refreshing=settings_status.set_settings_response_refreshing,
        build_cache_status=cache_status_service.build_cache_status,
        build_ai_status=ai_routes.build_ai_status,
        people_status_payload=lambda: _app_compat_callable(
            "_people_status_payload",
            people_routes.people_status_payload,
        )(),
        invalidate_image_flag_caches=invalidate_image_flag_caches,
        invalidate_pairing_cache=invalidate_pairing_cache,
        invalidate_cache_status_cache=cache_status_service.invalidate_cache_status_cache,
        invalidate_ai_status_response_cache=ai_routes.invalidate_ai_status_response_cache,
        invalidate_settings_response_cache=settings_status.invalidate_settings_response_cache,
        invalidate_rankings_cache=invalidate_rankings_cache,
        invalidate_vector_derived_caches=invalidate_vector_derived_caches,
    )

    return AppRuntimeServices(
        cache_root=cache_root,
        invalidate_pairing_cache=invalidate_pairing_cache,
        invalidate_rankings_cache=invalidate_rankings_cache,
        invalidate_image_flag_caches=invalidate_image_flag_caches,
        invalidate_vector_derived_caches=invalidate_vector_derived_caches,
        invalidate_interaction_response_cache=invalidate_interaction_response_cache,
        schedule_pairing_propagation=schedule_pairing_propagation,
        resolve_text_search=resolve_text_search,
        resolve_library_constraints=resolve_library_constraints,
    )


def configure_app_lifecycle(shell: AppShell) -> AppLifecycleHandlers:
    import db
    import face_worker
    import resource_governor
    import settings
    import thumbnails
    from features.cache import status as cache_status_service
    from features.catalog import metadata as catalog_metadata
    from features.compare import service as compare_service

    runtime_services = shell.runtime_services
    if runtime_services is None:
        raise RuntimeError("App runtime services must be configured before lifecycle wiring")

    lifecycle = register_app_lifecycle(
        shell,
        AppLifecycleDependencies(
            smoke_mode_enabled=background_runtime.smoke_mode_enabled,
            warm_templates=lambda: _app_compat_callable("_warm_templates", shell.warm_templates)(),
            thumbnails=thumbnails,
            settings=settings,
            resource_governor=resource_governor,
            face_worker=face_worker,
            init_db=lambda: db.init_db(),
            get_filter_options=lambda: db.get_filter_options(),
            get_date_groups=lambda **kwargs: db.get_date_groups(**kwargs),
            get_catalog_image_counts=lambda: db.get_catalog_image_counts(),
            get_stats=lambda: db.get_stats(),
            get_ai_status_counts=lambda: db.get_ai_status_counts(),
            get_visible_orientation_pairing_pool_counts=lambda size, cache_root, orientation: (
                db.get_visible_orientation_pairing_pool_counts(size, cache_root, orientation)
            ),
            get_catalog_summary=lambda: db.get_catalog_summary(),
            cache_root=runtime_services.cache_root,
            build_ai_status=ai_routes.build_ai_status,
            build_cache_status=cache_status_service.build_cache_status,
            api_rankings=library_routes.api_rankings,
            api_folders=catalog_routes.api_folders,
            api_map_markers=library_routes.api_map_markers,
            api_date_groups=library_routes.api_date_groups,
            api_settings=settings_routes.api_settings,
            mosaic_next=compare_routes.mosaic_next,
            compare_next=compare_routes.compare_next,
            default_visible_pairing_candidates=compare_service.default_visible_pairing_candidates,
            warm_filtered_visible_ranked_candidates=compare_service.warm_filtered_visible_ranked_candidates,
            get_visible_past_matchups=compare_service.get_visible_past_matchups,
            classify_orientations_background=catalog_metadata.classify_orientations_background,
            scan_metadata_background=catalog_metadata.scan_metadata_background,
            swiss_pair_window=compare_service._SWISS_PAIR_WINDOW,
            filtered_swiss_pair_window=compare_service._FILTERED_SWISS_PAIR_WINDOW,
            filtered_mosaic_window=compare_service._FILTERED_MOSAIC_WINDOW,
            mosaic_explore_window=compare_service._MOSAIC_EXPLORE_WINDOW,
            mosaic_diverse_window=compare_service._MOSAIC_DIVERSE_WINDOW,
            interaction_cache_warmup_delay_seconds=INTERACTION_CACHE_WARMUP_DELAY_SECONDS,
        ),
    )
    object.__setattr__(shell, "lifecycle", lifecycle)
    return lifecycle


def configure_idle_activity_middleware(shell: AppShell) -> Callable:
    import thumbnails

    middleware = shell.install_idle_activity_middleware(
        thumbnails=thumbnails,
        excluded_paths=shell.idle_activity_excluded_paths,
    )
    object.__setattr__(shell, "idle_activity_middleware", middleware)
    return middleware


def create_app_shell(
    *,
    base_dir: str | None = None,
    started_at: float | None = None,
    title: str = "photoArchive",
) -> AppShell:
    root = base_dir or os.path.dirname(os.path.dirname(__file__))
    app = create_base_app(base_dir=root, title=title)
    templates = create_templates(base_dir=root)
    static_assets = StaticAssetContext(
        app_dir=root,
        repo_dir=os.path.dirname(root),
        started_at=started_at,
    )
    shell = AppShell(
        app=app,
        templates=templates,
        static_assets=static_assets,
    )
    from core import wiring

    wiring.configure_database_backed_providers()
    wiring.configure_people_routes()
    wiring.configure_status_media_search_providers()
    wiring.configure_cache_events()
    wiring.configure_catalog_routes()
    wiring.configure_library_routes()
    wiring.configure_search_routes()
    object.__setattr__(shell, "runtime_services", configure_app_runtime_services(shell))
    page_routes.configure(templates=templates, template_context=shell.template_context)
    app.include_router(page_routes.router)
    app.include_router(people_routes.router)
    dev_routes.configure(started_at=static_assets.started_at, git_commit=static_assets.git_commit)
    app.include_router(dev_routes.router)
    app.include_router(catalog_routes.router)
    app.include_router(compare_routes.router)
    app.include_router(media_routes.router)
    app.include_router(library_routes.router)
    app.include_router(export_routes.router)
    app.include_router(search_routes.router)
    app.include_router(settings_routes.router)
    app.include_router(cache_routes.router)
    app.include_router(ai_routes.router)
    configure_idle_activity_middleware(shell)
    configure_app_lifecycle(shell)
    return shell
