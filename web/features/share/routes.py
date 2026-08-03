import db
from features.share.visitor import (
    attachment_name as _attachment_name,
    brand as _brand_payload,
    no_leak as _public_response,
)
import asyncio
import json
import os
import tempfile
import time
import zipfile
from collections.abc import Awaitable, Callable
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from features.share import auth


router = APIRouter()
UNLOCK_FAILURE_LIMIT = 5
UNLOCK_FAILURE_WINDOW_SECONDS = 15 * 60
MAX_UNLOCK_PASSWORD_LENGTH = 256
MAX_TRACKED_UNLOCK_TOKENS = 2048
SHARE_DOWNLOAD_TIER = "lg"

ThumbnailResponse = Callable[..., Awaitable[Response]]
GetCollection = Callable[..., Awaitable[dict | None]]
ResolveSmartImageIds = Callable[[dict], Awaitable[list[int]]]

_templates: Jinja2Templates | None = None
_thumbnail_response: ThumbnailResponse | None = None
_resolve_smart_image_ids: ResolveSmartImageIds | None = None
_unlock_failures: dict[str, dict[str, float | int]] = {}


class ShareBody(BaseModel):
    rotate: bool = False
    expires_in_days: int | None = Field(default=None, ge=1, le=3660)
    password: str | None = Field(default=None, max_length=MAX_UNLOCK_PASSWORD_LENGTH)
    clear_password: bool = False


class FavoriteBody(BaseModel):
    image_id: int | None = None
    on: bool = False
    name: str | None = None
    done: bool = False


def configure(
    *,
    templates: Jinja2Templates,
    thumbnail_response: ThumbnailResponse,
    resolve_smart_image_ids: ResolveSmartImageIds | None = None,
) -> None:
    global _templates
    global _thumbnail_response
    global _resolve_smart_image_ids
    _templates = templates
    _thumbnail_response = thumbnail_response
    _resolve_smart_image_ids = resolve_smart_image_ids


def _configured() -> None:
    if (
        _templates is None
        or db.create_or_rotate_share is None
        or db.get_collection_share is None
        or db.revoke_collection_share is None
        or db.set_collection_share_password is None
        or db.record_share_view is None
        or db.resolve_share_token is None
        or db.share_token_allows_image is None
        or db.set_share_favorite is None
        or db.list_share_favorites is None
        or db.favorites_for_collection is None
        or db.favorite_visitors_for_collection is None
        or _thumbnail_response is None
    ):
        raise RuntimeError("Share routes are not configured")


async def _snapshot_image_ids_for_collection(collection_id: int) -> list[int] | None:
    if db.get_collection is None:
        return None
    collection = await db.get_collection(collection_id, limit=1, offset=0)
    if collection is None or not collection.get("smart"):
        return None
    if _resolve_smart_image_ids is None:
        raise RuntimeError("Share routes are not configured")
    return await _resolve_smart_image_ids(collection["query"] or {})


def _share_url(request: Request, token: str) -> str:
    base = (os.environ.get("AZIMUTH_SHARE_BASE_URL") or str(request.base_url)).strip()
    return f"{base.rstrip('/')}/s/{token}"


def _share_payload(request: Request, share: dict | None) -> dict | None:
    if share is None:
        return None
    return {
        "token": share["token"],
        "url": _share_url(request, share["token"]),
        "created_at": share["created_at"],
        "expires_at": share["expires_at"],
        "expired": bool(share.get("expired")),
        "protected": bool(share.get("password_hash")),
        "view_count": int(share.get("view_count") or 0),
        "first_viewed_at": share.get("first_viewed_at"),
        "last_viewed_at": share.get("last_viewed_at"),
    }


def _date_subtitle(collection: dict | None) -> str:
    if not collection:
        return ""
    start = (collection.get("date_min") or "")[:10]
    end = (collection.get("date_max") or "")[:10]
    if start and end and start != end:
        return f"{start} to {end}"
    return start or end


def _gallery_payload(token: str, collection: dict | None, *, base_url: str = "") -> dict:
    images = []
    if collection:
        for row in collection.get("images") or []:
            image_id = int(row["id"])
            filename = row.get("filename") or f"photo-{image_id}.jpg"
            images.append(
                {
                    "id": image_id,
                    "filename": filename,
                    "aspect_ratio": float(row.get("aspect_ratio") or 1.5),
                    "date_taken": row.get("date_taken"),
                    "thumb": f"/s/{token}/thumb/sm/{image_id}",
                    "preview": f"/s/{token}/thumb/md/{image_id}",
                    "full": f"/s/{token}/img/{image_id}",
                    "download": f"/s/{token}/img/{image_id}",
                    "download_name": _download_name(image_id, filename),
                }
            )
    for index, image in enumerate(images, start=1):
        image["display_label"] = f"Photo {index} of {len(images)}"
    return {
        "token": token,
        "name": collection.get("name") if collection else "Share unavailable",
        "photo_count": len(images),
        "date_range": _date_subtitle(collection),
        "brand": _brand_payload(),
        "download_size_label": "web-size copy",
        "images": images,
        "download_all_url": f"/s/{token}/download-all",
        "og_image": f"{base_url.rstrip('/')}{images[0]['preview']}" if images and base_url else "",
    }


def _download_name(image_id: int, filename: str) -> str:
    basename = os.path.basename(filename or "").strip() or f"photo-{image_id}.jpg"
    return basename.replace('"', "").replace("'", "")


def _zip_arcname(image_id: int, filename: str, used_names: set[str]) -> str:
    """Unique zip entry name via publishing's zip-slip-safe attachment sanitizer."""
    candidate = _attachment_name(image_id, filename, suffix=".jpg")
    stem = Path(candidate).stem
    extension = Path(candidate).suffix or ".jpg"
    suffix = 2
    while candidate.casefold() in used_names:
        candidate = f"{stem}-{suffix}{extension}"
        suffix += 1
    used_names.add(candidate.casefold())
    return candidate


async def _write_share_zip(collection: dict, request: Request, destination: str) -> dict:
    skipped = []
    included = 0
    used_names: set[str] = {"azimuth-download-manifest.json"}
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for image in collection.get("images") or []:
            image_id = int(image["id"])
            filename = str(image.get("filename") or f"photo-{image_id}.jpg")
            try:
                response = await _thumbnail_response(request, SHARE_DOWNLOAD_TIER, image_id)
                if response.status_code != 200:
                    raise FileNotFoundError("Gallery-ready image unavailable")
                arcname = _zip_arcname(image_id, filename, used_names)
                response_path = getattr(response, "path", None)
                if response_path:
                    archive.write(response_path, arcname=arcname)
                else:
                    body = getattr(response, "body", None)
                    if body is None:
                        raise FileNotFoundError("Gallery-ready image unavailable")
                    archive.writestr(arcname, body)
                included += 1
            except (OSError, ValueError):
                skipped.append({
                    "image_id": image_id,
                    "filename": Path(filename.replace("\\", "/")).name,
                    "reason": "preview unavailable",
                })
        if skipped:
            archive.writestr(
                "azimuth-download-manifest.json",
                json.dumps({
                    "requested_count": len(collection.get("images") or []),
                    "included_count": included,
                    "skipped_count": len(skipped),
                    "skipped": skipped,
                }, indent=2),
            )
    return {"path": destination, "included": included, "skipped": skipped}


def _zip_share(collection: dict, request: Request, destination: str) -> dict:
    return asyncio.run(_write_share_zip(collection, request, destination))


def _favorite_ids(favorites: list[dict]) -> list[int]:
    return [int(row["image_id"]) for row in favorites]


async def _password_hash_for_payload(payload: ShareBody) -> str | None:
    if payload.clear_password:
        return None
    if payload.password is None:
        return None
    return await asyncio.to_thread(auth.hash_password, payload.password)


def _has_password_change(payload: ShareBody) -> bool:
    return payload.password is not None or payload.clear_password


def _is_password_only_update(payload: ShareBody) -> bool:
    return (
        not payload.rotate
        and payload.expires_in_days is None
        and _has_password_change(payload)
    )


def _unlock_retry_after(token: str, now: float | None = None) -> int | None:
    now = time.time() if now is None else now
    failure = _unlock_failures.get(token)
    if not failure:
        return None
    first_at = float(failure.get("first_at") or now)
    count = int(failure.get("count") or 0)
    expires_at = first_at + UNLOCK_FAILURE_WINDOW_SECONDS
    if now >= expires_at:
        _unlock_failures.pop(token, None)
        return None
    if count >= UNLOCK_FAILURE_LIMIT:
        return max(1, int(expires_at - now))
    return None


def _record_unlock_failure(token: str, now: float | None = None) -> None:
    now = time.time() if now is None else now
    expired = [
        key
        for key, value in _unlock_failures.items()
        if now >= float(value.get("first_at") or now) + UNLOCK_FAILURE_WINDOW_SECONDS
    ]
    for key in expired:
        _unlock_failures.pop(key, None)
    if token not in _unlock_failures and len(_unlock_failures) >= MAX_TRACKED_UNLOCK_TOKENS:
        # Capacity pressure must never clear a live lockout: that is how an
        # attempt flood would buy itself fresh guesses against a real token.
        evictable = [
            key
            for key, value in _unlock_failures.items()
            if int(value.get("count") or 0) < UNLOCK_FAILURE_LIMIT
        ]
        if not evictable:
            return
        _unlock_failures.pop(
            min(evictable, key=lambda key: float(_unlock_failures[key].get("first_at") or 0)),
            None,
        )
    failure = _unlock_failures.get(token)
    if not failure or now >= float(failure.get("first_at") or now) + UNLOCK_FAILURE_WINDOW_SECONDS:
        _unlock_failures[token] = {"count": 1, "first_at": now}
        return
    failure["count"] = int(failure.get("count") or 0) + 1


def _clear_unlock_failures(token: str) -> None:
    _unlock_failures.pop(token, None)


def _locked_share_response(
    request: Request,
    token: str,
    collection: dict,
    *,
    unlock_error: bool = False,
    throttle_seconds: int | None = None,
    password_too_long: bool = False,
    status_code: int = 200,
) -> Response:
    response = _templates.TemplateResponse(
        request,
        "share_gallery.html",
        {
            "not_found": False,
            "locked": True,
            "unlock_error": unlock_error,
            "throttle_seconds": throttle_seconds,
            "throttle_minutes": ((throttle_seconds or 0) + 59) // 60 if throttle_seconds else None,
            "password_too_long": password_too_long,
            "token": token,
            "collection_name": collection.get("name") or "Protected share",
            "photo_count": int(collection.get("image_count") or 0),
            "date_range": _date_subtitle(collection),
            "brand": _brand_payload(),
        },
        status_code=status_code,
    )
    if throttle_seconds is not None:
        response.headers["Retry-After"] = str(throttle_seconds)
    return _public_response(response)


@router.post("/api/user-collections/{collection_id}/share")
async def api_create_share(collection_id: int, payload: ShareBody, request: Request):
    _configured()
    if _is_password_only_update(payload):
        active = await db.get_collection_share(collection_id)
        if active is not None:
            share = await db.set_collection_share_password(collection_id, await _password_hash_for_payload(payload))
            return {"ok": True, "share": _share_payload(request, share)}

    expires_at = None
    if payload.expires_in_days is not None:
        expires_at = time.time() + (payload.expires_in_days * 86400)
    password_hash = await _password_hash_for_payload(payload) if _has_password_change(payload) else None
    if payload.rotate and not _has_password_change(payload):
        active = await db.get_collection_share(collection_id)
        password_hash = active.get("password_hash") if active else None
    share = await db.create_or_rotate_share(
        collection_id,
        expires_at=expires_at,
        rotate=payload.rotate,
        password_hash=password_hash,
        snapshot_image_ids=await _snapshot_image_ids_for_collection(collection_id),
    )
    if share is None:
        return JSONResponse({"error": "Collection not found"}, status_code=404)
    return {"ok": True, "share": _share_payload(request, share)}


@router.get("/api/user-collections/{collection_id}/share")
async def api_get_share(collection_id: int, request: Request):
    _configured()
    share = await db.get_collection_share(collection_id)
    return {"share": _share_payload(request, share)}


@router.post("/api/user-collections/{collection_id}/share/revoke")
async def api_revoke_share(collection_id: int):
    _configured()
    revoked = await db.revoke_collection_share(collection_id)
    if not revoked:
        return JSONResponse({"error": "Share not found"}, status_code=404)
    return {"ok": True}


@router.get("/api/user-collections/{collection_id}/share/favorites")
async def api_share_favorites(collection_id: int):
    _configured()
    favorites = await db.favorites_for_collection(collection_id)
    owner_favorites = [
        {"image_id": int(row["image_id"]), "created_at": float(row["created_at"])}
        for row in favorites
    ]
    share = await db.get_collection_share(collection_id)
    groups = await db.favorite_visitors_for_collection(collection_id)
    visitors = [
        {
            "visitor": auth.visitor_label(str(group["visitor_id"])),
            "count": int(group["count"]),
            "image_ids": group["image_ids"],
        }
        for group in groups
    ]
    return {
        "favorites": owner_favorites,
        "count": len(owner_favorites),
        "client_finished_at": share.get("client_finished_at") if share else None,
        "visitors": visitors,
    }


@router.get("/s/{token}", response_class=HTMLResponse)
async def public_share_gallery(token: str, request: Request):
    _configured()
    collection = await db.resolve_share_token(token)
    status_code = 200 if collection is not None else 404
    locked = bool(collection is not None and not auth.is_unlocked(request, collection))
    if locked:
        return _locked_share_response(
            request,
            token,
            collection,
            unlock_error=request.query_params.get("e") == "1",
            status_code=status_code,
        )

    page = _gallery_payload(token, collection, base_url=str(request.base_url))
    response = _templates.TemplateResponse(
        request,
        "share_gallery.html",
        {
            "not_found": collection is None,
            "locked": False,
            "token": token,
            "collection_name": page["name"],
            "photo_count": page["photo_count"],
            "date_range": page["date_range"],
            "gallery_json": page,
            "brand": page["brand"],
            "og_image": page["og_image"],
        },
        status_code=status_code,
    )
    if collection is not None and not request.cookies.get(auth.VIEW_COOKIE_NAME):
        await db.record_share_view(token)
        auth.set_view_cookie(response, token, request=request)
    return _public_response(response)


@router.get("/s/{token}/favorites")
async def public_share_favorites(token: str, request: Request):
    _configured()
    collection = await db.resolve_share_token(token)
    if collection is None or not auth.is_unlocked(request, collection):
        return _public_response(JSONResponse({"error": "Not found"}, status_code=404))
    visitor_id = auth.visitor_id(request)
    favorites = (
        await db.list_share_favorites(int(collection["share_id"]), visitor_id=visitor_id)
        if visitor_id is not None
        else []
    )
    return _public_response(JSONResponse({"favorites": _favorite_ids(favorites), "done": bool(collection.get("client_finished_at"))}))


@router.post("/s/{token}/favorite")
async def public_share_favorite(token: str, payload: FavoriteBody, request: Request):
    _configured()
    collection = await db.resolve_share_token(token)
    if collection is None or not auth.is_unlocked(request, collection):
        return _public_response(JSONResponse({"error": "Not found"}, status_code=404))
    if payload.done:
        finished_at = await db.mark_share_finished(int(collection["share_id"])) if db.mark_share_finished else None
        visitor_id = auth.visitor_id(request)
        favorites = (
            await db.list_share_favorites(int(collection["share_id"]), visitor_id=visitor_id)
            if visitor_id is not None
            else []
        )
        return _public_response(JSONResponse({"ok": finished_at is not None, "favorites": _favorite_ids(favorites), "done": finished_at is not None}))
    if payload.image_id is None:
        return _public_response(JSONResponse({"error": "Image required"}, status_code=422))
    existing_visitor_id = auth.visitor_id(request)
    visitor_id = existing_visitor_id or auth.new_visitor_id()
    ok = await db.set_share_favorite(
        int(collection["share_id"]),
        int(payload.image_id),
        bool(payload.on),
        client_name=payload.name,
        visitor_id=visitor_id,
    )
    if not ok:
        return _public_response(JSONResponse({"error": "Not found"}, status_code=404))
    favorites = await db.list_share_favorites(int(collection["share_id"]), visitor_id=visitor_id)
    response = JSONResponse({"ok": True, "favorites": _favorite_ids(favorites), "done": False})
    if existing_visitor_id is None:
        auth.set_visitor_cookie(response, token, visitor_id, request=request)
    return _public_response(response)


@router.post("/s/{token}/unlock")
async def public_share_unlock(token: str, request: Request):
    _configured()
    collection = await db.resolve_share_token(token)
    retry_after = _unlock_retry_after(token)
    if retry_after is not None:
        if collection is not None:
            return _locked_share_response(
                request,
                token,
                collection,
                throttle_seconds=retry_after,
                status_code=429,
            )
        response = JSONResponse({"error": "Too many unlock attempts"}, status_code=429)
        response.headers["Retry-After"] = str(retry_after)
        return _public_response(response)
    password = await auth.read_form_password(request)
    if password is None or len(password) > MAX_UNLOCK_PASSWORD_LENGTH:
        if collection is not None:
            return _locked_share_response(
                request,
                token,
                collection,
                password_too_long=True,
                status_code=413,
            )
        return _public_response(JSONResponse({"error": "Password is too long"}, status_code=413))
    unlocked = collection is not None and await asyncio.to_thread(
        auth.verify_password, password, collection.get("password_hash")
    )
    if not unlocked:
        # Only real tokens are tracked: made-up ones must not be able to flood
        # the failure table and evict a token that is genuinely under attack.
        if collection is not None:
            _record_unlock_failure(token)
        await asyncio.sleep(0.4)
        return _public_response(RedirectResponse(f"/s/{token}?e=1", status_code=303))

    _clear_unlock_failures(token)
    response = RedirectResponse(f"/s/{token}", status_code=303)
    auth.set_unlock_cookie(
        response,
        token,
        auth.cookie_value(token, collection["password_hash"]),
        request=request,
    )
    return _public_response(response)


@router.get("/s/{token}/thumb/{size}/{image_id}")
async def public_share_thumbnail(request: Request, token: str, size: str, image_id: int):
    _configured()
    if size not in {"sm", "md", "lg"}:
        return _public_response(JSONResponse({"error": "Not found"}, status_code=404))
    collection = await db.resolve_share_token(token)
    if collection is None or not auth.is_unlocked(request, collection):
        return _public_response(JSONResponse({"error": "Not found"}, status_code=404))
    allowed = await db.share_token_allows_image(token, image_id)
    if not allowed:
        return _public_response(JSONResponse({"error": "Not found"}, status_code=404))
    response = await _thumbnail_response(request, size, image_id)
    return _public_response(response)


@router.get("/s/{token}/img/{image_id}")
async def public_share_image(request: Request, token: str, image_id: int):
    _configured()
    collection = await db.resolve_share_token(token)
    if collection is None or not auth.is_unlocked(request, collection):
        return _public_response(JSONResponse({"error": "Not found"}, status_code=404))
    allowed = await db.share_token_allows_image(token, image_id)
    if not allowed:
        return _public_response(JSONResponse({"error": "Not found"}, status_code=404))
    response = await _thumbnail_response(request, SHARE_DOWNLOAD_TIER, image_id)
    return _public_response(response)


@router.get("/s/{token}/download-all")
async def public_share_download_all(token: str, request: Request):
    _configured()
    collection = await db.resolve_share_token(token)
    if collection is None or not auth.is_unlocked(request, collection):
        return _public_response(JSONResponse({"error": "Not found"}, status_code=404))
    handle = tempfile.NamedTemporaryFile(prefix="azimuth-share-", suffix=".zip", delete=False)
    handle.close()
    try:
        result = await asyncio.to_thread(_zip_share, collection, request, handle.name)
    except Exception:
        Path(handle.name).unlink(missing_ok=True)
        return _public_response(JSONResponse({"error": "Could not prepare share download"}, status_code=422))
    raw_name = str(collection.get("name") or "Shared photos")
    safe_name = "".join(c for c in raw_name if c.isalnum() or c in " ._-").strip()[:120] or "Shared photos"

    async def _stream_and_cleanup(path: str):
        # `finally` runs on normal completion AND on aclose() when the client
        # disconnects mid-stream, so the temp zip is never orphaned in /tmp.
        try:
            with open(path, "rb") as fh:
                for chunk in iter(lambda: fh.read(1 << 16), b""):
                    yield chunk
        finally:
            Path(path).unlink(missing_ok=True)

    return _public_response(
        StreamingResponse(
            _stream_and_cleanup(handle.name),
            media_type="application/zip",
            headers={
                "Content-Disposition": f'attachment; filename="{safe_name}.zip"',
                "X-Azimuth-Skipped-Count": str(len(result["skipped"])),
            },
        )
    )
