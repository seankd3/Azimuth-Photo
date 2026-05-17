import csv
import io
from collections.abc import Awaitable, Callable

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from core.requests import clamp_int
from data.repositories import images as image_repository
from data.repositories import rankings as ranking_repository
from data.repositories import stats as stats_repository


router = APIRouter()
ResolveLibraryConstraints = Callable[..., Awaitable[dict]]
DbPathProvider = Callable[[], str]
_resolve_library_constraints: ResolveLibraryConstraints | None = None
_db_path: DbPathProvider | None = None

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


def configure(
    *,
    resolve_library_constraints: ResolveLibraryConstraints,
    db_path: DbPathProvider,
) -> None:
    global _resolve_library_constraints, _db_path
    _resolve_library_constraints = resolve_library_constraints
    _db_path = db_path


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
):
    db_path = _configured_db_path()
    if ids:
        id_list = [int(x) for x in ids.split(",") if x.strip().isdigit()]
        images_dict = await image_repository.get_images_by_ids(db_path, id_list)
        return [images_dict[i] for i in id_list if i in images_dict]

    if _resolve_library_constraints is None:
        raise RuntimeError("Export routes are not configured")
    limit = clamp_int(limit, 10000, 1, 50000)
    search = await _resolve_library_constraints(q, people=people, deep=deep)
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
        id_filter=search.get("id_filter"),
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


@router.get("/api/export")
async def export_rankings(
    format: str = "json", ids: str = "", limit: int = 10000, sort: str = "elo",
    orientation: str = "", compared: str = "", min_stars: int = 0,
    folder: str = "", flag: str = "", date_taken: str = "", file_type: str = "",
    camera: str = "", lens: str = "", q: str = "", deep: bool = False, people: str = "",
):
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
    )
    data = [_export_row(index + 1, dict(image)) for index, image in enumerate(images)]

    if format == "csv":
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
