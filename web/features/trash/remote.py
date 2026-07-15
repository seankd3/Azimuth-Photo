"""Satellite-to-hub handoff for permanent Trash deletion."""

from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request

from data.repositories import catalog as catalog_repository
from features.sync import satellite


FORWARDED_HEADER = "X-PhotoArchive-Trash-Forwarded"


class HubTrashRequestError(RuntimeError):
    pass


async def empty_hub_trash(hub_url: str, hub_image_ids: list[int]) -> dict:
    """Empty only the mirrored hub rows confirmed by the satellite owner."""
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
            with urllib.request.urlopen(req, timeout=30) as response:
                return int(response.status), response.read()
        except urllib.error.HTTPError as error:
            return int(error.code), error.read()

    aggregate = {"deleted_count": 0, "freed_bytes": 0, "errors": [], "skipped_offline": 0}
    for chunk in catalog_repository._chunked(hub_image_ids):
        try:
            # This is an owner-triggered foreground action. Do not queue it behind
            # the bounded background sync pool, which may be busy prefetching.
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
    return aggregate
