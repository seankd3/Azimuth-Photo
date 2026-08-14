from core.runtime_paths import apply_environment_defaults

apply_environment_defaults()

import asyncio
import os
import socket

from fastapi import Request
from fastapi.responses import JSONResponse

from core.app_factory import create_app
from features.develop import ai_mask_routes, hdr_routes, import_routes, preset_routes, routes as develop_routes, xmp_write_routes
from features.library import keyword_routes, saved_views, watched_routes
from features.develop import export_presets
from features.media import routes as media_routes
from features.quality import routes as quality_routes
from features.backup import routes as cloud_backup_routes
from features.system import backup_routes, health_routes, quit_routes, version_routes
from features.sync import lr_routes


app = create_app()

import db as _db
from archive import role

app.include_router(hdr_routes.router)
app.include_router(ai_mask_routes.router)
app.include_router(preset_routes.router)
app.include_router(export_presets.router)
app.include_router(develop_routes.router)
app.include_router(import_routes.router)
app.include_router(xmp_write_routes.router)
app.include_router(saved_views.router)
app.include_router(keyword_routes.router)
app.include_router(backup_routes.router)
app.include_router(cloud_backup_routes.router)
app.include_router(health_routes.router)
app.include_router(quit_routes.router)
app.include_router(version_routes.router)
app.include_router(quality_routes.router)
app.include_router(watched_routes.router)
app.include_router(lr_routes.router)


@app.on_event("startup")
async def _repair_metadata_search_index():
    """Put a drifted search index back in step — after the grid is serving.

    Rebuilding costs seconds on a large library, and nothing about opening the
    app should wait for it. Metadata search keeps working throughout: the index
    is a speed path, and a drifted one is still almost entirely right.
    """

    async def _repair() -> None:
        import logging

        from data import schema as data_schema

        from data import connection as data_connection

        conn = await _db.get_db()
        try:
            drift = await data_schema.metadata_fts_drift(conn)
            if drift <= 0:
                return
            logging.getLogger(__name__).info(
                "Repairing metadata search index (off by %s rows)", drift
            )
            await data_schema.rebuild_metadata_fts(conn)
        finally:
            await data_connection.close_async(conn, db_path=_db.DB_PATH)

    app.state.azimuth_shell.track_background_task(_repair())
