import asyncio
import csv
import io
import os
import tempfile
import zipfile
from collections.abc import Awaitable, Callable

from fastapi import APIRouter
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from starlette.background import BackgroundTask

from core.requests import clamp_int
from data.repositories import images as image_repository
from data.repositories import rankings as ranking_repository
from data.repositories import stats as stats_repository
import thumbnails


router = APIRouter()
ResolveLibraryConstraints = Callable[..., Awaitable[dict]]
DbPathProvider = Callable[[], str]
GetImportBatchImageIds = Callable[[int], Awaitable[set[int] | None]]
_resolve_library_constraints: ResolveLibraryConstraints | None = None
_db_path: DbPathProvider | None = None
_get_import_batch_image_ids: GetImportBatchImageIds | None = None

EXPORT_FIELD_NAMES = (
    "rank",
    "filename",
    "filepath",
    "elo",
    "comparisons",
    "propagated_updates",
    "status",
    "flag",
    "date_taken",
    "camera_make",
    "camera_model",
    "lens",
    "file_ext",
    "file_size",
    "file_modified_at",
    "width",
    "height",
    "latitude",
    "longitude",
)
ZIP_EXPORT_MAX_IMAGES = 2000
ZIP_EXPORT_SIZES = {"original", "lg", "md"}


def configure(
    *,
    resolve_library_constraints: ResolveLibraryConstraints,
    db_path: DbPathProvider,
    get_import_batch_image_ids: GetImportBatchImageIds | None = None,
) -> None:
    global _resolve_library_constraints, _db_path, _get_import_batch_image_ids
    _resolve_library_constraints = resolve_library_constraints
    _db_path = db_path
    _get_import_batch_image_ids = get_import_batch_image_ids


def _configured_db_path() -> str:
    if _db_path is None:
        raise RuntimeError("Export routes are not configured")
    return _db_path()


async def _get_export_images(
    *,
    ids: str,
    limit: int,
    sort: str,
    orientation: str,
    compared: str,
    min_stars: int,
    folder: str,
    flag: str,
    date_taken: str,
    file_type: str,
    camera: str,
    lens: str,
    q: str,
    deep: bool,
    people: str,
    import_batch: int = 0,
):
    db_path = _configured_db_path()
    if ids:
        id_list = [int(x) for x in ids.split(",") if x.strip().isdigit()][:50000]
        images_dict = await image_repository.get_images_by_ids(db_path, id_list)
        return [images_dict[i] for i in id_list if i in images_dict]

    if _resolve_library_constraints is None:
        raise RuntimeError("Export routes are not configured")
    limit = clamp_int(limit, 10000, 1, 50000)
    search = await _resolve_library_constraints(q, people=people, deep=deep)
    id_filter = search.get("id_filter")
    if import_batch > 0:
        if _get_import_batch_image_ids is None:
            raise RuntimeError("Export routes are not configured")
        batch_ids = await _get_import_batch_image_ids(import_batch)
        if batch_ids is None:
            id_filter = set()
        elif id_filter is None:
            id_filter = set(batch_ids)
        else:
            id_filter = {int(image_id) for image_id in id_filter}.intersection(batch_ids)
    db_sort = "elo" if sort == "similarity" else sort
    return await ranking_repository.rankings(
        db_path,
        catalog_counts=await stats_repository.catalog_image_counts_cached(db_path),
        limit=limit,
        offset=0,
        sort=db_sort,
        orientation=orientation,
        compared=compared,
        min_stars=min_stars,
        folder=folder,
        flag=flag,
        date_taken=date_taken,
        file_type=file_type,
        camera=camera,
        lens=lens,
        id_filter=id_filter,
        text_query=search.get("text_query") or "",
    )


def _export_row(rank: int, image: dict) -> dict:
    return {
        "rank": rank,
        "filename": image["filename"],
        "filepath": image["filepath"],
        "elo": round(image["elo"], 1),
        "comparisons": image["comparisons"],
        "propagated_updates": image.get("propagated_updates") or 0,
        "status": image["status"],
        "flag": image.get("flag") or "unflagged",
        "date_taken": image.get("date_taken"),
        "camera_make": image.get("camera_make"),
        "camera_model": image.get("camera_model"),
        "lens": image.get("lens"),
        "file_ext": image.get("file_ext"),
        "file_size": image.get("file_size"),
        "file_modified_at": image.get("file_modified_at"),
        "width": image.get("width"),
        "height": image.get("height"),
        "latitude": image.get("latitude"),
        "longitude": image.get("longitude"),
    }


def _parse_ids(ids: str, *, max_ids: int | None = None) -> list[int]:
    parsed = []
    seen = set()
    for value in ids.split(","):
        value = value.strip()
        if not value.isdigit():
            continue
        image_id = int(value)
        if image_id <= 0 or image_id in seen:
            continue
        seen.add(image_id)
        parsed.append(image_id)
        if max_ids is not None and len(parsed) > max_ids:
            break
    return parsed


def _safe_zip_name(image_id: int, filename: str) -> str:
    basename = os.path.basename(filename or f"image-{image_id}") or f"image-{image_id}"
    safe = "".join(char if char.isalnum() or char in "._- " else "_" for char in basename).strip()
    return f"{image_id}-{safe or f'image-{image_id}'}"


def _zip_source_for_image(image: dict, size: str) -> tuple[str | None, str]:
    image_id = int(image["id"])
    if size == "original":
        path = image.get("filepath") or ""
        if not path:
            return None, "source path missing"
        if not os.path.exists(path):
            return None, "source file unavailable"
        return path, ""
    entry = thumbnails.fast_disk_path_entry(size, image_id)
    if entry is None:
        return None, f"{size} cache entry missing"
    _signature, path = entry
    return path, ""


def _build_zip_file(images: list[dict], size: str) -> tuple[str, int]:
    temp = tempfile.NamedTemporaryFile(prefix="photoarchive-export-", suffix=".zip", delete=False)
    temp_path = temp.name
    temp.close()
    written = 0
    skipped = []
    try:
        with zipfile.ZipFile(temp_path, "w", compression=zipfile.ZIP_STORED) as archive:
            for image in images:
                image_id = int(image["id"])
                path, reason = _zip_source_for_image(image, size)
                if path is None:
                    skipped.append(f"{image_id}: {reason}")
                    continue
                archive.write(path, arcname=_safe_zip_name(image_id, image.get("filename") or path))
                written += 1
            if skipped:
                archive.writestr(
                    "manifest.txt",
                    "Skipped images:\n" + "\n".join(skipped) + "\n",
                )
        return temp_path, written
    except Exception:
        try:
            os.remove(temp_path)
        except OSError:
            pass
        raise


@router.get("/api/export")
async def export_rankings(
    format: str = "json", ids: str = "", limit: int = 10000, sort: str = "elo",
    orientation: str = "", compared: str = "", min_stars: int = 0,
    folder: str = "", flag: str = "", date_taken: str = "", file_type: str = "",
    camera: str = "", lens: str = "", q: str = "", deep: bool = False, people: str = "",
    import_batch: int = 0, size: str = "original",
):
    normalized_format = (format or "json").lower()
    if normalized_format == "zip":
        if size not in ZIP_EXPORT_SIZES:
            return JSONResponse({"detail": "size must be original, lg, or md"}, status_code=400)
        if ids and len(_parse_ids(ids, max_ids=ZIP_EXPORT_MAX_IMAGES)) > ZIP_EXPORT_MAX_IMAGES:
            return JSONResponse(
                {"detail": f"Zip export is limited to {ZIP_EXPORT_MAX_IMAGES} images"},
                status_code=400,
            )
    images = await _get_export_images(
        ids=ids,
        limit=limit,
        sort=sort,
        orientation=orientation,
        compared=compared,
        min_stars=min_stars,
        folder=folder,
        flag=flag,
        date_taken=date_taken,
        file_type=file_type,
        camera=camera,
        lens=lens,
        q=q,
        deep=deep,
        people=people,
        import_batch=import_batch,
    )
    if normalized_format == "zip":
        if len(images) > ZIP_EXPORT_MAX_IMAGES:
            return JSONResponse(
                {"detail": f"Zip export is limited to {ZIP_EXPORT_MAX_IMAGES} images"},
                status_code=400,
            )
        zip_path, written_count = await asyncio.to_thread(_build_zip_file, [dict(image) for image in images], size)
        return FileResponse(
            zip_path,
            media_type="application/zip",
            filename=f"photoarchive-export-{written_count}.zip",
            background=BackgroundTask(os.remove, zip_path),
        )

    data = [_export_row(index + 1, dict(image)) for index, image in enumerate(images)]

    if normalized_format == "csv":
        output = io.StringIO()
        if data:
            writer = csv.DictWriter(output, fieldnames=EXPORT_FIELD_NAMES)
            writer.writeheader()
            writer.writerows(data)
        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=rankings.csv"},
        )

    return data
