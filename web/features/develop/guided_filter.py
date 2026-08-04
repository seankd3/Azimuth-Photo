"""Reusable grey guided-filter primitives for Develop mask refine and tone.

Classic He–Sun guided filter uses O(n) integral-image box means. EIGF normalizes
variance by exposure so shadow and highlight edges stick equally on linear guides.
Feather → (radius, ε) follows darktable tone-equalizer conventions: larger Feather
widens the region average; ε = 1/feathering so higher Feather sticks harder to edges.

Large rasters use the darktable fast path: moments at 1/subsample, upsample a/b.
"""

from __future__ import annotations

import math

import numpy as np

from . import ops_constants as C


def box_mean(values: np.ndarray, radius: int) -> np.ndarray:
    """Edge-padded box average via integral image — O(n) in pixel count."""

    field = np.asarray(values, dtype=np.float32)
    if radius <= 0:
        return field.copy()
    padded = np.pad(field, ((radius, radius), (radius, radius)), mode="edge")
    integral = np.pad(padded, ((1, 0), (1, 0)), mode="constant").cumsum(axis=0).cumsum(axis=1)
    span = radius * 2 + 1
    return (
        integral[span:, span:] - integral[:-span, span:] - integral[span:, :-span] + integral[:-span, :-span]
    ) / float(span * span)


def _downsample(field: np.ndarray, subsample: int) -> np.ndarray:
    """Area-average downsample used by darktable's fast guided filter."""

    height, width = field.shape[:2]
    trimmed_h = height - (height % subsample)
    trimmed_w = width - (width % subsample)
    if trimmed_h <= 0 or trimmed_w <= 0:
        return field[::subsample, ::subsample].astype(np.float32, copy=False)
    blocks = field[:trimmed_h, :trimmed_w].reshape(
        trimmed_h // subsample, subsample, trimmed_w // subsample, subsample
    )
    return blocks.mean(axis=(1, 3), dtype=np.float64).astype(np.float32)


def _upsample(field: np.ndarray, width: int, height: int) -> np.ndarray:
    """Nearest-neighbor upsample of low-res a/b, then one box smooth."""

    source_height, source_width = field.shape[:2]
    if (source_width, source_height) == (width, height):
        return field.astype(np.float32, copy=False)
    y = np.minimum((np.arange(height) * source_height) // height, source_height - 1)
    x = np.minimum((np.arange(width) * source_width) // width, source_width - 1)
    return field[y[:, None], x[None, :]].astype(np.float32, copy=False)


def _guided_coefficients(
    guide: np.ndarray,
    source: np.ndarray,
    radius: int,
    epsilon: float,
) -> tuple[np.ndarray, np.ndarray]:
    mean_guide = box_mean(guide, radius)
    mean_source = box_mean(source, radius)
    variance = box_mean(guide * guide, radius) - mean_guide * mean_guide
    covariance = box_mean(guide * source, radius) - mean_guide * mean_source
    a = covariance / (variance + epsilon)
    b = mean_source - a * mean_guide
    return box_mean(a, radius), box_mean(b, radius)


def guided_filter(
    guide: np.ndarray,
    source: np.ndarray,
    radius: int,
    epsilon: float = C.GUIDED_EPSILON_DEFAULT,
    *,
    fast: bool = True,
) -> np.ndarray:
    """Classic grey guided filter (He–Sun): mean(a)·I + mean(b)."""

    guide_f = np.asarray(guide, dtype=np.float32)
    source_f = np.asarray(source, dtype=np.float32)
    radius = max(0, int(radius))
    epsilon = float(max(epsilon, C.LOCAL_RANGE_EPSILON))
    height, width = guide_f.shape[:2]
    subsample = int(C.GUIDED_FAST_SUBSAMPLE)
    if (
        fast
        and subsample > 1
        and min(height, width) >= C.GUIDED_FAST_MIN_SIDE
        and radius >= subsample
    ):
        small_radius = max(1, radius // subsample)
        small_guide = _downsample(guide_f, subsample)
        small_source = _downsample(source_f, subsample)
        mean_a, mean_b = _guided_coefficients(small_guide, small_source, small_radius, epsilon)
        mean_a = box_mean(_upsample(mean_a, width, height), max(1, subsample // 2))
        mean_b = box_mean(_upsample(mean_b, width, height), max(1, subsample // 2))
        return mean_a * guide_f + mean_b
    mean_a, mean_b = _guided_coefficients(guide_f, source_f, radius, epsilon)
    return mean_a * guide_f + mean_b


def eigf_guided_filter(
    guide: np.ndarray,
    source: np.ndarray,
    radius: int,
    epsilon: float = C.GUIDED_EPSILON_DEFAULT,
) -> np.ndarray:
    """Exposure-independent guided filter (darktable eigf.h linearization).

    Not selected by anything today. It came out of the darktable study, which
    ranked guided filtering as worth adopting, and it keeps its test — but the
    `mode='eigf'` switch that used to reach it was never flipped by any caller,
    so the switch is gone and this is a reference implementation until a caller
    asks for it by name.

    Uses normalized variance ``var / (avg·I)`` and skips the final coeff blur so
    bright edges do not halo when the guide spans many stops.
    """

    guide_f = np.asarray(guide, dtype=np.float32)
    source_f = np.asarray(source, dtype=np.float32)
    radius = max(0, int(radius))
    epsilon = float(max(epsilon, C.LOCAL_RANGE_EPSILON))
    mean_guide = box_mean(guide_f, radius)
    mean_source = box_mean(source_f, radius)
    variance = box_mean(guide_f * guide_f, radius) - mean_guide * mean_guide
    covariance = box_mean(guide_f * source_f, radius) - mean_guide * mean_source
    norm_guide = np.maximum(mean_guide * guide_f, C.LOCAL_RANGE_EPSILON)
    norm_source = np.maximum(mean_source * source_f, C.LOCAL_RANGE_EPSILON)
    normalized_var = variance / norm_guide
    normalized_cov = covariance / np.sqrt(norm_guide * norm_source)
    a = normalized_cov / (normalized_var + epsilon)
    b = mean_source - a * mean_guide
    return a * guide_f + b


def feather_to_guided_params(feather: float, width: int, height: int) -> tuple[int, float]:
    """Map an existing 0–1 Feather slider to guided radius and ε."""

    amount = float(np.clip(feather, 0.0, 1.0))
    min_side = max(1, min(int(width), int(height)))
    radius = max(1, int(round(amount * min_side * C.GUIDED_RADIUS_FRACTION)))
    feathering = C.GUIDED_FEATHERING_MIN + amount * C.GUIDED_FEATHERING_RANGE
    epsilon = 1.0 / max(feathering, C.LOCAL_RANGE_EPSILON)
    return radius, float(epsilon)


def _gaussian_soft(field: np.ndarray, sigma: float) -> np.ndarray:
    """Separable reflect-padded gaussian — the pre-guided mask softener."""

    if sigma <= 0.0:
        return field.astype(np.float32, copy=True)
    radius = max(1, int(math.ceil(C.GAUSSIAN_TRUNCATE * sigma)))
    x = np.arange(-radius, radius + 1, dtype=np.float32)
    kernel = np.exp(-0.5 * (x / sigma) ** 2)
    kernel /= kernel.sum()
    padded_x = np.pad(field, ((0, 0), (radius, radius)), mode="reflect")
    horizontal = np.apply_along_axis(lambda row: np.convolve(row, kernel, mode="valid"), 1, padded_x)
    padded_y = np.pad(horizontal, ((radius, radius), (0, 0)), mode="reflect")
    return np.apply_along_axis(lambda column: np.convolve(column, kernel, mode="valid"), 0, padded_y).astype(np.float32)


def soft_mask(
    mask: np.ndarray,
    radius: float | int,
    *,
    guide: np.ndarray | None = None,
    epsilon: float = C.GUIDED_EPSILON_DEFAULT,
) -> np.ndarray:
    """Edge-aware softener that wraps the old gaussian blur path.

    With a guide, runs the guided filter. Without one, falls back to the
    separable gaussian used historically for mask softening (sigma ≈ radius/2).
    """

    field = np.asarray(mask, dtype=np.float32)
    if guide is None:
        sigma = max(float(radius), 0.0) * C.GUIDED_GAUSSIAN_SIGMA_SCALE
        return _gaussian_soft(field, sigma) if sigma > 0.0 else field.copy()
    radius_i = max(0, int(math.ceil(float(radius))))
    if radius_i <= 0:
        return field.copy()
    return guided_filter(guide, field, radius_i, epsilon)


def refine_mask(
    mask: np.ndarray,
    guide: np.ndarray,
    *,
    feather: float = C.GUIDED_DEFAULT_FEATHER,
    radius: int | None = None,
    epsilon: float | None = None,
) -> np.ndarray:
    """Stick a combined correction mask to guide edges using Feather mapping."""

    field = np.asarray(mask, dtype=np.float32)
    guide_f = np.asarray(guide, dtype=np.float32)
    height, width = field.shape[:2]
    if radius is None or epsilon is None:
        mapped_radius, mapped_epsilon = feather_to_guided_params(feather, width, height)
        radius = mapped_radius if radius is None else radius
        epsilon = mapped_epsilon if epsilon is None else epsilon
    if float(feather) <= C.LOCAL_RANGE_EPSILON and radius is not None and int(radius) <= 0:
        return field.copy()
    return soft_mask(field, int(radius), guide=guide_f, epsilon=float(epsilon))
