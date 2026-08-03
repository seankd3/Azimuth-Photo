"""HTTP surface for in-app Cloud Backup (rclone vault sync)."""

from __future__ import annotations

from core.catalog_path import catalog_path

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from features.backup import cloud


router = APIRouter()
DbPathProvider = Callable[[], str]


class CloudBackupConfigBody(BaseModel):
    remote: str = ""
    dest_prefix: str = ""
    trees: list[str] = Field(default_factory=list)
    bwlimit: str = cloud.DEFAULT_BWLIMIT
    exclude_from_catalog: bool = True
    nightly_enabled: bool = False




@router.get("/api/backup/cloud/status")
async def api_cloud_backup_status() -> dict[str, Any]:
    return cloud.status_payload(db_path=catalog_path())




@router.get("/api/backup/cloud/config")
async def api_cloud_backup_get_config() -> dict[str, Any]:
    availability = cloud.feature_availability()
    return {
        "available": availability["available"],
        "reason": availability["reason"],
        "remotes": availability["remotes"],
        "config": cloud.config_from_settings(),
    }


@router.put("/api/backup/cloud/config")
async def api_cloud_backup_put_config(body: CloudBackupConfigBody):
    if not cloud.rclone_available():
        return JSONResponse(
            {
                "ok": False,
                "error": "rclone is not installed — Cloud Backup is unavailable",
                "available": False,
            },
            status_code=503,
        )
    try:
        saved = cloud.save_config(body.model_dump())
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    return {
        "ok": True,
        "config": saved,
        "available": True,
        "remotes": cloud.list_remotes(),
    }


@router.post("/api/backup/cloud/start")
async def api_cloud_backup_start():
    if not cloud.rclone_available():
        return JSONResponse(
            {
                "ok": False,
                "error": "rclone is not installed — Cloud Backup is unavailable",
                "available": False,
            },
            status_code=503,
        )
    try:
        payload = cloud.start_sync(catalog_path(), manual_override=True)
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    except RuntimeError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=409)
    return {"ok": True, **payload}


@router.post("/api/backup/cloud/stop")
async def api_cloud_backup_stop() -> dict[str, Any]:
    payload = cloud.stop_sync()
    return {"ok": True, **payload}
