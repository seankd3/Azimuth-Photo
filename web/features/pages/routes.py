import os
import sqlite3

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse


router = APIRouter()
_templates = None
_template_context = None
_setup_known_done = False
_STATIC_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "static",
)


def configure(*, templates, template_context) -> None:
    global _templates, _template_context
    _templates = templates
    _template_context = template_context


def _render(request: Request, template_name: str):
    if _templates is None or _template_context is None:
        raise RuntimeError("Page routes are not configured")
    return _templates.TemplateResponse(request, template_name, _template_context(request))


def needs_setup() -> bool:
    """True only for a genuinely fresh install: no completed setup, no sources."""

    global _setup_known_done
    if _setup_known_done:
        return False
    if os.environ.get("PHOTOARCHIVE_SMOKE_MODE"):
        return False
    import settings as app_settings

    if app_settings.get_settings().get("setup_completed"):
        _setup_known_done = True
        return False
    import db

    try:
        conn = sqlite3.connect(db.DB_PATH)
        try:
            count = conn.execute("SELECT COUNT(*) FROM catalog_sources").fetchone()[0]
        finally:
            conn.close()
    except sqlite3.Error:
        return False
    if count > 0:
        _setup_known_done = True
        return False
    return True


def reset_setup_cache() -> None:
    global _setup_known_done
    _setup_known_done = False


@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    if needs_setup():
        return RedirectResponse("/setup", status_code=307)
    return _render(request, "desktop.html")


@router.get("/m", response_class=HTMLResponse)
async def mobile_page(request: Request):
    if needs_setup():
        return RedirectResponse("/setup", status_code=307)
    return _render(request, "mobile.html")


@router.get("/d", response_class=HTMLResponse)
async def desktop_page(request: Request):
    if needs_setup():
        return RedirectResponse("/setup", status_code=307)
    return _render(request, "desktop.html")


@router.get("/setup", response_class=HTMLResponse)
async def setup_page(request: Request):
    return _render(request, "setup.html")


@router.post("/api/setup/complete")
async def setup_complete():
    import settings as app_settings

    config = dict(app_settings.get_settings())
    config["setup_completed"] = True
    app_settings.save_settings(config)
    global _setup_known_done
    _setup_known_done = True
    return JSONResponse({"ok": True})


@router.get("/sw.js")
async def service_worker():
    """Serve the mobile service worker from the site root so it can claim scope /."""
    return FileResponse(
        os.path.join(_STATIC_DIR, "sw.js"),
        media_type="application/javascript",
        headers={
            "Service-Worker-Allowed": "/",
            "Cache-Control": "no-cache",
        },
    )
