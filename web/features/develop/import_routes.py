"""HTTP entry points for the asynchronous Develop XMP importer."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from core.requests import json_object
from features.develop import importer, lrcat_import


router = APIRouter()
DbPath = Callable[[], str]
PrefetchThumbnails = Callable[[list[dict]], Awaitable[int]]
_db_path: DbPath | None = None
_prefetch_thumbnails: PrefetchThumbnails | None = None
_LOG = logging.getLogger(__name__)


def configure(*, db_path: DbPath, prefetch_thumbnails: PrefetchThumbnails | None = None) -> None:
    global _db_path, _prefetch_thumbnails
    _db_path = db_path
    _prefetch_thumbnails = prefetch_thumbnails


def _configured_db_path() -> str:
    if _db_path is None:
        raise RuntimeError("Develop import routes are not configured")
    return _db_path()


async def _scan_in_background(root: str, db_path: str) -> None:
    try:
        result = await asyncio.to_thread(importer.scan_raws, root, db_path, claimed=True)
    except Exception:
        importer.finish_scan()  # Keep a failed background setup retryable.
        _LOG.exception("Develop import worker could not start")
        return
    # Existing thumbnail generation owns rawpy JPEG/BITMAP extraction and cache
    # writes. Warm only the initial visible batch; the remaining RAWs stay lazy.
    if _prefetch_thumbnails and result.get("images"):
        await _prefetch_thumbnails(result["images"][:60])


@router.post("/api/develop/import/scan")
async def api_scan_develop_import(request: Request):
    payload, error = await json_object(request)
    if error:
        return error
    root = str(payload.get("root") or importer.DEFAULT_RAWS_ROOT)
    try:
        claimed_root = importer.begin_scan(root)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    if claimed_root is None:
        return JSONResponse({"error": "Develop import is already running", "status": importer.import_status()}, status_code=409)
    asyncio.create_task(_scan_in_background(claimed_root, _configured_db_path()))
    return {"started": True, "status": importer.import_status()}


@router.get("/api/develop/import/status")
async def api_develop_import_status():
    return importer.import_status()


async def _scan_lrcat_in_background(paths: list[str], db_path: str) -> None:
    await asyncio.to_thread(lrcat_import.scan_catalogs, paths, db_path, claimed=True)


@router.post("/api/develop/lrcat/scan")
async def api_scan_lrcat(request: Request):
    payload, error = await json_object(request)
    if error:
        return error
    requested_path = str(payload.get("catalog_path") or "").strip()
    scan_all = payload.get("all") is True
    if not requested_path and not scan_all:
        return JSONResponse({"error": "Provide catalog_path or all: true"}, status_code=400)
    paths = [requested_path] if requested_path else lrcat_import.catalog_paths()
    if not paths:
        return JSONResponse({"error": "No Lightroom catalogs found"}, status_code=404)
    if not lrcat_import.begin_scan():
        return JSONResponse({"error": "Lightroom catalog import is already running", "status": lrcat_import.import_status()}, status_code=409)
    asyncio.create_task(_scan_lrcat_in_background(paths, _configured_db_path()))
    return {"started": True, "catalogs": paths, "status": lrcat_import.import_status()}


@router.get("/api/develop/lrcat/status")
async def api_lrcat_status():
    return lrcat_import.import_status()
