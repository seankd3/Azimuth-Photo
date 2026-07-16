"""Date conversion helpers for untrusted photo metadata."""

from __future__ import annotations

import math
from datetime import datetime, tzinfo
from typing import Any


def safe_timestamp(dt: datetime) -> float | None:
    """Return a POSIX timestamp without leaking platform range errors."""
    try:
        timestamp = dt.timestamp()
        return timestamp if math.isfinite(timestamp) else None
    except (AttributeError, OSError, OverflowError, TypeError, ValueError):
        return None


def safe_datetime_fromtimestamp(value: Any, tz: tzinfo | None = None) -> datetime | None:
    """Convert a stored timestamp for display without leaking range errors."""
    try:
        timestamp = float(value)
        if not math.isfinite(timestamp):
            return None
        return datetime.fromtimestamp(timestamp, tz)
    except (OSError, OverflowError, TypeError, ValueError):
        return None


def parse_taken_timestamp(value: Any) -> float | None:
    """Parse common photo capture-date forms into a safe POSIX timestamp."""
    if isinstance(value, datetime):
        return safe_timestamp(value)
    if isinstance(value, (int, float)):
        try:
            timestamp = float(value)
        except (OverflowError, ValueError):
            return None
        return timestamp if math.isfinite(timestamp) else None

    text = str(value or "").strip()
    if not text:
        return None
    candidates = (
        text.replace("Z", "+00:00"),
        text.replace(" ", "T", 1).replace("Z", "+00:00"),
    )
    for candidate in candidates:
        try:
            timestamp = safe_timestamp(datetime.fromisoformat(candidate))
        except ValueError:
            continue
        if timestamp is not None:
            return timestamp
    for fmt, length in (
        ("%Y-%m-%d %H:%M:%S", 19),
        ("%Y:%m:%d %H:%M:%S", 19),
        ("%Y-%m-%d", 10),
    ):
        try:
            timestamp = safe_timestamp(datetime.strptime(text[:length], fmt))
        except ValueError:
            continue
        if timestamp is not None:
            return timestamp
    return None
