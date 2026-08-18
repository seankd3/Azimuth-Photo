"""The library surface over the local desktop transport."""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Request

from model import scope as scopes


router = APIRouter()
_SCOPE_KEYS = frozenset(("folder", "stars", "set", "ids"))
_MAX_IDS = 10_000


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
