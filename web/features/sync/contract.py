"""Versioned satellite-to-hub contract probing and safety gates."""

from __future__ import annotations

import asyncio
import json
import time
import urllib.error
import urllib.request
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from fastapi import Request

from core.version import API_REV


API_REV_HEADER = "X-PA-Api-Rev"
PROBE_INTERVAL_SECONDS = 10 * 60
RequestFn = Callable[..., Awaitable[tuple[int, dict, bytes]]]


@dataclass(frozen=True)
class HubContract:
    reachable: bool
    api_rev: int | None
    app_version: str | None
    capabilities: frozenset[str]
    checked_at: float | None
    sha: str | None = None
    bundle_sha256: str | None = None
    schema_version: int | None = None


_contracts: dict[str, HubContract] = {}


def request_headers() -> dict[str, str]:
    """Headers every satellite-originated hub request must carry."""

    return {API_REV_HEADER: str(API_REV)}


async def _request_version(method: str, url: str, *, body=None, headers=None) -> tuple[int, dict, bytes]:
    def request() -> tuple[int, dict, bytes]:
        outbound_headers = {**request_headers(), **dict(headers or {})}
        req = urllib.request.Request(url, data=body, headers=outbound_headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=3) as response:  # noqa: S310 - configured tailnet hub.
                return int(response.status), dict(response.headers), response.read()
        except urllib.error.HTTPError as error:
            return int(error.code), dict(error.headers or {}), error.read()

    return await asyncio.to_thread(request)


def _legacy_contract(*, checked_at: float) -> HubContract:
    """A reachable peer without this endpoint is too old for new contracts."""

    return HubContract(True, None, None, frozenset(), checked_at)


def _parse_contract(payload: object, *, checked_at: float) -> HubContract:
    if not isinstance(payload, dict):
        return _legacy_contract(checked_at=checked_at)
    api_rev = payload.get("api_rev")
    app_version = payload.get("app_version")
    capabilities = payload.get("capabilities")
    if not isinstance(api_rev, int) or isinstance(api_rev, bool) or not isinstance(capabilities, list):
        return _legacy_contract(checked_at=checked_at)
    sha = payload.get("sha")
    bundle_sha256 = payload.get("bundle_sha256")
    schema_version = payload.get("schema_version")
    return HubContract(
        reachable=True,
        api_rev=api_rev,
        app_version=str(app_version) if app_version else None,
        capabilities=frozenset(str(value) for value in capabilities if isinstance(value, str)),
        checked_at=checked_at,
        sha=str(sha) if sha else None,
        bundle_sha256=str(bundle_sha256) if bundle_sha256 else None,
        schema_version=int(schema_version) if isinstance(schema_version, int) and not isinstance(schema_version, bool) else None,
    )


async def refresh_hub_contract(
    hub: str,
    *,
    request: RequestFn | None = None,
    force: bool = False,
) -> HubContract:
    """Probe the hub at most once per ten minutes unless a caller asks now."""

    clean_hub = hub.rstrip("/")
    current = _contracts.get(clean_hub)
    now = time.time()
    if current and not force and current.checked_at is not None and now - current.checked_at < PROBE_INTERVAL_SECONDS:
        return current
    request_fn = request or _request_version
    try:
        status, _headers, body = await request_fn("GET", f"{clean_hub}/api/version", headers=request_headers())
    except (OSError, TimeoutError, urllib.error.URLError):
        contract = HubContract(False, None, None, frozenset(), now)
    else:
        if not 200 <= status < 300:
            contract = _legacy_contract(checked_at=now)
        else:
            try:
                contract = _parse_contract(json.loads(body or b"{}"), checked_at=now)
            except (TypeError, ValueError, json.JSONDecodeError):
                contract = _legacy_contract(checked_at=now)
    _contracts[clean_hub] = contract
    return contract


async def hub_supports(
    capability: str,
    *,
    hub: str,
    request: RequestFn | None = None,
    force: bool = False,
) -> bool:
    """Whether the paired hub has explicitly advertised a safe capability."""

    contract = await refresh_hub_contract(hub, request=request, force=force)
    return contract.reachable and capability in contract.capabilities


def hub_status(hub: str) -> dict[str, str | int | None]:
    """Small, UI-ready health payload for the satellite sync chip."""

    contract = _contracts.get(hub.rstrip("/"))
    if contract is None or not contract.reachable:
        return {"hub_health": "unreachable", "api_rev": None, "app_version": None}
    if contract.api_rev is None or contract.api_rev < API_REV:
        return {"hub_health": "needs_update", "api_rev": contract.api_rev, "app_version": contract.app_version}
    return {"hub_health": "ok", "api_rev": contract.api_rev, "app_version": contract.app_version}


def reset_hub_contract_cache() -> None:
    """Test-only reset for process-global runtime probe state."""

    _contracts.clear()


class ApiRevisionMismatch(Exception):
    """A newer peer reached a route whose request semantics may differ."""


async def require_compatible_api_revision(request: Request) -> None:
    """Refuse only sensitive routes when a newer peer could change semantics."""

    supplied = request.headers.get(API_REV_HEADER)
    try:
        peer_rev = int(supplied) if supplied is not None else None
    except ValueError:
        peer_rev = None
    if peer_rev is not None and peer_rev > API_REV:
        raise ApiRevisionMismatch()
