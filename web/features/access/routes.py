"""Remote access API — Tailscale probe + guided Serve apply (hub mode)."""

from __future__ import annotations

import os

from fastapi import APIRouter, Request

from features.access import tailscale as ts


router = APIRouter()


def _server_port(request: Request) -> int:
    try:
        return int(os.getenv("PHOTOARCHIVE_PORT") or 0) or int(request.url.port or 8000)
    except (TypeError, ValueError):
        return int(request.url.port or 8000)


def _local_serve_target(request: Request) -> str:
    """Local origin Tailscale Serve should proxy to (loopback + app port)."""
    port = _server_port(request)
    return f"http://127.0.0.1:{port}"


def _enrich_urls(probe: dict, request: Request) -> dict:
    port = _server_port(request)
    host = probe.get("dns_name") or probe.get("ip") or ""
    http_url = f"http://{host}:{port}" if host else ""
    https = probe.get("https_url") or ""
    if not https and probe.get("dns_name"):
        https = ts.https_url_for(str(probe.get("dns_name") or ""))
    return {
        **probe,
        "url": http_url or probe.get("url") or "",
        "https_url": https,
        "serve_command": ts.serve_command(local_target=_local_serve_target(request)),
    }


@router.get("/api/remote-access")
async def api_remote_access(request: Request):
    hub_mode = ts.is_hub_mode()
    probe = _enrich_urls(ts.probe(), request) if hub_mode else {
        "state": "absent",
        "available": False,
        "ip": "",
        "dns_name": "",
        "install_url": ts.INSTALL_URL,
        "up_command": ts.UP_COMMAND,
        "serve_command": ts.serve_command(local_target=_local_serve_target(request)),
        "https_url": "",
        "url": "",
        "dry_run": ts.dry_run_enabled(),
        "error": "Remote access is available in hub mode only",
    }
    return {
        "access_mode": os.getenv("PHOTOARCHIVE_ACCESS", "local"),
        "current_url": str(request.base_url).rstrip("/"),
        "hub_mode": hub_mode,
        "mode": "hub" if hub_mode else (
            "satellite" if os.environ.get("PHOTOARCHIVE_HUB_URL", "").strip() else "standalone"
        ),
        "tailscale": probe,
    }


@router.post("/api/remote-access/serve")
async def api_remote_access_serve(request: Request):
    """Apply ``tailscale serve`` from FIELD_HTTPS.md — user-click only."""
    if not ts.is_hub_mode():
        return {
            "ok": False,
            "dry_run": ts.dry_run_enabled(),
            "command": ts.serve_command(local_target=_local_serve_target(request)),
            "https_url": "",
            "error": "Remote access Serve apply is hub mode only",
            "hub_mode": False,
        }
    result = ts.apply_serve(local_target=_local_serve_target(request))
    if result.get("tailscale"):
        result["tailscale"] = _enrich_urls(result["tailscale"], request)
        if result.get("https_url"):
            result["tailscale"]["https_url"] = result["https_url"]
    result["hub_mode"] = True
    return result
