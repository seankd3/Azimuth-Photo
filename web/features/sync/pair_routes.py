"""Pairing, devices, and discovery HTTP routes."""

from __future__ import annotations

import asyncio
import os
import platform as py_platform
import socket
import time
from collections.abc import Callable
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request as UrlRequest, urlopen

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

import settings
from features.sync import device_auth, mdns, pairing, satellite


router = APIRouter(tags=["pairing"])
_db_path: Callable[[], str] | None = None

# Redeem stays public by necessity, so failures share one throttled bucket
# (same budget as share unlock).
REDEEM_FAILURE_LIMIT = 5
REDEEM_FAILURE_WINDOW_SECONDS = 15 * 60
_redeem_failures = {"count": 0, "first_at": 0.0}


def _redeem_retry_after(now: float | None = None) -> int | None:
    now = time.time() if now is None else now
    if _redeem_failures["count"] < REDEEM_FAILURE_LIMIT:
        return None
    remaining = _redeem_failures["first_at"] + REDEEM_FAILURE_WINDOW_SECONDS - now
    if remaining <= 0:
        reset_redeem_throttle_for_tests()
        return None
    return int(remaining) + 1


def _record_redeem_failure(now: float | None = None) -> None:
    now = time.time() if now is None else now
    if not _redeem_failures["count"] or now >= _redeem_failures["first_at"] + REDEEM_FAILURE_WINDOW_SECONDS:
        _redeem_failures.update(count=1, first_at=now)
    else:
        _redeem_failures["count"] += 1


def reset_redeem_throttle_for_tests() -> None:
    _redeem_failures.update(count=0, first_at=0.0)


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


def _redeem_pair_request(req: UrlRequest) -> tuple[int, bytes]:
    """Complete the blocking urllib exchange entirely outside the event loop."""

    try:
        with urlopen(req, timeout=5) as response:  # noqa: S310 - user-supplied hub URL
            return response.status, response.read()
    except HTTPError as exc:
        return exc.code, exc.read()


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
    retry_after = _redeem_retry_after()
    if retry_after is not None:
        raise HTTPException(
            status_code=429,
            detail="Too many pairing attempts",
            headers={"Retry-After": str(retry_after)},
        )
    try:
        return await pairing.pair_device(
            _configured_db_path(),
            code=body.code,
            device_name=body.device_name,
            platform=body.platform,
        )
    except LookupError as exc:
        _record_redeem_failure()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        _record_redeem_failure()
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

    req = UrlRequest(
        f"{hub}/api/pair",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            **satellite.hub_request_headers(),
        },
        method="POST",
    )
    try:
        status, raw = await asyncio.to_thread(_redeem_pair_request, req)
    except URLError as exc:
        raise HTTPException(status_code=400, detail=f"could not reach hub: {exc.reason}") from exc
    if not 200 <= status < 300:
        detail = raw.decode("utf-8", errors="replace")[:300]
        raise HTTPException(status_code=400, detail=f"hub pair failed ({status}): {detail}")
    result = json.loads(raw.decode("utf-8"))
    token = str(result.get("device_token") or "").strip()
    hub_id = str(result.get("hub_id") or "").strip()
    if not token:
        raise HTTPException(status_code=400, detail="hub did not return a device_token")
    attach = await satellite.attach_hub(hub, token, hub_id=hub_id)
    saved = settings.get_settings()
    return {
        "ok": True,
        "sync_started": bool(attach.get("sync_started")),
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
