import json
import os
import subprocess

from fastapi import APIRouter, Request


router = APIRouter()


def _tailscale_status() -> dict:
    try:
        ip_proc = subprocess.run(
            ["tailscale", "ip", "-4"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=2,
            check=False,
        )
        status_proc = subprocess.run(
            ["tailscale", "status", "--json"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=2,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return {"available": False, "error": "Tailscale is unavailable"}

    ip = ip_proc.stdout.strip().splitlines()[0] if ip_proc.returncode == 0 and ip_proc.stdout.strip() else ""
    dns_name = ""
    if status_proc.returncode == 0 and status_proc.stdout.strip():
        try:
            data = json.loads(status_proc.stdout)
            dns_name = ((data.get("Self") or {}).get("DNSName") or "").rstrip(".")
        except json.JSONDecodeError:
            dns_name = ""
    return {"available": bool(ip or dns_name), "ip": ip, "dns_name": dns_name}


def _server_port(request: Request) -> int:
    try:
        return int(os.getenv("PHOTOARCHIVE_PORT") or 0) or int(request.url.port or 8000)
    except (TypeError, ValueError):
        return int(request.url.port or 8000)


def _https_serve_url(dns_name: str) -> str:
    """HTTPS origin for PWA/SW via Tailscale Serve (see docs/FIELD_HTTPS.md).

    PHOTOARCHIVE_HTTPS_PORT defaults to 8443 (tailnet-only serve). Set to 443
    or empty to omit the port. Never points at the public Funnel on :443.
    """
    name = (dns_name or "").strip().rstrip(".")
    if not name:
        return ""
    raw = os.getenv("PHOTOARCHIVE_HTTPS_PORT", "8443").strip()
    if raw in ("", "443"):
        return f"https://{name}"
    return f"https://{name}:{raw}"


@router.get("/api/remote-access")
async def api_remote_access(request: Request):
    tailscale = _tailscale_status()
    port = _server_port(request)
    host = tailscale.get("dns_name") or tailscale.get("ip") or ""
    url = f"http://{host}:{port}" if host else ""
    https_url = _https_serve_url(str(tailscale.get("dns_name") or ""))
    return {
        "access_mode": os.getenv("PHOTOARCHIVE_ACCESS", "local"),
        "current_url": str(request.base_url).rstrip("/"),
        "tailscale": {**tailscale, "url": url, "https_url": https_url},
    }
