"""Pairing, devices, and discovery HTTP routes."""

from __future__ import annotations

import os
import platform as py_platform
import socket
from collections.abc import Callable
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

import settings
from features.sync import device_auth, mdns, pairing, satellite


router = APIRouter(tags=["pairing"])
_db_path: Callable[[], str] | None = None


class PairRequest(BaseModel):
    code: str = Field(min_length=4, max_length=16)
    device_name: str = Field(default="Device", max_length=120)
    platform: str = Field(default="", max_length=64)


class ConnectRequest(BaseModel):
    hub_url: str = Field(min_length=1, max_length=512)
    code: str = Field(min_length=4, max_length=16)
    device_name: str = Field(default="", max_length=120)
    platform: str = Field(default="", max_length=64)


def configure(*, db_path: Callable[[], str]) -> None:
    global _db_path
    _db_path = db_path
    device_auth.configure(db_path=db_path)


def _configured_db_path() -> str:
    if _db_path is None:
        raise RuntimeError("pairing routes are not configured")
    return _db_path()


def _hub_public_url(request: Request) -> str:
    configured = (
        os.environ.get("PHOTOARCHIVE_PUBLIC_URL")
        or os.environ.get("PHOTOARCHIVE_HUB_PUBLIC_URL")
        or ""
    ).strip().rstrip("/")
    if configured:
        return configured
    return str(request.base_url).rstrip("/")


def _default_device_name() -> str:
    try:
        return socket.gethostname() or "Device"
    except OSError:
        return "Device"


@router.post("/api/devices/link")
async def api_devices_link(request: Request):
    """Hub: mint a one-time pair code + QR for the Devices panel."""
    if not mdns.is_hub_mode() and os.environ.get("PHOTOARCHIVE_MODE", "").strip().lower() == "satellite":
        # Allow link codes on any instance that owns a catalog; satellite can still
        # act as a temporary hub in tests. Prefer hub mode in production.
        pass
    hub_url = _hub_public_url(request)
    try:
        return pairing.create_pair_code(hub_url=hub_url)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/api/pair")
async def api_pair(body: PairRequest):
    try:
        return await pairing.pair_device(
            _configured_db_path(),
            code=body.code,
            device_name=body.device_name,
            platform=body.platform,
        )
    except LookupError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/api/devices")
async def api_list_devices():
    hub_id = await pairing.get_hub_id(_configured_db_path())
    devices = await pairing.list_devices(_configured_db_path())
    return {
        "hub_id": hub_id,
        "devices": devices,
        "require_device_token": bool(settings.get_settings().get("require_device_token")),
    }


@router.post("/api/devices/{device_id}/revoke")
async def api_revoke_device(device_id: int):
    try:
        return await pairing.revoke_device(_configured_db_path(), device_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/api/discover")
async def api_discover():
    hubs = await mdns.browse_hubs(timeout_seconds=2.0)
    return {"hubs": hubs, "zeroconf": mdns.zeroconf_available(), "disabled": mdns.mdns_disabled()}


@router.post("/api/pair/connect")
async def api_pair_connect(body: ConnectRequest):
    """Satellite/standalone: redeem a hub pair code and persist hub credentials."""
    hub = body.hub_url.strip().rstrip("/")
    if not hub.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="hub_url must be an http(s) URL")
    device_name = body.device_name.strip() or _default_device_name()
    plat = body.platform.strip() or f"{py_platform.system()}".strip() or "desktop"
    payload = {
        "code": body.code.strip().upper(),
        "device_name": device_name,
        "platform": plat,
    }
    import json

    req = Request(
        f"{hub}/api/pair",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urlopen(req, timeout=10) as response:  # noqa: S310 - user-supplied hub URL
            raw = response.read()
            status = response.status
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        raise HTTPException(status_code=400, detail=f"hub pair failed ({exc.code}): {detail}") from exc
    except URLError as exc:
        raise HTTPException(status_code=400, detail=f"could not reach hub: {exc.reason}") from exc
    if not 200 <= status < 300:
        raise HTTPException(status_code=400, detail=f"hub pair failed ({status})")
    result = json.loads(raw.decode("utf-8"))
    token = str(result.get("device_token") or "").strip()
    hub_id = str(result.get("hub_id") or "").strip()
    if not token:
        raise HTTPException(status_code=400, detail="hub did not return a device_token")
    current = settings.get_settings()
    current["hub_url"] = hub
    current["device_token"] = token
    current["paired_hub_id"] = hub_id
    saved = settings.save_settings(current)
    return {
        "ok": True,
        "hub_url": hub,
        "hub_id": hub_id,
        "device_name": device_name,
        "platform": plat,
        "has_hub": satellite.has_hub(),
        "settings": {
            "hub_url": saved.get("hub_url"),
            "paired_hub_id": saved.get("paired_hub_id"),
            "has_device_token": bool(saved.get("device_token")),
        },
    }


@router.get("/api/pair/status")
async def api_pair_status() -> dict[str, Any]:
    cfg = settings.get_settings()
    return {
        "mode": "satellite" if satellite.is_satellite_mode() else ("hub" if mdns.is_hub_mode() else "standalone"),
        "has_hub": satellite.has_hub(),
        "hub_url": satellite.hub_url(),
        "paired_hub_id": str(cfg.get("paired_hub_id") or ""),
        "has_device_token": bool(str(cfg.get("device_token") or "").strip()),
        "require_device_token": bool(cfg.get("require_device_token")),
        "zeroconf": mdns.zeroconf_available(),
    }
