from core import wiring
from core.app_factory import create_app
from features.develop import import_routes, routes as develop_routes


app = create_app()
wiring.configure_develop_routes()
wiring.configure_develop_import_routes()
app.include_router(develop_routes.router)
app.include_router(import_routes.router)
