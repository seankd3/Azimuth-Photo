"""Pure NumPy implementation of the frozen develop v1 color pipeline.

The input is linear sRGB float data and the result is gamma-encoded sRGB in
the inclusive 0..1 range. Geometry remains outside this module; crop values are
read only so the post-crop vignette is positioned in the same space as export.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence

import numpy as np

from . import ops_constants as C


def _number(settings: Mapping[str, object], key: str, default: float = 0.0) -> float:
    value = settings.get(key, default)
    try:
        return float(value)  # Lightroom XMP values arrive as strings.
    except (TypeError, ValueError):
        return default


def _slider(settings: Mapping[str, object], key: str) -> float:
    return np.clip(_number(settings, key), -100.0, 100.0) / 100.0


def _smoothstep(edge0: float, edge1: float, value: np.ndarray) -> np.ndarray:
    t = np.clip((value - edge0) / (edge1 - edge0), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def linear_to_srgb(linear: np.ndarray) -> np.ndarray:
    """Encode clipped linear RGB with the IEC sRGB transfer curve."""
    linear = np.clip(linear, 0.0, 1.0).astype(np.float32, copy=False)
    return np.where(
        linear <= C.SRGB_LINEAR_THRESHOLD,
        linear * C.SRGB_ENCODE_SCALE,
        C.SRGB_ENCODE_A * np.power(linear, C.SRGB_ENCODE_GAMMA) - C.SRGB_ENCODE_B,
    ).astype(np.float32)


def luma(rgb: np.ndarray) -> np.ndarray:
    return (
        rgb[..., 0] * C.LUMA_RED
        + rgb[..., 1] * C.LUMA_GREEN
        + rgb[..., 2] * C.LUMA_BLUE
    ).astype(np.float32)


def _as_shot_temperature(settings: Mapping[str, object], explicit: float | None) -> float:
    if explicit is not None:
        return float(explicit)
    for key in ("AsShotTemperature", "asshot_temperature", "_asshot_temperature"):
        temperature = _number(settings, key)
        if temperature > 0.0:
            return temperature
    return 5500.0


def _apply_white_balance(
    rgb: np.ndarray, settings: Mapping[str, object], asshot_temperature: float | None
) -> np.ndarray:
    temperature = _number(settings, "Temperature")
    white_balance = str(settings.get("WhiteBalance", "")).strip().lower()
    if temperature <= 0.0 or white_balance == "as shot":
        return rgb
    asshot = max(_as_shot_temperature(settings, asshot_temperature), 1.0)
    dm = 1_000_000.0 / asshot - 1_000_000.0 / max(temperature, 1.0)
    result = rgb.copy()
    result[..., 0] *= np.float32(np.exp2(dm * C.K_TEMP))
    result[..., 2] *= np.float32(np.exp2(-dm * C.K_TEMP))
    result[..., 1] *= np.float32(np.exp2(-_number(settings, "Tint") * C.K_TINT))
    return result


def _soft_clamp(value: np.ndarray) -> np.ndarray:
    above = value > 1.0
    return np.where(
        value < 0.0,
        0.0,
        np.where(above, 1.0 + (value - 1.0) / (1.0 + C.SOFT_CLAMP_FACTOR * (value - 1.0)), value),
    ).astype(np.float32)


def _region_tone_map(rgb: np.ndarray, settings: Mapping[str, object]) -> np.ndarray:
    y = luma(rgb)
    t = np.power(np.clip(y, 0.0, 1.0), 1.0 / C.TONE_GAMMA)
    highlights = _slider(settings, "Highlights2012")
    shadows = _slider(settings, "Shadows2012")
    whites = _slider(settings, "Whites2012")
    blacks = _slider(settings, "Blacks2012")
    wh = _smoothstep(0.45, 1.0, t)
    ws = 1.0 - _smoothstep(0.0, 0.55, t)
    ww = _smoothstep(0.75, 1.0, t)
    wb = 1.0 - _smoothstep(0.0, 0.25, t)
    t2 = (
        t
        + C.TONE_HIGHLIGHTS_FACTOR
        * (highlights * wh * (1.0 - t) + shadows * ws * (1.0 - t) * t * 2.0)
        + C.TONE_WHITES_FACTOR * whites * ww
        + C.TONE_BLACKS_FACTOR * blacks * wb
    )
    t3 = 0.5 + (t2 - 0.5) * (1.0 + C.CONTRAST_FACTOR * _slider(settings, "Contrast2012"))
    t3 = _soft_clamp(t3)
    gain = np.power(t3, C.TONE_GAMMA) / np.maximum(y, C.TONE_EPSILON)
    return (rgb * gain[..., None]).astype(np.float32)


def _curve_points(raw_points: object) -> list[tuple[float, float]]:
    if not isinstance(raw_points, Sequence) or isinstance(raw_points, (str, bytes)):
        return []
    points: list[tuple[float, float]] = []
    for item in raw_points:
        try:
            if isinstance(item, str):
                x_text, y_text = item.split(",", 1)
                x, y = float(x_text), float(y_text)
            else:
                x, y = item  # type: ignore[misc]
                x, y = float(x), float(y)
            points.append((np.clip(x, 0.0, 255.0) / 255.0, np.clip(y, 0.0, 255.0) / 255.0))
        except (TypeError, ValueError):
            continue
    deduped = {x: y for x, y in points}
    return sorted(deduped.items())


def build_monotone_cubic_lut(raw_points: object) -> np.ndarray:
    """Build the 256-entry Fritsch--Carlson curve LUT used by both renderers."""
    points = _curve_points(raw_points)
    samples = np.linspace(0.0, 1.0, C.CURVE_LUT_SIZE, dtype=np.float32)
    if len(points) < 2:
        return samples
    x = np.asarray([point[0] for point in points], dtype=np.float32)
    y = np.asarray([point[1] for point in points], dtype=np.float32)
    dx = np.diff(x)
    slopes = np.diff(y) / dx
    tangents = np.empty_like(x)
    tangents[0], tangents[-1] = slopes[0], slopes[-1]
    for index in range(1, len(x) - 1):
        left, right = slopes[index - 1], slopes[index]
        if left * right <= 0.0:
            tangents[index] = 0.0
        else:
            h_left, h_right = dx[index - 1], dx[index]
            tangents[index] = (h_left + h_right) / (h_right / left + h_left / right)
    for index, slope in enumerate(slopes):
        if slope == 0.0:
            tangents[index] = tangents[index + 1] = 0.0
            continue
        a, b = tangents[index] / slope, tangents[index + 1] / slope
        length = math.hypot(float(a), float(b))
        if length > C.CURVE_MONOTONE_LIMIT:
            scale = C.CURVE_MONOTONE_LIMIT / length
            tangents[index] *= scale
            tangents[index + 1] *= scale
    segment = np.clip(np.searchsorted(x, samples, side="right") - 1, 0, len(x) - 2)
    h = x[segment + 1] - x[segment]
    u = np.clip((samples - x[segment]) / h, 0.0, 1.0)
    h00 = 2.0 * u**3 - 3.0 * u**2 + 1.0
    h10 = u**3 - 2.0 * u**2 + u
    h01 = -2.0 * u**3 + 3.0 * u**2
    h11 = u**3 - u**2
    lut = h00 * y[segment] + h10 * h * tangents[segment] + h01 * y[segment + 1] + h11 * h * tangents[segment + 1]
    return np.clip(lut, 0.0, 1.0).astype(np.float32)


def _apply_lut(channel: np.ndarray, lut: np.ndarray) -> np.ndarray:
    # np.interp is the same linear sampling between the 256 fixed LUT cells,
    # without materializing several full-resolution integer index buffers.
    positions = np.linspace(0.0, 1.0, C.CURVE_LUT_SIZE, dtype=np.float32)
    return np.interp(np.clip(channel, 0.0, 1.0), positions, lut).astype(np.float32)


def _apply_tone_curves(c: np.ndarray, settings: Mapping[str, object]) -> np.ndarray:
    result = c.copy()
    main_lut = build_monotone_cubic_lut(settings.get("ToneCurvePV2012"))
    # A main curve is RGB-linked; component curves are applied after it.
    for index in range(3):
        result[..., index] = _apply_lut(c[..., index], main_lut)
    for index, suffix in enumerate(("Red", "Green", "Blue")):
        result[..., index] = _apply_lut(
            result[..., index], build_monotone_cubic_lut(settings.get(f"ToneCurvePV2012{suffix}"))
        )
    return result


def rgb_to_hsv(rgb: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    red, green, blue = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    maximum = np.maximum(np.maximum(red, green), blue)
    minimum = np.minimum(np.minimum(red, green), blue)
    delta = maximum - minimum
    hue = np.zeros_like(maximum)
    nonzero = delta > 0.0
    red_max = nonzero & (maximum == red)
    green_max = nonzero & (maximum == green)
    blue_max = nonzero & (maximum == blue)
    hue[red_max] = np.mod((green[red_max] - blue[red_max]) / delta[red_max], 6.0)
    hue[green_max] = (blue[green_max] - red[green_max]) / delta[green_max] + 2.0
    hue[blue_max] = (red[blue_max] - green[blue_max]) / delta[blue_max] + 4.0
    hue = np.mod(hue * 60.0, 360.0)
    saturation = np.divide(delta, maximum, out=np.zeros_like(delta), where=maximum > 0.0)
    return hue.astype(np.float32), saturation.astype(np.float32), maximum.astype(np.float32)


def hsv_to_rgb(hue: np.ndarray, saturation: np.ndarray, value: np.ndarray) -> np.ndarray:
    h = np.mod(hue, 360.0) / 60.0
    chroma = value * saturation
    x = chroma * (1.0 - np.abs(np.mod(h, 2.0) - 1.0))
    zeros = np.zeros_like(chroma)
    rgb_prime = np.stack(
        (
            np.select([h < 1, h < 2, h < 3, h < 4, h < 5], [chroma, x, zeros, zeros, x], default=chroma),
            np.select([h < 1, h < 2, h < 3, h < 4, h < 5], [x, chroma, chroma, x, zeros], default=zeros),
            np.select([h < 1, h < 2, h < 3, h < 4, h < 5], [zeros, zeros, x, chroma, chroma], default=x),
        ),
        axis=-1,
    )
    return (rgb_prime + (value - chroma)[..., None]).astype(np.float32)


def hsl_band_weights(hue: np.ndarray) -> np.ndarray:
    """Raised-cosine, circular weights with exactly two active adjacent bands."""
    centers = np.asarray(C.BAND_CENTERS, dtype=np.float32)
    wrapped = np.mod(hue, 360.0)
    extended = np.concatenate((centers, [centers[0] + 360.0]))
    right = np.searchsorted(extended, wrapped, side="right")
    right = np.where(right == len(extended), 1, right)
    left = right - 1
    left = np.where(left == len(centers), len(centers) - 1, left)
    right_center = extended[right]
    left_center = extended[left]
    span = right_center - left_center
    # ``extended`` supplies the 360-degree right edge for the final segment;
    # hue itself is already in that final interval and must not be shifted.
    position = wrapped
    fraction = (position - left_center) / span
    left_weight = 0.5 * (1.0 + np.cos(np.pi * fraction))
    weights = np.zeros(hue.shape + (len(centers),), dtype=np.float32)
    flat = weights.reshape(-1, len(centers))
    indices = np.arange(flat.shape[0])
    flat[indices, left.reshape(-1)] = left_weight.reshape(-1)
    flat[indices, (right % len(centers)).reshape(-1)] += 1.0 - left_weight.reshape(-1)
    return weights


def _hsl_and_black_white(c: np.ndarray, settings: Mapping[str, object], dehaze: float) -> np.ndarray:
    hue, saturation, value = rgb_to_hsv(c)
    source_hue = hue.copy()
    neutral = _smoothstep(C.NEUTRAL_PROTECT_START, C.NEUTRAL_PROTECT_END, saturation)
    centers = np.asarray(C.BAND_CENTERS, dtype=np.float32)

    def segments():
        for index, left_center in enumerate(centers):
            right_index = (index + 1) % len(centers)
            right_center = centers[right_index] + (360.0 if right_index == 0 else 0.0)
            position = np.where(source_hue < left_center, source_hue + 360.0, source_hue)
            mask = (position >= left_center) & (position < right_center)
            yield index, right_index, mask, (position[mask] - left_center) / (right_center - left_center)

    if str(settings.get("ConvertToGrayscale", "False")).strip().lower() == "true":
        gray = luma(c)
        for index, right_index, mask, fraction in segments():
            left_weight = 0.5 * (1.0 + np.cos(np.pi * fraction))
            mixer = (
                left_weight * _slider(settings, f"GrayMixer{C.BAND_NAMES[index]}")
                + (1.0 - left_weight) * _slider(settings, f"GrayMixer{C.BAND_NAMES[right_index]}")
            )
            gray[mask] *= 1.0 + mixer * C.GRAY_MIXER_FACTOR
        return np.repeat(gray[..., None], 3, axis=-1).astype(np.float32)
    for index, right_index, mask, fraction in segments():
        left_weight = 0.5 * (1.0 + np.cos(np.pi * fraction))
        right_weight = 1.0 - left_weight
        hue_adjustment = (
            left_weight * _slider(settings, f"HueAdjustment{C.BAND_NAMES[index]}")
            + right_weight * _slider(settings, f"HueAdjustment{C.BAND_NAMES[right_index]}")
        )
        saturation_adjustment = (
            left_weight * _slider(settings, f"SaturationAdjustment{C.BAND_NAMES[index]}")
            + right_weight * _slider(settings, f"SaturationAdjustment{C.BAND_NAMES[right_index]}")
        )
        luminance_adjustment = (
            left_weight * _slider(settings, f"LuminanceAdjustment{C.BAND_NAMES[index]}")
            + right_weight * _slider(settings, f"LuminanceAdjustment{C.BAND_NAMES[right_index]}")
        )
        hue[mask] += hue_adjustment * neutral[mask] * C.HUE_SHIFT_DEGREES
        saturation[mask] *= 1.0 + saturation_adjustment * neutral[mask]
        value[mask] *= 1.0 + luminance_adjustment * neutral[mask] * C.HSL_LUMINANCE_FACTOR
    saturation *= 1.0 + _slider(settings, "Saturation") + C.DEHAZE_SATURATION_FACTOR * dehaze
    saturation += _slider(settings, "Vibrance") * (1.0 - saturation) * saturation * C.VIBRANCE_FACTOR
    return hsv_to_rgb(hue, np.clip(saturation, 0.0, 1.0), value)


def gaussian_blur(field: np.ndarray, sigma: float) -> np.ndarray:
    """Reflect-padded separable Gaussian blur using only NumPy convolution."""
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


def _detail(c: np.ndarray, settings: Mapping[str, object], *, blur_min_dimension: int | None = None) -> np.ndarray:
    lightness = luma(c)
    result = c.copy()
    minimum_dimension = blur_min_dimension or min(lightness.shape)
    clarity = _slider(settings, "Clarity2012")
    if clarity != 0.0:
        large = gaussian_blur(lightness, C.BLUR_LARGE_FACTOR * minimum_dimension)
        midtones = np.clip(4.0 * lightness * (1.0 - lightness), 0.0, 1.0)
        result += ((lightness - large) * C.CLARITY_FACTOR * clarity * midtones)[..., None]
    texture = _slider(settings, "Texture")
    if texture != 0.0:
        small = gaussian_blur(lightness, C.BLUR_SMALL_FACTOR * minimum_dimension)
        result += ((lightness - small) * C.TEXTURE_FACTOR * texture)[..., None]
    sharpness = np.clip(_number(settings, "Sharpness"), 0.0, 150.0)
    if sharpness != 0.0:
        radius = np.clip(_number(settings, "SharpenRadius", 1.0), 0.5, 3.0)
        sharp = gaussian_blur(lightness, radius)
        result += ((lightness - sharp) * (sharpness / 150.0) * C.SHARPEN_FACTOR)[..., None]
    return result


def _vignette(
    c: np.ndarray,
    settings: Mapping[str, object],
    *,
    pixel_offset: tuple[int, int] = (0, 0),
    canvas_size: tuple[int, int] | None = None,
) -> np.ndarray:
    amount = _slider(settings, "PostCropVignetteAmount")
    if amount == 0.0:
        return c
    height, width = c.shape[:2]
    canvas_width, canvas_height = canvas_size or (width, height)
    offset_x, offset_y = pixel_offset
    left = np.clip(_number(settings, "CropLeft", 0.0), 0.0, 1.0)
    top = np.clip(_number(settings, "CropTop", 0.0), 0.0, 1.0)
    right = np.clip(_number(settings, "CropRight", 1.0), left + C.TONE_EPSILON, 1.0)
    bottom = np.clip(_number(settings, "CropBottom", 1.0), top + C.TONE_EPSILON, 1.0)
    x = (np.arange(width, dtype=np.float32) + offset_x + 0.5) / canvas_width
    y = (np.arange(height, dtype=np.float32) + offset_y + 0.5) / canvas_height
    center_x, center_y = (left + right) * 0.5, (top + bottom) * 0.5
    crop_aspect = (right - left) * canvas_width / max((bottom - top) * canvas_height, 1.0)
    roundness = 1.0 + _slider(settings, "PostCropVignetteRoundness") * C.VIGNETTE_ROUNDNESS_FACTOR
    dx = (x[None, :] - center_x) * 2.0 * crop_aspect / max(roundness, C.TONE_EPSILON)
    dy = (y[:, None] - center_y) * 2.0 * max(roundness, C.TONE_EPSILON)
    rho = np.sqrt(dx * dx + dy * dy)
    midpoint = C.VIGNETTE_MIDPOINT_MIN + np.clip(_number(settings, "PostCropVignetteMidpoint", 50.0), 0.0, 100.0) / 100.0 * C.VIGNETTE_MIDPOINT_RANGE
    feather = C.VIGNETTE_FEATHER_MIN + np.clip(_number(settings, "PostCropVignetteFeather", 50.0), 0.0, 100.0) / 100.0 * C.VIGNETTE_FEATHER_RANGE
    strength = _smoothstep(midpoint, midpoint + feather, rho) * amount * C.VIGNETTE_FACTOR
    if amount < 0.0:
        return c * (1.0 + strength[..., None])
    # Mixing toward white is the screen blend with white and naturally protects highlights.
    return c + (1.0 - c) * strength[..., None]


def grain_hash_u32(x: np.ndarray | int, y: np.ndarray | int, seed: int = C.GRAIN_SEED) -> np.ndarray:
    """The shared unsigned-32-bit grain hash before its 16-bit normalization."""
    x32 = np.asarray(x, dtype=np.uint32)
    y32 = np.asarray(y, dtype=np.uint32)
    with np.errstate(over="ignore"):
        h = (x32 * np.uint32(C.GRAIN_X_MULTIPLIER) + y32 * np.uint32(C.GRAIN_Y_MULTIPLIER)) ^ np.uint32(seed)
        h = (h ^ (h >> np.uint32(C.GRAIN_HASH_SHIFT))) * np.uint32(C.GRAIN_HASH_MULTIPLIER)
    return h


def grain_hash(x: np.ndarray | int, y: np.ndarray | int, seed: int = C.GRAIN_SEED) -> np.ndarray:
    """The shared unsigned-32-bit grain hash, returned as a [0, 1] float."""
    h = grain_hash_u32(x, y, seed)
    return ((h >> np.uint32(C.GRAIN_OUTPUT_SHIFT)) & np.uint32(C.GRAIN_OUTPUT_MASK)).astype(np.float32) / C.GRAIN_OUTPUT_DIVISOR


def _grain(c: np.ndarray, settings: Mapping[str, object], *, pixel_offset: tuple[int, int] = (0, 0)) -> np.ndarray:
    amount = _slider(settings, "GrainAmount")
    if amount == 0.0:
        return c
    cell_size = C.GRAIN_CELL_SIZE_MIN + np.clip(_number(settings, "GrainSize", 25.0), 0.0, 100.0) / 100.0 * C.GRAIN_CELL_SIZE_RANGE
    height, width = c.shape[:2]
    offset_x, offset_y = pixel_offset
    x = ((np.arange(width) + offset_x) / cell_size).astype(np.int64)
    y = ((np.arange(height) + offset_y) / cell_size).astype(np.int64)
    noise = grain_hash(x[None, :], y[:, None]) - 0.5
    return c + noise[..., None] * amount * C.GRAIN_FACTOR


def apply_pipeline(
    linear_rgb: np.ndarray,
    settings: Mapping[str, object] | None = None,
    *,
    asshot_temperature: float | None = None,
    pixel_offset: tuple[int, int] = (0, 0),
    canvas_size: tuple[int, int] | None = None,
    blur_min_dimension: int | None = None,
) -> np.ndarray:
    """Apply v1 color operations in their frozen order and return float32 sRGB."""
    settings = settings or {}
    rgb = np.asarray(linear_rgb, dtype=np.float32)
    if rgb.ndim != 3 or rgb.shape[-1] != 3:
        raise ValueError("linear_rgb must have shape (height, width, 3)")
    rgb = _apply_white_balance(rgb, settings, asshot_temperature)
    rgb *= np.float32(np.exp2(_number(settings, "Exposure2012")))
    rgb = _region_tone_map(rgb, settings)
    dehaze = _slider(settings, "Dehaze")
    if dehaze != 0.0:
        rgb = (rgb - C.DEHAZE_AIRLIGHT_FACTOR * dehaze) / (1.0 - C.DEHAZE_AIRLIGHT_FACTOR * dehaze)
        rgb = np.maximum(rgb, 0.0)
    c = linear_to_srgb(rgb)
    c = _apply_tone_curves(c, settings)
    c = _hsl_and_black_white(c, settings, dehaze)
    c = _detail(c, settings, blur_min_dimension=blur_min_dimension)
    c = _vignette(c, settings, pixel_offset=pixel_offset, canvas_size=canvas_size)
    c = _grain(c, settings, pixel_offset=pixel_offset)
    return np.clip(c, 0.0, 1.0).astype(np.float32)


# Intentional explicit alias for route/render callers.
render_pipeline = apply_pipeline
