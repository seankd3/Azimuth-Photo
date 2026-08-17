"""Saved views: a named scope whose members a query decides.

The other half of `model.sets`. A collection and a keyword remember which
photographs are in them; a saved view remembers the *question*, and its members
are whoever answers it today. That is the only difference between the three, and
it is one field — which is why they are one primitive and not three features.

`saved_views` held zero rows on the live catalog, so nothing is being migrated.
What went with the table: a second id space, a `_view_or_404`, an
`_open_saved_views_db`, timestamps nobody read, and the ordering rules that come
free from `sets.all`.

**The query is opaque here, deliberately.** The desktop serialises its own
`{scope, layout}` and parses it back; the backend has never interpreted it and
does not start now. Making a view a scope the *server* can resolve — so that
exporting one, counting one or owing work over one becomes possible — is a real
next step, and it needs the desktop to send a spec rather than a snapshot. Until
something asks for that, resolving it here would be a mechanism with no caller.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from core.catalog_path import catalog_path
from data import connection
from model import sets

router = APIRouter()



class SavedViewCreate(BaseModel):
    name: str = Field(max_length=180)
    query: str = Field(default="", max_length=100_000)


class SavedViewUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=180)
    query: str | None = Field(default=None, max_length=100_000)


def _card(said: dict) -> dict:
    return {"id": said["id"], "name": said["name"], "query": said.get("query") or ""}


@router.get("/api/saved-views")
async def list_saved_views():
    conn = connection.reading(catalog_path())
    return {"views": [_card(s) for s in sets.all(conn, kind=sets.VIEW)]}


@router.post("/api/saved-views", status_code=201)
async def create_saved_view(payload: SavedViewCreate):
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="a saved view needs a name")
    query = payload.query or ""

    def job(conn):
        view_id = sets.create(conn, name, kind=sets.VIEW, query=query)
        conn.commit()
        return view_id

    view_id = await connection.writing(catalog_path(), job)
    return {"view": {"id": view_id, "name": name, "query": query}}


@router.patch("/api/saved-views/{view_id}")
async def update_saved_view(view_id: str, payload: SavedViewUpdate):
    def job(conn):
        said = sets.amend(
            conn, view_id,
            name=(payload.name or "").strip() or None,
            query=payload.query if payload.query is not None else None,
        )
        if said is not None:
            conn.commit()
        return said

    said = await connection.writing(catalog_path(), job)
    if said is None:
        raise HTTPException(status_code=404, detail="No such saved view")
    return {"view": _card(said)}


@router.delete("/api/saved-views/{view_id}")
async def delete_saved_view(view_id: str):
    def job(conn):
        if sets.describe(conn, view_id) is None:
            return False
        sets.forget(conn, view_id)
        conn.commit()
        return True

    if not await connection.writing(catalog_path(), job):
        raise HTTPException(status_code=404, detail="No such saved view")
    return {"ok": True}
