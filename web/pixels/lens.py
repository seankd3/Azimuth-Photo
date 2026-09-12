"""EXIF and Lensfun resolution for Develop lens corrections.

The returned payload is renderer-neutral JSON.  Lensfun supplies interpolated
calibration terms; the NumPy and WebGL twins apply those terms with shared math.
"""

from __future__ import annotations

import math
from typing import Mapping

from . import ops_constants as C


def distortion_radial_scale(radius: float, distortion: Mapping[str, object] | None) -> float:
    """Lensfun's inverse-remap radial multiplier for one normalized radius."""
    if not distortion:
        return 1.0
    try:
        terms = [float(value) for value in distortion.get("terms", ())]
    except (AttributeError, TypeError, ValueError):
        return 1.0
    model = str(distortion.get("model") or "").lower()
    r2 = float(radius) ** 2
    if model == "poly3" and terms:
        return 1.0 - terms[0] + terms[0] * r2
    if model == "poly5" and len(terms) >= 2:
        return 1.0 + terms[0] * r2 + terms[1] * r2 * r2
    if model == "ptlens" and len(terms) >= 3:
        a, b, c = terms[:3]
        return a * radius * r2 + b * r2 + c * radius + 1.0 - a - b - c
    return 1.0


def distortion_auto_crop_scale(
    distortion: Mapping[str, object] | None,
    width: float,
    height: float,
    crop_ratio: float = 1.0,
) -> float:
    """Largest centered UV rectangle whose radial remap stays in-frame."""
    if not distortion or width <= 0 or height <= 0:
        return 1.0
    half_min = min(width, height) / C.LENS_NORMALIZED_HALF_MIN
    maximum = 1.0
    for index in range(C.LENS_AUTO_CROP_EDGE_SAMPLES + 1):
        t = index / C.LENS_AUTO_CROP_EDGE_SAMPLES
        for u, v in ((t, 0.0), (t, 1.0), (0.0, t), (1.0, t)):
            px = (u * width - width * C.LENS_IMAGE_CENTER) / half_min * crop_ratio
            py = (v * height - height * C.LENS_IMAGE_CENTER) / half_min * crop_ratio
            maximum = max(maximum, distortion_radial_scale(math.hypot(px, py), distortion))
    return min(1.0, 1.0 / max(maximum, C.TONE_EPSILON))
