"""Local lifecycle and route truth for the V2 desktop."""

from pathlib import Path

from fastapi import APIRouter, Request, Response
from fastapi.responses import FileResponse


router = APIRouter()
_SHELL = Path(__file__).parents[1] / "templates" / "v2.html"
PRODUCT = "azimuth-v2"


def _ready(request: Request) -> bool:
    return not bool(getattr(request.app.state, "closing", False))


@router.get("/")
@router.get("/api/system/health")
async def health(request: Request, response: Response):
    ready = _ready(request)
    if not ready:
        response.status_code = 503
    return {"product": PRODUCT, "ready": ready}


@router.get("/d", include_in_schema=False)
async def desktop():
    return FileResponse(_SHELL)


@router.get("/api/routes")
async def routes(request: Request):
    return sorted(
        path for path in request.app.openapi()["paths"] if path.startswith("/api/")
    )


@router.post("/api/system/prepare-quit")
async def prepare_quit(request: Request):
    request.app.state.closing = True
    await request.app.state.library.close()
    return {"ok": True}
