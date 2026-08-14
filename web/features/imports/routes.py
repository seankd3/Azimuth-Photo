import asyncio
from typing import Literal

from fastapi import APIRouter, File, Form, UploadFile
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

import db
import settings
from core import cache_events
from data.repositories import imports as import_repository
from features.catalog import routes as catalog_routes
from features.imports import film
from features.imports import taxonomy
from features.imports import service as import_service
from features.sync import hub as sync_hub
from features.imports import staging
from features.quality import routes as quality_routes


router = APIRouter()


class ScanRequest(BaseModel):
    path: str
    include_subfolders: bool = False


class CommitRequest(BaseModel):
    scan_id: str
    keys: list[str] | Literal["all_checked_default"]
    mode: Literal["copy", "add", "move"]
    skip_suspects: bool = True
    clear_card: bool = False
    category: Literal["raw", "personal", "film", "export"] | None = None
    keywords: list[str] = Field(default_factory=list)
    collection_id: int | None = None


class CancelRequest(BaseModel):
    pass


class ReclassifyRequest(BaseModel):
    confirm: bool = False
    move_files: bool = True
    dry_run: bool = True


def _import_library_url(batch_id: int) -> str:
    return f"/#import_batch={int(batch_id)}"


@router.get("/api/import/sources")
async def api_import_sources():
    return {"sources": await staging.sources()}


@router.get("/api/import/browse")
async def api_import_browse(path: str = ""):
    try:
        return {"dirs": await staging.browse(path)}
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


@router.post("/api/import/scan")
async def api_import_scan(body: ScanRequest):
    try:
        scan = await staging.start_scan(body.path, body.include_subfolders)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return {"scan_id": scan.id}


@router.get("/api/import/scan/{scan_id}")
async def api_import_scan_status(scan_id: str, offset: int = 0):
    scan = staging.scan_for_id(scan_id)
    if scan is None:
        return JSONResponse({"error": "Import scan not found"}, status_code=404)
    return staging.scan_page(scan, offset)


# Lab TIFFs decode whole (draft cannot scale them): 170MB frames at seconds
# each. Forty canvas tiles arriving at once used to saturate the pool and
# every tile starved blank; two at a time fills the canvas progressively and
# each finished preview is cached for instant re-serve.
_scan_thumb_gate = asyncio.Semaphore(2)


@router.get("/api/import/scan/{scan_id}/thumb/{key}")
async def api_import_scan_thumb(scan_id: str, key: str):
    scan = staging.scan_for_id(scan_id)
    entry = staging.entry_for_key(scan, key) if scan else None
    if entry is None:
        return JSONResponse({"error": "Import preview not found"}, status_code=404)
    try:
        async with _scan_thumb_gate:
            data = await asyncio.to_thread(staging.thumbnail_bytes, scan, entry)
    except (OSError, ValueError) as exc:
        return JSONResponse({"error": str(exc) or "Preview could not be decoded"}, status_code=422)
    return Response(data, media_type="image/jpeg")


@router.post("/api/import/film")
async def api_import_film(files: list[UploadFile] = File(...)):
    """Film-scan intake: ZIP archives (extracted server-side) or loose TIFF/image
    scans, staged for the import canvas. Destination is Film Scans/<archive name>/."""
    try:
        staged = await film.stage_uploads(files)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    scan = await staging.start_film_scan(staged["path"], label=staged["label"])
    return {
        "scan_id": scan.id,
        "label": staged["label"],
        "path": staged["path"],
        "staged_files": staged["staged_files"],
        "skipped": staged["skipped"],
    }


@router.post("/api/import/commit")
async def api_import_commit(body: CommitRequest):
    scan = staging.scan_for_id(body.scan_id)
    if scan is None:
        return JSONResponse({"error": "Import scan not found"}, status_code=404)
    try:
        job = await staging.start_commit(
            scan,
            keys=body.keys,
            mode=body.mode,
            skip_suspects=body.skip_suspects,
            clear_card=body.clear_card,
            keyword_paths=body.keywords,
            collection_id=body.collection_id,
            category=body.category,
        )
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return {"job_id": job.id, "batch_id": job.batch_id}


@router.get("/api/import/jobs/{job_id}")
async def api_import_job(job_id: str):
    job = staging.job_for_id(job_id)
    if job is None:
        return JSONResponse({"error": "Import job not found"}, status_code=404)
    return job.status()


@router.post("/api/import/jobs/{job_id}/cancel")
async def api_import_cancel(job_id: str, _body: CancelRequest | None = None):
    job = staging.job_for_id(job_id)
    if job is None:
        return JSONResponse({"error": "Import job not found"}, status_code=404)
    staging.request_cancel(job)
    return job.status()






def _preset_options(import_root: str, catalog: dict | None = None) -> list[dict]:
    presets = [
        {"label": "Import Inbox", "path": import_root},
        {"label": "Pictures", "path": import_service.normalize_server_dir("~/Pictures", import_root)},
    ]
    seen = {item["path"] for item in presets}
    for source in (catalog or {}).get("sources") or []:
        path = source.get("path") or ""
        if not path or path in seen or not source.get("included", True):
            continue
        seen.add(path)
        presets.append({"label": source.get("display_name") or path, "path": path})
        if len(presets) >= 8:
            break
    return presets


@router.get("/api/imports/options")
async def api_import_options():
    config = settings.get_settings()
    import_root = config.get("import_root") or settings.DEFAULT_SETTINGS["import_root"]
    catalog = await db.get_catalog_summary()
    return {
        "import_root": import_root,
        "presets": _preset_options(import_root, catalog),
        "supported_extensions": sorted(import_service.scanner.SUPPORTED_EXTENSIONS),
        "today": import_service.date.today().isoformat(),
    }


@router.get("/api/imports")
async def api_import_batches(limit: int = 20):
    batches = await import_repository.recent_import_batches(db.DB_PATH, limit=limit)
    return {"imports": batches}


@router.get("/api/imports/{batch_id}")
async def api_import_batch(batch_id: int):
    batch = await import_repository.import_batch(db.DB_PATH, batch_id)
    if not batch:
        return JSONResponse({"error": "Import batch not found"}, status_code=404)
    return {"ok": True, "batch": batch, "library_url": _import_library_url(batch_id)}


@router.post("/api/imports")
async def api_create_import(
    files: list[UploadFile] = File(...),
    relative_paths: list[str] = Form(default=[]),
    destination_mode: str = Form(default="date_shoot"),
    import_root: str = Form(default=""),
    manual_destination: str = Form(default=""),
    preset_path: str = Form(default=""),
    shoot_date: str = Form(default=""),
    shoot_name: str = Form(default=""),
    preserve_structure: str = Form(default="false"),
):
    if not files:
        return JSONResponse({"error": "Choose at least one photo to import"}, status_code=400)

    config = settings.get_settings()
    plan = import_service.destination_plan(
        mode=destination_mode,
        import_root=import_root or config.get("import_root") or settings.DEFAULT_SETTINGS["import_root"],
        manual_destination=manual_destination,
        preset_path=preset_path,
        shoot_date=shoot_date,
        shoot_name=shoot_name,
    )
    preserve = import_service.truthy(preserve_structure)
    batch_id = await import_repository.create_import_batch(
        db.DB_PATH,
        {
            "name": plan["name"],
            "destination_mode": plan["mode"],
            "destination_root": plan["root"],
            "destination_path": plan["destination"],
            "preserve_structure": preserve,
            "total_files": len(files),
        },
    )

    try:
        copy_result = await import_service.copy_import_files(
            uploads=files,
            relative_paths=relative_paths,
            destination_path=plan["destination"],
            preserve_structure=preserve,
        )
        copied = copy_result["copied"]
        source = None
        image_rows = []
        if copied:
            source = await db.add_or_restore_source(plan["root"])
            await db.insert_images_batch(
                [
                    (
                        item["filename"],
                        item["filepath"],
                        item["file_ext"],
                        item["file_size"],
                        item["file_modified_at"],
                    )
                    for item in copied
                ],
                source_id=int(source["id"]),
            )
            ids_by_path = await import_repository.image_ids_by_filepaths(
                db.DB_PATH,
                [item["filepath"] for item in copied],
            )
            image_rows = [
                {
                    "image_id": ids_by_path[item["filepath"]],
                    "filepath": item["filepath"],
                    "original_name": item["original_name"],
                }
                for item in copied
                if item["filepath"] in ids_by_path
            ]

        await import_repository.complete_import_batch(
            db.DB_PATH,
            batch_id,
            source_id=int(source["id"]) if source else None,
            image_rows=image_rows,
            imported_files=len(image_rows),
            skipped_files=len(copy_result["skipped"]),
            collision_count=copy_result["collision_count"],
        )
    except Exception as exc:
        await import_repository.fail_import_batch(db.DB_PATH, batch_id, str(exc))
        raise

    if image_rows:
        await quality_routes.scan_image_ids(
            [int(row["image_id"]) for row in image_rows]
        )
    cache_events.invalidate_rankings_cache()
    cache_events.invalidate_pairing_cache(matchups=True)
    catalog_routes.invalidate_folders_cache()
    return {
        "ok": True,
        "batch_id": batch_id,
        "imported_files": len(image_rows),
        "skipped_files": len(copy_result["skipped"]),
        "collision_count": copy_result["collision_count"],
        "destination_path": plan["destination"],
        "library_url": _import_library_url(batch_id),
    }


@router.get("/api/import/taxonomy")
async def api_import_taxonomy():
    """Documented destination table for library imports."""
    return {
        "destinations": [
            {
                "id": "edits",
                "folder": taxonomy.DEST_EDITS,
                "label": "Edits",
                "rule": "Edited exports from Develop (incl. film-scan edits)",
            },
            {
                "id": "raws",
                "folder": taxonomy.DEST_DIGITAL,
                "label": "Raws",
                "rule": "Digital-camera RAW (CR3/CR2/ARW/NEF/RAF/ORF/RW2/DNG, …), filed on the Digital shelf",
            },
            {
                "id": "film_scans",
                "folder": taxonomy.DEST_FILM,
                "label": "Film Scans",
                "rule": "Scanner / lab film-scan inputs (typically TIFF), filed under Raws",
            },
            {
                "id": "snapshots",
                "folder": taxonomy.DEST_SNAPSHOTS,
                "label": "Snapshots",
                "rule": "Phone / cellphone stills (JPEG/HEIC/HEIF), takeout dumps, and the phone upload queue",
            },
        ],
        "misplaced_personal_under_raws": (
            await taxonomy.preview_misplaced_personal_photos(
                db.DB_PATH,
                sync_hub.default_library_root(),
            )
        ),
    }


@router.post("/api/import/taxonomy/reclassify-personal")
async def api_import_taxonomy_reclassify(body: ReclassifyRequest):
    """Explicit repair for phone files nested under RAWS/Personal Photos.

    Never runs automatically. confirm=true and dry_run=false required to mutate.
    """
    result = await taxonomy.reclassify_misplaced_personal_photos(
        db.DB_PATH,
        sync_hub.default_library_root(),
        confirm=body.confirm,
        move_files=body.move_files,
        dry_run=body.dry_run,
    )
    if result.get("updated"):
        catalog_routes.invalidate_folders_cache()
        cache_events.invalidate_catalog_cache()
        cache_events.invalidate_rankings_cache()
    return result
