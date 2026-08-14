"""Release version metadata read from the repository's VERSION file."""

from __future__ import annotations

from pathlib import Path
import sys

VERSION_FILE = Path(__file__).resolve().parents[2] / "VERSION"

# Satellite ⇄ hub wire-contract revision. Bump this whenever an existing
# cross-node request or response changes meaning; app releases alone do not
# imply compatibility.
API_REV = 2
CAPABILITIES = frozenset({
    "trash.scoped_empty",
    "sync.catalog_v2",
    "thumbs.trash_readthrough",
})


def _version_file() -> Path:
    """Locate VERSION both in a checkout and PyInstaller's bundled data root."""

    bundle_root = getattr(sys, "_MEIPASS", None)
    return Path(bundle_root) / "VERSION" if bundle_root else VERSION_FILE


def app_version() -> str:
    """Return the release version without depending on an installed package."""

    try:
        return _version_file().read_text(encoding="utf-8").strip()
    except OSError:
        # A missing VERSION must never take down /api/version — satellites use
        # it for the compatibility handshake.
        return "0.0.0-unknown"



def version_payload() -> dict[str, str | int | list[str]]:
    payload: dict[str, str | int | list[str]] = {
        "app_version": app_version(),
        "api_rev": API_REV,
        "capabilities": sorted(CAPABILITIES),
    }
    return payload
