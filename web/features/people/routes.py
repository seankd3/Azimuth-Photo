import asyncio
import io
import os
from collections.abc import Awaitable, Callable

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

import face_worker
import settings
import thumbnails
from core.requests import json_object, positive_int


router = APIRouter()
AsyncDictBuilder = Callable[..., Awaitable[dict]]
AsyncOptionalDictBuilder = Callable[..., Awaitable[dict | None]]

_get_people_review: AsyncDictBuilder | None = None
_get_face_thumbnail_context: AsyncOptionalDictBuilder | None = None
_label_person: AsyncDictBuilder | None = None
_merge_people: AsyncDictBuilder | None = None
_reject_merge_suggestion: AsyncDictBuilder | None = None
_assign_face: AsyncDictBuilder | None = None
_ignore_face: AsyncDictBuilder | None = None
_ignore_person: AsyncDictBuilder | None = None


def configure(
    *,
    get_people_review: AsyncDictBuilder,
    get_face_thumbnail_context: AsyncOptionalDictBuilder,
    label_person: AsyncDictBuilder,
    merge_people: AsyncDictBuilder,
    reject_merge_suggestion: AsyncDictBuilder,
    assign_face: AsyncDictBuilder,
    ignore_face: AsyncDictBuilder,
    ignore_person: AsyncDictBuilder,
) -> None:
    global _get_people_review, _get_face_thumbnail_context, _label_person
    global _merge_people, _reject_merge_suggestion, _assign_face
    global _ignore_face, _ignore_person
    _get_people_review = get_people_review
    _get_face_thumbnail_context = get_face_thumbnail_context
    _label_person = label_person
    _merge_people = merge_people
    _reject_merge_suggestion = reject_merge_suggestion
    _assign_face = assign_face
    _ignore_face = ignore_face
    _ignore_person = ignore_person


def _configured():
    values = (
        _get_people_review,
        _get_face_thumbnail_context,
        _label_person,
        _merge_people,
        _reject_merge_suggestion,
        _assign_face,
        _ignore_face,
        _ignore_person,
    )
    if any(value is None for value in values):
        raise RuntimeError("People routes are not configured")


async def people_status_payload(review: dict | None = None) -> dict:
    config = settings.get_settings()
    worker = face_worker.get_worker_status()
    if review is None:
        try:
            _configured()
            review = await _get_people_review(limit=12)
        except Exception:
            review = {}
    counts = dict(review.get("counts", {}) if isinstance(review, dict) else {})
    counts.setdefault("pending_cached_images", int(worker.get("pending_cached_images") or 0))
    return {
        "active": bool(config.get("people_scan_enabled", True)),
        "automatic": True,
        "auto_install": bool(config.get("people_auto_install", True)),
        "model_id": config.get("face_model_id") or "buffalo_l",
        "model_dir": config.get("face_model_dir") or "",
        "detection_size": int(config.get("face_detection_size") or 640),
        "similarity_threshold": float(config.get("face_similarity_threshold") or 0.52),
        "merge_suggestion_threshold": float(config.get("face_merge_suggestion_threshold") or 0.62),
        "identity_policy": "face_embeddings_only",
        "contextual_search_policy": "qwen_after_people_filter",
        "source_files_preserved": True,
        "source_media_read": "app_owned_cached_previews_only",
        "model_license": face_worker.FACE_MODEL_LICENSE_TEXT,
        "worker": worker,
        "counts": counts,
    }


@router.get("/api/people/status")
async def api_people_status():
    return await people_status_payload()


@router.get("/api/people")
async def api_people(limit: int = 24):
    _configured()
    review = await _get_people_review(limit=limit)
    return {**review, "status": await people_status_payload(review)}


def _render_face_thumbnail(face: dict, output_size: int) -> tuple[str, bytes] | None:
    from PIL import Image as PILImage, ImageOps

    cache_path = str(face.get("cache_path") or "")
    if not cache_path or not os.path.isfile(cache_path):
        return None

    bbox_x = float(face.get("bbox_x") or 0.0)
    bbox_y = float(face.get("bbox_y") or 0.0)
    bbox_w = float(face.get("bbox_w") or 0.0)
    bbox_h = float(face.get("bbox_h") or 0.0)
    if bbox_w <= 0.0 or bbox_h <= 0.0:
        return None

    with PILImage.open(cache_path) as opened:
        image = ImageOps.exif_transpose(opened).convert("RGB")

    image_width, image_height = image.size
    max_side = float(min(image_width, image_height))
    if max_side <= 0.0:
        return None

    center_x = max(0.0, min(float(image_width), bbox_x + bbox_w / 2.0))
    center_y = max(0.0, min(float(image_height), bbox_y + bbox_h / 2.0))
    crop_side = min(max(max(bbox_w, bbox_h) * 2.35, 24.0), max_side)
    left = min(max(center_x - crop_side / 2.0, 0.0), float(image_width) - crop_side)
    top = min(max(center_y - crop_side / 2.0, 0.0), float(image_height) - crop_side)
    crop = image.crop((
        int(round(left)),
        int(round(top)),
        int(round(left + crop_side)),
        int(round(top + crop_side)),
    ))

    resampling = getattr(getattr(PILImage, "Resampling", PILImage), "LANCZOS")
    crop = crop.resize((output_size, output_size), resampling)
    buffer = io.BytesIO()
    crop.save(buffer, format="JPEG", quality=88, optimize=True)

    stat = os.stat(cache_path)
    signature = (
        f"face-{int(face.get('face_id') or 0)}-{int(face.get('image_id') or 0)}-"
        f"{stat.st_mtime_ns}-{stat.st_size}-{output_size}-"
        f"{bbox_x:.2f}-{bbox_y:.2f}-{bbox_w:.2f}-{bbox_h:.2f}"
    )
    return signature, buffer.getvalue()


@router.get("/api/people/faces/{face_id}/thumb")
async def api_people_face_thumb(request: Request, face_id: int, size: int = 160):
    _configured()
    output_size = max(64, min(int(size or 160), 512))
    face = await _get_face_thumbnail_context(face_id)
    if face is None:
        return JSONResponse({"error": "Face not found"}, status_code=404)

    rendered = await asyncio.get_event_loop().run_in_executor(
        None,
        _render_face_thumbnail,
        face,
        output_size,
    )
    if rendered is None:
        return Response(status_code=404, headers={"Cache-Control": "no-store"})

    signature, data = rendered
    headers = {
        "Cache-Control": (
            f"public, max-age={thumbnails.BROWSER_CACHE_MAX_AGE}, "
            f"stale-while-revalidate={thumbnails.BROWSER_CACHE_STALE_WHILE_REVALIDATE}"
        ),
        "ETag": f'"{signature}"',
    }
    if request.headers.get("if-none-match") == headers["ETag"]:
        return Response(status_code=304, headers=headers)
    return Response(content=data, media_type="image/jpeg", headers=headers)


@router.post("/api/people/scan/pause")
async def api_people_scan_pause():
    face_worker.pause_face_worker()
    return {"ok": True, "status": await people_status_payload()}


@router.post("/api/people/scan/resume")
async def api_people_scan_resume():
    face_worker.resume_face_worker()
    return {"ok": True, "status": await people_status_payload()}


@router.post("/api/people/{person_id}/label")
async def api_label_person(person_id: int, request: Request):
    _configured()
    body, error = await json_object(request)
    if error:
        return error
    result = await _label_person(person_id, body.get("name") or body.get("label") or "")
    if not result.get("ok"):
        return JSONResponse({"error": result.get("error") or "Could not label person"}, status_code=400)
    return result


@router.post("/api/people/merge")
async def api_merge_people(request: Request):
    _configured()
    body, error = await json_object(request)
    if error:
        return error
    source_id = positive_int(body.get("source_person_id"))
    target_id = positive_int(body.get("target_person_id"))
    if source_id is None or target_id is None:
        return JSONResponse({"error": "source_person_id and target_person_id are required"}, status_code=400)
    result = await _merge_people(source_id, target_id)
    if not result.get("ok"):
        return JSONResponse({"error": result.get("error") or "Could not merge people"}, status_code=400)
    return result


@router.post("/api/people/merge-suggestions/{suggestion_id}/reject")
async def api_reject_people_merge(suggestion_id: int):
    _configured()
    return await _reject_merge_suggestion(suggestion_id)


@router.post("/api/people/faces/{face_id}/assign")
async def api_assign_face(face_id: int, request: Request):
    _configured()
    body, error = await json_object(request)
    if error:
        return error
    person_id = positive_int(body.get("person_id"))
    result = await _assign_face(face_id, person_id=person_id, name=body.get("name") or "")
    if not result.get("ok"):
        return JSONResponse({"error": result.get("error") or "Could not assign face"}, status_code=400)
    return result


@router.post("/api/people/faces/{face_id}/ignore")
async def api_ignore_face(face_id: int):
    _configured()
    return await _ignore_face(face_id)


@router.post("/api/people/{person_id}/ignore")
async def api_ignore_person(person_id: int):
    _configured()
    return await _ignore_person(person_id)
