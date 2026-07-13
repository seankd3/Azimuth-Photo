"""Local, frozen client-gallery HTTP surface.

These routes deliberately never import or call the website publisher.  A
gallery is a private share token scoped to a collection snapshot.
"""

from __future__ import annotations

import asyncio
import html
import json
import os
import tempfile
import time
import zipfile
from collections.abc import Awaitable, Callable
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

from data import connection
from core.source_files import source_file_is_safe
from features.publishing import galleries
from features.share import auth


router = APIRouter()
DbPathProvider = Callable[[], str]
ThumbnailResponse = Callable[..., Awaitable[Response]]
_db_path: DbPathProvider | None = None
_thumbnail_response: ThumbnailResponse | None = None
_unlock_failures: dict[str, tuple[int, float]] = {}
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
        _configured_db_path(), gallery_id, options=_options(body), password_hash=password_hash
    )
    return {"ok": True, "gallery": _owner_payload(request, updated)}


def _public_page_payload(gallery: dict) -> dict:
    token = gallery["token"]
    download_size = gallery["download_size"]
    images = []
    for index, image in enumerate(gallery["images"], start=1):
        image_id = int(image["id"])
        filename = str(image.get("filename") or f"photo-{image_id}.jpg")
        images.append({
            "id": image_id, "filename": filename,
            "label": f"Photo {index} of {len(gallery['images'])}",
            "aspect": max(0.45, min(2.4, float(image.get("width") or 1) / max(1, float(image.get("height") or 1)))),
            "thumb": f"/s/gallery/{token}/thumb/sm/{image_id}",
            "preview": f"/s/gallery/{token}/thumb/lg/{image_id}",
            "download": f"/s/gallery/{token}/download/{download_size}/{image_id}",
        })
    return {
        "token": token, "title": gallery["title"], "layout": gallery["layout"], "theme": gallery["theme"],
        "download_size": download_size, "allow_download_all": gallery["allow_download_all"], "images": images,
    }


def _safe_json(value: dict) -> str:
    return json.dumps(value, separators=(",", ":")).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def _public_response(response: Response) -> Response:
    response.headers["Referrer-Policy"] = "no-referrer"
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


def _gallery_html(data: dict) -> str:
    title = html.escape(str(data["title"]))
    state = _safe_json(data)
    all_button = '<button id="download-all">Download all</button>' if data["allow_download_all"] else ''
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{title}</title>
<style>
:root{{--ink:#202126;--muted:#6a6870;--paper:#f7f5f0;--card:#fff;--accent:#996e40;--line:#ded9d0}}body[data-theme="dark"]{{--ink:#f4f2ef;--muted:#c1bcb5;--paper:#161616;--card:#212121;--line:#3a3937}}body[data-theme="warm"]{{--ink:#372b22;--muted:#796557;--paper:#f4eadc;--card:#fffaf1;--line:#decdb8;--accent:#b36136}}*{{box-sizing:border-box}}body{{margin:0;background:var(--paper);color:var(--ink);font:16px/1.5 ui-sans-serif,system-ui,sans-serif}}main{{max-width:1400px;margin:auto;padding:clamp(22px,5vw,72px)}}header{{display:flex;justify-content:space-between;gap:24px;align-items:end;margin-bottom:30px;border-bottom:1px solid var(--line);padding-bottom:20px}}h1{{font:600 clamp(28px,5vw,52px)/1.06 ui-serif,Georgia,serif;margin:0}}header p{{margin:7px 0 0;color:var(--muted)}}button{{border:0;border-radius:999px;padding:11px 17px;background:var(--ink);color:var(--paper);font:inherit;cursor:pointer}}#grid{{gap:8px}}#grid[data-layout="grid"]{{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr))}}#grid[data-layout="masonry"]{{columns:260px 4;display:block}}#grid[data-layout="slideshow"]{{display:flex;overflow:auto;scroll-snap-type:x mandatory;padding-bottom:12px}}.photo{{position:relative;display:block;overflow:hidden;background:var(--card);border-radius:5px;break-inside:avoid;margin:0 0 8px;cursor:zoom-in}}[data-layout="slideshow"] .photo{{flex:0 0 min(78vw,940px);scroll-snap-align:center}}.photo img{{display:block;width:100%;aspect-ratio:var(--aspect);object-fit:cover;transition:transform .2s}}.photo:hover img{{transform:scale(1.015)}}.photo a{{position:absolute;right:10px;bottom:10px;color:white;background:#0009;text-decoration:none;border-radius:999px;padding:7px 10px;font-size:13px}}#lightbox[hidden]{{display:none}}#lightbox{{position:fixed;inset:0;background:#000e;z-index:2;display:grid;place-items:center;padding:24px}}#lightbox img{{max-width:95vw;max-height:84vh}}#lightbox button{{position:absolute;right:24px;top:20px}}.lock{{min-height:72vh;display:grid;place-items:center;text-align:center}}.lock form{{display:grid;gap:10px;width:min(340px,100%)}}.lock input{{padding:12px;border:1px solid var(--line);border-radius:8px;font:inherit}}
</style></head><body data-theme="{html.escape(data['theme'])}"><main><header><div><h1>{title}</h1><p>{len(data['images'])} photo{'s' if len(data['images']) != 1 else ''} · {html.escape(data['download_size'])} downloads</p></div>{all_button}</header><section id="grid" data-layout="{html.escape(data['layout'])}"></section></main><div id="lightbox" hidden><button id="close" aria-label="Close">×</button><img alt=""></div><script id="gallery-data" type="application/json">{state}</script><script>const d=JSON.parse(document.querySelector('#gallery-data').textContent),g=document.querySelector('#grid'),l=document.querySelector('#lightbox'),i=l.querySelector('img');g.innerHTML=d.images.map((p,n)=>`<article class="photo" style="--aspect:${{p.aspect}}" data-index="${{n}}"><img loading="lazy" src="${{p.thumb}}" alt="${{p.label}}"><a href="${{p.download}}" download>Download</a></article>`).join('');g.onclick=e=>{{if(e.target.closest('a'))return;const p=e.target.closest('.photo');if(!p)return;const x=d.images[Number(p.dataset.index)];i.src=x.preview;i.alt=x.label;l.hidden=false}};document.querySelector('#close').onclick=()=>{{l.hidden=true;i.removeAttribute('src')}};document.querySelector('#download-all')?.addEventListener('click',()=>location.href=`/s/gallery/${{d.token}}/download-all`)</script></body></html>'''


def _locked_html(token: str, gallery: dict, error: bool = False) -> str:
    title = html.escape(gallery["title"])
    message = "That password did not unlock this gallery." if error else ""
    return f'''<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1"><title>{title}</title><style>body{{font:16px system-ui;margin:0;background:#f7f5f0;color:#202126}}main{{min-height:100vh;display:grid;place-items:center;padding:24px}}form{{display:grid;gap:12px;width:min(340px,100%)}}input,button{{font:inherit;padding:12px;border-radius:8px;border:1px solid #d8d2c9}}button{{background:#202126;color:#fff}}</style><main><form method="post" action="/s/gallery/{html.escape(token)}/unlock"><h1>{title}</h1><p>This client gallery is password protected.</p><input name="password" type="password" autocomplete="current-password" autofocus required><button>Unlock</button><small>{message}</small></form></main>'''


def _is_unlocked(request: Request, gallery: dict) -> bool:
    return auth.is_unlocked(request, gallery)


@router.get("/s/gallery/{token}", response_class=HTMLResponse)
async def public_gallery(token: str, request: Request):
    gallery = await galleries.resolve_token(_configured_db_path(), token)
    if gallery is None:
        return _public_response(HTMLResponse("<h1>Gallery unavailable</h1>", status_code=404))
    if not _is_unlocked(request, gallery):
        return _public_response(HTMLResponse(_locked_html(token, gallery, request.query_params.get("e") == "1")))
    if not request.cookies.get(auth.VIEW_COOKIE_NAME):
        await galleries.record_view(_configured_db_path(), token)
    response = HTMLResponse(_gallery_html(_public_page_payload(gallery)))
    auth.set_view_cookie(response, token, request=request)
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
        return _public_response(HTMLResponse(_locked_html(token, gallery), status_code=429))
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
    if not await _safe_original(image):
        return _public_response(JSONResponse({"error": "Not found"}, status_code=404))
    return _public_response(
        FileResponse(image["filepath"], filename=_attachment_name(image_id, image["filename"]))
    )


def _zip_gallery(gallery: dict, destination: str) -> str:
    """Build a local zip from frozen members. Preview sizes remain JPEG-only."""
    import thumbnails

    size = gallery["download_size"]
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for image in gallery["images"]:
            filename = Path(image["filename"])
            if size == "original":
                if not source_file_is_safe(
                    str(image.get("filepath") or ""),
                    str(image.get("source_path") or ""),
                ):
                    continue
                archive.write(
                    image["filepath"],
                    arcname=_attachment_name(int(image["id"]), filename.name),
                )
            else:
                data = asyncio.run(thumbnails.get_thumbnail(image["filepath"], size, int(image["id"])))
                archive.writestr(f"{filename.stem}.jpg", data)
    return destination


@router.get("/s/gallery/{token}/download-all")
async def public_gallery_download_all(token: str, request: Request):
    gallery = await _public_gallery(token, request)
    if gallery is None or not gallery["allow_download_all"]:
        return _public_response(JSONResponse({"error": "Not found"}, status_code=404))
    handle = tempfile.NamedTemporaryFile(prefix="photoarchive-gallery-", suffix=".zip", delete=False)
    handle.close()
    try:
        await asyncio.to_thread(_zip_gallery, gallery, handle.name)
    except Exception:
        Path(handle.name).unlink(missing_ok=True)
        return _public_response(JSONResponse({"error": "Could not prepare gallery download"}, status_code=422))
    return _public_response(
        FileResponse(
            handle.name,
            filename=f"{gallery['title']}.zip",
            background=BackgroundTask(lambda: Path(handle.name).unlink(missing_ok=True)),
        )
    )
