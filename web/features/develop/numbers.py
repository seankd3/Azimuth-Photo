"""Reading numbers out of settings that came from somewhere else.

Develop settings arrive from XMP, from Lightroom catalogs, from JSON on the
wire and from the editor itself. Any of those can hand over a string, a None, or
a NaN, and every module that reads one had written the same four lines to cope —
nine copies across this package, in two shapes that were the same shape.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any


def number(value: Any, default: float | None = None) -> float | None:
    """A finite float, or ``default`` when the value is not one."""

    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if math.isfinite(parsed) else default


def setting(values: Mapping[str, object] | None, key: str, default: float = 0.0) -> float:
    """A finite float from a settings mapping — the same rule, by key."""

    return number((values or {}).get(key), default)
