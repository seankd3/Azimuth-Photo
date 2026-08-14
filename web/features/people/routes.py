import db
import asyncio
import io
import os
import time
from collections.abc import Awaitable, Callable

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

# The face worker died with the derivation fleet (2026-08-14 gutting
# order). People shows what has been derived; these constants keep the
# status payload's shape for the surfaces that read it.
FACE_MODEL_LICENSE_TEXT = (
    "InsightFace model packs are non-commercial research models by default. "
    "Use face_model_dir with a licensed compatible model for other use."
)
_WORKER_ABSENT = {
    "state": "unavailable",
    "ready": False,
    "running": False,
    "manual_pause": False,
    "message": "Faces were derived on the hub; this device shows the results.",
    "last_error": "",
    "pending_cached_images": 0,
}
import settings
import thumbnails
from core import capabilities
from core.background import track_background_task
from core.requests import json_object, positive_int
from data.repositories import people as people_repository
from archive import role


router = APIRouter()
AsyncDictBuilder = Callable[..., Awaitable[dict]]




_people_status_counts_cache: dict[str, object] = {"counts": None, "expires": 0.0}
_people_status_counts_cache_ttl_seconds = 10.0
_people_status_counts_refreshing = False
_PEOPLE_COUNTS_INITIAL_WAIT_SECONDS = 0.05


def invalidate_people_status_cache() -> None:
    global _people_status_counts_refreshing
    _people_status_counts_cache.update({"counts": None, "expires": 0.0})
    _people_status_counts_refreshing = False


def _minimal_people_counts(worker: dict) -> dict:
    return {
        "people": 0,
        "named_people": 0,
        "unknown_people": 0,
        "detected_faces": 0,
        "pending_cached_images": int(worker.get("pending_cached_images") or 0),
        "other_faces": 0,
        "merge_suggestions": 0,
        "scan": {},
    }


def _schedule_people_counts_refresh() -> asyncio.Task | None:
    global _people_status_counts_refreshing
    if _people_status_counts_refreshing:
        return None
    _people_status_counts_refreshing = True

    async def refresh() -> None:
        global _people_status_counts_refreshing
        try:
            counts = await db.get_people_status_counts()
            _people_status_counts_cache["counts"] = dict(counts or {})
            _people_status_counts_cache["expires"] = (
                time.monotonic() + _people_status_counts_cache_ttl_seconds
            )
        finally:
            _people_status_counts_refreshing = False

    return track_background_task(refresh())


async def _fast_people_counts(worker: dict) -> tuple[dict, bool]:
    cached = _people_status_counts_cache.get("counts")
    fresh = float(_people_status_counts_cache.get("expires") or 0.0) > time.monotonic()
    if fresh and isinstance(cached, dict):
        return dict(cached), False

    refresh_task = _schedule_people_counts_refresh()
    if cached is None and refresh_task is not None:
        await asyncio.wait({refresh_task}, timeout=_PEOPLE_COUNTS_INITIAL_WAIT_SECONDS)
        cached = _people_status_counts_cache.get("counts")
        fresh = float(_people_status_counts_cache.get("expires") or 0.0) > time.monotonic()
    if isinstance(cached, dict):
        return dict(cached), not fresh
    return _minimal_people_counts(worker), True


async def people_status_payload(review: dict | None = None) -> dict:
    started = time.perf_counter()
    config = settings.get_settings()
    worker = dict(_WORKER_ABSENT)
    capability = capabilities.capability_status("people")
    # A hub-backed satellite never runs this worker: the hub owns face work and
    # the satellite receives the results. Saying so is the honest answer. The
    # old fall-through left the counts cache permanently unfilled, so the panel
    # showed "Refreshing…" forever for something that was never going to run.
    deferred = role.defers_bulk_compute()
    if not capability["available"] or deferred:
        worker = {
            **worker,
            "state": "unavailable",
            "ready": False,
            "running": False,
            "message": (
                "Faces are found on the hub; this device shows the results."
                if deferred and capability["available"]
                else capability["message"]
            ),
            "last_error": "",
        }
    counts_stale = False
    if review is None:
        counts, counts_stale = await _fast_people_counts(worker)
    else:
        counts = dict(review.get("counts", {}) if isinstance(review, dict) else {})
    counts.setdefault("pending_cached_images", int(worker.get("pending_cached_images") or 0))
    return {
        "capability": capability,
        "active": False,
        "automatic": bool(config["people_scan_enabled"]),
        "auto_install": False,
        "auto_install_legacy": bool(config.get("people_auto_install", True)),
        "runtime_install": False,
        "model_id": config.get("face_model_id") or "buffalo_l",
        "model_dir": config.get("face_model_dir") or "",
        "detection_size": int(config.get("face_detection_size") or 640),
        "similarity_threshold": float(config.get("face_similarity_threshold") or 0.52),
        "merge_suggestion_threshold": float(config.get("face_merge_suggestion_threshold") or 0.62),
        "identity_policy": "face_embeddings_only",
        "contextual_search_policy": "qwen_after_people_filter",
        "source_files_preserved": True,
        "source_media_read": "app_owned_cached_previews_only",
        "model_license": FACE_MODEL_LICENSE_TEXT,
        "worker": worker,
        "counts": counts,
        "counts_stale": counts_stale,
        "status_stale": counts_stale,
        "latency_ms": round((time.perf_counter() - started) * 1000, 1),
    }


@router.get("/api/people/status")
async def api_people_status():
    return await people_status_payload()


@router.get("/api/people")
async def api_people(limit: int = 24):
    review = await db.get_people_review(limit=limit)
    return {**review, "status": await people_status_payload(review)}


def _render_face_thumbnail(face: dict, output_size: int) -> tuple[str, bytes] | None:
    from PIL import Image as PILImage, ImageOps
    from core import pil_limits  # noqa: F401  # disables the decompression-bomb limit process-wide

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
    output_size = max(64, min(int(size or 160), 512))
    face = await people_repository.get_face_thumbnail_context(db.DB_PATH, face_id)
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


@router.post("/api/people/{person_id}/label")
async def api_label_person(person_id: int, request: Request):
    body, error = await json_object(request)
    if error:
        return error
    result = await db.label_person(person_id, body.get("name") or body.get("label") or "")
    if not result.get("ok"):
        return JSONResponse({"error": result.get("error") or "Could not label person"}, status_code=400)
    return result


@router.post("/api/people/merge")
async def api_merge_people(request: Request):
    body, error = await json_object(request)
    if error:
        return error
    source_id = positive_int(body.get("source_person_id"))
    target_id = positive_int(body.get("target_person_id"))
    if source_id is None or target_id is None:
        return JSONResponse({"error": "source_person_id and target_person_id are required"}, status_code=400)
    result = await db.merge_people(source_id, target_id)
    if not result.get("ok"):
        return JSONResponse({"error": result.get("error") or "Could not merge people"}, status_code=400)
    return result


@router.post("/api/people/merge-suggestions/{suggestion_id}/reject")
async def api_reject_people_merge(suggestion_id: int):
    result = await people_repository.reject_merge_suggestion(db.DB_PATH, suggestion_id)
    if not isinstance(result, dict) or not result.get("ok"):
        error = result.get("error") if isinstance(result, dict) else ""
        return JSONResponse({"error": error or "Could not reject suggestion"}, status_code=400)
    return result






@router.post("/api/people/faces/{face_id}/assign")
async def api_assign_face(face_id: int, request: Request):
    body, error = await json_object(request)
    if error:
        return error
    person_id = positive_int(body.get("person_id"))
    result = await db.assign_face(face_id, person_id=person_id, name=body.get("name") or "")
    if not result.get("ok"):
        return JSONResponse({"error": result.get("error") or "Could not assign face"}, status_code=400)
    return result


@router.post("/api/people/faces/{face_id}/ignore")
async def api_ignore_face(face_id: int):
    result = await db.ignore_face(face_id)
    if not isinstance(result, dict) or not result.get("ok"):
        error = result.get("error") if isinstance(result, dict) else ""
        return JSONResponse({"error": error or "Could not ignore face"}, status_code=400)
    return result


@router.post("/api/people/{person_id}/ignore")
async def api_ignore_person(person_id: int):
    result = await db.ignore_person(person_id)
    if not isinstance(result, dict) or not result.get("ok"):
        error = result.get("error") if isinstance(result, dict) else ""
        return JSONResponse({"error": error or "Could not ignore person"}, status_code=400)
    return result
