"""HTTP surface for Develop presets (list / create / rename / delete / apply)."""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from data import connection
from features.develop import presets as presets_mod


router = APIRouter()
DbPathProvider = Callable[[], str]
_db_path: DbPathProvider | None = None
_lr_import_attempted = False


def _auto_import_enabled() -> bool:
    if os.environ.get("PHOTOARCHIVE_SKIP_LR_PRESET_IMPORT", "").strip() in {"1", "true", "yes"}:
        return False
    # Smoke/unit runs stay fast; explicit /import-lightroom still works.
    if os.environ.get("PHOTOARCHIVE_SMOKE_MODE", "").strip() in {"1", "true", "yes"}:
        return False
    return True


class PresetCreateBody(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    folder: str = Field(default="", max_length=200)
    settings: dict[str, Any] = Field(default_factory=dict)


class PresetRenameBody(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    folder: str | None = Field(default=None, max_length=200)


class PresetApplyBody(BaseModel):
    image_id: int = Field(gt=0)


def configure(*, db_path: DbPathProvider) -> None:
    global _db_path
    _db_path = db_path


def _configured_db_path() -> str:
    if _db_path is None:
        raise RuntimeError("Develop preset routes are not configured")
    return _db_path()


async def _conn():
    return await connection.open_async(_configured_db_path())


async def _maybe_import_lr(conn) -> None:
    """One-shot LR .xmp import into folder Lightroom — never blocks callers on miss."""
    global _lr_import_attempted
    if _lr_import_attempted or not _auto_import_enabled():
        return
    _lr_import_attempted = True
    try:
        await presets_mod.import_lightroom_presets(conn)
    except Exception:
        # Report lives on presets.import_report(); routes stay available.
        pass


@router.get("/api/develop/presets")
async def api_list_presets():
    conn = await _conn()
    try:
        await presets_mod.ensure_develop_presets(conn)
        await _maybe_import_lr(conn)
        items = await presets_mod.list_presets(conn)
        return {"presets": items, "import": presets_mod.import_report()}
    finally:
        await connection.close_async(conn, db_path=_configured_db_path())


@router.post("/api/develop/presets")
async def api_create_preset(body: PresetCreateBody):
    conn = await _conn()
    try:
        preset = await presets_mod.create_preset(
            conn,
            name=body.name,
            folder=body.folder,
            settings=body.settings,
        )
        return {"preset": preset}
    finally:
        await connection.close_async(conn, db_path=_configured_db_path())


@router.post("/api/develop/presets/import-lightroom")
async def api_import_lightroom_presets():
    """Explicit re-scan of expansion LR preset folders (idempotent)."""
    global _lr_import_attempted
    conn = await _conn()
    try:
        _lr_import_attempted = True
        report = await presets_mod.import_lightroom_presets(conn)
        return {"import": report, "presets": await presets_mod.list_presets(conn)}
    finally:
        await connection.close_async(conn, db_path=_configured_db_path())


@router.get("/api/develop/presets/{preset_id}")
async def api_get_preset(preset_id: int):
    conn = await _conn()
    try:
        preset = await presets_mod.get_preset(conn, preset_id)
        if preset is None:
            return JSONResponse({"error": "Preset not found"}, status_code=404)
        return {"preset": preset}
    finally:
        await connection.close_async(conn, db_path=_configured_db_path())


@router.patch("/api/develop/presets/{preset_id}")
async def api_rename_preset(preset_id: int, body: PresetRenameBody):
    if body.name is None and body.folder is None:
        return JSONResponse({"error": "Provide name and/or folder"}, status_code=400)
    conn = await _conn()
    try:
        preset = await presets_mod.rename_preset(
            conn, preset_id, name=body.name, folder=body.folder
        )
        if preset is None:
            return JSONResponse({"error": "Preset not found"}, status_code=404)
        return {"preset": preset}
    finally:
        await connection.close_async(conn, db_path=_configured_db_path())


@router.delete("/api/develop/presets/{preset_id}")
async def api_delete_preset(preset_id: int):
    conn = await _conn()
    try:
        deleted = await presets_mod.delete_preset(conn, preset_id)
        if not deleted:
            return JSONResponse({"error": "Preset not found"}, status_code=404)
        return {"deleted": True, "id": preset_id}
    finally:
        await connection.close_async(conn, db_path=_configured_db_path())


@router.post("/api/develop/presets/{preset_id}/apply")
async def api_apply_preset(preset_id: int, body: PresetApplyBody):
    conn = await _conn()
    try:
        result = await presets_mod.apply_preset_to_image(conn, preset_id, body.image_id)
        if result is None:
            return JSONResponse({"error": "Preset not found"}, status_code=404)
        return result
    finally:
        await connection.close_async(conn, db_path=_configured_db_path())
