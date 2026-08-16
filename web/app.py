"""The one assembly: create the shell, mount every router, arm the lifecycle."""

from core.runtime_paths import apply_environment_defaults

apply_environment_defaults()

import os

from core import cache_events
from core.app_factory import (
    AppShell,
    configure_app_lifecycle,
    configure_idle_activity_middleware,
    create_base_app,
    create_templates,
)
from core.static_assets import StaticAssetContext
import api as core_api
from features.ai import routes as ai_routes
from features.backup import routes as cloud_backup_routes
from features.cache import routes as cache_routes
from features.captions import routes as caption_routes
from features.catalog import routes as catalog_routes
from features.collections import routes as collection_routes
from features.dev import routes as dev_routes
from features.develop import ai_mask_routes, hdr_routes, import_routes, preset_routes, routes as develop_routes, xmp_write_routes
from features.develop import export_presets
from features.export import routes as export_routes
from features.imports import routes as imports_routes
from features.library import keyword_routes, routes as library_routes, saved_views, watched_routes
from features.media import routes as media_routes
from features.pages import routes as page_routes
from features.people import routes as people_routes
from features.quality import routes as quality_routes
from features.search import routes as search_routes
from features.settings import routes as settings_routes
from features.stacks import routes as stack_routes
from features.sync import lr_routes
from features.system import backup_routes, health_routes, quit_routes, version_routes
from features.trash import routes as trash_routes

_ROOT = os.path.dirname(__file__)

app = create_base_app(base_dir=_ROOT)
templates = create_templates(base_dir=_ROOT)
shell = AppShell(
    app=app,
    templates=templates,
    static_assets=StaticAssetContext(app_dir=_ROOT, repo_dir=os.path.dirname(_ROOT)),
)
app.state.azimuth_shell = shell
cache_events.register_with_db()

page_routes.configure(templates=templates, template_context=shell.template_context)
dev_routes.configure(
    started_at=shell.static_assets.started_at, git_commit=shell.static_assets.git_commit
)

for router in (
    # The core's routes come first, because FastAPI matches in registration
    # order. Everything below this line is the old implementation being
    # replaced one surface at a time; as each route moves up here, the module
    # that used to answer it is deleted rather than left shadowed.
    core_api.router,
    page_routes.router,
    people_routes.router,
    dev_routes.router,
    catalog_routes.router,
    collection_routes.router,
    stack_routes.router,
    trash_routes.router,
    export_routes.router,
    imports_routes.router,
    search_routes.router,
    settings_routes.router,
    cache_routes.router,
    caption_routes.router,
    ai_routes.router,
    hdr_routes.router,
    ai_mask_routes.router,
    preset_routes.router,
    export_presets.router,
    develop_routes.router,
    import_routes.router,
    xmp_write_routes.router,
    saved_views.router,
    keyword_routes.router,
    backup_routes.router,
    cloud_backup_routes.router,
    health_routes.router,
    quit_routes.router,
    version_routes.router,
    quality_routes.router,
    watched_routes.router,
    lr_routes.router,
):
    app.include_router(router)

configure_idle_activity_middleware(shell)
configure_app_lifecycle(shell)


@app.on_event("startup")
async def _repair_metadata_search_index():
    """Put a drifted search index back in step — after the grid is serving.

    Rebuilding costs seconds on a large library, and nothing about opening the
    app should wait for it. Metadata search keeps working throughout: the index
    is a speed path, and a drifted one is still almost entirely right.
    """

    async def _repair() -> None:
        import logging

        import db
        from data import connection as data_connection
        from data import schema as data_schema

        conn = await db.get_db()
        try:
            drift = await data_schema.metadata_fts_drift(conn)
            if drift <= 0:
                return
            logging.getLogger(__name__).info(
                "Repairing metadata search index (off by %s rows)", drift
            )
            await data_schema.rebuild_metadata_fts(conn)
        finally:
            await data_connection.close_async(conn, db_path=db.DB_PATH)

    shell.track_background_task(_repair())
