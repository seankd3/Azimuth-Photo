"""Making a number out of whatever arrived.

Values reach this app as strings from XMP, as None from a half-filled catalog
row, as NaN from a pipeline that divided by nothing. Every module that read one
had written the same four lines to cope — eleven copies, in three shapes that
were the same shape.

A non-finite number is not a number here. It cannot be drawn, and `json.dumps`
writes it as a bare `NaN`, which a strict client refuses to parse — so the value
never leaves this function.
"""

from __future__ import annotations

import math
import re
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


def integer(value: Any, default: int = 0) -> int:
    """A whole number, or ``default`` when the value is not one."""

    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def field(row: Any, key: str, default: Any = None) -> Any:
    """A value off an image row, whether it is a mapping or a database row."""

    if hasattr(row, "get"):
        return row.get(key, default)
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return default


def unique_image_ids(values) -> list[int]:
    """Positive integer ids, in order, without repeats."""

    ids: list[int] = []
    seen: set[int] = set()
    for value in values or []:
        try:
            image_id = int(value)
        except (TypeError, ValueError):
            continue
        if image_id <= 0 or image_id in seen:
            continue
        seen.add(image_id)
        ids.append(image_id)
    return ids


_NUMBER = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$")


def spelled(value: object) -> bool | int | float | str:
    """What an XMP string spells: a truth, a whole number, a number, or itself."""

    clean = str(value or "").strip()
    if clean.lower() == "true":
        return True
    if clean.lower() == "false":
        return False
    if not _NUMBER.fullmatch(clean):
        return clean
    numeric = float(clean)
    return int(numeric) if numeric.is_integer() else numeric
