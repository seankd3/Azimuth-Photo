"""Satellite HTTP surface for the Lightroom peer plugin."""

from __future__ import annotations

from typing import Any

import db
from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from features.sync import elo_stars, export_relation, lr_bridge, lr_connect, lr_status, shoot_rank

router = APIRouter(tags=["lr-bridge"])


class LrDeltaItem(BaseModel):
    filepath: str = Field(min_length=1)
    family: str
    value: Any = None
    observed_at: float | str | None = None


class LrDeltasBody(BaseModel):
    items: list[LrDeltaItem] = Field(default_factory=list)


class LrExportBody(BaseModel):
    source_filepath: str | None = None
    export_filepath: str | None = None
    source_image_id: int | None = None
    export_image_id: int | None = None


class LrConnectBody(BaseModel):
    satellite_url: str | None = None


def _request_satellite_url(request: Request, override: str | None = None) -> str:
    if override and str(override).strip():
        return str(override).strip().rstrip("/")
    # Prefer the URL the desktop itself is using (loopback for local satellite).
    port = request.url.port or (443 if request.url.scheme == "https" else 80)
    host = request.url.hostname or "127.0.0.1"
    if host in {"0.0.0.0", "::", "[::]"}:
        host = "127.0.0.1"
    if (request.url.scheme == "http" and port == 80) or (request.url.scheme == "https" and port == 443):
        return f"{request.url.scheme}://{host}"
    return f"{request.url.scheme}://{host}:{port}"


@router.get("/api/lr/connect")
async def get_lr_connect(request: Request):
    """Detect LR + plugin install state for the one-click Connect button."""

    status = lr_connect.connect_status(satellite_url=_request_satellite_url(request))
    return status


@router.post("/api/lr/connect")
async def post_lr_connect(request: Request, body: LrConnectBody | None = None):
    """Install the plugin into LR Modules and write satellite URL config."""

    url = _request_satellite_url(request, (body.satellite_url if body else None))
    result = lr_connect.connect_plugin(satellite_url=url)
    status = 200 if result.get("ok") else 400
    return JSONResponse(result, status_code=status)


@router.delete("/api/lr/connect")
async def delete_lr_connect():
    """Remove the installed plugin. Idempotent."""

    return lr_connect.disconnect_plugin()


@router.post("/api/lr/deltas")
async def post_lr_deltas(body: LrDeltasBody):
    """Inbound LR flag/lr_rating batch → family-clock apply with origin lr."""

    result = await lr_bridge.apply_inbound_deltas(
        db.DB_PATH,
        [item.model_dump() for item in body.items],
    )
    lr_status.note_delta_exchange(direction="in")
    return result


@router.get("/api/lr/deltas")
async def get_lr_deltas(
    since: float = Query(default=0.0),
    limit: int = Query(default=500, ge=1, le=2000),
):
    """Outbound stream: non-LR flag winners + elo_stars projections."""

    flags = await lr_bridge.outbound_flag_deltas(db.DB_PATH, since=since, limit=limit)
    remaining = max(0, limit - len(flags))
    stars = (
        await elo_stars.outbound_elo_star_deltas(db.DB_PATH, since=since, limit=remaining)
        if remaining
        else []
    )
    items = [*flags, *stars]
    clock = since
    for item in items:
        clock = max(clock, float(item.get("ts") or 0.0))
    lr_status.note_delta_exchange(direction="out")
    # Per-shoot local ladder for Best-of collections + contextual whisper.
    # Full snapshot each poll (idempotent membership); cheap ranked-only query.
    shoot_context = await shoot_rank.shoot_rank_payload(db.DB_PATH)
    return {
        "since": since,
        "clock": clock,
        "items": items,
        "count": len(items),
        "shoot_context": shoot_context,
    }


@router.post("/api/lr/exports")
async def post_lr_export(body: LrExportBody):
    """Plugin-observed export relationship → version-stack export_of."""

    if not any(
        (
            body.source_filepath,
            body.export_filepath,
            body.source_image_id,
            body.export_image_id,
        )
    ):
        return JSONResponse({"error": "source and export identity required"}, status_code=400)
    result = await export_relation.link_export(
        db.DB_PATH,
        source_filepath=body.source_filepath,
        export_filepath=body.export_filepath,
        source_image_id=body.source_image_id,
        export_image_id=body.export_image_id,
    )
    if not result.get("linked") and result.get("reason") == "unresolved":
        # Fallback matcher when only the export side is known.
        if body.export_image_id or body.export_filepath:
            export_id = body.export_image_id
            if not export_id and body.export_filepath:
                identity = await lr_bridge.resolve_filepath(db.DB_PATH, body.export_filepath)
                export_id = identity["image_id"] if identity else None
            if export_id:
                result = await export_relation.ensure_export_link(db.DB_PATH, int(export_id))
    status = 200 if result.get("linked") else 404
    return JSONResponse(result, status_code=status)


@router.get("/api/lr/relation/{image_id}")
async def get_lr_relation(image_id: int):
    """export_of payload for either the RAW or the export."""

    relation = await export_relation.export_of_for_image(db.DB_PATH, int(image_id))
    if relation is None:
        return JSONResponse({"export_of": None, "id": image_id}, status_code=200)
    return {"id": image_id, "export_of": relation}
