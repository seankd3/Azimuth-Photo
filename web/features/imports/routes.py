from fastapi import APIRouter, File, Form, UploadFile
from fastapi.responses import JSONResponse

import db
import settings
from core import cache_events
from data.repositories import imports as import_repository
from features.catalog import routes as catalog_routes
from features.imports import service as import_service


router = APIRouter()


def _import_library_url(batch_id: int) -> str:
    return f"/#import_batch={int(batch_id)}"


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
