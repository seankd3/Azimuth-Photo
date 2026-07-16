"""Opponent-channel reconstruction for clipped scene-linear highlights."""

from __future__ import annotations

import numpy as np


RGB_CHANNEL_COUNT = 3
OPPONENT_CHANNEL_WEIGHT = np.float32(0.5)
DEFAULT_NEAR_CLIP_FRACTION = 0.2


def _opponent_reference(linear_rgb: np.ndarray) -> np.ndarray:
    """Return each channel's cbrt-space mean of the other two channels."""

    perceptual = np.cbrt(np.maximum(linear_rgb, np.float32(0.0)))
    opponent = np.empty_like(perceptual)
    opponent[..., 0] = OPPONENT_CHANNEL_WEIGHT * (perceptual[..., 1] + perceptual[..., 2])
    opponent[..., 1] = OPPONENT_CHANNEL_WEIGHT * (perceptual[..., 0] + perceptual[..., 2])
    opponent[..., 2] = OPPONENT_CHANNEL_WEIGHT * (perceptual[..., 0] + perceptual[..., 1])
    return opponent**3


def reconstruct_highlights(
    linear_rgb: np.ndarray,
    clips: tuple[float, float, float] | np.ndarray,
    *,
    near_clip: float = DEFAULT_NEAR_CLIP_FRACTION,
) -> np.ndarray:
    """Rebuild clipped RGB channels with darktable ``opposed.c`` math.

    ``linear_rgb`` is an HxWx3 scene-linear image and ``clips`` contains the
    saturation boundary in the same scale for each channel. Clipped channels
    use darktable's ``src/iop/hlreconstruct/opposed.c`` opponent reference plus
    global near-clip chrominance; all unclipped samples remain bit-identical.
    """

    rgb = np.asarray(linear_rgb, dtype=np.float32)
    clip_levels = np.asarray(clips, dtype=np.float32).reshape(RGB_CHANNEL_COUNT)
    clipped = rgb >= clip_levels[None, None, :]
    if not np.any(clipped):
        return rgb.copy()

    reference = _opponent_reference(rgb)
    near_clip_floor = np.float32(near_clip) * clip_levels[None, None, :]
    eligible = (rgb > near_clip_floor) & ~clipped
    chrominance = np.zeros(RGB_CHANNEL_COUNT, dtype=np.float32)
    for channel in range(RGB_CHANNEL_COUNT):
        samples = eligible[..., channel]
        if np.any(samples):
            chrominance[channel] = np.mean(
                rgb[..., channel][samples] - reference[..., channel][samples],
                dtype=np.float32,
            )

    rebuilt = np.maximum(rgb, reference + chrominance[None, None, :])
    return np.where(clipped, rebuilt, rgb).astype(np.float32, copy=False)
