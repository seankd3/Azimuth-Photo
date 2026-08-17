import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.gzip import GZipMiddleware

from core import background as background_runtime
from core import cache_events
from core.static_assets import StaticAssetContext, warm_templates


DEFAULT_TEMPLATE_WARMUP = ("desktop.html",)
INTERACTION_CACHE_WARMUP_DELAY_SECONDS = 0.05


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

    def install_idle_activity_middleware(self, *, excluded_paths=None):
        return background_runtime.install_idle_activity_middleware(
            self.app,
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
    app.mount("/static", StaticFiles(directory=os.path.join(root, "static")), name="static")
    return app


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
        # `import thumbnails` stood here, and a `thumbnails=` argument that
        # `run_shutdown` stopped taking. The module went in 6fc7e31c, so every
        # shutdown since has raised ModuleNotFoundError before reaching the
        # TypeError behind it — and nobody saw either, because a collection
        # error had the test suite reporting nothing for the same 32 commits.
        await background_runtime.run_shutdown(
            background_task_tracker=shell.background_task_tracker,
        )

    shell.app.router.on_startup.append(startup)
    shell.app.router.on_shutdown.append(shutdown)
    return AppLifecycleHandlers(startup=startup, shutdown=shutdown)


def configure_app_lifecycle(shell: AppShell) -> AppLifecycleHandlers:
    lifecycle = register_app_lifecycle(shell)
    object.__setattr__(shell, "lifecycle", lifecycle)
    return lifecycle


def configure_idle_activity_middleware(shell: AppShell) -> Callable:
    middleware = shell.install_idle_activity_middleware(
        excluded_paths=shell.idle_activity_excluded_paths,
    )
    object.__setattr__(shell, "idle_activity_middleware", middleware)
    return middleware
