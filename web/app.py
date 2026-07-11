from core import wiring
from core.app_factory import create_app
from features.develop import ai_mask_routes, hdr_routes, import_routes, pano_routes, preset_routes, routes as develop_routes, xmp_write_routes
from features.library import geo_routes, keyword_routes, saved_views, watched_routes
from features.publishing import routes as gallery_routes
from features.develop import export_presets
from features.media import routes as media_routes
from features.quality import routes as quality_routes
from features.system import backup_routes
from features.sync import hub_routes, satellite, satellite_routes
from features.sync.sync_worker import SyncWorker, configure_worker


app = create_app()
wiring.configure_develop_routes()
wiring.configure_develop_import_routes()
wiring.configure_develop_preset_routes()
wiring.configure_develop_ai_mask_routes()
wiring.configure_develop_hdr_routes()
wiring.configure_develop_pano_routes()
wiring.configure_develop_xmp_write_routes()
wiring.configure_system_backup_routes()
# PATCH: quality lane — register technical quality scorer routes
import db as _db
quality_routes.configure(db_path=lambda: _db.DB_PATH)
geo_routes.configure(db_path=lambda: _db.DB_PATH)
watched_routes.configure(db_path=lambda: _db.DB_PATH)
gallery_routes.configure(db_path=lambda: _db.DB_PATH, thumbnail_response=media_routes.thumbnail_response)
export_presets.configure(db_path=lambda: _db.DB_PATH)
hub_routes.configure(db_path=lambda: _db.DB_PATH)
app.include_router(hdr_routes.router)
app.include_router(pano_routes.router)
app.include_router(ai_mask_routes.router)
app.include_router(preset_routes.router)
app.include_router(develop_routes.router)
app.include_router(import_routes.router)
app.include_router(xmp_write_routes.router)
app.include_router(saved_views.router)
app.include_router(geo_routes.router)
app.include_router(keyword_routes.router)
app.include_router(gallery_routes.router)
app.include_router(export_presets.router)
app.include_router(backup_routes.router)
app.include_router(quality_routes.router)
app.include_router(watched_routes.router)
app.include_router(hub_routes.router)
app.include_router(satellite_routes.router)

# PATCH: satellite lane — keep the worker out of hub processes entirely.
if satellite.is_satellite_mode():
    _sync_worker = SyncWorker(db_path=_db.DB_PATH)
    configure_worker(_sync_worker)

    @app.on_event("startup")
    async def _start_satellite_sync_worker():
        await satellite.ensure_sync_state(_db.DB_PATH)
        app.state.photoarchive_shell.track_background_task(_sync_worker.run())

    @app.on_event("shutdown")
    async def _stop_satellite_sync_worker():
        _sync_worker.stop()
