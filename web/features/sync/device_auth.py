"""Optional device-token enforcement for hub sync endpoints."""

from __future__ import annotations

from collections.abc import Callable

from fastapi import Header, HTTPException, Request

import settings
from features.sync import pairing


_db_path: Callable[[], str] | None = None


def configure(*, db_path: Callable[[], str]) -> None:
    global _db_path
    _db_path = db_path


def require_device_token_enabled() -> bool:
    return bool(settings.get_settings().get("require_device_token"))


async def enforce_device_token(
    request: Request,
    x_device_token: str | None = Header(default=None, alias="X-Device-Token"),
) -> None:
    """Dependency: when require_device_token is on, demand a live device token."""
    if not require_device_token_enabled():
        # Still update last_seen when a valid token is presented.
        if x_device_token and _db_path is not None:
            await pairing.authenticate_device_token(_db_path(), x_device_token)
        return
    if _db_path is None:
        raise HTTPException(status_code=500, detail="device auth is not configured")
    device = await pairing.authenticate_device_token(_db_path(), x_device_token)
    if device is None:
        raise HTTPException(status_code=401, detail="device token required")
    request.state.paired_device = device
