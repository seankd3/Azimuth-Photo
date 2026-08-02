"""Optional device-token enforcement for hub sync endpoints."""

from __future__ import annotations

from core.catalog_path import catalog_path


from fastapi import Header, HTTPException, Request

import settings
from features.sync import pairing




def require_device_token_enabled() -> bool:
    return bool(settings.get_settings().get("require_device_token"))


async def enforce_device_token(
    request: Request,
    x_device_token: str | None = Header(default=None, alias="X-Device-Token"),
) -> None:
    """Dependency: when require_device_token is on, demand a live device token."""
    if not require_device_token_enabled():
        # Still update last_seen when a valid token is presented.
        if x_device_token:
            await pairing.authenticate_device_token(catalog_path(), x_device_token)
        return
    device = await pairing.authenticate_device_token(catalog_path(), x_device_token)
    if device is None:
        raise HTTPException(status_code=401, detail="device token required")
    request.state.paired_device = device
