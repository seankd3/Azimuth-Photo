"""The library surface over the local desktop transport."""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict

import render
from model import scope as scopes


router = APIRouter()
_SCOPE_KEYS = frozenset(("folder", "stars", "set", "ids"))
_MAX_IDS = 10_000


class DriveInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    root: str
    label: str = ""
    is_record: bool = False


def parse_scope(value: str | None) -> scopes.Scope:
    """Read one typed scope; uncertainty is an error, never ``EVERYTHING``."""

    if value is None or value == "":
        return scopes.EVERYTHING
    try:
        document = json.loads(value)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("scope must be a JSON object") from exc
    if not isinstance(document, dict):
        raise ValueError("scope must be a JSON object")
    unknown = set(document) - _SCOPE_KEYS
    if unknown:
        raise ValueError(f"unknown scope field: {sorted(unknown)[0]}")

    selected = []
    if "folder" in document:
        folder = document["folder"]
        if not isinstance(folder, str):
            raise ValueError("scope.folder must be text")
        selected.append(scopes.folder(folder))
    if "stars" in document:
        stars = document["stars"]
        if isinstance(stars, bool) or not isinstance(stars, int):
            raise ValueError("scope.stars must be an integer")
        selected.append(scopes.starred(stars))
    if "set" in document:
        set_id = document["set"]
        if not isinstance(set_id, str) or not set_id.strip():
            raise ValueError("scope.set must be non-empty text")
        selected.append(scopes.in_set(set_id))
    if "ids" in document:
        image_ids = document["ids"]
        if not isinstance(image_ids, list) or any(
            isinstance(image_id, bool) or not isinstance(image_id, int)
            for image_id in image_ids
        ):
            raise ValueError("scope.ids must be a list of integers")
        if len(image_ids) > _MAX_IDS:
            raise ValueError(f"scope.ids is limited to {_MAX_IDS} entries")
        selected.append(scopes.ids(image_ids))
    return scopes.all_of(*selected)


@router.get("/api/photos")
async def photos(
    request: Request,
    scope: str | None = None,
    sort: str = "newest",
    limit: int = 200,
    offset: int = 0,
):
    try:
        selected = parse_scope(scope)
        return await request.app.state.library.run(
            lambda product: product.browse(
                scope=selected,
                sort=sort,
                limit=limit,
                offset=offset,
            )
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/api/drives")
async def drives(request: Request):
    return await request.app.state.library.run(lambda product: product.attached())


@router.post("/api/drives")
async def attach_drive(request: Request, chosen: DriveInput):
    try:
        return await request.app.state.library.run(
            lambda product: product.attach(
                chosen.root,
                label=chosen.label,
                is_record=chosen.is_record,
            )
        )
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/drives/{drive_uuid}/refresh")
async def refresh_drive(request: Request, drive_uuid: str):
    return await request.app.state.library.refresh(drive_uuid)


@router.get("/api/photos/{photo_id}")
async def photo(request: Request, photo_id: int):
    answer = await request.app.state.library.run(
        lambda product: product.details(photo_id)
    )
    if answer is None:
        raise HTTPException(status_code=404, detail="photo is unavailable")
    return answer


@router.get("/api/photos/{photo_id}/tile")
async def tile(
    request: Request,
    photo_id: int,
    size: int = render.GRID,
    rotate: int = 0,
):
    try:
        body = await request.app.state.library.run(
            lambda product: product.tile(photo_id, size=size, rotate=rotate)
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if body is None:
        raise HTTPException(status_code=404, detail="photo is unavailable")
    return Response(
        body,
        media_type="image/jpeg",
        headers={"Cache-Control": "private, max-age=31536000, immutable"},
    )
