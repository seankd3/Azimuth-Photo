import asyncio
import logging
import os
import sqlite3
import stat
from collections.abc import Awaitable, Callable

from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.responses import FileResponse, JSONResponse, Response

from core import requests as request_helpers
from data.repositories import images as image_repository
import thumbnails


router = APIRouter()
CachedImageIds = Callable[[list[int], str], Awaitable[set[int]]]
ScheduleMemoryWarm = Callable[..., None]
DbPathProvider = Callable[[], str]
MarkImageMissing = Callable[[int], Awaitable[bool]]
_cached_image_ids: CachedImageIds | None = None
_schedule_cached_thumbnail_memory_warm: ScheduleMemoryWarm | None = None
_db_path: DbPathProvider | None = None
_mark_image_missing: MarkImageMissing | None = None
_browser_image_extensions = thumbnails.BROWSER_ORIGINAL_EXTENSIONS
log = logging.getLogger(__name__)


def configure(
    *,
    cached_image_ids: CachedImageIds,
    schedule_cached_thumbnail_memory_warm: ScheduleMemoryWarm,
    db_path: DbPathProvider,
    mark_image_missing: MarkImageMissing,
) -> None:
    global _cached_image_ids, _schedule_cached_thumbnail_memory_warm, _db_path, _mark_image_missing
    _cached_image_ids = cached_image_ids
    _schedule_cached_thumbnail_memory_warm = schedule_cached_thumbnail_memory_warm
    _db_path = db_path
    _mark_image_missing = mark_image_missing


def _configured_db_path() -> str:
    if _db_path is None:
        raise RuntimeError("Media routes are not configured")
    return _db_path()


def _cache_headers(signature: str) -> dict:
    return {
        "Cache-Control": (
            f"public, max-age={thumbnails.BROWSER_CACHE_MAX_AGE}, "
            f"stale-while-revalidate={thumbnails.BROWSER_CACHE_STALE_WHILE_REVALIDATE}"
        ),
        "ETag": f'"{signature}"',
    }


async def _source_state(image) -> str:
    if image["missing_at"] is not None:
        return "missing"
    filepath = str(image["filepath"] or "")
    try:
        source_stat = await asyncio.to_thread(os.stat, filepath)
    except FileNotFoundError:
        source_path = str(image["source_path"] or "")
        source_online = bool(
            image["source_online"]
            and source_path
            and await asyncio.to_thread(os.path.isdir, source_path)
        )
        return "missing" if source_online else "offline"
    except OSError:
        return "unavailable"
    if not stat.S_ISREG(source_stat.st_mode) or int(source_stat.st_size or 0) <= 0:
        return "corrupt"
    return "available"


async def _source_error_response(image, state: str) -> JSONResponse | None:
    image_id = int(image["id"])
    if state in {"missing", "corrupt"}:
        changed = False
        if _mark_image_missing is not None:
            changed = await _mark_image_missing(image_id)
        if changed:
            log.warning(
                "worker=media_request image_id=%s marked unavailable reason=%s path=%r",
                image_id,
                state,
                image["filepath"],
            )
        detail = (
            "The source file is empty or unreadable. Replace it, then rescan the source."
            if state == "corrupt"
            else "The source file is no longer on disk. Restore it, then rescan the source."
        )
        return JSONResponse(
            {"error": "Photo unavailable", "reason": f"source_{state}", "detail": detail},
            status_code=410,
        )
    if state == "unavailable":
        return JSONResponse(
            {
                "error": "Source file could not be read",
                "reason": "source_unavailable",
                "detail": "Check the source drive and file permissions, then try again.",
            },
            status_code=503,
        )
    return None


@router.get("/api/thumb/{size}/{image_id}")
async def serve_thumbnail(request: Request, size: str, image_id: int, cached: bool = False):
    return await thumbnail_response(request, size, image_id, cached=cached)


async def thumbnail_response(request: Request, size: str, image_id: int, cached: bool = False):
    if size not in thumbnails.SIZES:
        return JSONResponse({"error": "Invalid size"}, status_code=400)

    image = None
    source_state = "available"
    if not cached:
        image = await image_repository.get_media_image_by_id(_configured_db_path(), image_id)
        if not image:
            return JSONResponse({"error": "Image not found"}, status_code=404)
        source_state = await _source_state(image)
        source_error = await _source_error_response(image, source_state)
        if source_error is not None:
            return source_error

    # Cached probes remain DB-free; normal requests validate the original first.
    request_etag = request.headers.get("if-none-match")
    entry = thumbnails._memory_get_entry_fast(size, image_id)
    if entry is None:
        path_entry = thumbnails.fast_disk_path_entry(size, image_id)
        if path_entry is not None:
            signature, path = path_entry
            headers = _cache_headers(signature)
            if request_etag == headers["ETag"]:
                return Response(status_code=304, headers=headers)
            return FileResponse(path, media_type="image/jpeg", headers=headers)
        entry = await asyncio.get_event_loop().run_in_executor(
            None,
            thumbnails.fast_disk_read_entry,
            size,
            image_id,
            None,
        )
        if entry is not None:
            signature, data = entry
            thumbnails._memory_put(size, image_id, signature, data)
    if entry is not None:
        signature, data = entry
        headers = _cache_headers(signature)
        if request_etag == headers["ETag"]:
            return Response(status_code=304, headers=headers)
        return Response(content=data, media_type="image/jpeg", headers=headers)
    if cached:
        return Response(status_code=204, headers={"Cache-Control": "no-store"})

    if source_state == "offline":
        return JSONResponse(
            {
                "error": "Source drive is offline",
                "reason": "source_offline",
                "detail": "Reconnect the source drive or use a cached preview.",
            },
            status_code=404,
        )

    data = await thumbnails.get_thumbnail(image["filepath"], size, image_id)
    if not data:
        changed = await _mark_image_missing(image_id) if _mark_image_missing is not None else False
        if changed:
            log.warning(
                "worker=media_request image_id=%s marked unavailable reason=decode_failed path=%r",
                image_id,
                image["filepath"],
            )
        return JSONResponse(
            {
                "error": "Photo preview could not be created",
                "reason": "source_corrupt",
                "detail": "The file appears unreadable. Replace it, then rescan the source.",
            },
            status_code=410,
        )

    # response_headers stats the original file; keep slow/offline disks off
    # the event loop so one sleeping drive can't stall every request.
    headers = await asyncio.to_thread(thumbnails.response_headers, image["filepath"], size, image_id)
    return Response(content=data, media_type="image/jpeg", headers=headers)


@router.get("/api/full/{image_id}")
async def serve_full_image(request: Request, image_id: int, background_tasks: BackgroundTasks, cached: bool = False):
    request_etag = request.headers.get("if-none-match")
    if cached:
        entry = thumbnails.fast_disk_path_entry(thumbnails.FULL_TIER, image_id)
        if entry is None:
            return Response(status_code=204, headers={"Cache-Control": "no-store"})
        signature, path = entry
        headers = _cache_headers(signature)
        if request_etag == headers["ETag"]:
            return Response(status_code=304, headers=headers)
        return FileResponse(path, headers=headers)

    image = await image_repository.get_media_image_by_id(_configured_db_path(), image_id)
    if not image:
        return JSONResponse({"error": "Image not found"}, status_code=404)
    source_state = await _source_state(image)
    source_error = await _source_error_response(image, source_state)
    if source_error is not None:
        return source_error

    full_entry = thumbnails.fast_disk_path_entry(thumbnails.FULL_TIER, image_id)
    if full_entry is not None:
        signature, path = full_entry
        headers = _cache_headers(signature)
        if request_etag == headers["ETag"]:
            return Response(status_code=304, headers=headers)
        return FileResponse(path, headers=headers)

    if source_state == "offline":
        return JSONResponse(
            {
                "error": "Source drive is offline",
                "reason": "source_offline",
                "detail": "Reconnect the source drive or use a cached preview.",
            },
            status_code=404,
        )

    ext = os.path.splitext(image["filepath"])[1].lower()
    if ext not in _browser_image_extensions:
        headers = await asyncio.to_thread(thumbnails.response_headers, image["filepath"], "lg", image_id)
        if request_etag == headers["ETag"]:
            return Response(status_code=304, headers=headers)

        data = await thumbnails.get_thumbnail(image["filepath"], "lg", image_id)
        if not data:
            changed = await _mark_image_missing(image_id) if _mark_image_missing is not None else False
            if changed:
                log.warning(
                    "worker=media_request image_id=%s marked unavailable reason=decode_failed path=%r",
                    image_id,
                    image["filepath"],
                )
            return JSONResponse(
                {
                    "error": "Photo preview could not be created",
                    "reason": "source_corrupt",
                    "detail": "The file appears unreadable. Replace it, then rescan the source.",
                },
                status_code=410,
            )
        return Response(content=data, media_type="image/jpeg", headers=headers)

    headers = await asyncio.to_thread(
        thumbnails.response_headers, image["filepath"], thumbnails.FULL_TIER, image_id
    )
    if request_etag == headers["ETag"]:
        return Response(status_code=304, headers=headers)

    path = thumbnails.get_cached_full_image_path(image["filepath"], image_id)
    if path is None:
        path = image["filepath"]
        background_tasks.add_task(thumbnails.schedule_full_image_cache, image["filepath"], image_id)

    if not path or not await asyncio.to_thread(os.path.exists, path):
        changed = await _mark_image_missing(image_id) if _mark_image_missing is not None else False
        if changed:
            log.warning(
                "worker=media_request image_id=%s marked unavailable reason=missing path=%r",
                image_id,
                image["filepath"],
            )
        return JSONResponse(
            {
                "error": "Photo unavailable",
                "reason": "source_missing",
                "detail": "The source file is no longer on disk. Restore it, then rescan the source.",
            },
            status_code=410,
        )

    return FileResponse(path, headers=headers)


def image_media_status_payload(image_id: int) -> dict:
    tiers = {}
    for size in thumbnails.THUMB_TIERS:
        cached = thumbnails.has_cached_fast(size, image_id)
        tiers[size] = {
            "cached": cached,
            "url": f"/api/thumb/{size}/{image_id}",
            "cached_url": f"/api/thumb/{size}/{image_id}?cached=1",
        }

    full_cached = thumbnails.fast_disk_path_entry(thumbnails.FULL_TIER, image_id) is not None
    tiers[thumbnails.FULL_TIER] = {
        "cached": full_cached,
        "url": f"/api/full/{image_id}",
        "cached_url": f"/api/full/{image_id}?cached=1",
    }
    best_cached = next(
        (tier for tier in (thumbnails.FULL_TIER, "lg", "md", "sm") if tiers.get(tier, {}).get("cached")),
        None,
    )
    return {"id": image_id, "tiers": tiers, "best_cached": best_cached}


@router.get("/api/image/{image_id}/media-status")
async def image_media_status(image_id: int):
    return await asyncio.to_thread(image_media_status_payload, image_id)


@router.post("/api/images/media-status")
async def images_media_status(request: Request):
    body, error = await request_helpers.json_object(request)
    if error is not None:
        return error
    raw_ids = body.get("ids", [])
    if not isinstance(raw_ids, list):
        raw_ids = [raw_ids]
    ids = []
    seen = set()
    for value in raw_ids:
        try:
            image_id = int(value)
        except (TypeError, ValueError):
            continue
        if image_id <= 0 or image_id in seen:
            continue
        seen.add(image_id)
        ids.append(image_id)
        if len(ids) >= 96:
            break
    statuses = await asyncio.to_thread(
        lambda: [image_media_status_payload(image_id) for image_id in ids]
    )
    return {"statuses": statuses}


@router.post("/api/images/warm")
async def warm_images(request: Request):
    """Mark current/nearby images as hot and schedule SSD cache warming."""
    if _cached_image_ids is None or _schedule_cached_thumbnail_memory_warm is None:
        raise RuntimeError("Media routes are not configured")
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    tier_requests = body.get("tiers") or {}
    requested: dict[str, list[int]] = {}
    all_ids: set[int] = set()

    for tier, values in tier_requests.items():
        if tier not in thumbnails.ALL_TIERS:
            continue
        ids = []
        seen_for_tier = set()
        values_iter = values if isinstance(values, (list, tuple, set)) else [values]
        for value in values_iter or []:
            try:
                image_id = int(value)
            except (TypeError, ValueError):
                continue
            if image_id <= 0 or image_id in seen_for_tier:
                continue
            seen_for_tier.add(image_id)
            ids.append(image_id)
            all_ids.add(image_id)
        if ids:
            requested[tier] = ids[:96]

    if not requested or not all_ids:
        return {"scheduled": {}, "images": 0}

    try:
        rows_by_id = await image_repository.get_active_images_by_ids(_configured_db_path(), list(all_ids))
    except (sqlite3.OperationalError, OSError) as exc:
        log.warning("worker=media_warm image_ids=%s lookup skipped: %s", sorted(all_ids), exc)
        return {"scheduled": {tier: 0 for tier in requested}, "images": len(all_ids)}
    except Exception:
        log.exception("worker=media_warm image_ids=%s lookup failed", sorted(all_ids))
        return {"scheduled": {tier: 0 for tier in requested}, "images": len(all_ids)}

    scheduled = {}
    for tier in list(requested.keys()):
        if tier not in thumbnails.THUMB_TIERS:
            continue
        hot_rows = [rows_by_id[image_id] for image_id in requested[tier] if image_id in rows_by_id]
        if hot_rows:
            _schedule_cached_thumbnail_memory_warm(
                hot_rows,
                tier,
                limit=len(hot_rows),
            )
        cached_ids = await _cached_image_ids(requested[tier], tier)
        if not cached_ids:
            continue
        requested[tier] = [image_id for image_id in requested[tier] if image_id not in cached_ids]
        if not requested[tier]:
            scheduled[tier] = 0

    for tier, ids in requested.items():
        rows = [rows_by_id[image_id] for image_id in ids if image_id in rows_by_id]
        if not rows:
            scheduled[tier] = 0
            continue
        if tier in thumbnails.THUMB_TIERS:
            try:
                scheduled[tier] = await thumbnails.prefetch_images(
                    rows,
                    tier,
                    limit=len(rows),
                    hot=True,
                )
            except (sqlite3.OperationalError, OSError) as exc:
                log.warning(
                    "worker=media_warm tier=%s image_ids=%s skipped: %s",
                    tier,
                    [int(row["id"]) for row in rows],
                    exc,
                )
                scheduled[tier] = 0
            except Exception:
                log.exception(
                    "worker=media_warm tier=%s image_ids=%s failed",
                    tier,
                    [int(row["id"]) for row in rows],
                )
                scheduled[tier] = 0
        elif tier == thumbnails.FULL_TIER:
            count = 0
            for row in rows[:12]:
                ext = os.path.splitext(row["filepath"])[1].lower()
                if ext not in _browser_image_extensions:
                    continue
                try:
                    await thumbnails.schedule_full_image_cache(row["filepath"], row["id"], hot=True)
                    count += 1
                except (sqlite3.OperationalError, OSError) as exc:
                    log.warning(
                        "worker=media_warm tier=full image_id=%s skipped: %s",
                        row["id"],
                        exc,
                    )
                except Exception:
                    log.exception(
                        "worker=media_warm tier=full image_id=%s failed",
                        row["id"],
                    )
            scheduled[tier] = count

    return {"scheduled": scheduled, "images": len(all_ids)}
