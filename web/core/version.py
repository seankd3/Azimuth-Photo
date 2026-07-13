"""Release version metadata read from the repository's VERSION file."""

from __future__ import annotations

from pathlib import Path
import sys

from data.schema import SCHEMA_VERSION


VERSION_FILE = Path(__file__).resolve().parents[2] / "VERSION"


def _version_file() -> Path:
    """Locate VERSION both in a checkout and PyInstaller's bundled data root."""

    bundle_root = getattr(sys, "_MEIPASS", None)
    return Path(bundle_root) / "VERSION" if bundle_root else VERSION_FILE


def app_version() -> str:
    """Return the release version without depending on an installed package."""

    return _version_file().read_text(encoding="utf-8").strip()


def version_payload(*, mode: str) -> dict[str, str | int]:
    return {
        "version": app_version(),
        "schema_version": SCHEMA_VERSION,
        "mode": mode,
    }
