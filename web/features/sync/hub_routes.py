"""FastAPI routes for the hub side of FIELD_SPEC synchronization."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from features.sync import hub


router = APIRouter(tags=["sync"])
_db_path: Callable[[], str] | None = None
_intake_root: Callable[[], Path] = hub.default_intake_root
_raws_root: Callable[[], Path] | None = None
_backfill_status: dict[str, Any] = {"state": "idle", "counts": {}, "error": ""}
_backfill_task: asyncio.Task | None = None


class ManifestItem(BaseModel):
    content_hash: str
    full_hash: str | None = None
    bytes: int = Field(ge=0)
    filename: str
    date_taken: str | None = None


class ManifestRequest(BaseModel):
    items: list[ManifestItem]


class MetadataItem(BaseModel):
    model_config = ConfigDict(extra="allow")

    content_hash: str
    flag: str | None = None
    rating: int | float | None = None
    develop_settings: dict[str, Any] | None = None
    develop_updated_at: str | None = None
    keywords: list[str] | None = None
    iptc: dict[str, Any] | None = None


class MetadataRequest(BaseModel):
    items: list[MetadataItem]


def configure(
    *,
    db_path: Callable[[], str],
    intake_root: Callable[[], str | Path] | None = None,
    raws_root: Callable[[], str | Path] | None = None,
) -> None:
    global _db_path, _intake_root, _raws_root
    _db_path = db_path
    if intake_root is not None:
        _intake_root = lambda: Path(intake_root())
    if raws_root is not None:
        _raws_root = lambda: Path(raws_root())


def _configured_db_path() -> str:
    if _db_path is None:
        raise RuntimeError("hub sync routes are not configured")
    return _db_path()


def _configured_intake_root() -> Path:
    return Path(_intake_root())


def _configured_raws_root() -> Path:
    return Path(_raws_root()) if _raws_root else hub.default_raws_root(_configured_intake_root())


@router.post("/api/sync/manifest")
async def api_sync_manifest(body: ManifestRequest):
    try:
        return await hub.manifest(_configured_db_path(), [item.model_dump() for item in body.items])
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/api/sync/upload/{content_hash}/status")
async def api_sync_upload_status(content_hash: str):
    try:
        return await hub.upload_status(_configured_intake_root(), content_hash)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/sync/upload/{content_hash}")
async def api_sync_upload(
    content_hash: str,
    request: Request,
    x_offset: int = Header(alias="X-Offset"),
    x_total_bytes: int = Header(alias="X-Total-Bytes"),
):
    chunk = await request.body()
    try:
        return await hub.append_upload_chunk(
            _configured_db_path(),
            _configured_intake_root(),
            _configured_raws_root(),
            content_hash,
            offset=x_offset,
            total_bytes=x_total_bytes,
            chunk=chunk,
        )
    except FileExistsError as exc:
        return JSONResponse({"error": "offset-mismatch", "offset": int(str(exc))}, status_code=409)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ArithmeticError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/sync/metadata")
async def api_sync_metadata(body: MetadataRequest):
    try:
        return await hub.merge_metadata(
            _configured_db_path(), [item.model_dump(exclude_none=True) for item in body.items]
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/api/sync/base/{content_hash}")
async def api_sync_base(content_hash: str):
    try:
        paths, _meta = await hub.base_artifacts(_configured_db_path(), content_hash)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except hub.rawproc.RawDecodeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return StreamingResponse(
        hub.multipart_base_stream(paths),
        media_type="multipart/mixed; boundary=photoarchive-pabase1",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


@router.post("/api/sync/hash-backfill")
async def api_sync_hash_backfill():
    global _backfill_task
    if _backfill_task and not _backfill_task.done():
        return {"ok": True, "started": False, "backfill": dict(_backfill_status)}
    _backfill_status.clear()
    _backfill_status.update(state="queued", counts={}, error="")
    _backfill_task = asyncio.create_task(hub.run_hash_backfill(_configured_db_path(), _backfill_status))
    return {"ok": True, "started": True, "backfill": dict(_backfill_status)}
