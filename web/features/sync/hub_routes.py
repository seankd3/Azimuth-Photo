"""FastAPI routes for the hub side of FIELD_SPEC synchronization."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from core.background import track_background_task
from core.requests import RequestBodyTooLarge, read_body_limited
from core.source_files import source_file_is_safe
from data.repositories import images as image_repository
from features.sync import device_auth, embedding_sync, hub, mirror_export
from features.system import client_bundle


router = APIRouter(tags=["sync"], dependencies=[Depends(device_auth.enforce_device_token)])
_db_path: Callable[[], str] | None = None
_intake_root: Callable[[], Path] = hub.default_intake_root
_raws_root: Callable[[], Path] | None = None
_backfill_status: dict[str, Any] = {"state": "idle", "counts": {}, "error": ""}
_backfill_task: asyncio.Task | None = None


class ManifestItem(BaseModel):
    content_hash: str = Field(min_length=32, max_length=128)
    full_hash: str | None = Field(default=None, min_length=32, max_length=128)
    bytes: int = Field(ge=0)
    filename: str = Field(min_length=1, max_length=255)
    date_taken: str | None = Field(default=None, max_length=64)
    folder: str | None = Field(default=None, max_length=64)


class ManifestRequest(BaseModel):
    items: list[ManifestItem] = Field(max_length=5000)


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
    items: list[MetadataItem] = Field(max_length=5000)


class HaveRequest(BaseModel):
    content_hashes: list[str] = Field(max_length=1000)


class FullProofItem(BaseModel):
    content_hash: str = Field(min_length=32, max_length=128)
    full_hash: str = Field(min_length=32, max_length=128)


class FullProofRequest(BaseModel):
    items: list[FullProofItem] = Field(max_length=1000)


def configure(
    *,
    db_path: Callable[[], str],
    intake_root: Callable[[], str | Path] | None = None,
    raws_root: Callable[[], str | Path] | None = None,
) -> None:
    global _db_path, _intake_root, _raws_root
    _db_path = db_path
    device_auth.configure(db_path=db_path)
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
        return await hub.manifest(
            _configured_db_path(),
            [item.model_dump() for item in body.items],
            intake_root=_configured_intake_root(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/sync/have")
async def api_sync_have(body: HaveRequest):
    try:
        present = await hub.have_content_hashes(_configured_db_path(), body.content_hashes)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"present": present}


@router.post("/api/sync/have/full")
async def api_sync_have_full(body: FullProofRequest):
    try:
        present = await hub.have_full_hashes(
            _configured_db_path(),
            [item.model_dump() for item in body.items],
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"present": present}


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
    try:
        chunk = await read_body_limited(request, hub.MAX_CHUNK_BYTES)
    except RequestBodyTooLarge:
        return JSONResponse({"error": "Upload chunk is too large"}, status_code=413)
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
        media_type="multipart/mixed; boundary=azimuth-pabase1",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


@router.get("/api/sync/original/{image_id}")
async def api_sync_original(image_id: int):
    image = await image_repository.get_media_image_by_id(_configured_db_path(), image_id)
    if (
        image is None
        or int(image["hub_remote"] or 0) == 1
        or image["missing_at"] is not None
        or not await asyncio.to_thread(
            source_file_is_safe,
            str(image["filepath"] or ""),
            str(image["source_path"] or ""),
        )
    ):
        raise HTTPException(status_code=404, detail="Original unavailable")
    return FileResponse(str(image["filepath"]), filename=str(image["filename"] or f"photo-{image_id}"))


@router.get("/api/sync/catalog/export")
async def api_sync_catalog_export(cursor: int = 0, limit: int = 0):
    try:
        parsed_cursor = mirror_export.parse_cursor(cursor)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    limit = max(0, min(int(limit or 0), 20000))
    return StreamingResponse(
        mirror_export.gzip_catalog_export_stream(_configured_db_path(), parsed_cursor, limit),
        media_type="application/x-ndjson",
        headers={"Content-Encoding": "gzip", "Cache-Control": "no-store"},
    )


@router.get("/api/sync/embeddings/pack")
async def api_sync_embedding_pack(cursor: int = 0, limit: int = 0):
    """Semantic vectors, so a satellite can search without asking the hub."""

    body = await embedding_sync.export_page(
        _configured_db_path(),
        cursor=max(0, int(cursor or 0)),
        limit=int(limit or embedding_sync.PAGE_LIMIT),
    )
    return Response(
        content=body,
        media_type="application/x-ndjson",
        headers={"Content-Encoding": "gzip", "Cache-Control": "no-store"},
    )


@router.get("/api/sync/thumbs/pack")
async def api_sync_thumb_pack(
    size: str, after_id: int = 0, limit: int = 500, order: str = "asc"
):
    try:
        size, after_id, limit, order = mirror_export.validate_thumb_pack_request(
            size, after_id, limit, order
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return StreamingResponse(
        mirror_export.thumbnail_pack_stream(
            _configured_db_path(),
            size=size,
            after_id=after_id,
            limit=limit,
            order=order,
        ),
        media_type="application/x-tar",
        headers={"Cache-Control": "no-store"},
    )


@router.post("/api/sync/hash-backfill")
async def api_sync_hash_backfill():
    global _backfill_task
    if _backfill_task and not _backfill_task.done():
        return {"ok": True, "started": False, "backfill": dict(_backfill_status)}
    _backfill_status.clear()
    _backfill_status.update(state="queued", counts={}, error="")
    _backfill_task = track_background_task(hub.run_hash_backfill(_configured_db_path(), _backfill_status))
    return {"ok": True, "started": True, "backfill": dict(_backfill_status)}


@router.get("/api/client/bundle")
async def api_client_bundle():
    """Serve the hub's cached git-archive tar.gz (same device auth as /api/sync/*)."""

    identity = client_bundle.get_hub_client_identity()
    if identity is None:
        try:
            identity = client_bundle.init_hub_client_identity()
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
    path = client_bundle.cached_bundle_path()
    if path is None:
        raise HTTPException(status_code=503, detail="client bundle is not available")
    return FileResponse(
        path,
        media_type="application/gzip",
        filename=f"azimuth-{identity.sha[:12]}.tar.gz",
        headers={
            "Cache-Control": "no-store",
            "X-Content-SHA256": identity.bundle_sha256,
            "X-Content-SHA": identity.sha,
        },
    )
