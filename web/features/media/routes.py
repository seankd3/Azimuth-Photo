import asyncio
import logging
import os
import sqlite3
from collections.abc import Awaitable, Callable

from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.responses import FileResponse, JSONResponse, Response

from core import requests as request_helpers
from core.source_files import inspect_source_file
from data import connection as data_connection
from data.repositories import images as image_repository
from features.sync import preview_mirror, satellite
from features.sync.prefetch import (
    ThumbPrefetcher,
    _foreground_urllib_request as _urllib_request,
    _urllib_request as _background_urllib_request,
)
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
_REMOTE_MEDIA_FOREGROUND_TIMEOUT_SECONDS = 2.0
_remote_prefetch_tasks: dict[tuple[int, str], asyncio.Task] = {}


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
    if int(image["hub_remote"] or 0) == 1:
        return "remote"
    filepath = str(image["filepath"] or "")
    source_path = str(image["source_path"] or "")
    file_state, _source_stat = await asyncio.to_thread(
        inspect_source_file,
        filepath,
        source_path,
    )
    if file_state == "missing":
        source_online = bool(
            image["source_online"]
            and source_path
            and await asyncio.to_thread(os.path.isdir, source_path)
        )
        return "missing" if source_online else "offline"
    if file_state == "unavailable":
        return "unavailable"
    if file_state == "unsafe":
        return "unsafe"
    if file_state in {"not_regular", "empty"}:
        return "corrupt"
    return "available"


async def _source_error_response(image, state: str) -> JSONResponse | None:
    image_id = int(image["id"])
    if state in {"missing", "corrupt"}:
        changed = False
        if _mark_image_missing is not None:
            try:
                changed = await _mark_image_missing(image_id)
            except Exception as exc:
                if not data_connection.is_sqlite_locked_error(exc):
                    raise
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
    if state == "unsafe":
        return JSONResponse(
            {
                "error": "Photo unavailable",
                "reason": "source_invalid",
                "detail": "The catalog entry does not resolve to a regular file inside its source.",
            },
            status_code=404,
        )
    return None


def _remote_media_endpoint(remote_id: int, tier: str) -> str:
    if tier == thumbnails.FULL_TIER:
        return f"/api/full/{remote_id}"
    return f"/api/thumb/{tier}/{remote_id}"


def _remote_media_pending_response(tier: str) -> Response:
    if tier != thumbnails.FULL_TIER:
        return Response(status_code=204, headers={"Cache-Control": "no-store"})
    return JSONResponse(
        {
            "error": "Hub media is still loading",
            "reason": "hub_media_pending",
        },
        status_code=503,
        headers={"Cache-Control": "no-store", "Retry-After": "2"},
    )


def _cache_remote_media(image, tier: str, data: bytes) -> str:
    image_id = int(image["id"])
    if tier in preview_mirror.MIRROR_SIZES:
        signature = preview_mirror.preview_version_for_image(image)
        preview_mirror.put(image_id, tier, signature, data, hot=True)
        return signature
    remote_id = int(image["hub_image_id"])
    signature = ThumbPrefetcher._signature(remote_id, data)
    thumbnails._write_thumbnail_to_disk(tier, image_id, signature, data, hot=False)
    if tier != thumbnails.FULL_TIER:
        thumbnails._memory_put(tier, image_id, signature, data)
    return signature


async def _prefetch_remote_media(image: dict, tier: str) -> None:
    hub = satellite.hub_url().rstrip("/")
    if not hub:
        return
    remote_id = int(image["hub_image_id"])
    try:
        status_code, _headers, data = await _background_urllib_request(
            "GET",
            hub + _remote_media_endpoint(remote_id, tier),
        )
        if 200 <= status_code < 300 and data:
            await asyncio.to_thread(_cache_remote_media, image, tier, data)
    except Exception as exc:
        log.debug(
            "worker=hub_media_prefetch image_id=%s tier=%s error=%s",
            image.get("id"),
            tier,
            exc,
        )


def _schedule_remote_media_prefetch(image, tier: str) -> None:
    image_data = dict(image)
    key = (int(image_data["id"]), tier)
    existing = _remote_prefetch_tasks.get(key)
    if existing is not None and not existing.done():
        return
    task = asyncio.create_task(_prefetch_remote_media(image_data, tier))
    _remote_prefetch_tasks[key] = task
    task.add_done_callback(lambda _done, task_key=key: _remote_prefetch_tasks.pop(task_key, None))


async def _remote_media_response(image, tier: str) -> Response:
    hub = satellite.hub_url().rstrip("/")
    if not hub:
        return _remote_media_pending_response(tier)
    remote_id = int(image["hub_image_id"])
    endpoint = _remote_media_endpoint(remote_id, tier)
    try:
        status_code, response_headers, data = await asyncio.wait_for(
            _urllib_request("GET", hub + endpoint, headers=satellite.hub_request_headers()),
            timeout=_REMOTE_MEDIA_FOREGROUND_TIMEOUT_SECONDS,
        )
    except Exception:
        _schedule_remote_media_prefetch(image, tier)
        return _remote_media_pending_response(tier)
    if not 200 <= status_code < 300:
        if status_code >= 500:
            _schedule_remote_media_prefetch(image, tier)
            return _remote_media_pending_response(tier)
        return JSONResponse(
            {"error": "Hub media unavailable", "reason": "hub_media_unavailable"},
            status_code=status_code,
        )
    if not data:
        _schedule_remote_media_prefetch(image, tier)
        return _remote_media_pending_response(tier)
    signature = _cache_remote_media(image, tier, data)
    media_type = next(
        (value for key, value in response_headers.items() if key.lower() == "content-type"),
        "image/jpeg",
    )
    return Response(content=data, media_type=media_type, headers=_cache_headers(signature))


@router.get("/api/thumb/{size}/{image_id}")
async def serve_thumbnail(request: Request, size: str, image_id: int, cached: bool = False):
    return await thumbnail_response(request, size, image_id, cached=cached)


async def thumbnail_response(request: Request, size: str, image_id: int, cached: bool = False):
    if size not in thumbnails.SIZES:
        return JSONResponse({"error": "Invalid size"}, status_code=400)

    preview_mirror.note_request()
    image = None
    source_state = "available"
    cache_only = cached
    mirror_version: str | None = None
    if not cached:
        image = await image_repository.get_media_image_by_id(_configured_db_path(), image_id)
        if not image:
            return JSONResponse({"error": "Image not found"}, status_code=404)
        if image["status"] == "trashed":
            # Local originals were intentionally moved, but a mirrored Trash row
            # can still be read through from its hub when no cached preview exists.
            source_state = await _source_state(image)
            cache_only = source_state != "remote"
        else:
            source_state = await _source_state(image)
            source_error = await _source_error_response(image, source_state)
            if source_error is not None:
                return source_error
        if source_state == "remote" and size in preview_mirror.MIRROR_SIZES:
            mirror_version = preview_mirror.preview_version_for_image(image)

    # Cached probes remain DB-free. Active images validate the original first;
    # trashed rows are cache-only because their original path was intentionally moved.
    # Remote sm/md: require preview_version match (stale = lazy miss).
    request_etag = request.headers.get("if-none-match")
    required_signature = mirror_version
    entry = thumbnails._memory_get_entry_fast(size, image_id)
    if entry is not None and required_signature is not None and entry[0] != required_signature:
        entry = None
    if entry is None:
        if required_signature is not None:
            path_hit = preview_mirror.get_local(image_id, size, required_signature, touch=True)
            if path_hit is not None:
                signature, path = path_hit
                headers = _cache_headers(signature)
                if request_etag == headers["ETag"]:
                    return Response(status_code=304, headers=headers)
                return FileResponse(path, media_type="image/jpeg", headers=headers)
        else:
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
            required_signature,
        )
        if entry is not None:
            signature, data = entry
            thumbnails._memory_put(size, image_id, signature, data)
        elif required_signature is not None:
            # Version mismatch left a stale unversioned index hit — drop it.
            stale = thumbnails.fast_disk_path_entry(size, image_id)
            if stale is not None and stale[0] != required_signature:
                preview_mirror.delete_entry(image_id, size)
    if entry is not None:
        signature, data = entry
        headers = _cache_headers(signature)
        if request_etag == headers["ETag"]:
            return Response(status_code=304, headers=headers)
        return Response(content=data, media_type="image/jpeg", headers=headers)
    if cache_only:
        return Response(status_code=204, headers={"Cache-Control": "no-store"})

    if source_state == "remote":
        return await _remote_media_response(image, size)

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

    if source_state == "remote":
        return await _remote_media_response(image, thumbnails.FULL_TIER)

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


def _normalize_warm_requests(tier_requests) -> tuple[dict[str, list[int]], set[int]]:
    requested: dict[str, list[int]] = {}
    all_ids: set[int] = set()
    if not isinstance(tier_requests, dict):
        return requested, all_ids
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
            if len(ids) >= 96:
                break
        if ids:
            requested[tier] = ids
    return requested, all_ids


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
    requested, all_ids = _normalize_warm_requests(tier_requests)

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
