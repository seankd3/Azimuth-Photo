from core.catalog_path import catalog_path
import asyncio
import csv
import io
import os
import shutil
import stat
import tempfile
import zipfile

from fastapi import APIRouter
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from starlette.background import BackgroundTask

from core.path_groups import safe_commonpath
from core.requests import COMPUTED_SORTS, FolderScope, RankingSort
from data.repositories import catalog as catalog_repository
from data.repositories import images as image_repository
from data.repositories import imports as import_repository
from data.repositories import stats as stats_repository
import settings


import db
from core import query_constraints
from data import connection
from model import photos, sets
router = APIRouter()

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
EXPORT_PAGE_SIZE = 10000
ZIP_EXPORT_MAX_IMAGES = 2000
ZIP_EXPORT_SIZES = {"original", "lg", "md"}
ZIP_EXPORT_ORIGINAL_MAX_BYTES = 8 * 1024 * 1024 * 1024
_zip_export_semaphore = asyncio.Semaphore(2)


class InsufficientExportStorage(Exception):
    pass


async def _get_export_images(
    *,
    ids: str,
    limit: int | None,
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
    tag: str,
    q: str,
    deep: bool,
    people: str,
    import_batch: int = 0,
    collection_id: str = "",
    stacks: str = "expanded",
):
    db_path = catalog_path()
    if ids:
        id_list = _parse_ids(ids)
        images_dict = await image_repository.get_images_by_ids(db_path, id_list)
        return [images_dict[i] for i in id_list if i in images_dict]

    search = await query_constraints.resolve_configured_library_constraints(q, people=people, deep=deep)
    id_filter = search.get("id_filter")
    if import_batch > 0:
        batch_ids = await import_repository.import_batch_image_ids(catalog_path(), import_batch)
        if batch_ids is None:
            id_filter = set()
        elif id_filter is None:
            id_filter = set(batch_ids)
        else:
            id_filter = {int(image_id) for image_id in id_filter}.intersection(batch_ids)
    # A collection is an enumerated set, so narrowing by one is an intersection
    # here rather than a scope threaded down into the ranking query.
    if collection_id:
        conn = connection.inline_reader(catalog_path())
        members = set(photos.ids(conn, sets.members(conn, str(collection_id))))
        id_filter = members if id_filter is None else {int(i) for i in id_filter} & members
        collection_id = 0
    # The computed orders need the service's search/blend context, which an
    # export of raw rows does not carry. Elo is taste's own backbone, stated
    # here rather than left to a silent registry fallback.
    db_sort = "elo" if sort in COMPUTED_SORTS else sort
    ranking_args = dict(
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
        tag=tag,
        caption_model_key=settings.active_caption_config()["model_key"],
        id_filter=id_filter,
        collection_id=collection_id,
        text_query=search.get("text_query") or "",
        exclude_collapsed_stack_members=(stacks or "").strip().lower() == "collapsed",
    )
    # The ranked library, read straight from the catalog. This used to page
    # through the ranking repository, which is the only reason a 2,482-line
    # module survived every route it served being rebuilt. An export is a
    # SELECT: it wants columns and an order, not a cached, faceted,
    # scope-counted ranking response.
    import asyncio

    import library

    # `from data import connection` stood here and made the name local to the
    # whole of `_get_export_images`, so the use 34 lines above raised
    # UnboundLocalError the moment an export was narrowed by a collection. The
    # module imports it at the top; once is enough.
    def _read() -> list[dict]:
        conn = connection.inline_reader(db_path)
        sql = (
            "SELECT i.id, i.filename, i.filepath, i.tail, i.elo, i.comparisons,"
            " i.propagated_updates, i.status, i.flag, i.date_taken, i.camera_make,"
            " i.camera_model, i.lens, i.file_ext, i.file_size, i.file_modified_at,"
            " i.width, i.height, i.latitude, i.longitude"
            f" FROM images i WHERE {library.IN_LIBRARY}"
            " ORDER BY i.elo DESC, i.id DESC"
        )
        if limit is not None:
            sql += f" LIMIT {max(1, int(limit))}"
        return [dict(row) for row in conn.execute(sql)]

    return await asyncio.to_thread(_read)


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
    entry = cache_entries.fast_disk_path_entry(size, image_id)
    if entry is None:
        return None, f"{size} cache entry missing"
    _signature, path = entry
    return path, ""


def _active_source_roots() -> list[str]:
    rows = catalog_repository.folder_source_rows(catalog_path())
    roots = []
    for _source_id, source_path, _active_count in rows:
        if source_path:
            roots.append(catalog_repository.normalize_source_path(source_path))
    return roots


def _path_is_under_root(path: str, roots: list[str]) -> bool:
    for root in roots:
        if safe_commonpath([root, path]) == root:
            return True
    return False


def _regular_file_size(path: str, *, source_roots: list[str] | None = None) -> tuple[int | None, str]:
    try:
        lstat_result = os.lstat(path)
    except OSError:
        return None, "source file unavailable"
    if stat.S_ISLNK(lstat_result.st_mode):
        return None, "source path is a symlink"
    if not stat.S_ISREG(lstat_result.st_mode):
        return None, "source path is not a regular file"
    real_path = os.path.realpath(path)
    if source_roots is not None and not _path_is_under_root(real_path, source_roots):
        return None, "outside library"
    return int(lstat_result.st_size), ""


def _estimate_zip_size(images: list[dict], size: str) -> int:
    if size == "original":
        source_roots = _active_source_roots()
    else:
        source_roots = None
    total = 0
    for image in images:
        path, reason = _zip_source_for_image(image, size)
        if path is None:
            continue
        file_size, reason = _regular_file_size(path, source_roots=source_roots)
        if file_size is None:
            continue
        total += file_size
        if size == "original" and total >= ZIP_EXPORT_ORIGINAL_MAX_BYTES:
            return ZIP_EXPORT_ORIGINAL_MAX_BYTES
    return total


def _ensure_export_storage(estimated_size: int) -> None:
    free_bytes = shutil.disk_usage(tempfile.gettempdir()).free
    if free_bytes < max(1, estimated_size) * 2:
        raise InsufficientExportStorage()


def _build_zip_file(images: list[dict], size: str) -> tuple[str, int]:
    estimated_size = _estimate_zip_size(images, size)
    _ensure_export_storage(estimated_size)
    temp = tempfile.NamedTemporaryFile(prefix="azimuth-export-", suffix=".zip", delete=False)
    temp_path = temp.name
    temp.close()
    written = 0
    skipped = []
    cutoff = False
    total_original_bytes = 0
    source_roots = _active_source_roots() if size == "original" else None
    try:
        with zipfile.ZipFile(temp_path, "w", compression=zipfile.ZIP_STORED) as archive:
            for image in images:
                image_id = int(image["id"])
                path, reason = _zip_source_for_image(image, size)
                if path is None:
                    skipped.append(f"{image_id}: {reason}")
                    continue
                file_size, reason = _regular_file_size(path, source_roots=source_roots)
                if file_size is None:
                    skipped.append(f"{image_id}: {reason}")
                    continue
                if (
                    size == "original"
                    and total_original_bytes + file_size > ZIP_EXPORT_ORIGINAL_MAX_BYTES
                ):
                    cutoff = True
                    skipped.append(f"{image_id}: original export size limit reached")
                    break
                archive.write(path, arcname=_safe_zip_name(image_id, image.get("filename") or path))
                if size == "original":
                    total_original_bytes += file_size
                written += 1
            if cutoff:
                skipped.append(
                    f"cutoff: original export limited to {ZIP_EXPORT_ORIGINAL_MAX_BYTES} bytes"
                )
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
    format: str = "json", ids: str = "", limit: int | None = None, sort: RankingSort = "elo",
    orientation: str = "", compared: str = "", min_stars: int = 0,
    folder: FolderScope = "", flag: str = "", date_taken: str = "", file_type: str = "",
    camera: str = "", lens: str = "", tag: str = "", q: str = "", deep: bool = False, people: str = "",
    import_batch: int = 0, collection_id: str = "", stacks: str = "expanded",
    size: str = "original",
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
    export_limit = limit
    if normalized_format == "zip":
        export_limit = min(
            max(1, int(limit)) if limit is not None else ZIP_EXPORT_MAX_IMAGES + 1,
            ZIP_EXPORT_MAX_IMAGES + 1,
        )
    images = await _get_export_images(
        ids=ids,
        limit=export_limit,
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
        tag=tag,
        q=q,
        deep=deep,
        people=people,
        import_batch=import_batch,
        collection_id=collection_id,
        stacks=stacks,
    )
    if normalized_format == "zip":
        if len(images) > ZIP_EXPORT_MAX_IMAGES:
            return JSONResponse(
                {"detail": f"Zip export is limited to {ZIP_EXPORT_MAX_IMAGES} images"},
                status_code=400,
            )
        try:
            async with _zip_export_semaphore:
                zip_path, written_count = await asyncio.to_thread(
                    _build_zip_file,
                    [dict(image) for image in images],
                    size,
                )
        except InsufficientExportStorage:
            return JSONResponse(
                {"detail": "Not enough temporary disk space for export"},
                status_code=507,
            )
        return FileResponse(
            zip_path,
            media_type="application/zip",
            filename=f"azimuth-photo-export-{written_count}.zip",
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
