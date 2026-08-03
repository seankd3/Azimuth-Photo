import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.gzip import GZipMiddleware

from core import background as background_runtime
from core.browser_origin import BrowserOriginGuardMiddleware
from core.owner_auth import OwnerAuthMiddleware
from core.static_assets import StaticAssetContext, warm_templates
from features.access import routes as access_routes
from features.auth import routes as auth_routes
from features.ai import routes as ai_routes
from features.cache import routes as cache_routes
from features.captions import routes as caption_routes
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
from features.publish import routes as publish_routes
from features.search import routes as search_routes
from features.share import routes as share_routes
from features.shared import routes as shared_routes
from features.stacks import routes as stack_routes
from features.settings import routes as settings_routes
from features.trash import routes as trash_routes


DEFAULT_TEMPLATE_WARMUP = ("desktop.html", "mobile.html", "share_gallery.html")
INTERACTION_CACHE_WARMUP_DELAY_SECONDS = 0.05


@dataclass(frozen=True)
class AppRuntimeServices:
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
    """Cache static assets; versioned URLs (?v=) are immutable for a year."""

    def __init__(self, app, max_age: int = 300, versioned_max_age: int = 31_536_000):
        self.app = app
        self.max_age = max_age
        self.versioned_max_age = versioned_max_age

    def _cache_control(self, scope) -> str:
        query = scope.get("query_string") or b""
        if b"v=" in query:
            return f"public, max-age={self.versioned_max_age}, immutable"
        # ES modules are imported by bare relative specifier, so they never
        # carry the ?v= stamp the entry script gets. Letting them go stale
        # runs a fresh shell against old modules for up to an hour after an
        # update; they are small, so revalidate and let ETag answer 304.
        path = scope.get("path") or ""
        if path.endswith(".js") or path.endswith(".mjs"):
            return "public, no-cache"
        return f"public, max-age={self.max_age}, stale-while-revalidate=3600"

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or not (scope.get("path") or "").startswith("/static/"):
            await self.app(scope, receive, send)
            return

        cache_control = self._cache_control(scope)

        async def send_with_cache_headers(message):
            if message.get("type") == "http.response.start":
                headers = list(message.get("headers") or [])
                if not any(name.lower() == b"cache-control" for name, _value in headers):
                    headers.append((b"cache-control", cache_control.encode("ascii")))
                    message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_cache_headers)


def create_base_app(*, base_dir: str | None = None, title: str = "Azimuth Photo") -> FastAPI:
    """Create the bare FastAPI app with middleware and static mounting only."""

    root = base_dir or os.path.dirname(os.path.dirname(__file__))
    app = FastAPI(title=title)
    app.add_middleware(SelectiveGZipMiddleware, minimum_size=1000)
    app.add_middleware(StaticCacheHeadersMiddleware, max_age=300)
    app.add_middleware(BrowserOriginGuardMiddleware)
    app.add_middleware(OwnerAuthMiddleware)
    app.mount("/static", StaticFiles(directory=os.path.join(root, "static")), name="static")
    return app


def create_app(*, base_dir: str | None = None, title: str = "Azimuth Photo") -> FastAPI:
    """Create the routed app shell with runtime and lifecycle wiring."""

    return create_app_shell(base_dir=base_dir, title=title).app


def create_templates(*, base_dir: str | None = None) -> Jinja2Templates:
    root = base_dir or os.path.dirname(os.path.dirname(__file__))
    return Jinja2Templates(directory=os.path.join(root, "templates"))


def register_app_lifecycle(shell: AppShell) -> AppLifecycleHandlers:
    async def startup() -> None:
        await background_runtime.run_startup(
            warm_templates=shell.warm_templates,
            track_background_task=shell.track_background_task,
        )

    async def shutdown() -> None:
        import caption_worker
        import thumbnails

        await background_runtime.run_shutdown(
            thumbnails=thumbnails,
            background_task_tracker=shell.background_task_tracker,
            caption_worker=caption_worker,
        )

    shell.app.router.on_startup.append(startup)
    shell.app.router.on_shutdown.append(shutdown)
    return AppLifecycleHandlers(startup=startup, shutdown=shutdown)


def configure_app_runtime_services(shell: AppShell) -> AppRuntimeServices:
    import db
    from core import cache_events, query_constraints, wiring
    from features.cache import status as cache_status_service
    from features.compare import service as compare_service
    from features.library import service as library_service
    from features.media import warm as media_warm
    from features.settings import status as settings_status

    def invalidate_pairing_cache(*, matchups: bool = False) -> None:
        cache_events.invalidate_pairing_cache(matchups=matchups)

    def invalidate_rankings_cache() -> None:
        cache_events.invalidate_rankings_cache()

    def invalidate_image_flag_caches() -> None:
        cache_events.invalidate_rankings_cache()
        db._invalidate_ranking_count_cache()
        db._invalidate_filter_options_cache()

    def invalidate_vector_derived_caches() -> None:
        cache_events.invalidate_vector_derived_caches()

    def invalidate_interaction_response_cache() -> None:
        cache_events.invalidate_interaction_response_cache()

    def apply_propagated_pairing_updates(*, elo_deltas=None) -> None:
        """Patch the ids propagation touched; clear only when they are unknown.

        A pick schedules its own propagation, so clearing every reservoir on
        drain threw away the rows that same pick had just patched and made the
        next click fully cold.
        """
        if elo_deltas is None:
            invalidate_pairing_cache()
            return
        compare_service.patch_propagated_pairing_cache(elo_deltas)

    def schedule_pairing_propagation(coro) -> None:
        from core import propagation_queue

        propagation_queue.schedule(
            coro,
            invalidate_callback=apply_propagated_pairing_updates,
        )

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
    )
    wiring.configure_library_service(
        resolve_library_constraints=resolve_library_constraints,
        schedule_thumbnail_prefetch=media_warm.schedule_thumbnail_prefetch,
        schedule_result_thumbnail_memory_warm=media_warm.schedule_result_thumbnail_memory_warm,
        rankings_response_cache_ttl_seconds=lambda: library_service._rankings_response_cache_ttl_seconds,
    )
    wiring.configure_collection_routes(resolve_library_constraints=resolve_library_constraints)
    wiring.configure_share_routes(
        templates=shell.templates,
        resolve_library_constraints=resolve_library_constraints,
    )
    wiring.configure_publish_routes(
        templates=shell.templates,
        resolve_library_constraints=resolve_library_constraints,
        track_background_task=shell.track_background_task,
    )
    wiring.configure_shared_routes()
    wiring.configure_export_routes(
        resolve_library_constraints=resolve_library_constraints,
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
    lifecycle = register_app_lifecycle(shell)
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
    title: str = "Azimuth Photo",
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
    app.state.azimuth_shell = shell
    from core import wiring

    wiring.configure_people_routes()
    wiring.configure_status_media_search_providers()
    wiring.configure_cache_events()
    wiring.configure_library_routes()
    wiring.configure_collection_routes()
    wiring.configure_share_routes(templates=templates)
    wiring.configure_publish_routes(templates=templates)
    wiring.configure_shared_routes()
    object.__setattr__(shell, "runtime_services", configure_app_runtime_services(shell))
    page_routes.configure(templates=templates, template_context=shell.template_context)
    app.include_router(page_routes.router)
    auth_routes.configure(templates=templates)
    app.include_router(auth_routes.router)
    app.include_router(access_routes.router)
    app.include_router(people_routes.router)
    dev_routes.configure(started_at=static_assets.started_at, git_commit=static_assets.git_commit)
    app.include_router(dev_routes.router)
    app.include_router(catalog_routes.router)
    app.include_router(compare_routes.router)
    app.include_router(media_routes.router)
    app.include_router(library_routes.router)
    app.include_router(collection_routes.router)
    app.include_router(stack_routes.router)
    app.include_router(trash_routes.router)
    app.include_router(share_routes.router)
    app.include_router(publish_routes.router)
    app.include_router(shared_routes.router)
    app.include_router(export_routes.router)
    app.include_router(imports_routes.router)
    app.include_router(search_routes.router)
    app.include_router(settings_routes.router)
    app.include_router(cache_routes.router)
    app.include_router(caption_routes.router)
    app.include_router(ai_routes.router)
    configure_idle_activity_middleware(shell)
    configure_app_lifecycle(shell)
    return shell
