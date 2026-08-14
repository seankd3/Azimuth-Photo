from core.catalog_path import catalog_path
import asyncio
import logging
import os
import time

from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.responses import FileResponse, JSONResponse, Response

from core import hdd_governor
from core.source_files import inspect_source_file
from data import connection as data_connection
from data.repositories import images as image_repository
from features.sync import preview_mirror, satellite
from features.sync.prefetch import ThumbPrefetcher
from photo import location
import thumbnails
from thumbnails import cache_entries, config


import db
from archive import transport
router = APIRouter()
_browser_image_extensions = config.BROWSER_ORIGINAL_EXTENSIONS
log = logging.getLogger(__name__)
# Thumb miss never awaits the hub on the request path (was 2.0s). Kept as a
# named constant so tests/docs can assert the non-blocking contract.
# Bound how long an interactive thumb request may wait on a cold decode.
# Beyond this the decode keeps running in the shared inflight map; the client
# gets a fast 204 and retries — never a 10s "library isn't responding" toast.
_ON_DEMAND_FOREGROUND_TIMEOUT_SECONDS = float(
    os.environ.get("AZIMUTH_ON_DEMAND_FOREGROUND_TIMEOUT", "1.5")
)
_SLOW_THUMB_LOG_MS = float(os.environ.get("AZIMUTH_SLOW_THUMB_MS", "1000"))
_remote_prefetch_tasks: dict[tuple[int, str], asyncio.Task] = {}
_local_thumb_fill_tasks: dict[tuple[int, str], asyncio.Task] = {}


def _cache_headers(signature: str) -> dict:
    return {
        "Cache-Control": (
            f"public, max-age={thumbnails.BROWSER_CACHE_MAX_AGE}, "
            f"stale-while-revalidate={thumbnails.BROWSER_CACHE_STALE_WHILE_REVALIDATE}"
        ),
        "ETag": f'"{signature}"',
    }


def _row_source_hint(image) -> str | None:
    """Catalog-only source state — never touches the spindle."""

    if image["missing_at"] is not None:
        return "missing"
    if int(image["hub_remote"] or 0) == 1:
        return "remote"
    status = image["status"] if "status" in image else None
    if str(status or "") == "trashed":
        # Local trash moved the original; serve cache only unless hub-remote.
        return "cache_only"
    return None


async def _source_state(image) -> str:
    hinted = _row_source_hint(image)
    if hinted == "missing":
        return "missing"
    if hinted == "remote":
        return "remote"
    if hinted == "cache_only":
        return "cache_only"
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


def _pending_thumb_response() -> Response:
    return Response(
        status_code=204,
        headers={"Cache-Control": "no-store", "Retry-After": "1"},
    )


# Local-first: what is already on this device is always worth painting.
_SMALLER_TIERS = {"full": ("lg", "md", "sm"), "lg": ("md", "sm"), "md": ("sm",)}


def _local_stand_in_response(size: str, image_id: int) -> Response | None:
    """Serve the best smaller tier this device already holds, or None.

    A tile with no bytes yet is not the same as a tile with nothing to show.
    Waiting for the exact tier — decoded here or fetched from the hub — leaves
    the app blank while a perfectly good preview sits in the local cache, so
    the smaller one paints now and the requested tier upgrades the tile when
    the background fill lands. Never cached: it is deliberately provisional.
    """
    for smaller in _SMALLER_TIERS.get(size, ()):
        entry = thumbnails._memory_get_entry_fast(smaller, image_id)
        if entry is not None:
            return Response(
                content=entry[1],
                media_type="image/jpeg",
                headers={"Cache-Control": "no-store", "X-Azimuth-Tier": smaller},
            )
        path_entry = cache_entries.fast_disk_path_entry(smaller, image_id)
        if path_entry is not None:
            return FileResponse(
                path_entry[1],
                media_type="image/jpeg",
                headers={"Cache-Control": "no-store", "X-Azimuth-Tier": smaller},
            )
    return None


def _schedule_local_thumb_fill(filepath: str, size: str, image_id: int) -> None:
    """Keep a cold decode running after the request path returns 204."""

    key = (int(image_id), size)
    existing = _local_thumb_fill_tasks.get(key)
    if existing is not None and not existing.done():
        return

    async def _fill() -> None:
        try:
            await thumbnails.get_thumbnail(filepath, size, image_id)
        except Exception as exc:
            log.debug(
                "worker=local_thumb_fill image_id=%s size=%s error=%s",
                image_id,
                size,
                exc,
            )

    task = asyncio.create_task(_fill())
    _local_thumb_fill_tasks[key] = task
    task.add_done_callback(lambda _done, task_key=key: _local_thumb_fill_tasks.pop(task_key, None))


async def _await_thumbnail_bounded(filepath: str, size: str, image_id: int) -> bytes | None:
    """Wait briefly for an on-demand decode; leave it running on timeout."""

    timeout = max(0.05, _ON_DEMAND_FOREGROUND_TIMEOUT_SECONDS)
    gen_task = asyncio.create_task(thumbnails.get_thumbnail(filepath, size, image_id))
    done, _pending = await asyncio.wait({gen_task}, timeout=timeout)
    if gen_task in done:
        return gen_task.result()
    # Decode continues via get_thumbnail's inflight map + this task.
    _schedule_local_thumb_fill(filepath, size, image_id)
    return None


async def _source_error_response(image, state: str) -> JSONResponse | None:
    image_id = int(image["id"])
    if state in {"missing", "corrupt"}:
        changed = False
        try:
            changed = await db.mark_image_missing(image_id)
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
    if tier == config.FULL_TIER:
        return f"/api/full/{remote_id}"
    return f"/api/thumb/{tier}/{remote_id}"


def _remote_media_pending_response(tier: str) -> Response:
    if tier != config.FULL_TIER:
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
    else:
        signature = ThumbPrefetcher._signature(int(image["hub_image_id"]), data)
    preview_mirror.store(image_id, tier, signature, data, hot=True)
    return signature


async def _prefetch_remote_media(image: dict, tier: str) -> None:
    hub = satellite.hub_url().rstrip("/")
    if not hub:
        return
    remote_id = int(image["hub_image_id"])
    try:
        status_code, _headers, data = await transport.request_async(
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
    """Never await the hub on the request path — paint pending, fill behind.

    Local cache hits are served before this runs. On miss, return the same
    pending/204 the client already understands and enqueue a hub fetch; SWR
    refresh picks the tile up when it lands. Hub mode never reaches here for
    local files.
    """

    hub = satellite.hub_url().rstrip("/")
    if not hub:
        return _remote_media_pending_response(tier)
    _schedule_remote_media_prefetch(image, tier)
    # Paint whatever this device already holds while the hub copy is on its way.
    stand_in = _local_stand_in_response(tier, int(image["id"]))
    if stand_in is not None:
        return stand_in
    return _remote_media_pending_response(tier)


@router.get("/api/thumb/{size}/{image_id}")
async def serve_thumbnail(request: Request, size: str, image_id: int, cached: bool = False):
    return await thumbnail_response(request, size, image_id, cached=cached)


async def thumbnail_response(request: Request, size: str, image_id: int, cached: bool = False):
    """Serve one preview. A tile is the unit of feeling fast, so any request
    that takes longer than a blink is logged with the stage that cost the
    time — the app must never be slow without saying where."""
    started = time.monotonic()
    marks: list[tuple[str, float]] = []

    def mark(stage: str) -> None:
        marks.append((stage, round((time.monotonic() - started) * 1000)))

    try:
        return await _thumbnail_response_inner(
            request, size, image_id, cached=cached, mark=mark
        )
    finally:
        elapsed_ms = (time.monotonic() - started) * 1000
        if elapsed_ms >= _SLOW_THUMB_LOG_MS:
            log.warning(
                "slow thumb size=%s image_id=%s total=%.0fms stages=%s",
                size,
                image_id,
                elapsed_ms,
                marks,
            )


async def _thumbnail_response_inner(
    request: Request,
    size: str,
    image_id: int,
    *,
    cached: bool = False,
    mark=lambda _stage: None,
):
    if size not in config.SIZES:
        return JSONResponse({"error": "Invalid size"}, status_code=400)

    preview_mirror.note_request()
    image = None
    source_state = "available"
    cache_only = cached
    mirror_version: str | None = None
    if not cached:
        image = await image_repository.get_media_image_by_id(catalog_path(), image_id)
        mark("catalog_row")
        if not image:
            return JSONResponse({"error": "Image not found"}, status_code=404)
        # Catalog hints only — never lstat the original before an SSD cache hit.
        hinted = _row_source_hint(image)
        if hinted == "remote":
            source_state = "remote"
            if size in preview_mirror.MIRROR_SIZES:
                mirror_version = preview_mirror.preview_version_for_image(image)
        elif hinted == "cache_only":
            cache_only = True
            source_state = "cache_only"
        elif hinted == "missing":
            source_error = await _source_error_response(image, "missing")
            if source_error is not None:
                return source_error

    # Serve from memory/SSD cache before any spindle inspect. Interactive browse
    # under bulk HDD load must not pay an original lstat on every warm thumb.
    request_etag = request.headers.get("if-none-match")
    required_signature = mirror_version
    entry = thumbnails._memory_get_entry_fast(size, image_id)
    if entry is not None and required_signature is not None and entry[0] != required_signature:
        entry = None
    if entry is None:
        if required_signature is not None:
            path_hit = preview_mirror.get_local(image_id, size, required_signature, touch=True)
            mark("mirror_get_local")
            if path_hit is not None:
                signature, path = path_hit
                headers = _cache_headers(signature)
                if request_etag == headers["ETag"]:
                    return Response(status_code=304, headers=headers)
                return FileResponse(path, media_type="image/jpeg", headers=headers)
        else:
            path_entry = cache_entries.fast_disk_path_entry(size, image_id)
            if path_entry is not None:
                signature, path = path_entry
                headers = _cache_headers(signature)
                if request_etag == headers["ETag"]:
                    return Response(status_code=304, headers=headers)
                return FileResponse(path, media_type="image/jpeg", headers=headers)
        entry = await asyncio.to_thread(
            cache_entries.fast_disk_read_entry,
            size,
            image_id,
            required_signature,
        )
        mark("disk_read")
        if entry is not None:
            signature, data = entry
            thumbnails._memory_put(size, image_id, signature, data)
        elif required_signature is not None:
            # Version mismatch left a stale unversioned index hit — drop it.
            stale = cache_entries.fast_disk_path_entry(size, image_id)
            if stale is not None and stale[0] != required_signature:
                preview_mirror.delete_entry(image_id, size)
    if entry is not None:
        signature, data = entry
        headers = _cache_headers(signature)
        if request_etag == headers["ETag"]:
            return Response(status_code=304, headers=headers)
        return Response(content=data, media_type="image/jpeg", headers=headers)
    if cache_only:
        stand_in = _local_stand_in_response(size, image_id)
        if stand_in is not None:
            return stand_in
        return Response(status_code=204, headers={"Cache-Control": "no-store"})

    # Cache miss: now it's worth inspecting the original (HDD) and decoding.
    # When bulk workers already hold the spindle, skip the interactive lstat —
    # it only adds seek contention. Try a bounded decode (governor-bypass) or
    # return 204 so the client retries once the wave eases.
    if source_state not in {"remote", "cache_only"}:
        if hdd_governor.bulk_hdd_holds() > 0:
            data = await _await_thumbnail_bounded(image["filepath"], size, image_id)
            if data is None:
                return _pending_thumb_response()
            if not data:
                # Fall through to a real inspect so missing/corrupt still quarantine.
                source_state = await _source_state(image)
                source_error = await _source_error_response(image, source_state)
                if source_error is not None:
                    return source_error
            else:
                headers = await asyncio.to_thread(
                    thumbnails.response_headers, image["filepath"], size, image_id
                )
                return Response(content=data, media_type="image/jpeg", headers=headers)
        else:
            source_state = await _source_state(image)
            source_error = await _source_error_response(image, source_state)
            if source_error is not None:
                return source_error

    if source_state == "remote":
        # The archive's own disk may be plugged into this machine. When the
        # photo's bytes are reachable there, derive the tile locally instead
        # of asking an absent hub — size-verified, so a same-named stranger
        # never serves. The tile is stored under the mirror's signature (the
        # same call the hub-fetch path uses) so warm lookups for this
        # hub-remote row keep matching; a generation-signed entry would be
        # dropped as stale on the very next request.
        resolved = await asyncio.to_thread(
            location.local_path, image["filepath"], expected_size=image["file_size"]
        )
        if resolved is None:
            return await _remote_media_response(image, size)
        data = await _await_thumbnail_bounded(resolved, size, image_id)
        if data is None:
            stand_in = _local_stand_in_response(size, image_id)
            return stand_in if stand_in is not None else _pending_thumb_response()
        if not data:
            # Unreadable on the attached volume; the hub's copy stays canonical.
            return await _remote_media_response(image, size)
        signature = await asyncio.to_thread(_cache_remote_media, image, size, data)
        headers = _cache_headers(signature)
        if request_etag == headers["ETag"]:
            return Response(status_code=304, headers=headers)
        return Response(content=data, media_type="image/jpeg", headers=headers)

    if source_state == "offline":
        return JSONResponse(
            {
                "error": "Source drive is offline",
                "reason": "source_offline",
                "detail": "Reconnect the source drive or use a cached preview.",
            },
            status_code=404,
        )

    if source_state == "cache_only":
        stand_in = _local_stand_in_response(size, image_id)
        return stand_in if stand_in is not None else _pending_thumb_response()

    data = await _await_thumbnail_bounded(image["filepath"], size, image_id)
    if data is None:
        # The decode is still running or the spindle is busy; show the smaller
        # local preview now rather than a hole in the grid.
        stand_in = _local_stand_in_response(size, image_id)
        return stand_in if stand_in is not None else _pending_thumb_response()
    if not data:
        changed = await db.mark_image_missing(image_id)
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
        entry = cache_entries.fast_disk_path_entry(config.FULL_TIER, image_id)
        if entry is None:
            return Response(status_code=204, headers={"Cache-Control": "no-store"})
        signature, path = entry
        headers = _cache_headers(signature)
        if request_etag == headers["ETag"]:
            return Response(status_code=304, headers=headers)
        return FileResponse(path, headers=headers)

    image = await image_repository.get_media_image_by_id(catalog_path(), image_id)
    if not image:
        return JSONResponse({"error": "Image not found"}, status_code=404)

    hinted = _row_source_hint(image)
    if hinted == "missing":
        source_error = await _source_error_response(image, "missing")
        if source_error is not None:
            return source_error

    # SSD full-tier hit before any original spindle inspect.
    full_entry = cache_entries.fast_disk_path_entry(config.FULL_TIER, image_id)
    if full_entry is not None:
        signature, path = full_entry
        headers = _cache_headers(signature)
        if request_etag == headers["ETag"]:
            return Response(status_code=304, headers=headers)
        return FileResponse(path, headers=headers)

    if hinted == "remote":
        # Attached-archive read-through: when the archive's disk is plugged
        # in, continue as a local photo — the extension dance and headers
        # below behave exactly as they do for native files.
        resolved = await asyncio.to_thread(
            location.local_path, image["filepath"], expected_size=image["file_size"]
        )
        if resolved is None:
            return await _remote_media_response(image, config.FULL_TIER)
        image = dict(image)
        image["filepath"] = resolved
        image["hub_remote"] = 0

    source_state = await _source_state(image)
    source_error = await _source_error_response(image, source_state)
    if source_error is not None:
        return source_error

    if source_state == "remote":
        return await _remote_media_response(image, config.FULL_TIER)

    if source_state == "offline":
        return JSONResponse(
            {
                "error": "Source drive is offline",
                "reason": "source_offline",
                "detail": "Reconnect the source drive or use a cached preview.",
            },
            status_code=404,
        )

    if source_state == "cache_only":
        return Response(status_code=204, headers={"Cache-Control": "no-store"})

    ext = os.path.splitext(image["filepath"])[1].lower()
    if ext not in _browser_image_extensions:
        headers = await asyncio.to_thread(thumbnails.response_headers, image["filepath"], "lg", image_id)
        if request_etag == headers["ETag"]:
            return Response(status_code=304, headers=headers)

        data = await _await_thumbnail_bounded(image["filepath"], "lg", image_id)
        if data is None:
            return _pending_thumb_response()
        if not data:
            changed = await db.mark_image_missing(image_id)
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
        thumbnails.response_headers, image["filepath"], config.FULL_TIER, image_id
    )
    if request_etag == headers["ETag"]:
        return Response(status_code=304, headers=headers)

    path = thumbnails.get_cached_full_image_path(image["filepath"], image_id)
    if path is None:
        path = image["filepath"]
        background_tasks.add_task(thumbnails.schedule_full_image_cache, image["filepath"], image_id)

    if not path or not await asyncio.to_thread(os.path.exists, path):
        changed = await db.mark_image_missing(image_id)
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
    for size in config.THUMB_TIERS:
        cached = thumbnails.has_cached_fast(size, image_id)
        tiers[size] = {
            "cached": cached,
            "url": f"/api/thumb/{size}/{image_id}",
            "cached_url": f"/api/thumb/{size}/{image_id}?cached=1",
        }

    full_cached = cache_entries.fast_disk_path_entry(config.FULL_TIER, image_id) is not None
    tiers[config.FULL_TIER] = {
        "cached": full_cached,
        "url": f"/api/full/{image_id}",
        "cached_url": f"/api/full/{image_id}?cached=1",
    }
    best = thumbnails.best_cached_rendition(image_id)
    return {"id": image_id, "tiers": tiers, "best_cached": best[0] if best else None}


def _normalize_warm_requests(tier_requests) -> tuple[dict[str, list[int]], set[int]]:
    requested: dict[str, list[int]] = {}
    all_ids: set[int] = set()
    if not isinstance(tier_requests, dict):
        return requested, all_ids
    for tier, values in tier_requests.items():
        if tier not in config.ALL_TIERS:
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


