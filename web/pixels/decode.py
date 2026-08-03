"""One linear RAW decode, at whatever scale the caller needs.

Develop's editor preview and its exported file used to be decoded by two
different pieces of code. They shared the obvious arguments and differed in
four that decide how highlights and white balance land:

    adjust_maximum_thr   base 0.0        export omitted (LibRaw default 0.75)
    user_sat             base per-channel white level   export omitted
    white-balance gain   base restored   export left as LibRaw normalised it
    highlight recovery   base applied    export omitted

So what the editor showed was not what the export wrote. That is one function's
worth of difference, not two implementations, and this is the function.

Scale is a parameter, not a fork: ``half_size=True`` is the editor's 2048px
working base, ``False`` is the full-resolution export.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from typing import Any

import numpy as np

try:
    import rawpy
except ImportError:  # pragma: no cover - rawpy is an application dependency
    rawpy = None

UINT16_FULL_SCALE = np.float32(np.iinfo(np.uint16).max)


class RawDecodeError(RuntimeError):
    """An image exists but could not be decoded into linear pixels."""


@dataclass
class DecodedRaw:
    """Linear, sRGB-primary uint16 pixels, plus what the sensor said."""

    rgb: np.ndarray
    camera_wb: list[float] = field(default_factory=list)
    daylight_wb: list[float] = field(default_factory=list)
    color_matrix: Any = None
    iso: float | None = None
    white_levels: list[float] = field(default_factory=list)


def _finite_positive(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def postprocess_arguments(*, half_size: bool) -> dict[str, Any]:
    """The one set of LibRaw arguments Azimuth decodes RAW with.

    ``adjust_maximum_thr=0`` matters: LibRaw's 0.75 default pulls the white
    point down from the per-channel maximum, which clips highlight detail that
    reconstruction could otherwise recover.
    """

    return {
        "use_camera_wb": True,
        "output_bps": 16,
        "no_auto_bright": True,
        "adjust_maximum_thr": 0.0,
        "gamma": (1, 1),
        "output_color": rawpy.ColorSpace.sRGB,
        "highlight_mode": rawpy.HighlightMode.Blend,
        "half_size": half_size,
    }


def decode_linear(
    path: str | os.PathLike[str],
    *,
    half_size: bool,
    reconstruct: Any = None,
    clip_levels_for: Any = None,
    color_matrix_for: Any = None,
) -> DecodedRaw:
    """Decode one RAW into linear uint16 sRGB-primary pixels.

    ``reconstruct``, ``clip_levels_for`` and ``color_matrix_for`` inject
    Develop's highlight recovery and its matrix reader, so this module stays
    free of the render pipeline. Omit them and the decode is identical minus
    those steps.
    """

    if rawpy is None:
        raise RawDecodeError("rawpy is unavailable")

    try:
        with rawpy.imread(str(path)) as raw:
            camera_wb = list(raw.camera_whitebalance or [])
            daylight_wb = list(raw.daylight_whitebalance or [])
            saturation_level = _finite_positive(raw.white_level)
            white_levels = [saturation_level] * 4 if saturation_level is not None else []
            iso = _finite_positive(getattr(getattr(raw, "metadata", None), "iso_speed", None))
            color_matrix = color_matrix_for(raw) if color_matrix_for is not None else None

            arguments = postprocess_arguments(half_size=half_size)
            camera_white = raw.camera_white_level_per_channel
            if camera_white:
                valid_white = [int(value) for value in camera_white if int(value) > 0]
                if valid_white:
                    saturation_level = float(max(valid_white))
                    arguments["user_sat"] = int(saturation_level)
                    white_levels = [
                        float(value) if _finite_positive(value) is not None else saturation_level
                        for value in camera_white
                    ]

            rgb = raw.postprocess(**arguments)
            black_levels = raw.black_level_per_channel

            valid_wb = [float(value) for value in camera_wb[:4] if _finite_positive(value)]
            if valid_wb:
                # LibRaw normalises camera WB so its largest multiplier is one.
                # DNG reference-neutral semantics divide by the unnormalised
                # AsShotNeutral, so restore that discarded common gain here, at
                # the linear boundary, while highlight recovery can still act.
                green_wb = _finite_positive(camera_wb[1]) if len(camera_wb) > 1 else None
                wb_scale = np.float32(max(valid_wb) / (green_wb or min(valid_wb)))
                linear = np.asarray(rgb, dtype=np.float32)
                np.multiply(linear, wb_scale, out=linear)
                if reconstruct is not None and clip_levels_for is not None:
                    clip_levels = clip_levels_for(
                        white_levels,
                        black_levels,
                        camera_wb,
                        saturation_level=saturation_level,
                    )
                    if clip_levels is not None:
                        linear = reconstruct(linear, clip_levels)
                np.rint(linear, out=linear)
                np.clip(linear, 0, UINT16_FULL_SCALE, out=linear)
                rgb = linear.astype(np.uint16)
    except Exception as exc:
        raise RawDecodeError(f"RAW decode failed: {exc}") from exc

    return DecodedRaw(
        rgb=rgb,
        camera_wb=camera_wb,
        daylight_wb=daylight_wb,
        color_matrix=color_matrix,
        iso=iso,
        white_levels=white_levels,
    )
