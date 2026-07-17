import os
import re
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


_MODULE_PRELOAD_CACHE: dict = {}
_IMPORT_FROM_RE = re.compile(r"""from\s*['"](\.\.?/[^'"]+?\.js)['"]""")
_IMPORT_SIDE_RE = re.compile(r"""^\s*import\s+['"](\.\.?/[^'"]+?\.js)['"]""")


def _module_static_imports(text: str) -> list:
    deps = []
    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("//") or stripped.startswith("*"):
            continue
        if "import(" in line or "import (" in line:  # dynamic import -> lazy chunk
            continue
        m = _IMPORT_FROM_RE.search(line) or _IMPORT_SIDE_RE.search(line)
        if m:
            deps.append(m.group(1))
    return deps


def _eager_module_preloads(entry: str) -> list:
    """Modules statically reachable from `entry` (relative to static/js/) for
    <link rel=modulepreload>. Excludes the entry and dynamically-imported chunks."""
    cached = _MODULE_PRELOAD_CACHE.get(entry)
    if cached is not None:
        return cached
    js_root = os.path.join(_STATIC_DIR, "js")
    ordered: list = []
    seen: set = set()
    queue = [entry]
    while queue:
        rel = queue.pop(0)
        if rel in seen:
            continue
        seen.add(rel)
        path = os.path.normpath(os.path.join(js_root, rel))
        if not path.startswith(js_root) or not os.path.isfile(path):
            continue
        if rel != entry:
            ordered.append(rel)
        try:
            text = open(path, encoding="utf-8").read()
        except OSError:
            continue
        cur_dir = os.path.dirname(rel)
        for dep in _module_static_imports(text):
            dep_rel = os.path.normpath(os.path.join(cur_dir, dep)).replace(os.sep, "/")
            if dep_rel not in seen:
                queue.append(dep_rel)
    _MODULE_PRELOAD_CACHE[entry] = ordered
    return ordered


def _render(request: Request, template_name: str):
    if _templates is None or _template_context is None:
        raise RuntimeError("Page routes are not configured")
    context = dict(_template_context(request))
    if template_name == "desktop.html":
        # Preload the browse-critical eager graph. Exclude the heavy RAW develop
        # editor (develop/**) so it does not high-priority-crowd first paint; it
        # still loads on demand for editing.
        context["module_preloads"] = [
            mod for mod in _eager_module_preloads("desktop/bootstrap.js")
            if "/develop/" not in mod
        ]
    elif template_name == "mobile.html":
        context["module_preloads"] = _eager_module_preloads("mobile/bootstrap.js")
    return _templates.TemplateResponse(request, template_name, context)


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
