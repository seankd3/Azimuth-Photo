"""HTTP surface for catalog backups and integrity audits."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from features.system import backups


router = APIRouter()
DbPathProvider = Callable[[], str]
_db_path: DbPathProvider | None = None


class RestoreBody(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class IntegrityScanBody(BaseModel):
    limit: int | None = Field(default=None, ge=1, le=10_000)


def configure(*, db_path: DbPathProvider) -> None:
    global _db_path
    _db_path = db_path


def _configured_db_path() -> str:
    if _db_path is None:
        raise RuntimeError("System backup routes are not configured")
    return _db_path()


@router.post("/api/system/backup/now")
async def api_backup_now() -> dict[str, Any]:
    result = await asyncio.to_thread(backups.create_snapshot, _configured_db_path())
    return result


@router.get("/api/system/backup/list")
async def api_backup_list() -> dict[str, Any]:
    items = await asyncio.to_thread(backups.list_backups)
    return {"backups": items, "count": len(items)}


@router.post("/api/system/backup/restore")
async def api_backup_restore(body: RestoreBody):
    try:
        result = await asyncio.to_thread(backups.restore_backup, _configured_db_path(), body.name)
    except FileNotFoundError as exc:
        return JSONResponse({"error": str(exc)}, status_code=404)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return result


@router.post("/api/system/integrity/scan")
async def api_integrity_scan(body: IntegrityScanBody | None = None):
    limit = 50 if body is None or body.limit is None else int(body.limit)
    result = await asyncio.to_thread(
        backups.begin_integrity_scan,
        _configured_db_path(),
        limit=limit,
    )
    if not result.get("ok"):
        return JSONResponse(result, status_code=409)
    return result


@router.get("/api/system/integrity/status")
async def api_integrity_status() -> dict[str, Any]:
    return await asyncio.to_thread(backups.integrity_summary, _configured_db_path())
