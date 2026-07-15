"""FIELD_SPEC_V2 oplog pull/push endpoints."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from features.sync import device_auth, oplog


router = APIRouter(tags=["sync"], dependencies=[Depends(device_auth.enforce_device_token)])
_db_path: Callable[[], str] | None = None


class OplogEntry(BaseModel):
    model_config = ConfigDict(extra="ignore")

    origin: str = Field(min_length=1, max_length=128)
    origin_seq: int = Field(gt=0)
    content_hash: str = Field(min_length=32, max_length=128)
    family: str = Field(min_length=1, max_length=64)
    payload: dict[str, Any] | list[Any]
    ts: float = Field(gt=0)
    applied_from: str | None = Field(default=None, max_length=128)


class OplogPullRequest(BaseModel):
    device_id: str = Field(min_length=1, max_length=128)
    cursors: dict[str, int] = Field(default_factory=dict, max_length=256)


class OplogPushRequest(BaseModel):
    device_id: str | None = Field(default=None, max_length=128)
    entries: list[OplogEntry] = Field(max_length=5000)


def configure(*, db_path: Callable[[], str]) -> None:
    global _db_path
    _db_path = db_path
    device_auth.configure(db_path=db_path)


def _configured_db_path() -> str:
    if _db_path is None:
        raise RuntimeError("oplog routes are not configured")
    return _db_path()


@router.post("/api/sync/oplog/pull")
async def api_sync_oplog_pull(body: OplogPullRequest):
    try:
        return await oplog.pull_entries(
            _configured_db_path(), device=body.device_id, cursors=body.cursors
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/sync/oplog/push")
async def api_sync_oplog_push(body: OplogPushRequest):
    try:
        return await oplog.apply_entries(
            _configured_db_path(),
            [entry.model_dump(exclude_none=True) for entry in body.entries],
            applied_from=body.device_id or "push",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
