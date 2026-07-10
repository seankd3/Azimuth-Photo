"""Saved library workspaces.

The table is intentionally created on first use so saved views can land without
waiting for a schema-version migration. ``query`` is an opaque JSON string owned
by the desktop client; keeping it opaque lets a view restore future scope fields
without a backend migration.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

import db


router = APIRouter()

SAVED_VIEWS_DDL = """
CREATE TABLE IF NOT EXISTS saved_views (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    query TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_saved_views_created_at ON saved_views(created_at DESC, id DESC);
"""

MAX_NAME_LENGTH = 120
MAX_QUERY_LENGTH = 48_000


class SavedViewCreate(BaseModel):
    name: str = Field(max_length=MAX_NAME_LENGTH)
    query: str = Field(max_length=MAX_QUERY_LENGTH)


class SavedViewUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=MAX_NAME_LENGTH)
    query: str | None = Field(default=None, max_length=MAX_QUERY_LENGTH)


def _clean_name(name: str) -> str:
    clean = str(name or "").strip()
    if not clean:
        raise HTTPException(status_code=422, detail="A view needs a name")
    return clean


def _row_payload(row) -> dict:
    return {
        "id": int(row["id"]),
        "name": row["name"],
        "query": row["query"],
        "created_at": row["created_at"],
    }


async def _open_saved_views_db():
    conn = await db.get_db()
    await conn.executescript(SAVED_VIEWS_DDL)
    await conn.commit()
    return conn


async def _view_or_404(conn, view_id: int):
    cursor = await conn.execute(
        "SELECT id, name, query, created_at FROM saved_views WHERE id = ?",
        (view_id,),
    )
    row = await cursor.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Saved view not found")
    return row


@router.get("/api/saved-views")
async def list_saved_views():
    conn = await _open_saved_views_db()
    try:
        cursor = await conn.execute(
            "SELECT id, name, query, created_at FROM saved_views ORDER BY created_at DESC, id DESC"
        )
        return {"views": [_row_payload(row) for row in await cursor.fetchall()]}
    finally:
        await conn.close()


@router.post("/api/saved-views", status_code=201)
async def create_saved_view(payload: SavedViewCreate):
    name = _clean_name(payload.name)
    conn = await _open_saved_views_db()
    try:
        created_at = datetime.now(timezone.utc).isoformat()
        cursor = await conn.execute(
            "INSERT INTO saved_views (name, query, created_at) VALUES (?, ?, ?)",
            (name, payload.query, created_at),
        )
        await conn.commit()
        row = await _view_or_404(conn, int(cursor.lastrowid))
        return {"view": _row_payload(row)}
    finally:
        await conn.close()


@router.patch("/api/saved-views/{view_id}")
async def update_saved_view(view_id: int, payload: SavedViewUpdate):
    fields = getattr(payload, "model_fields_set", None)
    if fields is None:  # Pydantic v1 compatibility.
        fields = getattr(payload, "__fields_set__", set())
    if not fields:
        raise HTTPException(status_code=422, detail="Provide a name or query to update")
    conn = await _open_saved_views_db()
    try:
        current = await _view_or_404(conn, view_id)
        name = _clean_name(payload.name) if "name" in fields else current["name"]
        query = payload.query if "query" in fields else current["query"]
        await conn.execute(
            "UPDATE saved_views SET name = ?, query = ? WHERE id = ?",
            (name, query, view_id),
        )
        await conn.commit()
        return {"view": _row_payload(await _view_or_404(conn, view_id))}
    finally:
        await conn.close()


@router.delete("/api/saved-views/{view_id}")
async def delete_saved_view(view_id: int):
    conn = await _open_saved_views_db()
    try:
        await _view_or_404(conn, view_id)
        await conn.execute("DELETE FROM saved_views WHERE id = ?", (view_id,))
        await conn.commit()
        return {"ok": True, "id": view_id}
    finally:
        await conn.close()
