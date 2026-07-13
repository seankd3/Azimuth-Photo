"""Public release metadata for clients and paired satellites."""

from fastapi import APIRouter

from core.version import version_payload
from features.sync import satellite


router = APIRouter(tags=["system"])


def _mode() -> str:
    if satellite.has_hub():
        return "satellite"
    if satellite.is_satellite_mode():
        return "standalone"
    return "hub"


@router.get("/api/version")
async def api_version() -> dict[str, str | int]:
    return version_payload(mode=_mode())
