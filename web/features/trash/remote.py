"""Satellite-to-hub handoff for permanent Trash deletion."""

from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request

from data.repositories import catalog as catalog_repository
from features.sync import satellite
from features.sync.versioning import compare_semver


FORWARDED_HEADER = "X-PhotoArchive-Trash-Forwarded"
MIN_SCOPED_TRASH_HUB = "0.1.1"
HUB_UPDATE_MESSAGE = "The hub needs an update before synced photos can be permanently deleted from here."
_VERSION_TIMEOUT_SECONDS = 2
_REQUEST_TIMEOUT_SECONDS = 5
_TOTAL_EMPTY_TIMEOUT_SECONDS = 15


class HubTrashRequestError(RuntimeError):
    pass


async def require_scoped_empty_support(hub_url: str) -> None:
    """Refuse to contact hubs that predate scoped satellite Trash deletion."""

    url = hub_url.rstrip("/") + "/api/version"

    def request() -> tuple[int, bytes]:
        req = urllib.request.Request(url, headers={"Accept": "application/json"}, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=_VERSION_TIMEOUT_SECONDS) as response:  # noqa: S310 - paired hub URL.
                return int(response.status), response.read()
        except urllib.error.HTTPError as error:
            return int(error.code), error.read()

    try:
        status, body = await asyncio.wait_for(
            asyncio.to_thread(request), timeout=_VERSION_TIMEOUT_SECONDS + 1
        )
        payload = json.loads(body or b"{}") if 200 <= status < 300 else {}
    except (OSError, TimeoutError, json.JSONDecodeError, asyncio.TimeoutError) as error:
        raise HubTrashRequestError(HUB_UPDATE_MESSAGE) from error
    version = payload.get("version") if isinstance(payload, dict) else None
    comparison = compare_semver(version, MIN_SCOPED_TRASH_HUB)
    if comparison is None or comparison < 0:
        raise HubTrashRequestError(HUB_UPDATE_MESSAGE)


async def empty_hub_trash(hub_url: str, hub_image_ids: list[int]) -> dict:
    """Empty only the mirrored hub rows confirmed by the satellite owner."""
    await require_scoped_empty_support(hub_url)
    url = hub_url.rstrip("/") + "/api/trash/empty"

    def request(chunk: list[int]) -> tuple[int, bytes]:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            FORWARDED_HEADER: "1",
            **satellite.device_auth_headers(),
        }
        body = json.dumps({"hub_image_ids": chunk}, separators=(",", ":")).encode()
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT_SECONDS) as response:  # noqa: S310 - paired hub URL.
                return int(response.status), response.read()
        except urllib.error.HTTPError as error:
            return int(error.code), error.read()

    aggregate = {"deleted_count": 0, "freed_bytes": 0, "errors": [], "skipped_offline": 0}
    async def forward() -> None:
        for chunk in catalog_repository._chunked(hub_image_ids):
            try:
                # This remains outside the bounded background sync pool, but is
                # deliberately capped so a locked hub cannot freeze the desktop.
                status, body = await asyncio.to_thread(request, chunk)
            except OSError as error:
                raise HubTrashRequestError(f"The hub could not be reached: {error}") from error
            if not 200 <= status < 300:
                raise HubTrashRequestError(
                    f"The hub refused Empty Trash ({status}): {body.decode(errors='replace')[:300]}"
                )
            try:
                result = json.loads(body or b"{}")
            except json.JSONDecodeError as error:
                raise HubTrashRequestError("The hub returned an invalid Empty Trash response") from error
            if not isinstance(result, dict):
                raise HubTrashRequestError("The hub returned an invalid Empty Trash response")
            aggregate["deleted_count"] += int(result.get("deleted_count") or 0)
            aggregate["freed_bytes"] += int(result.get("freed_bytes") or 0)
            aggregate["errors"].extend(result.get("errors") or [])
            aggregate["skipped_offline"] += int(result.get("skipped_offline") or 0)

    try:
        await asyncio.wait_for(forward(), timeout=_TOTAL_EMPTY_TIMEOUT_SECONDS)
    except asyncio.TimeoutError as error:
        raise HubTrashRequestError("The hub is taking too long to empty synced Trash. We'll keep trying in the background.") from error
    return aggregate
