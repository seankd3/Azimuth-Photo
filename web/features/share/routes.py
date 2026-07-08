import os
import time
from collections.abc import Awaitable, Callable

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field


router = APIRouter()

CreateOrRotateShare = Callable[..., Awaitable[dict | None]]
GetShare = Callable[[int], Awaitable[dict | None]]
RevokeShare = Callable[[int], Awaitable[bool]]
ResolveToken = Callable[[str], Awaitable[dict | None]]
TokenAllowsImage = Callable[[str, int], Awaitable[bool]]
ThumbnailResponse = Callable[..., Awaitable[Response]]

_templates: Jinja2Templates | None = None
_create_or_rotate_share: CreateOrRotateShare | None = None
_get_share: GetShare | None = None
_revoke_share: RevokeShare | None = None
_resolve_token: ResolveToken | None = None
_token_allows_image: TokenAllowsImage | None = None
_thumbnail_response: ThumbnailResponse | None = None


class ShareBody(BaseModel):
    rotate: bool = False
    expires_in_days: int | None = Field(default=None, ge=1, le=3660)


def configure(
    *,
    templates: Jinja2Templates,
    create_or_rotate_share: CreateOrRotateShare,
    get_share: GetShare,
    revoke_share: RevokeShare,
    resolve_token: ResolveToken,
    token_allows_image: TokenAllowsImage,
    thumbnail_response: ThumbnailResponse,
) -> None:
    global _templates, _create_or_rotate_share, _get_share, _revoke_share
    global _resolve_token, _token_allows_image, _thumbnail_response
    _templates = templates
    _create_or_rotate_share = create_or_rotate_share
    _get_share = get_share
    _revoke_share = revoke_share
    _resolve_token = resolve_token
    _token_allows_image = token_allows_image
    _thumbnail_response = thumbnail_response


def _configured() -> None:
    if (
        _templates is None
        or _create_or_rotate_share is None
        or _get_share is None
        or _revoke_share is None
        or _resolve_token is None
        or _token_allows_image is None
        or _thumbnail_response is None
    ):
        raise RuntimeError("Share routes are not configured")


def _share_url(request: Request, token: str) -> str:
    base = (os.environ.get("PHOTOARCHIVE_SHARE_BASE_URL") or str(request.base_url)).strip()
    return f"{base.rstrip('/')}/s/{token}"


def _share_payload(request: Request, share: dict | None) -> dict | None:
    if share is None:
        return None
    return {
        "token": share["token"],
        "url": _share_url(request, share["token"]),
        "created_at": share["created_at"],
        "expires_at": share["expires_at"],
    }


def _public_response(response: Response) -> Response:
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


def _date_subtitle(collection: dict | None) -> str:
    if not collection:
        return ""
    start = (collection.get("date_min") or "")[:10]
    end = (collection.get("date_max") or "")[:10]
    if start and end and start != end:
        return f"{start} to {end}"
    return start or end


def _gallery_payload(token: str, collection: dict | None) -> dict:
    images = []
    if collection:
        for row in collection.get("images") or []:
            image_id = int(row["id"])
            images.append(
                {
                    "id": image_id,
                    "filename": row.get("filename") or f"Photo {image_id}",
                    "aspect_ratio": float(row.get("aspect_ratio") or 1.5),
                    "date_taken": row.get("date_taken"),
                    "thumb": f"/s/{token}/thumb/sm/{image_id}",
                    "preview": f"/s/{token}/thumb/md/{image_id}",
                    "full": f"/s/{token}/img/{image_id}",
                }
            )
    return {
        "token": token,
        "name": collection.get("name") if collection else "Share unavailable",
        "photo_count": len(images),
        "date_range": _date_subtitle(collection),
        "images": images,
    }


@router.post("/api/user-collections/{collection_id}/share")
async def api_create_share(collection_id: int, payload: ShareBody, request: Request):
    _configured()
    expires_at = None
    if payload.expires_in_days is not None:
        expires_at = time.time() + (payload.expires_in_days * 86400)
    share = await _create_or_rotate_share(
        collection_id,
        expires_at=expires_at,
        rotate=payload.rotate,
    )
    if share is None:
        return JSONResponse({"error": "Collection not found"}, status_code=404)
    return {"ok": True, "share": _share_payload(request, share)}


@router.get("/api/user-collections/{collection_id}/share")
async def api_get_share(collection_id: int, request: Request):
    _configured()
    share = await _get_share(collection_id)
    return {"share": _share_payload(request, share)}


@router.post("/api/user-collections/{collection_id}/share/revoke")
async def api_revoke_share(collection_id: int):
    _configured()
    revoked = await _revoke_share(collection_id)
    if not revoked:
        return JSONResponse({"error": "Share not found"}, status_code=404)
    return {"ok": True}


@router.get("/s/{token}", response_class=HTMLResponse)
async def public_share_gallery(token: str, request: Request):
    _configured()
    collection = await _resolve_token(token)
    status_code = 200 if collection is not None else 404
    page = _gallery_payload(token, collection)
    response = _templates.TemplateResponse(
        request,
        "share_gallery.html",
        {
            "not_found": collection is None,
            "collection_name": page["name"],
            "photo_count": page["photo_count"],
            "date_range": page["date_range"],
            "gallery_json": page,
        },
        status_code=status_code,
    )
    return _public_response(response)


@router.get("/s/{token}/thumb/{size}/{image_id}")
async def public_share_thumbnail(request: Request, token: str, size: str, image_id: int):
    _configured()
    if size not in {"sm", "md", "lg"}:
        return _public_response(JSONResponse({"error": "Not found"}, status_code=404))
    allowed = await _token_allows_image(token, image_id)
    if not allowed:
        return _public_response(JSONResponse({"error": "Not found"}, status_code=404))
    response = await _thumbnail_response(request, size, image_id)
    return _public_response(response)


@router.get("/s/{token}/img/{image_id}")
async def public_share_image(request: Request, token: str, image_id: int):
    _configured()
    allowed = await _token_allows_image(token, image_id)
    if not allowed:
        return _public_response(JSONResponse({"error": "Not found"}, status_code=404))
    response = await _thumbnail_response(request, "lg", image_id)
    return _public_response(response)
