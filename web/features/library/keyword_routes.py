"""HTTP contract for manual keywording and editable IPTC metadata."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from features.library import keywords


router = APIRouter(tags=["keywords"])


class KeywordCreate(BaseModel):
    name: str = Field(max_length=180)
    parent_id: int | None = None


class KeywordPath(BaseModel):
    path: str = Field(max_length=2000)


class KeywordUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=180)
    parent_id: int | None = None
    move: bool = False


class KeywordAssignment(BaseModel):
    keyword_id: int
    image_ids: list[int] = Field(min_length=1, max_length=10_000)


class IptcFields(BaseModel):
    title: str = Field(default="", max_length=10_000)
    caption: str = Field(default="", max_length=10_000)
    copyright: str = Field(default="", max_length=10_000)
    creator: str = Field(default="", max_length=10_000)


def _http_error(error: Exception) -> HTTPException:
    if isinstance(error, LookupError):
        return HTTPException(status_code=404, detail=str(error))
    return HTTPException(status_code=422, detail=str(error))


@router.get("/api/keywords")
async def api_keywords(q: str = ""):
    return {"keywords": await keywords.list_keywords(query=q)}


@router.post("/api/keywords", status_code=201)
async def api_create_keyword(body: KeywordCreate):
    try:
        return {"keyword": await keywords.create_keyword(body.name, body.parent_id)}
    except (LookupError, ValueError) as error:
        raise _http_error(error) from error


@router.post("/api/keywords/resolve", status_code=201)
async def api_resolve_keyword(body: KeywordPath):
    try:
        return {"keyword": await keywords.resolve_keyword_path(body.path)}
    except ValueError as error:
        raise _http_error(error) from error


@router.patch("/api/keywords/{keyword_id}")
async def api_update_keyword(keyword_id: int, body: KeywordUpdate):
    try:
        return {"keyword": await keywords.update_keyword(keyword_id, name=body.name, parent_id=body.parent_id, move=body.move)}
    except (LookupError, ValueError) as error:
        raise _http_error(error) from error


@router.delete("/api/keywords/{keyword_id}", status_code=204)
async def api_delete_keyword(keyword_id: int):
    try:
        await keywords.delete_keyword(keyword_id)
    except (LookupError, ValueError) as error:
        raise _http_error(error) from error


@router.get("/api/images/{image_id}/keywords")
async def api_image_keywords(image_id: int):
    return {"keywords": await keywords.image_keywords(image_id)}


@router.get("/api/keywords/{keyword_id}/images")
async def api_keyword_images(keyword_id: int):
    return {"image_ids": await keywords.keyword_image_ids(keyword_id)}


@router.post("/api/keywords/assign")
async def api_assign_keyword(body: KeywordAssignment):
    try:
        return {"assigned": await keywords.assign_keyword(body.image_ids, body.keyword_id)}
    except LookupError as error:
        raise _http_error(error) from error


@router.post("/api/keywords/unassign")
async def api_unassign_keyword(body: KeywordAssignment):
    return {"unassigned": await keywords.unassign_keyword(body.image_ids, body.keyword_id)}


@router.get("/api/images/{image_id}/iptc")
async def api_get_iptc(image_id: int):
    return {"iptc": await keywords.get_iptc(image_id)}


@router.put("/api/images/{image_id}/iptc")
async def api_save_iptc(image_id: int, body: IptcFields):
    try:
        return {"iptc": await keywords.save_iptc(image_id, **body.model_dump())}
    except ValueError as error:
        raise _http_error(error) from error
