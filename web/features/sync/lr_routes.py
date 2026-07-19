"""Satellite HTTP surface for the Lightroom peer plugin."""

from __future__ import annotations

from typing import Any

import db
from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from features.sync import elo_stars, export_relation, lr_bridge

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


@router.post("/api/lr/deltas")
async def post_lr_deltas(body: LrDeltasBody):
    """Inbound LR flag/lr_rating batch → family-clock apply with origin lr."""

    result = await lr_bridge.apply_inbound_deltas(
        db.DB_PATH,
        [item.model_dump() for item in body.items],
    )
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
    return {"since": since, "clock": clock, "items": items, "count": len(items)}


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
