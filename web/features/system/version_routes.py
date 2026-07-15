"""Public release metadata for clients and paired satellites."""

from fastapi import APIRouter

from core.version import version_payload


router = APIRouter(tags=["system"])

@router.get("/api/version")
async def api_version() -> dict[str, str | int | list[str]]:
    """Return the same cross-surface contract in hub and satellite modes."""

    return version_payload()
