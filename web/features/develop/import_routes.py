"""HTTP entry points for the asynchronous Develop XMP importer."""

from __future__ import annotations

from core.catalog_path import catalog_path

import asyncio
import logging
from collections.abc import Callable

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from core.background import track_background_task
from core.requests import json_object
from features.develop import importer, lrcat_import


def _prefetch_sm(images):
    return thumbnails.prefetch_images(images, "sm", limit=len(images))

import thumbnails
router = APIRouter()
DbPath = Callable[[], str]
_LOG = logging.getLogger(__name__)



async def _scan_in_background(root: str, db_path: str) -> None:
    try:
        result = await asyncio.to_thread(importer.scan_raws, root, db_path, claimed=True)
    except Exception:
        importer.finish_scan()  # Keep a failed background setup retryable.
        _LOG.exception("Develop import worker could not start")
        return
    # Existing thumbnail generation owns rawpy JPEG/BITMAP extraction and cache
    # writes. Warm only the initial visible batch; the remaining RAWs stay lazy.
    if _prefetch_sm and result.get("images"):
        await _prefetch_sm(result["images"][:60])


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
    track_background_task(_scan_in_background(claimed_root, catalog_path()))
    return {"started": True, "status": importer.import_status()}


@router.get("/api/develop/import/status")
async def api_develop_import_status():
    return importer.import_status()


async def _scan_lrcat_in_background(paths: list[str], db_path: str, dry_run: bool) -> None:
    try:
        await asyncio.to_thread(lrcat_import.scan_catalogs, paths, db_path, dry_run=dry_run, claimed=True)
    except Exception:
        _LOG.exception("Lightroom catalog import worker failed")


@router.get("/api/develop/lrcat/catalogs")
async def api_lrcat_catalogs():
    return {"catalogs": await asyncio.to_thread(lrcat_import.catalog_paths)}


@router.post("/api/develop/lrcat/scan")
async def api_scan_lrcat(request: Request):
    payload, error = await json_object(request)
    if error:
        return error
    requested_path = str(payload.get("catalog_path") or "").strip()
    scan_all = payload.get("all") is True
    dry_run = payload.get("dry_run") is True
    if not requested_path and not scan_all:
        return JSONResponse({"error": "Provide catalog_path or all: true"}, status_code=400)
    paths = [requested_path] if requested_path else lrcat_import.catalog_paths()
    if not paths:
        return JSONResponse({"error": "No Lightroom catalogs found"}, status_code=404)
    if not lrcat_import.begin_scan(dry_run=dry_run):
        return JSONResponse({"error": "Lightroom catalog import is already running", "status": lrcat_import.import_status()}, status_code=409)
    track_background_task(_scan_lrcat_in_background(paths, catalog_path(), dry_run))
    return {"started": True, "catalogs": paths, "status": lrcat_import.import_status()}


@router.get("/api/develop/lrcat/status")
async def api_lrcat_status():
    return lrcat_import.import_status()
