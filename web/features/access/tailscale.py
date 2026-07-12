"""Tailscale probe + guided Serve apply for hub remote access.

States (DISTRIBUTION_SPEC / docs/FIELD_HTTPS.md):
  absent     — `tailscale` not on PATH
  logged-out — CLI present but BackendState is not Running / no node key
  up         — Running with a MagicDNS name (or Tailscale IPv4)

Serve is never applied automatically — only via ``apply_serve()`` on user click.
``PHOTOARCHIVE_TS_DRYRUN=1`` (and tests) skip the real CLI and return a dry-run result.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Callable, Sequence
from typing import Any


INSTALL_URL = "https://tailscale.com/download"
UP_COMMAND = "tailscale up"
SERVE_HTTPS_PORT = 8443
DEFAULT_LOCAL_TARGET = "http://127.0.0.1:8000"

Runner = Callable[[Sequence[str], float], subprocess.CompletedProcess[str]]


def _default_runner(argv: Sequence[str], timeout: float) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(argv),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout,
        check=False,
    )


_runner: Runner = _default_runner


def set_runner(runner: Runner | None) -> None:
    """Swap the subprocess runner (tests). Pass None to restore the default."""
    global _runner
    _runner = runner or _default_runner


def dry_run_enabled() -> bool:
    return os.environ.get("PHOTOARCHIVE_TS_DRYRUN", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def force_state() -> str | None:
    raw = os.environ.get("PHOTOARCHIVE_TS_FORCE_STATE", "").strip().lower()
    return raw if raw in {"absent", "logged-out", "up"} else None


def is_hub_mode() -> bool:
    """Hub mode only — Remote access panel stays hidden for standalone/satellite."""
    mode = os.environ.get("PHOTOARCHIVE_MODE", "").strip().lower()
    hub = os.environ.get("PHOTOARCHIVE_HUB_URL", "").strip()
    if hub:
        return False
    if mode in {"satellite", "standalone"}:
        return False
    return True


def serve_command(*, local_target: str | None = None) -> str:
    target = (local_target or DEFAULT_LOCAL_TARGET).rstrip("/")
    return f"sudo tailscale serve --bg --https={SERVE_HTTPS_PORT} {target}"


def serve_argv(*, local_target: str | None = None) -> list[str]:
    target = (local_target or DEFAULT_LOCAL_TARGET).rstrip("/")
    return ["tailscale", "serve", "--bg", f"--https={SERVE_HTTPS_PORT}", target]


def https_url_for(dns_name: str) -> str:
    name = (dns_name or "").strip().rstrip(".")
    if not name:
        return ""
    raw = os.getenv("PHOTOARCHIVE_HTTPS_PORT", str(SERVE_HTTPS_PORT)).strip()
    if raw in ("", "443"):
        return f"https://{name}"
    return f"https://{name}:{raw}"


def _parse_status_json(raw: str) -> dict[str, Any]:
    try:
        data = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _state_from_status(data: dict[str, Any], ip: str) -> tuple[str, str, str]:
    """Return (state, ip, dns_name) from ``tailscale status --json`` (+ optional ip)."""
    self_node = data.get("Self") if isinstance(data.get("Self"), dict) else {}
    dns_name = str(self_node.get("DNSName") or "").rstrip(".")
    backend = str(data.get("BackendState") or "").strip()
    have_key = bool(data.get("HaveNodeKey"))
    ips = data.get("TailscaleIPs") if isinstance(data.get("TailscaleIPs"), list) else []
    if not ip and ips:
        ip = str(ips[0] or "").strip()

    if backend == "Running" and have_key and (dns_name or ip):
        return "up", ip, dns_name
    return "logged-out", ip, dns_name


def probe(*, timeout: float = 2.0) -> dict[str, Any]:
    """Probe Tailscale CLI + status. Never mutates Serve config."""
    forced = force_state()
    install_url = INSTALL_URL
    up_command = UP_COMMAND
    command = serve_command()

    if forced == "absent":
        return {
            "state": "absent",
            "available": False,
            "ip": "",
            "dns_name": "",
            "install_url": install_url,
            "up_command": up_command,
            "serve_command": command,
            "https_url": "",
            "url": "",
            "dry_run": dry_run_enabled(),
            "error": "Tailscale is not installed",
        }
    if forced == "logged-out":
        return {
            "state": "logged-out",
            "available": True,
            "ip": "",
            "dns_name": "",
            "install_url": install_url,
            "up_command": up_command,
            "serve_command": command,
            "https_url": "",
            "url": "",
            "dry_run": dry_run_enabled(),
            "error": "Tailscale is installed but not logged in",
        }
    if forced == "up":
        dns = "photoarchive.example.ts.net"
        https = https_url_for(dns)
        return {
            "state": "up",
            "available": True,
            "ip": "100.64.0.1",
            "dns_name": dns,
            "install_url": install_url,
            "up_command": up_command,
            "serve_command": command,
            "https_url": https,
            "url": f"http://{dns}:8000",
            "dry_run": dry_run_enabled(),
            "error": "",
        }

    if shutil.which("tailscale") is None:
        return {
            "state": "absent",
            "available": False,
            "ip": "",
            "dns_name": "",
            "install_url": install_url,
            "up_command": up_command,
            "serve_command": command,
            "https_url": "",
            "url": "",
            "dry_run": dry_run_enabled(),
            "error": "Tailscale is not installed",
        }

    ip = ""
    try:
        ip_proc = _runner(["tailscale", "ip", "-4"], timeout)
        if ip_proc.returncode == 0 and ip_proc.stdout.strip():
            ip = ip_proc.stdout.strip().splitlines()[0].strip()
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        ip = ""

    try:
        status_proc = _runner(["tailscale", "status", "--json"], timeout)
    except FileNotFoundError:
        return {
            "state": "absent",
            "available": False,
            "ip": "",
            "dns_name": "",
            "install_url": install_url,
            "up_command": up_command,
            "serve_command": command,
            "https_url": "",
            "url": "",
            "dry_run": dry_run_enabled(),
            "error": "Tailscale is not installed",
        }
    except (subprocess.TimeoutExpired, OSError) as exc:
        return {
            "state": "logged-out",
            "available": True,
            "ip": ip,
            "dns_name": "",
            "install_url": install_url,
            "up_command": up_command,
            "serve_command": command,
            "https_url": "",
            "url": "",
            "dry_run": dry_run_enabled(),
            "error": str(exc) or "Tailscale status unavailable",
        }

    data = _parse_status_json(status_proc.stdout if status_proc.returncode == 0 else "")
    state, ip, dns_name = _state_from_status(data, ip)
    https = https_url_for(dns_name) if state == "up" else ""
    host = dns_name or ip
    http_url = f"http://{host}:8000" if host else ""
    error = ""
    if state == "logged-out":
        error = "Tailscale is installed but not logged in"
    elif status_proc.returncode != 0 and not data:
        error = (status_proc.stderr or "tailscale status failed").strip()
        state = "logged-out"

    return {
        "state": state,
        "available": state != "absent",
        "ip": ip,
        "dns_name": dns_name,
        "install_url": install_url,
        "up_command": up_command,
        "serve_command": command,
        "https_url": https,
        "url": http_url,
        "dry_run": dry_run_enabled(),
        "error": error,
    }


def apply_serve(
    *,
    local_target: str | None = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Run the FIELD_HTTPS Serve command. Never called implicitly."""
    status = probe(timeout=min(timeout, 2.0))
    command = serve_command(local_target=local_target)
    argv = serve_argv(local_target=local_target)

    if status["state"] != "up":
        return {
            "ok": False,
            "dry_run": dry_run_enabled(),
            "command": command,
            "https_url": "",
            "error": status.get("error") or f"Tailscale is {status['state']}",
            "tailscale": status,
        }

    https = status.get("https_url") or https_url_for(str(status.get("dns_name") or ""))

    if dry_run_enabled():
        return {
            "ok": True,
            "dry_run": True,
            "command": command,
            "https_url": https,
            "error": "",
            "tailscale": {**status, "https_url": https, "serve_applied": True},
        }

    try:
        result = _runner(argv, timeout)
    except FileNotFoundError:
        return {
            "ok": False,
            "dry_run": False,
            "command": command,
            "https_url": "",
            "error": "Tailscale is not installed",
            "tailscale": {**status, "state": "absent"},
        }
    except (subprocess.TimeoutExpired, OSError) as exc:
        return {
            "ok": False,
            "dry_run": False,
            "command": command,
            "https_url": "",
            "error": str(exc) or "tailscale serve failed",
            "tailscale": status,
        }

    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "tailscale serve failed").strip()
        return {
            "ok": False,
            "dry_run": False,
            "command": command,
            "https_url": "",
            "error": detail,
            "tailscale": status,
        }

    return {
        "ok": True,
        "dry_run": False,
        "command": command,
        "https_url": https,
        "error": "",
        "tailscale": {**status, "https_url": https, "serve_applied": True},
    }
