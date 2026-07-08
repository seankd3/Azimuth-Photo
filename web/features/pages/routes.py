import os

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, HTMLResponse


router = APIRouter()
_templates = None
_template_context = None
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


@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return _render(request, "settings.html")


@router.get("/compare", response_class=HTMLResponse)
async def compare_page(request: Request):
    return _render(request, "compare.html")


@router.get("/rankings", response_class=HTMLResponse)
async def rankings_page(request: Request):
    return _render(request, "library.html")


@router.get("/library", response_class=HTMLResponse)
async def library_page(request: Request):
    return _render(request, "library.html")


@router.get("/people", response_class=HTMLResponse)
async def people_page(request: Request):
    return _render(request, "people.html")


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    return _render(request, "settings.html")


@router.get("/catalog", response_class=HTMLResponse)
async def catalog_page(request: Request):
    return _render(request, "settings.html")


@router.get("/m", response_class=HTMLResponse)
async def mobile_page(request: Request):
    return _render(request, "mobile.html")


@router.get("/d", response_class=HTMLResponse)
async def desktop_page(request: Request):
    return _render(request, "desktop.html")


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
