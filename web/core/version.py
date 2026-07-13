"""Release version metadata read from the repository's VERSION file."""

from __future__ import annotations

from pathlib import Path

from data.schema import SCHEMA_VERSION


VERSION_FILE = Path(__file__).resolve().parents[2] / "VERSION"


def app_version() -> str:
    """Return the release version without depending on an installed package."""

    return VERSION_FILE.read_text(encoding="utf-8").strip()


def version_payload(*, mode: str) -> dict[str, str | int]:
    return {
        "version": app_version(),
        "schema_version": SCHEMA_VERSION,
        "mode": mode,
    }
