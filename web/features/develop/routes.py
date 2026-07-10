"""HTTP surface for RAW Develop settings and base-preview artifacts."""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from data import connection
from data.repositories import images as image_repository
from features.develop import rawproc


router = APIRouter()
DbPathProvider = Callable[[], str]
_db_path: DbPathProvider | None = None
_pregen_tasks: set[asyncio.Task] = set()


class DevelopSettingsBody(BaseModel):
    settings: dict[str, Any] = Field(default_factory=dict)
    label: str | None = Field(default=None, max_length=160)


class DevelopPregenBody(BaseModel):
    image_ids: list[int] = Field(default_factory=list, max_length=12)


class DevelopExportBody(BaseModel):
    format: str
    quality: int = Field(default=88, ge=1, le=100)
    max_px: int | None = Field(default=None, ge=1, le=100000)


def configure(*, db_path: DbPathProvider) -> None:
    global _db_path
    _db_path = db_path


def _configured_db_path() -> str:
    if _db_path is None:
        raise RuntimeError("Develop routes are not configured")
    return _db_path()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_settings(raw: str | None) -> dict[str, Any]:
    try:
        result = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return result if isinstance(result, dict) else {}


async def _image_or_error(image_id: int):
    image = await image_repository.get_image_by_id(_configured_db_path(), image_id)
    if not image:
        return None, JSONResponse({"error": "Image not found"}, status_code=404)
    image = dict(image)
    if not rawproc.is_develop_path(image.get("filepath") or ""):
        return None, JSONResponse({"error": "Develop editing is available only for RAW files and HDR merges"}, status_code=400)
    if not await asyncio.to_thread(os.path.exists, image["filepath"]):
        return None, JSONResponse({"error": "RAW source file is unavailable"}, status_code=404)
    return image, None


async def _load_settings(image_id: int) -> dict[str, Any] | None:
    conn = await connection.open_async(_configured_db_path())
    try:
        cursor = await conn.execute(
            "SELECT settings, origin, xmp_path, xmp_mtime, updated_at FROM develop_settings WHERE image_id = ?",
            (image_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return dict(row)
    finally:
        await connection.close_async(conn, db_path=_configured_db_path())


async def _history(image_id: int) -> list[dict[str, Any]]:
    conn = await connection.open_async(_configured_db_path())
    try:
        cursor = await conn.execute(
            "SELECT id, settings, label, created_at FROM develop_history WHERE image_id = ? ORDER BY id DESC LIMIT 40",
            (image_id,),
        )
        return [
            {**dict(row), "settings": _json_settings(row["settings"])}
            for row in await cursor.fetchall()
        ]
    finally:
        await connection.close_async(conn, db_path=_configured_db_path())


async def _ensure_base(image_id: int, image: dict) -> tuple[rawproc.BasePaths, dict[str, Any]]:
    return await asyncio.to_thread(rawproc.ensure_base_cache, image_id, image["filepath"])


async def _upsert_settings(image_id: int, incoming: dict[str, Any], label: str | None) -> dict[str, Any]:
    conn = await connection.open_async(_configured_db_path())
    try:
        await conn.execute("BEGIN")
        cursor = await conn.execute(
            "SELECT settings, origin, xmp_path, xmp_mtime FROM develop_settings WHERE image_id = ?",
            (image_id,),
        )
        existing = await cursor.fetchone()
        now = _now()
        if existing is None:
            merged = dict(incoming)
            origin = "user"
            xmp_path = None
            xmp_mtime = None
        else:
            prior = _json_settings(existing["settings"])
            # Merge rather than replace: clients can render only v1 keys while
            # later phases and imported XMP keys survive every autosave.
            merged = {**prior, **incoming}
            origin = existing["origin"] or "user"
            xmp_path = existing["xmp_path"]
            xmp_mtime = existing["xmp_mtime"]
            if origin == "xmp":
                await conn.execute(
                    "INSERT INTO develop_history (image_id, settings, label, created_at) VALUES (?, ?, ?, ?)",
                    (image_id, json.dumps(prior, separators=(",", ":")), "Import from XMP", now),
                )
                origin = "user"
        settings_json = json.dumps(merged, separators=(",", ":"))
        await conn.execute(
            """
            INSERT INTO develop_settings (image_id, settings, origin, xmp_path, xmp_mtime, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(image_id) DO UPDATE SET
                settings = excluded.settings,
                origin = excluded.origin,
                xmp_path = excluded.xmp_path,
                xmp_mtime = excluded.xmp_mtime,
                updated_at = excluded.updated_at
            """,
            (image_id, settings_json, origin, xmp_path, xmp_mtime, now),
        )
        await conn.execute(
            "INSERT INTO develop_history (image_id, settings, label, created_at) VALUES (?, ?, ?, ?)",
            (image_id, settings_json, label, now),
        )
        await conn.commit()
        return {"settings": merged, "origin": origin, "updated_at": now}
    except Exception:
        await conn.rollback()
        raise
    finally:
        await connection.close_async(conn, db_path=_configured_db_path())


async def _reset_settings(image_id: int) -> dict[str, Any]:
    conn = await connection.open_async(_configured_db_path())
    try:
        await conn.execute("BEGIN")
        cursor = await conn.execute(
            "SELECT settings, origin, xmp_path, xmp_mtime FROM develop_settings WHERE image_id = ?",
            (image_id,),
        )
        current = await cursor.fetchone()
        if current is None:
            snapshot: dict[str, Any] = {}
            origin = "user"
            xmp_path = None
            xmp_mtime = None
        elif current["origin"] == "xmp":
            snapshot = _json_settings(current["settings"])
            origin = "xmp"
            xmp_path = current["xmp_path"]
            xmp_mtime = current["xmp_mtime"]
        else:
            cursor = await conn.execute(
                "SELECT settings FROM develop_history WHERE image_id = ? AND label = 'Import from XMP' ORDER BY id ASC LIMIT 1",
                (image_id,),
            )
            baseline = await cursor.fetchone()
            snapshot = _json_settings(baseline["settings"] if baseline else "{}")
            origin = "xmp" if baseline else "user"
            xmp_path = current["xmp_path"]
            xmp_mtime = current["xmp_mtime"]
        now = _now()
        encoded = json.dumps(snapshot, separators=(",", ":"))
        await conn.execute(
            """
            INSERT INTO develop_settings (image_id, settings, origin, xmp_path, xmp_mtime, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(image_id) DO UPDATE SET
                settings = excluded.settings, origin = excluded.origin,
                xmp_path = excluded.xmp_path, xmp_mtime = excluded.xmp_mtime,
                updated_at = excluded.updated_at
            """,
            (image_id, encoded, origin, xmp_path, xmp_mtime, now),
        )
        await conn.execute(
            "INSERT INTO develop_history (image_id, settings, label, created_at) VALUES (?, ?, ?, ?)",
            (image_id, encoded, "Reset", now),
        )
        await conn.commit()
        return {"settings": snapshot, "origin": origin, "updated_at": now}
    except Exception:
        await conn.rollback()
        raise
    finally:
        await connection.close_async(conn, db_path=_configured_db_path())


@router.get("/api/develop/{image_id}/base.bin")
async def api_develop_base_bin(image_id: int):
    image, error = await _image_or_error(image_id)
    if error:
        return error
    try:
        paths, _meta = await _ensure_base(image_id, image)
    except rawproc.RawDecodeError as exc:
        return JSONResponse({"error": str(exc)}, status_code=422)
    return FileResponse(
        paths.binary,
        media_type="application/octet-stream",
        headers={"Content-Encoding": "gzip", "Cache-Control": "public, max-age=31536000, immutable"},
    )


@router.get("/api/develop/{image_id}/base.jpg")
async def api_develop_base_jpg(image_id: int):
    image, error = await _image_or_error(image_id)
    if error:
        return error
    try:
        paths, _meta = await _ensure_base(image_id, image)
    except rawproc.RawDecodeError as exc:
        return JSONResponse({"error": str(exc)}, status_code=422)
    return FileResponse(paths.preview, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=31536000, immutable"})


@router.get("/api/develop/{image_id}")
async def api_get_develop(image_id: int):
    image, error = await _image_or_error(image_id)
    if error:
        return error
    try:
        _paths, meta = await _ensure_base(image_id, image)
    except rawproc.RawDecodeError as exc:
        return JSONResponse({"error": str(exc)}, status_code=422)
    row = await _load_settings(image_id)
    return {
        "settings": _json_settings(row["settings"]) if row else {},
        "origin": row["origin"] if row else "user",
        "meta": meta,
        "history": await _history(image_id),
    }


@router.put("/api/develop/{image_id}")
async def api_put_develop(image_id: int, body: DevelopSettingsBody):
    _image, error = await _image_or_error(image_id)
    if error:
        return error
    return await _upsert_settings(image_id, body.settings, body.label)


@router.post("/api/develop/{image_id}/reset")
async def api_reset_develop(image_id: int):
    _image, error = await _image_or_error(image_id)
    if error:
        return error
    return await _reset_settings(image_id)


async def _pregen(image_ids: list[int]) -> None:
    for image_id in image_ids:
        image, error = await _image_or_error(image_id)
        if error:
            continue
        try:
            await _ensure_base(image_id, image)
        except (rawproc.RawDecodeError, FileNotFoundError):
            continue


@router.post("/api/develop/pregen", status_code=202)
async def api_develop_pregen(body: DevelopPregenBody):
    image_ids = list(dict.fromkeys(image_id for image_id in body.image_ids if image_id > 0))
    task = asyncio.create_task(_pregen(image_ids))
    _pregen_tasks.add(task)
    task.add_done_callback(_pregen_tasks.discard)
    return {"queued": image_ids}


@router.post("/api/develop/{image_id}/export")
async def api_export_develop(image_id: int, body: DevelopExportBody):
    image, error = await _image_or_error(image_id)
    if error:
        return error
    if body.format not in {"jpeg", "tiff16"}:
        return JSONResponse({"error": "format must be jpeg or tiff16"}, status_code=400)
    # The RENDER lane owns the shared full-resolution pipeline. Keep this
    # endpoint's contract ready without duplicating color math in RAWPROC.
    try:
        from features.develop.render import RenderError, render_export_response
    except ImportError:
        return JSONResponse({"error": "Develop export renderer is not installed yet"}, status_code=503)
    row = await _load_settings(image_id)
    cached_meta = rawproc.read_base_metadata(image_id) or {}
    if not cached_meta.get("color"):
        try:
            _paths, cached_meta = await _ensure_base(image_id, image)
        except Exception:
            pass
    asshot = cached_meta.get("as_shot") if isinstance(cached_meta, dict) else {}
    try:
        return await render_export_response(
            image["filepath"],
            _json_settings(row["settings"]) if row else {},
            output_format=body.format,
            quality=body.quality,
            max_px=body.max_px,
            asshot_temperature=asshot.get("temperature") if isinstance(asshot, dict) else None,
            asshot_tint=asshot.get("tint") if isinstance(asshot, dict) else None,
            color_profile=cached_meta.get("color") if isinstance(cached_meta, dict) else None,
        )
    except (rawproc.RawDecodeError, RenderError, ValueError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=422)
