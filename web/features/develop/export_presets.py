"""Named settings for Develop exports, including print-ready recipes."""

from __future__ import annotations

from core.catalog_path import catalog_path

import json
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from data import connection


router = APIRouter()
DbPathProvider = Callable[[], str]

EXPORT_PRESETS_DDL = """
CREATE TABLE IF NOT EXISTS develop_export_presets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    options TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_develop_export_presets_name
ON develop_export_presets(name COLLATE NOCASE);
"""

VALID_FORMATS = {"jpeg", "tiff16"}
VALID_SHARPEN = {
    "none", "screen_low", "screen_standard", "screen_high",
    "print_low", "print_standard", "print_high",
}
VALID_COLOR_SPACES = {"srgb", "adobe_rgb"}


class ExportPresetBody(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    options: dict[str, Any] = Field(default_factory=dict)


async def ensure_export_presets(conn) -> None:
    await conn.executescript(EXPORT_PRESETS_DDL)


def normalize_options(options: dict[str, Any] | None) -> dict[str, Any]:
    """Keep only renderer-supported export controls in a stable API shape."""
    raw = options if isinstance(options, dict) else {}
    output_format = str(raw.get("format") or "jpeg").lower()
    sharpen = str(raw.get("sharpen") or "none").lower()
    color_space = str(raw.get("color_space") or "srgb").lower()
    try:
        quality = max(1, min(100, int(raw.get("quality", 92))))
    except (TypeError, ValueError):
        quality = 92
    try:
        max_px = int(raw["max_px"]) if raw.get("max_px") else None
    except (TypeError, ValueError):
        max_px = None
    if max_px is not None and not 1 <= max_px <= 100000:
        max_px = None
    try:
        border_px = int(raw.get("border_px") or 0)
    except (TypeError, ValueError):
        border_px = 0
    border_px = max(0, min(2000, border_px))
    pattern = str(raw.get("filename_pattern") or "").strip()[:160]
    return {
        "format": output_format if output_format in VALID_FORMATS else "jpeg",
        "quality": quality,
        "max_px": max_px,
        "sharpen": sharpen if sharpen in VALID_SHARPEN else "none",
        "filename_pattern": pattern or None,
        "save_to_library": bool(raw.get("save_to_library", False)),
        "print_ready": bool(raw.get("print_ready", False)),
        "dpi": 300 if bool(raw.get("print_ready", False)) else None,
        "color_space": color_space if color_space in VALID_COLOR_SPACES else "srgb",
        "border_px": border_px,
    }


def _row_payload(row) -> dict[str, Any]:
    raw = dict(row)
    try:
        options = json.loads(raw.get("options") or "{}")
    except (TypeError, ValueError):
        options = {}
    return {
        "id": int(raw["id"]), "name": str(raw["name"]),
        "options": normalize_options(options),
        "created_at": float(raw["created_at"]), "updated_at": float(raw["updated_at"]),
    }


async def list_export_presets(conn) -> list[dict[str, Any]]:
    await ensure_export_presets(conn)
    cursor = await conn.execute(
        "SELECT * FROM develop_export_presets ORDER BY name COLLATE NOCASE, id"
    )
    return [_row_payload(row) for row in await cursor.fetchall()]


async def save_export_preset(conn, *, name: str, options: dict[str, Any], now: float) -> dict[str, Any]:
    await ensure_export_presets(conn)
    clean_name = name.strip()
    clean_options = normalize_options(options)
    await conn.execute(
        """INSERT INTO develop_export_presets(name, options, created_at, updated_at)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(name) DO UPDATE SET options = excluded.options, updated_at = excluded.updated_at""",
        (clean_name, json.dumps(clean_options, separators=(",", ":")), now, now),
    )
    await conn.commit()
    cursor = await conn.execute(
        "SELECT * FROM develop_export_presets WHERE name = ? COLLATE NOCASE", (clean_name,)
    )
    row = await cursor.fetchone()
    return _row_payload(row)


async def delete_export_preset(conn, preset_id: int) -> bool:
    await ensure_export_presets(conn)
    cursor = await conn.execute("DELETE FROM develop_export_presets WHERE id = ?", (preset_id,))
    await conn.commit()
    return bool(cursor.rowcount)


async def _open_conn():
    return await connection.open_async(catalog_path())


@router.get("/api/develop/export-presets")
async def api_list_export_presets():
    conn = await _open_conn()
    try:
        return {"presets": await list_export_presets(conn)}
    finally:
        await connection.close_async(conn, db_path=catalog_path())


@router.post("/api/develop/export-presets")
async def api_save_export_preset(body: ExportPresetBody):
    import time

    conn = await _open_conn()
    try:
        preset = await save_export_preset(conn, name=body.name, options=body.options, now=time.time())
        return {"preset": preset}
    finally:
        await connection.close_async(conn, db_path=catalog_path())


@router.delete("/api/develop/export-presets/{preset_id}")
async def api_delete_export_preset(preset_id: int):
    conn = await _open_conn()
    try:
        if not await delete_export_preset(conn, preset_id):
            return JSONResponse({"error": "Export preset not found"}, status_code=404)
        return {"deleted": True, "id": preset_id}
    finally:
        await connection.close_async(conn, db_path=catalog_path())


def print_ready_options(*, color_space: str = "srgb", border_px: int = 0) -> dict[str, Any]:
    """The intentionally small, useful print preset: 300dpi TIFF16."""
    return normalize_options({
        "format": "tiff16", "quality": 100, "sharpen": "print_standard",
        "print_ready": True, "color_space": color_space, "border_px": border_px,
    })
