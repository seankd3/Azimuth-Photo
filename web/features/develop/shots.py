"""Facts about one captured frame, for deciding whether frames belong together.

HDR brackets and panoramas ask the same two questions of a candidate group —
how long was the shutter open, and was this shot on the same lens at the same
focal length — so they ask them in one place.
"""

from __future__ import annotations

from typing import Any

from core.numbers import number

FOCAL_LENGTH_TOLERANCE_MM = 0.01


def shutter_seconds(row: dict[str, Any]) -> float | None:
    """Exposure time in seconds, from EXIF directly or from its APEX value."""

    direct = number(row.get("ExposureTime"))
    if direct and direct > 0:
        return direct
    apex = number(row.get("ShutterSpeedValue"))
    return 2.0 ** (-apex) if apex is not None else None


def same_optical_setup(first: dict[str, Any], second: dict[str, Any]) -> bool:
    """Whether two frames were shot on the same lens at the same focal length."""

    first_lens, second_lens = first.get("lens_model"), second.get("lens_model")
    if not first_lens or not second_lens or first_lens.casefold() != second_lens.casefold():
        return False
    first_focal, second_focal = first.get("focal_length"), second.get("focal_length")
    if first_focal is None or second_focal is None:
        return False
    return abs(float(first_focal) - float(second_focal)) < FOCAL_LENGTH_TOLERANCE_MM
