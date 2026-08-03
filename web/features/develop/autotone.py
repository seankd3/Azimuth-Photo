"""Deterministic, scene-referred Auto tone settings.

The caller supplies the linear base after the regular white-balance stage.
This module deliberately only measures that base and returns the six CRs tone
keys; it neither mutates the input pixels nor applies any rendering operation.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import numpy as np

from . import ops_constants as C


_MEDIAN_TARGET = 0.18
_EXPOSURE_MIN = -3.0
_EXPOSURE_MAX = 3.0
_HIGHLIGHT_LIMIT = 0.95
_HIGHLIGHT_MASS_LIMIT = 0.005
_HIGHLIGHT_PERCENTILE = 99.5
_WHITE_PERCENTILE = 99.7
_BLACK_PERCENTILE = 0.3
_WHITE_TARGET = 0.90
_BLACK_TARGET = 0.01
_SHADOW_LIMIT = 0.02
_HIGHLIGHT_K = 800.0
_SHADOW_K2 = 90.0
_CONTRAST_IQR_TARGET = 0.55


def _luma(linear_base: np.ndarray) -> np.ndarray:
    rgb = np.asarray(linear_base, dtype=np.float32)
    if rgb.ndim != 3 or rgb.shape[-1] != 3:
        raise ValueError("linear_base must have shape (height, width, 3)")
    y = rgb[..., 0] * C.LUMA_RED + rgb[..., 1] * C.LUMA_GREEN + rgb[..., 2] * C.LUMA_BLUE
    y = y[np.isfinite(y)]
    if not y.size:
        raise ValueError("linear_base contains no finite pixels")
    return np.maximum(y, C.TONE_EPSILON)


def _gaussian_ev(value: float, center: float) -> float:
    z = (np.log2(max(value, C.TONE_EPSILON)) - center) / max(C.TONE_EV_SIGMA, C.TONE_EPSILON)
    return float(np.exp(-0.5 * z * z))


def _tone_slider_for_percentile(value: float, target: float, *, center: float, factor: float) -> int:
    """Invert the pipeline's local response at one scene-luma percentile."""
    desired_ev = np.log2(target / max(value, C.TONE_EPSILON))
    response = factor * _gaussian_ev(value, center)
    if response <= C.TONE_EPSILON:
        return 100 if desired_ev >= 0.0 else -100
    return int(np.clip(np.rint(100.0 * desired_ev / response), -100, 100))


def _protected_exposure(y: np.ndarray) -> float:
    exposure = float(np.clip(np.log2(_MEDIAN_TARGET / float(np.median(y))), _EXPOSURE_MIN, _EXPOSURE_MAX))
    if float(np.mean(y * np.exp2(exposure) > _HIGHLIGHT_LIMIT)) > _HIGHLIGHT_MASS_LIMIT:
        upper = float(np.percentile(y, _HIGHLIGHT_PERCENTILE))
        exposure = min(exposure, float(np.log2(_HIGHLIGHT_LIMIT / max(upper, C.TONE_EPSILON))))
    # Exposure uses a 0.01-stop UI step. Flooring makes rounding unable to
    # reintroduce the highlight mass that protection just removed.
    return float(np.floor(exposure * 100.0 + 1e-9) / 100.0)


def should_skip_batch_origin(origin: object, *, force: bool = False) -> bool:
    """Keep existing user-origin edits out of a non-forced batch."""
    return str(origin or "").strip().lower() == "user" and not force


def auto_tone_settings(linear_base: np.ndarray, current_settings: Mapping[str, Any] | None) -> dict[str, float | int]:
    """Return a deterministic CRs tone patch for a WB'd linear Develop base.

    ``current_settings`` is part of the pure-function contract because the
    caller uses it for the one white-balance pass before this function. Tone
    values themselves are replaced rather than compounded, so repeat presses
    on the same base produce an identical patch.
    """
    if current_settings is not None and not isinstance(current_settings, Mapping):
        raise TypeError("current_settings must be a mapping or None")
    y = _luma(linear_base)
    exposure = _protected_exposure(y)
    adjusted = y * np.exp2(exposure)
    high = float(np.percentile(adjusted, _WHITE_PERCENTILE))
    low = float(np.percentile(adjusted, _BLACK_PERCENTILE))
    clipped_highlight_mass = float(np.mean(adjusted > _HIGHLIGHT_LIMIT))
    shadow_mass = float(np.mean(adjusted < _SHADOW_LIMIT))
    iqr = float(np.percentile(adjusted, 75.0) - np.percentile(adjusted, 25.0))

    return {
        "Exposure2012": exposure,
        "Contrast2012": int(np.clip(np.rint(100.0 * (_CONTRAST_IQR_TARGET - iqr)), -100, 100)),
        "Highlights2012": -int(min(80.0, np.rint(_HIGHLIGHT_K * clipped_highlight_mass))),
        "Shadows2012": int(min(60.0, np.rint(_SHADOW_K2 * shadow_mass))),
        "Whites2012": _tone_slider_for_percentile(high, _WHITE_TARGET, center=C.TONE_EV_WHITES_CENTER, factor=C.TONE_WHITES_FACTOR),
        "Blacks2012": _tone_slider_for_percentile(low, _BLACK_TARGET, center=C.TONE_EV_BLACKS_CENTER, factor=C.TONE_BLACKS_FACTOR),
    }
