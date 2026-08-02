"""Canonical environment-variable access for Azimuth Photo."""

from __future__ import annotations

import os
from typing import Mapping


PREFIX = "AZIMUTH_"


def env_key(suffix: str) -> str:
    """Return the canonical environment key for a suffix such as ``HOME``."""

    normalized = str(suffix or "").strip().lstrip("_").upper()
    if not normalized:
        raise ValueError("environment suffix must not be empty")
    return f"{PREFIX}{normalized}"


def env_get(
    suffix: str,
    default: str = "",
    *,
    environ: Mapping[str, str] | None = None,
) -> str:
    """Read one canonical ``AZIMUTH_*`` value."""

    environment = os.environ if environ is None else environ
    value = environment.get(env_key(suffix))
    if value is None:
        return default
    normalized = str(value).strip()
    return normalized if normalized else default


def env_truthy(suffix: str, *, environ: Mapping[str, str] | None = None) -> bool:
    return env_get(suffix, "", environ=environ).lower() in {"1", "true", "yes", "on"}


def setdefault(suffix: str, value: str) -> None:
    """Set a canonical environment default without overriding deployment intent."""

    os.environ.setdefault(env_key(suffix), value)


_UNITS = {"g": 1024**3, "m": 1024**2, "k": 1024}


def env_bytes(suffix: str, default: int, *, environ: Mapping[str, str] | None = None) -> int:
    """Read a size written the way a person writes one: ``4g``, ``512m``, ``2048``."""

    raw = env_get(suffix, "", environ=environ).lower()
    scale = _UNITS.get(raw[-1:], 1)
    try:
        return int(float(raw[:-1] if scale > 1 else raw) * scale)
    except ValueError:
        return default


def env_float(suffix: str, default: float, *, environ: Mapping[str, str] | None = None) -> float:
    """Read a non-negative duration or fraction."""

    try:
        return max(0.0, float(env_get(suffix, "", environ=environ)))
    except ValueError:
        return default
