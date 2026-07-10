from core import wiring
from core.app_factory import create_app
from features.develop import ai_mask_routes, hdr_routes, import_routes, preset_routes, routes as develop_routes


app = create_app()
wiring.configure_develop_routes()
wiring.configure_develop_import_routes()
wiring.configure_develop_preset_routes()
wiring.configure_develop_ai_mask_routes()
wiring.configure_develop_hdr_routes()
app.include_router(hdr_routes.router)
app.include_router(ai_mask_routes.router)
app.include_router(preset_routes.router)
app.include_router(develop_routes.router)
app.include_router(import_routes.router)
