import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.gzip import GZipMiddleware

from core import background as background_runtime
from core.static_assets import StaticAssetContext, warm_templates
from features.access import routes as access_routes
from features.ai import routes as ai_routes
from features.cache import routes as cache_routes
from features.catalog import routes as catalog_routes
from features.collections import routes as collection_routes
from features.compare import routes as compare_routes
from features.dev import routes as dev_routes
from features.export import routes as export_routes
from features.imports import routes as imports_routes
from features.library import routes as library_routes
from features.media import routes as media_routes
from features.pages import routes as page_routes
from features.people import routes as people_routes
from features.search import routes as search_routes
from features.settings import routes as settings_routes


DEFAULT_TEMPLATE_WARMUP = ("settings.html", "library.html", "compare.html", "people.html")
INTERACTION_CACHE_WARMUP_DELAY_SECONDS = 0.05


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
    app.add_middleware(StaticCacheHeadersMiddleware, max_age=300)
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


def configure_app_runtime_services(shell: AppShell) -> AppRuntimeServices:
    import db
    import thumbnails
    from core import cache_events, query_constraints, wiring
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
        from core import propagation_queue

        propagation_queue.schedule(coro, invalidate_callback=invalidate_pairing_cache)

    async def resolve_text_search(q: str, *, deep: bool = False) -> dict:
        return await query_constraints.resolve_configured_text_search(
            q,
            deep=deep,
            encode_text=query_constraints.encode_text_with_config,
            start_model_load=query_constraints.start_search_model_load,
            apply_metadata_ids=query_constraints.apply_configured_metadata_search_ids,
        )

    async def resolve_library_constraints(q: str = "", *, people: str = "", deep: bool = False) -> dict:
        return await query_constraints.resolve_configured_library_constraints(
            q=q,
            people=people,
            deep=deep,
            resolve_text_search=resolve_text_search,
        )

    wiring.configure_compare_service(
        invalidate_rankings_cache=invalidate_rankings_cache,
        invalidate_interaction_response_cache=invalidate_interaction_response_cache,
        cache_root=cache_root,
        resolve_library_constraints=resolve_library_constraints,
        schedule_thumbnail_prefetch=media_warm.schedule_thumbnail_prefetch,
        schedule_cached_thumbnail_memory_warm=media_warm.schedule_cached_thumbnail_memory_warm,
    )
    wiring.configure_query_constraints(
        text_search_resolution_cache_ttl_seconds=lambda: query_constraints._text_search_resolution_cache_ttl_seconds,
    )
    wiring.configure_compare_routes(
        schedule_pairing_propagation=schedule_pairing_propagation,
        invalidate_pairing_cache=invalidate_pairing_cache,
        mosaic_next_handler=lambda **kwargs: compare_service.mosaic_next_impl(**kwargs),
        compare_next_handler=lambda **kwargs: compare_service.compare_next_impl(**kwargs),
    )
    wiring.configure_library_service(
        resolve_library_constraints=resolve_library_constraints,
        cache_root=cache_root,
        clamp_int=request_helpers.clamp_int,
        normalize_search_query=query_constraints.normalize_search_query,
        schedule_thumbnail_prefetch=media_warm.schedule_thumbnail_prefetch,
        schedule_result_thumbnail_memory_warm=media_warm.schedule_result_thumbnail_memory_warm,
        rankings_response_cache_ttl_seconds=lambda: library_service._rankings_response_cache_ttl_seconds,
    )
    wiring.configure_export_routes(
        resolve_library_constraints=resolve_library_constraints,
        db_path=lambda: db.DB_PATH,
        get_import_batch_image_ids=lambda batch_id: db.get_import_batch_image_ids(batch_id),
    )
    wiring.configure_settings_routes(
        settings_response_cache=settings_status._settings_response_cache,
        settings_response_cache_ttl_seconds=lambda: settings_status._settings_response_cache_ttl_seconds,
        build_settings_response=lambda: settings_status.build_settings_response(),
        copy_settings_response=settings_status.copy_settings_response,
        track_background_task=shell.track_background_task,
        get_refreshing=settings_status.get_settings_response_refreshing,
        set_refreshing=settings_status.set_settings_response_refreshing,
        build_cache_status=cache_status_service.build_cache_status,
        build_ai_status=ai_routes.build_ai_status,
        people_status_payload=lambda: people_routes.people_status_payload(),
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
            warm_templates=shell.warm_templates,
            thumbnails=thumbnails,
            settings=settings,
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
    app.state.photoarchive_shell = shell
    from core import wiring

    wiring.configure_database_backed_providers()
    wiring.configure_people_routes()
    wiring.configure_status_media_search_providers()
    wiring.configure_cache_events()
    wiring.configure_catalog_routes()
    wiring.configure_library_routes()
    wiring.configure_search_routes()
    wiring.configure_collection_routes()
    object.__setattr__(shell, "runtime_services", configure_app_runtime_services(shell))
    page_routes.configure(templates=templates, template_context=shell.template_context)
    app.include_router(page_routes.router)
    app.include_router(access_routes.router)
    app.include_router(people_routes.router)
    dev_routes.configure(started_at=static_assets.started_at, git_commit=static_assets.git_commit)
    app.include_router(dev_routes.router)
    app.include_router(catalog_routes.router)
    app.include_router(compare_routes.router)
    app.include_router(media_routes.router)
    app.include_router(library_routes.router)
    app.include_router(collection_routes.router)
    app.include_router(export_routes.router)
    app.include_router(imports_routes.router)
    app.include_router(search_routes.router)
    app.include_router(settings_routes.router)
    app.include_router(cache_routes.router)
    app.include_router(ai_routes.router)
    configure_idle_activity_middleware(shell)
    configure_app_lifecycle(shell)
    return shell
