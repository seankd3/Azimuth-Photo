"""Transform/Upright geometry shared by preview and export paths.

The values intentionally use Adobe Camera Raw's ``crs:Perspective*`` names.
That keeps a photo's edit portable: Lightroom can read the stored settings even
when this app supplied Auto or Guided Upright.  Geometry is expressed as a
forward source-to-output homography; renderers sample its inverse.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np

from . import ops_constants as C


# These fallbacks keep the isolated feature importable until the twin constants
# PATCH is applied.  The named values in that PATCH are the renderer contract.
PERSPECTIVE_AMOUNT_SCALE = getattr(C, "PERSPECTIVE_AMOUNT_SCALE", 0.0035)
PERSPECTIVE_ASPECT_SCALE = getattr(C, "PERSPECTIVE_ASPECT_SCALE", 0.01)
PERSPECTIVE_OFFSET_SCALE = getattr(C, "PERSPECTIVE_OFFSET_SCALE", 0.01)
PERSPECTIVE_SCALE_BASE = getattr(C, "PERSPECTIVE_SCALE_BASE", 100.0)
PERSPECTIVE_SCALE_MIN = getattr(C, "PERSPECTIVE_SCALE_MIN", 1.0)
HORIZON_ANGLE_LIMIT = getattr(C, "HORIZON_ANGLE_LIMIT", 20.0)
HORIZON_THETA_STEPS = getattr(C, "HORIZON_THETA_STEPS", 81)
HORIZON_EDGE_PERCENTILE = getattr(C, "HORIZON_EDGE_PERCENTILE", 92.0)
HORIZON_MAX_POINTS = getattr(C, "HORIZON_MAX_POINTS", 12000)

TRANSFORM_KEYS = (
    "PerspectiveVertical", "PerspectiveHorizontal", "PerspectiveRotate",
    "PerspectiveScale", "PerspectiveAspect", "PerspectiveX", "PerspectiveY",
    "PerspectiveUpright",
)


def _number(settings: Mapping[str, object] | None, key: str, default: float) -> float:
    try:
        value = float((settings or {}).get(key, default))
    except (TypeError, ValueError):
        return default
    return value if np.isfinite(value) else default


def transform_settings(settings: Mapping[str, object] | None) -> dict[str, float | str]:
    """Return bounded Adobe-native transform settings with usable defaults."""

    source = settings or {}
    upright = str(source.get("PerspectiveUpright", "Off") or "Off")
    return {
        "PerspectiveVertical": float(np.clip(_number(source, "PerspectiveVertical", 0.0), -100, 100)),
        "PerspectiveHorizontal": float(np.clip(_number(source, "PerspectiveHorizontal", 0.0), -100, 100)),
        "PerspectiveRotate": float(np.clip(_number(source, "PerspectiveRotate", 0.0), -45, 45)),
        "PerspectiveScale": float(max(PERSPECTIVE_SCALE_MIN, _number(source, "PerspectiveScale", PERSPECTIVE_SCALE_BASE))),
        "PerspectiveAspect": float(np.clip(_number(source, "PerspectiveAspect", 0.0), -100, 100)),
        "PerspectiveX": float(np.clip(_number(source, "PerspectiveX", 0.0), -100, 100)),
        "PerspectiveY": float(np.clip(_number(source, "PerspectiveY", 0.0), -100, 100)),
        "PerspectiveUpright": upright,
    }


def _mat(*rows: Sequence[float]) -> np.ndarray:
    return np.asarray(rows, dtype=np.float32)


def homography_from_settings(settings: Mapping[str, object] | None) -> np.ndarray:
    """Build the source-to-output 3x3 homography in centred normalized UV."""

    s = transform_settings(settings)
    vertical = s["PerspectiveVertical"] * PERSPECTIVE_AMOUNT_SCALE
    horizontal = s["PerspectiveHorizontal"] * PERSPECTIVE_AMOUNT_SCALE
    angle = np.deg2rad(s["PerspectiveRotate"])
    scale = s["PerspectiveScale"] / PERSPECTIVE_SCALE_BASE
    aspect = max(PERSPECTIVE_SCALE_MIN / PERSPECTIVE_SCALE_BASE, 1.0 + s["PerspectiveAspect"] * PERSPECTIVE_ASPECT_SCALE)
    offset_x = s["PerspectiveX"] * PERSPECTIVE_OFFSET_SCALE
    offset_y = s["PerspectiveY"] * PERSPECTIVE_OFFSET_SCALE

    # Apply projectivity first, then affine controls.  Positive screen-space
    # rotation is clockwise (UV y grows downward), matching canvas behavior.
    projective = _mat((1, 0, 0), (0, 1, 0), (horizontal, vertical, 1))
    rotate = _mat((np.cos(angle), -np.sin(angle), 0), (np.sin(angle), np.cos(angle), 0), (0, 0, 1))
    affine = _mat((scale * aspect, 0, offset_x), (0, scale, offset_y), (0, 0, 1))
    return (affine @ rotate @ projective).astype(np.float32)


def inverse_homography(settings: Mapping[str, object] | None) -> np.ndarray:
    """Return the output-to-source matrix used by pixel renderers."""

    return np.linalg.inv(homography_from_settings(settings)).astype(np.float32)


def map_uv(uv: np.ndarray, homography: np.ndarray) -> np.ndarray:
    """Map normalized UV coordinates through a centred-coordinate homography."""

    points = np.asarray(uv, dtype=np.float32)
    centered = (points - 0.5) * 2.0
    homogeneous = np.concatenate((centered, np.ones((*centered.shape[:-1], 1), dtype=np.float32)), axis=-1)
    mapped = homogeneous @ np.asarray(homography, dtype=np.float32).T
    safe_w = np.where(np.abs(mapped[..., 2:3]) < 1e-6, np.nan, mapped[..., 2:3])
    return (mapped[..., :2] / safe_w * 0.5 + 0.5).astype(np.float32)


def _bilinear_sample(image: np.ndarray, uv: np.ndarray) -> np.ndarray:
    height, width = image.shape[:2]
    x = uv[..., 0] * width - 0.5
    y = uv[..., 1] * height - 0.5
    # Matrix inversion can put edge-centre identity samples a few ULPs outside
    # their mathematically exact bounds; retain those pixels before clamping.
    valid = np.isfinite(x) & np.isfinite(y) & (x >= -1e-5) & (x <= width - 1 + 1e-5) & (y >= -1e-5) & (y <= height - 1 + 1e-5)
    x = np.clip(np.nan_to_num(x), 0, width - 1)
    y = np.clip(np.nan_to_num(y), 0, height - 1)
    x0, y0 = x.astype(np.intp), y.astype(np.intp)
    x1, y1 = np.minimum(x0 + 1, width - 1), np.minimum(y0 + 1, height - 1)
    wx, wy = (x - x0)[..., None], (y - y0)[..., None]
    top = image[y0, x0] * (1 - wx) + image[y0, x1] * wx
    bottom = image[y1, x0] * (1 - wx) + image[y1, x1] * wx
    sampled = top * (1 - wy) + bottom * wy
    sampled[~valid] = 0.0
    return sampled.astype(np.float32)


def apply_transform(rgb: np.ndarray, settings: Mapping[str, object] | None) -> np.ndarray:
    """Warp an RGB image through Transform/Upright using black outside bounds."""

    image = np.asarray(rgb, dtype=np.float32)
    if image.ndim != 3 or image.shape[-1] != 3:
        raise ValueError("rgb must have shape (height, width, 3)")
    height, width = image.shape[:2]
    x = (np.arange(width, dtype=np.float32) + 0.5) / width
    y = (np.arange(height, dtype=np.float32) + 0.5) / height
    uv = np.stack(np.meshgrid(x, y), axis=-1)
    return _bilinear_sample(image, map_uv(uv, inverse_homography(settings)))


def _line_angle(line: Mapping[str, Any] | Sequence[float]) -> float | None:
    if isinstance(line, Mapping):
        start, end = line.get("start"), line.get("end")
        if start is None or end is None:
            start = (line.get("x1"), line.get("y1"))
            end = (line.get("x2"), line.get("y2"))
    elif len(line) == 4:
        start, end = line[:2], line[2:]
    elif len(line) == 2:
        start, end = line
    else:
        return None
    try:
        dx, dy = float(end[0]) - float(start[0]), float(end[1]) - float(start[1])
    except (TypeError, ValueError, IndexError):
        return None
    if not np.isfinite(dx) or not np.isfinite(dy) or np.hypot(dx, dy) < 1e-5:
        return None
    return float(np.rad2deg(np.arctan2(dy, dx)))


def _signed_axis_error(angle: float) -> float:
    """Angle error to its nearest horizontal or vertical intended axis."""

    targets = (0.0, 90.0, -90.0, 180.0, -180.0)
    errors = [((angle - target + 90.0) % 180.0) - 90.0 for target in targets]
    return min(errors, key=abs)


def guided_upright(lines: Sequence[Mapping[str, Any] | Sequence[float]]) -> dict[str, float | str]:
    """Derive a stable guided Upright rotate from two to four user strokes.

    Lightroom's Guided mode accepts either two or four guides.  Each guide is
    snapped to the nearest cardinal family; their circular median is robust to
    one short or slightly imprecise line.  Perspective sliders stay available
    for the photographer to refine after the automatic levelling pass.
    """

    errors = [error for line in lines[:4] if (angle := _line_angle(line)) is not None for error in (_signed_axis_error(angle),)]
    if len(errors) < 2:
        raise ValueError("guided upright needs two to four non-zero lines")
    correction = -float(np.median(errors))
    return {"PerspectiveRotate": float(np.clip(correction, -45, 45)), "PerspectiveUpright": "Guided"}


def detect_horizon(image: np.ndarray) -> dict[str, float] | None:
    """Find the strongest near-horizontal edge with a compact deterministic Hough pass."""

    rgb = np.asarray(image, dtype=np.float32)
    if rgb.ndim != 3 or rgb.shape[-1] < 3 or min(rgb.shape[:2]) < 8:
        return None
    gray = rgb[..., :3] @ np.asarray((0.2126, 0.7152, 0.0722), dtype=np.float32)
    gy, gx = np.gradient(gray)
    magnitude = np.hypot(gx, gy)
    threshold = np.percentile(magnitude, HORIZON_EDGE_PERCENTILE)
    points = np.argwhere(magnitude >= max(float(threshold), 1e-6))
    if len(points) < 8:
        return None
    if len(points) > HORIZON_MAX_POINTS:
        points = points[np.linspace(0, len(points) - 1, HORIZON_MAX_POINTS, dtype=np.intp)]
    y, x = points[:, 0].astype(np.float32), points[:, 1].astype(np.float32)
    angles = np.linspace(90 - HORIZON_ANGLE_LIMIT, 90 + HORIZON_ANGLE_LIMIT, HORIZON_THETA_STEPS)
    best: tuple[int, float, float] | None = None
    diagonal = float(np.hypot(*gray.shape))
    for theta in angles:
        radians = np.deg2rad(theta)
        rho = x * np.cos(radians) + y * np.sin(radians)
        bins = np.floor((rho + diagonal) / 2.0).astype(np.intp)
        counts = np.bincount(bins, minlength=int(diagonal) + 2)
        index = int(np.argmax(counts))
        candidate = (int(counts[index]), theta, (index * 2.0 - diagonal))
        if best is None or candidate[0] > best[0]:
            best = candidate
    if best is None or best[0] < 6:
        return None
    # Tangent in image coordinates (x right, y down), normalized to ±45°.
    angle = float(np.rad2deg(np.arctan2(-np.cos(np.deg2rad(best[1])), np.sin(np.deg2rad(best[1])))))
    return {"angle": angle, "rotate": -angle, "votes": float(best[0]), "rho": float(best[2])}


def auto_level_settings(image: np.ndarray) -> dict[str, float | str] | None:
    horizon = detect_horizon(image)
    if horizon is None:
        return None
    return {"PerspectiveRotate": float(np.clip(horizon["rotate"], -45, 45)), "PerspectiveUpright": "Auto"}
