"""Circular clone/heal spots for Develop (§23).

Spots are intentionally rendered after every other Develop operation.  This
means a stamp copies what the user sees, rather than copying pre-tone RAW
values.  Coordinates are normalized in the oriented image space.
"""

from __future__ import annotations

from features.develop.ramps import smoothstep as _smoothstep

from core.numbers import number as _number

import json
from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np

from . import ops_constants as C


RETOUCH_SETTINGS_KEY = "pa_RetouchSpots"
_VALID_MODES = frozenset(("clone", "heal"))
# These fallbacks only keep this isolated lane importable until its required
# ops_constants PATCH block is merged; both renderers use the named constants.
RETOUCH_RENDER_CAP = getattr(C, "RETOUCH_RENDER_CAP", 32)
RETOUCH_RING_TAPS = getattr(C, "RETOUCH_RING_TAPS", 8)
RETOUCH_RING_SCALE = getattr(C, "RETOUCH_RING_SCALE", 1.5)
RETOUCH_MIN_RADIUS = getattr(C, "RETOUCH_MIN_RADIUS", 1e-4)



def _unit(value: Any, fallback: float) -> float:
    return float(np.clip(_number(value, fallback), 0.0, 1.0))


def normalize_spot(value: Mapping[str, Any]) -> dict[str, float | str] | None:
    """Return a canonical spot, or ``None`` for malformed persisted data."""
    if not isinstance(value, Mapping):
        return None
    mode = str(value.get("mode", "clone")).lower()
    if mode not in _VALID_MODES:
        return None
    radius = _number(value.get("radius"), 0.025)
    if radius <= 0.0:
        return None
    return {
        "src_x": _unit(value.get("src_x", value.get("srcX")), 0.5),
        "src_y": _unit(value.get("src_y", value.get("srcY")), 0.5),
        "dst_x": _unit(value.get("dst_x", value.get("dstX")), 0.5),
        "dst_y": _unit(value.get("dst_y", value.get("dstY")), 0.5),
        "radius": float(np.clip(radius, RETOUCH_MIN_RADIUS, 0.5)),
        "feather": _unit(value.get("feather"), 0.5),
        "opacity": _unit(value.get("opacity"), 1.0),
        "mode": mode,
    }


def spots_from_settings(settings: Mapping[str, Any] | None, *, cap: int = RETOUCH_RENDER_CAP) -> list[dict[str, float | str]]:
    """Read valid v1 spots, preserving saved spots beyond the render cap."""
    values = (settings or {}).get(RETOUCH_SETTINGS_KEY, ())
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
        return []
    spots: list[dict[str, float | str]] = []
    for value in values[:cap]:
        spot = normalize_spot(value) if isinstance(value, Mapping) else None
        if spot is not None:
            spots.append(spot)
    return spots


def _bilinear(image: np.ndarray, x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Sample ``image`` in normalized coordinates with clamp-to-edge semantics."""
    height, width = image.shape[:2]
    px = np.clip(x, 0.0, 1.0) * max(width - 1, 0)
    py = np.clip(y, 0.0, 1.0) * max(height - 1, 0)
    x0 = np.floor(px).astype(np.intp)
    y0 = np.floor(py).astype(np.intp)
    x1 = np.minimum(x0 + 1, width - 1)
    y1 = np.minimum(y0 + 1, height - 1)
    wx = (px - x0)[..., None]
    wy = (py - y0)[..., None]
    return ((image[y0, x0] * (1.0 - wx) + image[y0, x1] * wx) * (1.0 - wy)
            + (image[y1, x0] * (1.0 - wx) + image[y1, x1] * wx) * wy)



def _spot_weight(distance: np.ndarray, radius: float, feather: float, opacity: float) -> np.ndarray:
    """Circular feather matching the GLSL helper in the §23 integration patch."""
    hard_edge = radius * (1.0 - feather)
    return (1.0 - _smoothstep(hard_edge, radius, distance)) * opacity


def _ring_mean(image: np.ndarray, center_x: float, center_y: float, radius: float) -> np.ndarray:
    """Mean of a deterministic 8-tap ring around a normalized image point."""
    # Ring scales are duplicated in the ops_constants twin PATCH blocks.
    ring_radius = radius * RETOUCH_RING_SCALE
    angles = np.arange(RETOUCH_RING_TAPS, dtype=np.float32) * (2.0 * np.pi / RETOUCH_RING_TAPS)
    samples = _bilinear(
        image,
        center_x + np.cos(angles) * ring_radius,
        center_y + np.sin(angles) * ring_radius,
    )
    return samples.mean(axis=0)


def apply_retouch_spots(image: np.ndarray, settings_or_spots: Mapping[str, Any] | Sequence[Mapping[str, Any]] | None) -> np.ndarray:
    """Apply clone/heal spots to gamma sRGB ``image``.

    Clone samples the source disc at the same relative offset.  Heal performs
    the same copy then shifts it by ``destination-ring − source-ring``; this
    keeps source texture while matching the surrounding destination colour.
    Each spot samples the pre-spot image so overlapping stamps remain stable.
    """
    source = np.asarray(image, dtype=np.float32)
    if source.ndim != 3 or source.shape[-1] != 3:
        raise ValueError("image must have shape (height, width, 3)")
    if isinstance(settings_or_spots, Mapping):
        spots = spots_from_settings(settings_or_spots)
    else:
        spots = [spot for item in (settings_or_spots or ()) if (spot := normalize_spot(item)) is not None]
    if not spots:
        return source.copy()

    height, width = source.shape[:2]
    yy, xx = np.mgrid[0:height, 0:width].astype(np.float32)
    xx /= max(width - 1, 1)
    yy /= max(height - 1, 1)
    result = source.copy()
    for spot in spots:
        dst_x, dst_y = float(spot["dst_x"]), float(spot["dst_y"])
        radius, feather, opacity = float(spot["radius"]), float(spot["feather"]), float(spot["opacity"])
        distance = np.hypot(xx - dst_x, yy - dst_y)
        weight = _spot_weight(distance, radius, feather, opacity)
        if not np.any(weight > 0.0):
            continue
        sampled = _bilinear(source, xx + float(spot["src_x"]) - dst_x, yy + float(spot["src_y"]) - dst_y)
        if spot["mode"] == "heal":
            sampled = sampled + (_ring_mean(source, dst_x, dst_y, radius) - _ring_mean(source, float(spot["src_x"]), float(spot["src_y"]), radius))
        result = result * (1.0 - weight[..., None]) + sampled * weight[..., None]
    return np.clip(result, 0.0, 1.0).astype(np.float32)


def import_adobe_retouch_info(raw_settings: Mapping[str, Any]) -> list[dict[str, float | str]]:
    """Best-effort Adobe retouch importer.

    Lightroom's sampled ``RetouchInfo`` blobs are opaque binary/base64 data,
    not a documented stable schema.  We only import JSON-shaped values when a
    caller has already decoded them; opaque raw values are deliberately skipped
    rather than pretending they are compatible.
    """
    for key in ("RetouchInfo", "RetouchAreas", "PaintBasedCorrections"):
        raw = raw_settings.get(key)
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except json.JSONDecodeError:
                continue  # Adobe's opaque encoding: intentionally unsupported in v1.
        if isinstance(raw, Mapping):
            raw = raw.get("spots", raw.get("RetouchAreas", raw.get("PaintBasedCorrections", ())))
        if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
            spots = [spot for item in raw if isinstance(item, Mapping) and (spot := normalize_spot(item)) is not None]
            if spots:
                return spots
    return []
