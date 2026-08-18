"""Local lifecycle and route truth for the V2 desktop."""

from fastapi import APIRouter, Request, Response


router = APIRouter()


def _ready(request: Request) -> bool:
    return not bool(getattr(request.app.state, "closing", False))


@router.get("/")
@router.get("/api/system/health")
async def health(request: Request, response: Response):
    ready = _ready(request)
    if not ready:
        response.status_code = 503
    return {"ready": ready}


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
