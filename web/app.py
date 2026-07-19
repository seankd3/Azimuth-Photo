from core.runtime_paths import apply_environment_defaults

apply_environment_defaults()

import asyncio
import os
import socket

from fastapi import Request
from fastapi.responses import JSONResponse

from core import wiring
from core.app_factory import create_app
from features.develop import ai_mask_routes, hdr_routes, import_routes, pano_routes, preset_routes, routes as develop_routes, xmp_write_routes
from features.library import geo_routes, keyword_routes, saved_views, watched_routes
from features.publishing import routes as gallery_routes
from features.publish import routes as publish_routes
from features.develop import export_presets
from features.media import routes as media_routes
from features.quality import routes as quality_routes
from features.backup import routes as cloud_backup_routes
from features.system import backup_routes, health_routes, version_routes
from features.sync import hub_routes, lr_routes, mdns, oplog_routes, pair_routes, pairing, satellite, satellite_routes
from features.sync.contract import ApiRevisionMismatch
from features.sync.sync_worker import SyncWorker, configure_worker


app = create_app()


@app.exception_handler(ApiRevisionMismatch)
async def api_revision_mismatch(_request: Request, _error: ApiRevisionMismatch):
    return JSONResponse(
        status_code=409,
        content={
            "detail": "This hub needs an update before it can safely perform that action.",
            "code": "api_rev_mismatch",
        },
    )
wiring.configure_develop_routes()
wiring.configure_develop_import_routes()
wiring.configure_develop_preset_routes()
wiring.configure_develop_ai_mask_routes()
wiring.configure_develop_hdr_routes()
wiring.configure_develop_pano_routes()
wiring.configure_develop_xmp_write_routes()
wiring.configure_system_backup_routes()
wiring.configure_cloud_backup_routes()
wiring.configure_system_health_routes()
# PATCH: quality lane — register technical quality scorer routes
import db as _db
quality_routes.configure(db_path=lambda: _db.DB_PATH)
geo_routes.configure(db_path=lambda: _db.DB_PATH)
watched_routes.configure(db_path=lambda: _db.DB_PATH)
gallery_routes.configure(db_path=lambda: _db.DB_PATH, thumbnail_response=media_routes.thumbnail_response)
export_presets.configure(db_path=lambda: _db.DB_PATH)
hub_routes.configure(db_path=lambda: _db.DB_PATH)
oplog_routes.configure(db_path=lambda: _db.DB_PATH)
pair_routes.configure(db_path=lambda: _db.DB_PATH)
app.include_router(hdr_routes.router)
app.include_router(pano_routes.router)
app.include_router(ai_mask_routes.router)
app.include_router(preset_routes.router)
app.include_router(export_presets.router)
app.include_router(develop_routes.router)
app.include_router(import_routes.router)
app.include_router(xmp_write_routes.router)
app.include_router(saved_views.router)
app.include_router(geo_routes.router)
app.include_router(keyword_routes.router)
app.include_router(gallery_routes.router)
app.include_router(backup_routes.router)
app.include_router(cloud_backup_routes.router)
app.include_router(health_routes.router)
app.include_router(version_routes.router)
app.include_router(quality_routes.router)
app.include_router(watched_routes.router)
app.include_router(hub_routes.router)
app.include_router(oplog_routes.router)
app.include_router(satellite_routes.router)
app.include_router(lr_routes.router)
app.include_router(pair_routes.router)

# Keep the worker out of hub processes entirely. Standalone (satellite with no
# hub) runs everything except sync; attaching a hub at runtime starts it.
satellite.load_stored_hub()
if satellite.is_satellite_mode():

    _sync_worker_lock = asyncio.Lock()

    async def _start_sync_worker() -> bool:
        if not satellite.has_hub():
            return False
        async with _sync_worker_lock:
            if getattr(app.state, "photoarchive_sync_worker", None) is not None:
                return True
            await satellite.ensure_sync_state(_db.DB_PATH)
            updater = None
            install_root = os.environ.get("PHOTOARCHIVE_INSTALL_ROOT", "").strip()
            if install_root:
                from features.sync import client_update

                root = client_update.resolve_install_root(install_root)
                updater = client_update.ClientUpdater(
                    install_root=root,
                    hub=satellite.hub_url(),
                    local_sha=client_update.resolve_local_sha(install_root=root),
                    restart=client_update.request_process_restart,
                    ui_busy=client_update.ui_session_busy,
                )
            worker = SyncWorker(db_path=_db.DB_PATH, updater=updater)
            if updater is not None:
                updater.request = worker._hub_request
                updater.hub = worker.hub
                from features.sync.client_update import consume_rollback_notice_into_status

                consume_rollback_notice_into_status(updater)
            configure_worker(worker)
            app.state.photoarchive_sync_worker = worker
            app.state.photoarchive_shell.track_background_task(worker.run())
            return True

    satellite.register_sync_starter(_start_sync_worker)

    @app.on_event("startup")
    async def _start_satellite_sync_worker():
        await _start_sync_worker()

    @app.on_event("shutdown")
    async def _stop_satellite_sync_worker():
        worker = getattr(app.state, "photoarchive_sync_worker", None)
        if worker is not None:
            worker.stop()


@app.on_event("startup")
async def _init_hub_client_bundle_identity():
    """Cache /api/version identity + git-archive once at hub boot (not per request)."""

    if satellite.is_satellite_mode():
        return
    from features.system import client_bundle

    try:
        await asyncio.to_thread(client_bundle.init_hub_client_identity)
    except Exception:
        # A missing git checkout must not take the hub down; /api/version still
        # answers with sha=unknown and the bundle route returns 503.
        import logging

        logging.getLogger(__name__).exception("hub client bundle identity init failed")


@app.on_event("startup")
async def _start_hub_mdns():
    if not mdns.is_hub_mode():
        return
    import settings as _settings

    hub_id = await pairing.get_hub_id(_db.DB_PATH)
    name = (
        str(_settings.get_settings().get("share_brand_name") or "").strip()
        or os.environ.get("PHOTOARCHIVE_LIBRARY_NAME", "").strip()
        or socket.gethostname()
        or "Azimuth Photo"
    )
    port = int(os.environ.get("PHOTOARCHIVE_PORT") or 8000)
    await asyncio.to_thread(mdns.start_hub_announce, name=name, port=port, hub_id=hub_id)


@app.on_event("startup")
async def _resume_publish_hook_retries():
    await publish_routes.resume_pending_hook_retries()


@app.on_event("shutdown")
async def _stop_hub_mdns():
    mdns.stop_hub_announce()
