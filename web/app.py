from core import wiring
from core.app_factory import create_app
from features.develop import ai_mask_routes, hdr_routes, import_routes, pano_routes, preset_routes, routes as develop_routes, xmp_write_routes
from features.library import geo_routes, saved_views
from features.quality import routes as quality_routes
from features.system import backup_routes


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
app.include_router(hdr_routes.router)
app.include_router(pano_routes.router)
app.include_router(ai_mask_routes.router)
app.include_router(preset_routes.router)
app.include_router(develop_routes.router)
app.include_router(import_routes.router)
app.include_router(xmp_write_routes.router)
app.include_router(saved_views.router)
app.include_router(geo_routes.router)
app.include_router(backup_routes.router)
app.include_router(quality_routes.router)
