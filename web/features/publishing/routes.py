"""Local, frozen client-gallery HTTP surface.

These routes deliberately never import or call the website publisher.  A
gallery is a private share token scoped to a collection snapshot.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
import time
import zipfile
from collections.abc import Awaitable, Callable
from http.client import IncompleteRead
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

from data import connection
from core.source_files import source_file_is_safe
from features.publishing import galleries
from features.share import auth
from features.sync import readthrough
import settings


router = APIRouter()
DbPathProvider = Callable[[], str]
ThumbnailResponse = Callable[..., Awaitable[Response]]
_db_path: DbPathProvider | None = None
_thumbnail_response: ThumbnailResponse | None = None
_unlock_failures: dict[str, tuple[int, float]] = {}
_templates = Jinja2Templates(directory=str(Path(__file__).resolve().parents[2] / "templates"))
MAX_UNLOCK_PASSWORD_LENGTH = 256
UNLOCK_FAILURE_LIMIT = 5
UNLOCK_FAILURE_WINDOW_SECONDS = 15 * 60


class GalleryBody(BaseModel):
    title: str | None = Field(default=None, max_length=160)
    layout: str = "grid"
    theme: str = "light"
    cover_image_id: int | None = Field(default=None, gt=0)
    password: str | None = Field(default=None, max_length=MAX_UNLOCK_PASSWORD_LENGTH)
    clear_password: bool = False
    allow_download_all: bool = True
    download_size: str = "lg"


def configure(*, db_path: DbPathProvider, thumbnail_response: ThumbnailResponse) -> None:
    global _db_path, _thumbnail_response
    _db_path = db_path
    _thumbnail_response = thumbnail_response


def _configured_db_path() -> str:
    if _db_path is None:
        raise RuntimeError("Gallery routes are not configured")
    return _db_path()


def _configured_thumbnail_response() -> ThumbnailResponse:
    if _thumbnail_response is None:
        raise RuntimeError("Gallery routes are not configured")
    return _thumbnail_response


async def _collection_snapshot(collection_id: int) -> dict | None:
    """Read the current collection exactly once, then freeze this order."""
    conn = await connection.open_async(_configured_db_path())
    try:
        cursor = await conn.execute("SELECT id, name, query FROM collections WHERE id = ?", (collection_id,))
        collection = await cursor.fetchone()
        if collection is None:
            return None
        # Smart collections are live queries. Their existing conversion action makes
        # the frozen-snapshot behavior explicit before a client receives a link.
        if collection["query"] is not None:
            return {"id": int(collection["id"]), "name": collection["name"], "smart": True, "image_ids": []}
        cursor = await conn.execute(
            """SELECT ci.image_id FROM collection_images ci JOIN images i ON i.id = ci.image_id
               WHERE ci.collection_id = ? AND i.status IN ('kept', 'maybe') AND i.missing_at IS NULL
               ORDER BY ci.position, ci.image_id""",
            (collection_id,),
        )
        return {
            "id": int(collection["id"]), "name": collection["name"], "smart": False,
            "image_ids": [int(row["image_id"]) for row in await cursor.fetchall()],
        }
    finally:
        await connection.close_async(conn, db_path=_configured_db_path())


def _options(body: GalleryBody) -> dict:
    return {
        "layout": body.layout, "theme": body.theme, "cover_image_id": body.cover_image_id,
        "allow_download_all": body.allow_download_all, "download_size": body.download_size,
    }


def _owner_payload(request: Request, gallery: dict) -> dict:
    result = {key: value for key, value in gallery.items() if key != "password_hash"}
    result["url"] = f"{str(request.base_url).rstrip('/')}/s/gallery/{gallery['token']}"
    return result


@router.get("/api/user-collections/{collection_id}/galleries")
async def api_list_galleries(collection_id: int, request: Request):
    return {"galleries": [_owner_payload(request, item) for item in await galleries.list_galleries(_configured_db_path(), collection_id)]}


@router.post("/api/user-collections/{collection_id}/galleries")
async def api_create_gallery(collection_id: int, body: GalleryBody, request: Request):
    snapshot = await _collection_snapshot(collection_id)
    if snapshot is None:
        return JSONResponse({"error": "Collection not found"}, status_code=404)
    if snapshot["smart"]:
        return JSONResponse({"error": "Materialize this smart collection before creating a frozen client gallery."}, status_code=409)
    gallery = await galleries.create_gallery(
        _configured_db_path(), collection_id=collection_id,
        title=(body.title or snapshot["name"] or "Client gallery"), image_ids=snapshot["image_ids"],
        options=_options(body), password_hash=auth.hash_password(body.password) if body.password else None,
    )
    return {"ok": True, "gallery": _owner_payload(request, gallery)}


@router.patch("/api/user-collections/{collection_id}/galleries/{gallery_id}")
async def api_update_gallery(collection_id: int, gallery_id: int, body: GalleryBody, request: Request):
    current = await galleries.get_gallery(_configured_db_path(), gallery_id)
    if current is None or current["collection_id"] != collection_id:
        return JSONResponse({"error": "Gallery not found"}, status_code=404)
    password_hash: object = ...
    if body.clear_password:
        password_hash = None
    elif body.password is not None:
        password_hash = auth.hash_password(body.password) if body.password else None
    updated = await galleries.update_gallery(
        _configured_db_path(), gallery_id, title=body.title,
        options=_options(body), password_hash=password_hash,
    )
    return {"ok": True, "gallery": _owner_payload(request, updated)}


@router.delete("/api/user-collections/{collection_id}/galleries/{gallery_id}")
async def api_delete_gallery(collection_id: int, gallery_id: int):
    current = await galleries.get_gallery(_configured_db_path(), gallery_id)
    if current is None or current["collection_id"] != collection_id:
        return JSONResponse({"error": "Gallery not found"}, status_code=404)
    await galleries.delete_gallery(_configured_db_path(), gallery_id)
    return {"ok": True}


def _public_page_payload(gallery: dict) -> dict:
    token = gallery["token"]
    download_size = gallery["download_size"]
    images = []
    for index, image in enumerate(gallery["images"], start=1):
        image_id = int(image["id"])
        filename = str(image.get("filename") or f"photo-{image_id}.jpg")
        images.append({
            "id": image_id, "filename": filename,
            "display_label": f"Photo {index} of {len(gallery['images'])}",
            "aspect_ratio": max(0.45, min(2.4, float(image.get("width") or 1) / max(1, float(image.get("height") or 1)))),
            "thumb": f"/s/gallery/{token}/thumb/sm/{image_id}",
            "preview": f"/s/gallery/{token}/thumb/lg/{image_id}",
            "og_thumb": f"/s/gallery/{token}/thumb/md/{image_id}",
            "download": f"/s/gallery/{token}/download/{download_size}/{image_id}",
            "download_name": _attachment_name(image_id, filename, suffix=".jpg"),
        })
    return {
        "token": token, "name": gallery["title"], "photo_count": len(images),
        "download_size_label": f"{download_size} gallery copy", "images": images,
        "download_all_url": f"/s/gallery/{token}/download-all" if gallery["allow_download_all"] else "",
    }


def _brand_payload() -> dict:
    config = settings.get_settings()
    site_url = str(config.get("publish_site_base_url") or "").strip().rstrip("/")
    site_label = site_url.removeprefix("https://").removeprefix("http://").removeprefix("www.")
    name = str(config.get("share_brand_name") or "").strip() or site_label or "Your photographer"
    return {"name": name, "site_url": site_url, "site_label": site_label}


def _public_response(response: Response) -> Response:
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "private, no-store"
    return response


def _attachment_name(image_id: int, filename: str, *, suffix: str = "") -> str:
    basename = os.path.basename(str(filename or ""))
    stem, extension = os.path.splitext(basename)
    safe_stem = "".join(char if char.isalnum() or char in "._- " else "_" for char in stem)
    safe_stem = safe_stem.strip(" .")[:180] or f"photo-{image_id}"
    extension_text = (suffix or extension).lower().lstrip(".")
    safe_extension = "".join(char for char in extension_text if char.isalnum())[:12]
    safe_extension = f".{safe_extension}" if safe_extension else ""
    return f"{safe_stem}{safe_extension}"


async def _safe_original(image: dict) -> bool:
    return await asyncio.to_thread(
        source_file_is_safe,
        str(image.get("filepath") or ""),
        str(image.get("source_path") or ""),
    )


def _stream_hub_response(response):
    try:
        while chunk := response.read(1024 * 1024):
            yield chunk
    finally:
        response.close()


def _hub_media_type(response, default: str) -> str:
    headers = getattr(response, "headers", None)
    if headers is None:
        return default
    get_content_type = getattr(headers, "get_content_type", None)
    if callable(get_content_type):
        return get_content_type()
    return headers.get("Content-Type", default)


def _gallery_response(request: Request, gallery: dict) -> Response:
    page = _public_page_payload(gallery)
    return _templates.TemplateResponse(request, "share_gallery.html", {
        "not_found": False, "locked": False, "token": page["token"],
        "collection_name": page["name"], "photo_count": page["photo_count"],
        "date_range": "", "brand": _brand_payload(), "gallery_json": page,
        "og_image": f"{str(request.base_url).rstrip('/')}{page['images'][0]['og_thumb']}" if page["images"] else "",
        "favorites_enabled": False,
    })


def _locked_response(request: Request, token: str, gallery: dict, *, unlock_error: bool = False, throttle_seconds: int | None = None, status_code: int = 200) -> Response:
    response = _templates.TemplateResponse(request, "share_gallery.html", {
        "not_found": False, "locked": True, "token": token,
        "collection_name": gallery["title"], "photo_count": gallery["image_count"],
        "date_range": "", "brand": _brand_payload(), "unlock_error": unlock_error,
        "throttle_seconds": throttle_seconds,
        "throttle_minutes": ((throttle_seconds or 0) + 59) // 60 if throttle_seconds else None,
        "unlock_action": f"/s/gallery/{token}/unlock",
    }, status_code=status_code)
    if throttle_seconds is not None:
        response.headers["Retry-After"] = str(throttle_seconds)
    return response


def _is_unlocked(request: Request, gallery: dict) -> bool:
    return auth.is_unlocked(request, gallery)


@router.get("/s/gallery/{token}", response_class=HTMLResponse)
async def public_gallery(token: str, request: Request):
    gallery = await galleries.resolve_token(_configured_db_path(), token)
    if gallery is None:
        return _public_response(HTMLResponse("<h1>Gallery unavailable</h1>", status_code=404))
    if not _is_unlocked(request, gallery):
        return _public_response(_locked_response(request, token, gallery, unlock_error=request.query_params.get("e") == "1"))
    if not request.cookies.get(auth.VIEW_COOKIE_NAME):
        await galleries.record_view(_configured_db_path(), token)
    response = _gallery_response(request, gallery)
    auth.set_view_cookie(
        response,
        token,
        request=request,
        path=auth.gallery_cookie_path(token),
    )
    return _public_response(response)


def _unlock_retry_after(token: str) -> int | None:
    count, started = _unlock_failures.get(token, (0, 0.0))
    if not count:
        return None
    remaining = int(started + UNLOCK_FAILURE_WINDOW_SECONDS - time.time())
    if remaining <= 0:
        _unlock_failures.pop(token, None)
        return None
    return remaining if count >= UNLOCK_FAILURE_LIMIT else None


@router.post("/s/gallery/{token}/unlock")
async def public_gallery_unlock(token: str, request: Request):
    gallery = await galleries.resolve_token(_configured_db_path(), token)
    if gallery is None:
        return _public_response(HTMLResponse("<h1>Gallery unavailable</h1>", status_code=404))
    if _unlock_retry_after(token) is not None:
        return _public_response(_locked_response(request, token, gallery, throttle_seconds=_unlock_retry_after(token), status_code=429))
    password = await auth.read_form_password(request)
    if password is None or len(password) > MAX_UNLOCK_PASSWORD_LENGTH or not auth.verify_password(password, gallery.get("password_hash")):
        count, started = _unlock_failures.get(token, (0, time.time()))
        _unlock_failures[token] = (count + 1, started)
        await asyncio.sleep(0.2)
        return _public_response(RedirectResponse(f"/s/gallery/{token}?e=1", status_code=303))
    _unlock_failures.pop(token, None)
    response = RedirectResponse(f"/s/gallery/{token}", status_code=303)
    response.set_cookie(auth.COOKIE_NAME, auth.cookie_value(token, gallery["password_hash"]), max_age=auth.UNLOCK_MAX_AGE_SECONDS, httponly=True, samesite="lax", secure=auth.request_is_secure(request), path=f"/s/gallery/{token}")
    return _public_response(response)


async def _public_gallery(token: str, request: Request) -> dict | None:
    gallery = await galleries.resolve_token(_configured_db_path(), token)
    if gallery is None or not _is_unlocked(request, gallery):
        return None
    return gallery


@router.get("/s/gallery/{token}/thumb/{size}/{image_id}")
async def public_gallery_thumbnail(token: str, size: str, image_id: int, request: Request):
    gallery = await _public_gallery(token, request)
    if gallery is None or size not in {"sm", "md", "lg"} or not await galleries.gallery_allows_image(_configured_db_path(), token, image_id):
        return _public_response(JSONResponse({"error": "Not found"}, status_code=404))
    return _public_response(await _configured_thumbnail_response()(request, size, image_id))


@router.get("/s/gallery/{token}/download/{size}/{image_id}")
async def public_gallery_download(token: str, size: str, image_id: int, request: Request):
    gallery = await _public_gallery(token, request)
    if gallery is None or size not in galleries.VALID_DOWNLOAD_SIZES or size != gallery["download_size"]:
        return _public_response(JSONResponse({"error": "Not found"}, status_code=404))
    image = await galleries.image_file(_configured_db_path(), token, image_id)
    if image is None:
        return _public_response(JSONResponse({"error": "Not found"}, status_code=404))
    if size != "original":
        response = await _configured_thumbnail_response()(request, size, image_id)
        response.headers["Content-Disposition"] = (
            f'attachment; filename="{_attachment_name(image_id, image["filename"], suffix=".jpg")}"'
        )
        return _public_response(response)
    if int(image.get("hub_remote") or 0) == 1:
        remote = await asyncio.to_thread(
            readthrough.open_hub_original,
            int(image.get("hub_image_id") or 0),
        )
        if remote is None:
            return _public_response(JSONResponse(
                {"error": "Original unavailable", "reason": "hub_original_unavailable"},
                status_code=404,
            ))
        filename = _attachment_name(image_id, image["filename"])
        return _public_response(StreamingResponse(
            _stream_hub_response(remote),
            media_type=_hub_media_type(remote, "application/octet-stream"),
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        ))
    if not await _safe_original(image):
        return _public_response(JSONResponse({"error": "Not found"}, status_code=404))
    return _public_response(
        FileResponse(image["filepath"], filename=_attachment_name(image_id, image["filename"]))
    )


def _zip_gallery(gallery: dict, destination: str) -> dict:
    """Build a local zip from frozen members. Preview sizes remain JPEG-only."""
    import thumbnails

    size = gallery["download_size"]
    skipped = []
    included = 0
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for image in gallery["images"]:
            filename = Path(image["filename"])
            image_id = int(image["id"])
            remote = int(image.get("hub_remote") or 0) == 1
            try:
                if remote:
                    response = (
                        readthrough.open_hub_original(int(image.get("hub_image_id") or 0))
                        if size == "original"
                        else readthrough.open_hub_preview(int(image.get("hub_image_id") or 0), size)
                    )
                    if response is None:
                        raise FileNotFoundError("Hub media unavailable")
                    arcname = (
                        _attachment_name(image_id, filename.name)
                        if size == "original"
                        else _attachment_name(image_id, filename.name, suffix=".jpg")
                    )
                    try:
                        with tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024) as staged:
                            shutil.copyfileobj(response, staged, length=1024 * 1024)
                            staged.seek(0)
                            with archive.open(arcname, "w") as target:
                                shutil.copyfileobj(staged, target, length=1024 * 1024)
                    finally:
                        response.close()
                elif size == "original":
                    if not source_file_is_safe(
                        str(image.get("filepath") or ""),
                        str(image.get("source_path") or ""),
                    ):
                        raise FileNotFoundError("Local original unavailable")
                    archive.write(
                        image["filepath"],
                        arcname=_attachment_name(image_id, filename.name),
                    )
                else:
                    data = asyncio.run(thumbnails.get_thumbnail(image["filepath"], size, image_id))
                    if not data:
                        raise FileNotFoundError("Preview unavailable")
                    archive.writestr(_attachment_name(image_id, filename.name, suffix=".jpg"), data)
                included += 1
            except (IncompleteRead, OSError, ValueError):
                skipped.append({
                    "image_id": image_id,
                    "filename": filename.name,
                    "reason": "hub unavailable" if remote else "source unavailable",
                })
        if skipped:
            archive.writestr(
                "azimuth-download-manifest.json",
                json.dumps({
                    "requested_count": len(gallery["images"]),
                    "included_count": included,
                    "skipped_count": len(skipped),
                    "skipped": skipped,
                }, indent=2),
            )
    return {"path": destination, "included": included, "skipped": skipped}


@router.get("/s/gallery/{token}/download-all")
async def public_gallery_download_all(token: str, request: Request):
    gallery = await _public_gallery(token, request)
    if gallery is None or not gallery["allow_download_all"]:
        return _public_response(JSONResponse({"error": "Not found"}, status_code=404))
    handle = tempfile.NamedTemporaryFile(prefix="photoarchive-gallery-", suffix=".zip", delete=False)
    handle.close()
    try:
        result = await asyncio.to_thread(_zip_gallery, gallery, handle.name)
    except Exception:
        Path(handle.name).unlink(missing_ok=True)
        return _public_response(JSONResponse({"error": "Could not prepare gallery download"}, status_code=422))
    return _public_response(
        FileResponse(
            handle.name,
            filename=f"{gallery['title']}.zip",
            headers={"X-Azimuth-Skipped-Count": str(len(result["skipped"]))},
            background=BackgroundTask(lambda: Path(handle.name).unlink(missing_ok=True)),
        )
    )
