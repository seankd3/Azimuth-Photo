"""Shared NumPy mask rasterization and local-correction math.

Adobe mask geometry is stored verbatim in settings JSON. Raster outputs are
quarter-resolution float fields, matching ``mask_raster.js`` and the GL atlas.
"""

from __future__ import annotations

import logging
import math
import re
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

import numpy as np
from PIL import Image

from core.runtime_paths import resolve_runtime_paths
from . import ops_constants as C


_LOG = logging.getLogger(__name__)
_NUMBER_RE = re.compile(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?")
_LOCAL_KEYS = (
    "LocalExposure2012", "LocalContrast2012", "LocalHighlights2012", "LocalShadows2012",
    "LocalWhites2012", "LocalBlacks2012", "LocalClarity2012", "LocalDehaze",
    "LocalTexture", "LocalTemperature", "LocalTint", "LocalSaturation", "LocalHue",
)


def _number(values: Mapping[str, object], key: str, default: float = 0.0) -> float:
    try:
        value = float(values.get(key, default))
        return value if math.isfinite(value) else default
    except (TypeError, ValueError):
        return default


def _truth(value: object, default: bool = False) -> bool:
    if value is None:
        return default
    return value is True or value == 1 or str(value).strip().lower() == "true"


def localToSlider(local: Mapping[str, object], key: str) -> float:
    """Convert one Adobe-native local fraction at the sole normalization seam.

    Exposure returns EV (catalog evidence: stored value × 4). Temperature
    returns mired delta; every other rendered local returns its ±100 slider.
    """
    value = float(np.clip(_number(local, key), -1.0, 1.0))
    if key == "LocalExposure2012":
        return value * C.LOCAL_EXPOSURE_EV_SCALE
    if key == "LocalTemperature":
        return value * C.LOCAL_WB_MIRED_SCALE
    return value * C.LOCAL_SLIDER_SCALE


local_to_slider = localToSlider


def _smoothstep(edge0: float, edge1: float, value: np.ndarray) -> np.ndarray:
    if abs(edge1 - edge0) <= C.LOCAL_RANGE_EPSILON:
        return (value >= edge1).astype(np.float32)
    t = np.clip((value - edge0) / (edge1 - edge0), 0.0, 1.0)
    return (t * t * (3.0 - 2.0 * t)).astype(np.float32)


def _grid(
    width: int,
    height: int,
    *,
    pixel_offset: tuple[int, int] = (0, 0),
    region_size: tuple[int, int] | None = None,
    canvas_size: tuple[int, int] | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    region_width, region_height = region_size or (width, height)
    canvas_width, canvas_height = canvas_size or (region_width, region_height)
    offset_x, offset_y = pixel_offset
    x = (offset_x + (np.arange(width, dtype=np.float32) + 0.5) * region_width / width) / max(canvas_width, 1)
    y = (offset_y + (np.arange(height, dtype=np.float32) + 0.5) * region_height / height) / max(canvas_height, 1)
    return x[None, :], y[:, None]


def rasterize_gradient(
    mask: Mapping[str, object], width: int, height: int, *, grid: tuple[np.ndarray, np.ndarray] | None = None
) -> np.ndarray:
    x, y = grid or _grid(width, height)
    zero_x, zero_y = _number(mask, "ZeroX"), _number(mask, "ZeroY")
    full_x, full_y = _number(mask, "FullX", 1.0), _number(mask, "FullY")
    dx, dy = full_x - zero_x, full_y - zero_y
    length2 = dx * dx + dy * dy
    if length2 <= C.LOCAL_RANGE_EPSILON:
        return np.zeros((height, width), dtype=np.float32)
    projection = ((x - zero_x) * dx + (y - zero_y) * dy) / length2
    return _smoothstep(0.0, 1.0, np.broadcast_to(projection, (height, width)))


def rasterize_radial(
    mask: Mapping[str, object], width: int, height: int, *, grid: tuple[np.ndarray, np.ndarray] | None = None
) -> np.ndarray:
    x, y = grid or _grid(width, height)
    left, right = _number(mask, "Left"), _number(mask, "Right", 1.0)
    top, bottom = _number(mask, "Top"), _number(mask, "Bottom", 1.0)
    center_x, center_y = (left + right) * 0.5, (top + bottom) * 0.5
    radius_x = max(abs(right - left) * 0.5, C.LOCAL_RANGE_EPSILON)
    radius_y = max(abs(bottom - top) * 0.5, C.LOCAL_RANGE_EPSILON)
    angle = math.radians(_number(mask, "Angle"))
    cosine, sine = math.cos(angle), math.sin(angle)
    dx, dy = x - center_x, y - center_y
    rotated_x = cosine * dx + sine * dy
    rotated_y = -sine * dx + cosine * dy
    rho = np.sqrt((rotated_x / radius_x) ** 2 + (rotated_y / radius_y) ** 2)
    feather = float(np.clip(_number(mask, "Feather", 0.5), 0.0, 1.0))
    if feather <= C.LOCAL_RANGE_EPSILON:
        result = (rho <= 1.0).astype(np.float32)
    else:
        result = 1.0 - _smoothstep(1.0, 1.0 + feather, rho)
    if _truth(mask.get("Flipped")):
        result = 1.0 - result
    return result.astype(np.float32)


def _brush_dabs(raw: object) -> list[tuple[float, float, float]]:
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return []
    dabs: list[list[float | None]] = []
    current_radius = C.LOCAL_BRUSH_DEFAULT_RADIUS
    for item in raw:
        parts = str(item).strip().split()
        try:
            if parts and parts[0].lower() == "d" and len(parts) >= 3:
                dabs.append([float(parts[1]), float(parts[2]), None])
            elif parts and parts[0].lower() == "r" and len(parts) >= 2:
                current_radius = max(float(parts[1]), C.LOCAL_RANGE_EPSILON)
                if dabs and dabs[-1][2] is None:
                    dabs[-1][2] = current_radius
        except ValueError:
            continue
    return [(float(x), float(y), float(radius if radius is not None else current_radius)) for x, y, radius in dabs]


def rasterize_brush(
    mask: Mapping[str, object],
    width: int,
    height: int,
    *,
    grid: tuple[np.ndarray, np.ndarray] | None = None,
    canvas_size: tuple[int, int] | None = None,
) -> np.ndarray:
    x, y = grid or _grid(width, height)
    canvas_width, canvas_height = canvas_size or (width, height)
    longest = max(canvas_width, canvas_height, 1)
    flow = _number(mask, "Flow", 1.0)
    if flow > 1.0:
        flow /= 100.0
    flow = float(np.clip(flow, 0.0, 1.0))
    hardness = _number(mask, "CenterWeight", 0.0)
    if hardness > 1.0:
        hardness /= 100.0
    hardness = float(np.clip(hardness, 0.0, 1.0))
    result = np.zeros((height, width), dtype=np.float32)
    # Catalog row 50607 on an 8192×5464 image has reference/dab y=.701664,
    # beyond the .667 long-edge limit: x is /width and y is /height. Radius
    # remains a long-edge fraction, matching Lightroom's circular brush size.
    for dab_x, dab_y, radius in _brush_dabs(mask.get("Dabs")):
        distance = np.sqrt(((x - dab_x) * canvas_width) ** 2 + ((y - dab_y) * canvas_height) ** 2)
        radius_px = max(radius * longest, C.LOCAL_RANGE_EPSILON)
        hard_radius = radius_px * hardness
        soft_width = max(radius_px - hard_radius, C.LOCAL_RANGE_EPSILON)
        t = np.maximum(distance - hard_radius, 0.0) / soft_width
        stamp = np.exp(-0.5 * (t / C.LOCAL_BRUSH_GAUSSIAN_SIGMA) ** 2)
        stamp = np.where(distance <= radius_px, stamp, 0.0)
        result = np.minimum(result + stamp.astype(np.float32) * flow, 1.0)
    return result


def _quad(raw: object) -> tuple[float, float, float, float]:
    if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
        values = [float(value) for value in raw[:4]]
    else:
        values = [float(value) for value in _NUMBER_RE.findall(str(raw or ""))[:4]]
    if len(values) != 4:
        return 0.0, 0.0, 1.0, 1.0
    if max(abs(value) for value in values) > 1.0:
        values = [value / 255.0 for value in values]
    return tuple(float(np.clip(value, 0.0, 1.0)) for value in values)  # type: ignore[return-value]


def _resize_bilinear(field: np.ndarray, width: int, height: int) -> np.ndarray:
    source_height, source_width = field.shape[:2]
    if (source_width, source_height) == (width, height):
        return field.astype(np.float32, copy=False)
    x = (np.arange(width, dtype=np.float32) + 0.5) * source_width / width - 0.5
    y = (np.arange(height, dtype=np.float32) + 0.5) * source_height / height - 0.5
    x = np.clip(x, 0.0, source_width - 1.0)
    y = np.clip(y, 0.0, source_height - 1.0)
    x0 = np.floor(x).astype(np.intp)
    y0 = np.floor(y).astype(np.intp)
    x1, y1 = np.minimum(x0 + 1, source_width - 1), np.minimum(y0 + 1, source_height - 1)
    wx = (x - x0).reshape((1, width) + (1,) * (field.ndim - 2))
    wy = (y - y0).reshape((height, 1) + (1,) * (field.ndim - 2))
    top = field[y0[:, None], x0[None, :]] * (1.0 - wx) + field[y0[:, None], x1[None, :]] * wx
    bottom = field[y1[:, None], x0[None, :]] * (1.0 - wx) + field[y1[:, None], x1[None, :]] * wx
    return (top * (1.0 - wy) + bottom * wy).astype(np.float32)


def _sample_bilinear_grid(field: np.ndarray, x_normalized: np.ndarray, y_normalized: np.ndarray) -> np.ndarray:
    source_height, source_width = field.shape[:2]
    x = np.clip(x_normalized * source_width - 0.5, 0.0, source_width - 1.0)
    y = np.clip(y_normalized * source_height - 0.5, 0.0, source_height - 1.0)
    x0, y0 = np.floor(x).astype(np.intp), np.floor(y).astype(np.intp)
    x1, y1 = np.minimum(x0 + 1, source_width - 1), np.minimum(y0 + 1, source_height - 1)
    wx, wy = x - x0, y - y0
    top = field[y0, x0] * (1.0 - wx) + field[y0, x1] * wx
    bottom = field[y1, x0] * (1.0 - wx) + field[y1, x1] * wx
    return (top * (1.0 - wy) + bottom * wy).astype(np.float32)


def _image_rgb(image: np.ndarray | None, width: int, height: int) -> np.ndarray:
    if image is None:
        return np.zeros((height, width, 3), dtype=np.float32)
    rgb = np.asarray(image)
    if rgb.ndim != 3 or rgb.shape[-1] < 3:
        raise ValueError("range-mask image must have RGB or RGBA channels")
    rgb = rgb[..., :3].astype(np.float32)
    if np.issubdtype(np.asarray(image).dtype, np.integer):
        rgb /= np.iinfo(np.asarray(image).dtype).max
    return _resize_bilinear(rgb, width, height)


def rasterize_luminance_range(range_mask: Mapping[str, object], image: np.ndarray, width: int, height: int) -> np.ndarray:
    rgb = _image_rgb(image, width, height)
    luminance = rgb[..., 0] * C.LUMA_RED + rgb[..., 1] * C.LUMA_GREEN + rgb[..., 2] * C.LUMA_BLUE
    low_soft, low, high, high_soft = _quad(range_mask.get("LumRange"))
    return (_smoothstep(low_soft, low, luminance) * (1.0 - _smoothstep(high, high_soft, luminance))).astype(np.float32)


def _linear_to_oklab(linear: np.ndarray) -> np.ndarray:
    lms = linear @ np.asarray(C.OKLAB_M1, dtype=np.float32).T
    lms = np.sign(lms) * np.power(np.abs(lms), 1.0 / 3.0)
    return (lms @ np.asarray(C.OKLAB_M2, dtype=np.float32).T).astype(np.float32)


def _srgb_to_linear(srgb: np.ndarray) -> np.ndarray:
    srgb = np.clip(srgb, 0.0, 1.0).astype(np.float32, copy=False)
    return np.where(
        srgb <= C.SRGB_DECODE_THRESHOLD,
        srgb * C.SRGB_DECODE_SCALE,
        np.power((srgb + C.SRGB_DECODE_A) / C.SRGB_ENCODE_A, C.SRGB_DECODE_GAMMA),
    ).astype(np.float32)


def _linear_to_srgb(linear: np.ndarray) -> np.ndarray:
    linear = np.clip(linear, 0.0, 1.0).astype(np.float32, copy=False)
    return np.where(
        linear <= C.SRGB_LINEAR_THRESHOLD,
        linear * C.SRGB_ENCODE_SCALE,
        C.SRGB_ENCODE_A * np.power(linear, C.SRGB_ENCODE_GAMMA) - C.SRGB_ENCODE_B,
    ).astype(np.float32)


def _sample_colors(range_mask: Mapping[str, object]) -> list[tuple[float, float, float]]:
    """Read both our simple samples and Lightroom's PointModels resources."""
    raw = next((range_mask.get(key) for key in ("SampledColors", "ColorSamples", "Colors", "PointModels") if range_mask.get(key) is not None), [])
    if isinstance(raw, (str, bytes)):
        raw = [raw]
    if isinstance(raw, Mapping):
        raw = raw.get("PointModel") or raw.get("Color") or raw.get("RGB") or [raw]
    colors: list[tuple[float, float, float]] = []
    if isinstance(raw, Sequence):
        for item in raw:
            if isinstance(item, Mapping):
                item = item.get("Color") or item.get("RGB") or item.get("Value") or item
            values = [float(value) for value in _NUMBER_RE.findall(str(item))[:3]]
            if len(values) == 3:
                if max(abs(value) for value in values) > 1.0:
                    values = [value / 255.0 for value in values]
                colors.append(tuple(float(np.clip(value, 0.0, 1.0)) for value in values))
    return colors


def rasterize_color_range(range_mask: Mapping[str, object], image: np.ndarray, width: int, height: int) -> np.ndarray:
    colors = _sample_colors(range_mask)
    if not colors:
        return np.zeros((height, width), dtype=np.float32)
    rgb = _image_rgb(image, width, height)
    lab = _linear_to_oklab(_srgb_to_linear(rgb))
    samples = _linear_to_oklab(_srgb_to_linear(np.asarray(colors, dtype=np.float32)))
    distance2 = np.min(np.sum((lab[..., None, 1:3] - samples[None, None, :, 1:3]) ** 2, axis=-1), axis=-1)
    amount = _number(range_mask, "ColorAmount", 0.5)
    if amount > 1.0:
        amount /= 100.0
    sigma = C.LOCAL_COLOR_SIGMA_MIN + float(np.clip(amount, 0.0, 1.0)) * C.LOCAL_COLOR_SIGMA_RANGE
    weight = np.exp(-0.5 * distance2 / max(sigma * sigma, C.LOCAL_RANGE_EPSILON))
    # Lightroom may retain a luminance window on a color range.  It is the
    # same gamma-sRGB feather used by a standalone Type=1 range, multiplied
    # into the color weight rather than discarded during XMP round-tripping.
    if range_mask.get("LumRange") is not None:
        luminance = rgb[..., 0] * C.LUMA_RED + rgb[..., 1] * C.LUMA_GREEN + rgb[..., 2] * C.LUMA_BLUE
        low_soft, low, high, high_soft = _quad(range_mask.get("LumRange"))
        weight *= _smoothstep(low_soft, low, luminance) * (1.0 - _smoothstep(high, high_soft, luminance))
    return weight.astype(np.float32)


def load_ai_raster(
    mask: Mapping[str, object],
    width: int,
    height: int,
    *,
    cache_root: str | Path | None = None,
    loader: Callable[[Mapping[str, object]], np.ndarray | None] | None = None,
    grid: tuple[np.ndarray, np.ndarray] | None = None,
) -> np.ndarray:
    loaded = loader(mask) if loader else None
    if loaded is None:
        key = Path(str(mask.get("pa_cache_key") or "")).name
        if not key or not re.fullmatch(r"[A-Za-z0-9._-]+", key):
            return np.zeros((height, width), dtype=np.float32)
        root = Path(cache_root or resolve_runtime_paths().develop_cache_dir)
        filename = key if key.lower().endswith(".png") else f"{key}.png"
        path = next((candidate for candidate in (root / "ai-masks" / filename, root / "ai_masks" / filename, root / "masks" / filename, root / filename) if candidate.is_file()), None)
        if path is None:
            return np.zeros((height, width), dtype=np.float32)
        with Image.open(path) as source:
            loaded = np.asarray(source.convert("L"), dtype=np.float32) / 255.0
    field = np.asarray(loaded, dtype=np.float32)
    if field.ndim == 3:
        field = field[..., 0]
    if field.size and float(np.nanmax(field)) > 1.0:
        field /= 255.0
    if grid is not None:
        x, y = grid
        field = _sample_bilinear_grid(field, np.broadcast_to(x, (height, width)), np.broadcast_to(y, (height, width)))
    else:
        field = _resize_bilinear(field, width, height)
    return np.clip(field, 0.0, 1.0)


def rasterize_mask(
    mask: Mapping[str, object],
    width: int,
    height: int,
    *,
    image: np.ndarray | None = None,
    grid: tuple[np.ndarray, np.ndarray] | None = None,
    canvas_size: tuple[int, int] | None = None,
    ai_loader: Callable[[Mapping[str, object]], np.ndarray | None] | None = None,
    cache_root: str | Path | None = None,
) -> np.ndarray:
    nested = mask.get("Masks")
    if isinstance(nested, Sequence) and not isinstance(nested, (str, bytes)):
        result = np.zeros((height, width), dtype=np.float32)
        for primitive in nested:
            if isinstance(primitive, Mapping):
                result = np.maximum(result, rasterize_mask(primitive, width, height, image=image, grid=grid, canvas_size=canvas_size, ai_loader=ai_loader, cache_root=cache_root))
        return result
    what = str(mask.get("What") or mask.get("MaskType") or "")
    range_mask = mask.get("CorrectionRangeMask")
    range_mask = range_mask if isinstance(range_mask, Mapping) else mask
    if "Circular" in what or "Radial" in what:
        return rasterize_radial(mask, width, height, grid=grid)
    if "Gradient" in what:
        return rasterize_gradient(mask, width, height, grid=grid)
    if "Paint" in what or "Brush" in what or mask.get("Dabs") is not None:
        return rasterize_brush(mask, width, height, grid=grid, canvas_size=canvas_size)
    if "Image" in what or mask.get("pa_cache_key") is not None:
        return load_ai_raster(mask, width, height, cache_root=cache_root, loader=ai_loader, grid=grid)
    range_type = int(_number(range_mask, "Type", 0.0))
    if range_type == 1 or range_mask.get("LumRange") is not None or "Luminance" in what:
        return rasterize_luminance_range(range_mask, image, width, height) if image is not None else np.zeros((height, width), dtype=np.float32)
    if range_type == 2 or _sample_colors(range_mask) or "Color" in what:
        return rasterize_color_range(range_mask, image, width, height) if image is not None else np.zeros((height, width), dtype=np.float32)
    return np.zeros((height, width), dtype=np.float32)


def rasterize_correction(
    correction: Mapping[str, object],
    width: int,
    height: int,
    *,
    image: np.ndarray | None = None,
    downsample: int = C.LOCAL_MASK_DOWNSAMPLE,
    pixel_offset: tuple[int, int] = (0, 0),
    canvas_size: tuple[int, int] | None = None,
    ai_loader: Callable[[Mapping[str, object]], np.ndarray | None] | None = None,
    cache_root: str | Path | None = None,
) -> np.ndarray:
    raster_width = max(1, math.ceil(width / max(1, downsample)))
    raster_height = max(1, math.ceil(height / max(1, downsample)))
    if not _truth(correction.get("CorrectionActive"), True):
        return np.zeros((raster_height, raster_width), dtype=np.float32)
    full_canvas = canvas_size or (width, height)
    grid = _grid(raster_width, raster_height, pixel_offset=pixel_offset, region_size=(width, height), canvas_size=full_canvas)
    raster_image = _image_rgb(image, raster_width, raster_height) if image is not None else None
    combined = np.zeros((raster_height, raster_width), dtype=np.float32)
    masks = correction.get("CorrectionMasks")
    if isinstance(masks, Sequence) and not isinstance(masks, (str, bytes)):
        for mask in masks:
            if not isinstance(mask, Mapping) or not _truth(mask.get("MaskActive"), True):
                continue
            part = rasterize_mask(mask, raster_width, raster_height, image=raster_image, grid=grid, canvas_size=full_canvas, ai_loader=ai_loader, cache_root=cache_root)
            if _truth(mask.get("MaskInverted")):
                part = 1.0 - part
            part *= float(np.clip(_number(mask, "MaskValue", 1.0), 0.0, 1.0))
            if int(_number(mask, "MaskBlendMode")) == 1:
                combined *= part
            else:
                combined = np.maximum(combined, part)
    range_mask = correction.get("CorrectionRangeMask")
    if isinstance(range_mask, Mapping) and int(_number(range_mask, "Type")) in (1, 2):
        part = rasterize_mask(range_mask, raster_width, raster_height, image=raster_image, grid=grid, canvas_size=full_canvas)
        combined = combined * part if np.any(combined) else part
    amount = float(np.clip(_number(correction, "CorrectionAmount", 1.0), 0.0, 2.0))
    return np.clip(combined * amount, 0.0, 2.0).astype(np.float32)


def rasterize_corrections(
    settings: Mapping[str, object], width: int, height: int, *, image: np.ndarray | None = None, **kwargs
) -> list[tuple[int, np.ndarray]]:
    corrections = settings.get("MaskGroupBasedCorrections")
    if not isinstance(corrections, Sequence) or isinstance(corrections, (str, bytes)):
        return []
    if len(corrections) > C.LOCAL_RENDER_CAP:
        _LOG.warning("Rendering the first %d of %d local corrections", C.LOCAL_RENDER_CAP, len(corrections))
    return [
        (index, rasterize_correction(correction, width, height, image=image, **kwargs))
        for index, correction in enumerate(corrections[: C.LOCAL_RENDER_CAP])
        if isinstance(correction, Mapping)
    ]


def upsample_mask(mask: np.ndarray, width: int, height: int) -> np.ndarray:
    return np.clip(_resize_bilinear(np.asarray(mask, dtype=np.float32), width, height), 0.0, 2.0)


def _luma(rgb: np.ndarray) -> np.ndarray:
    return (rgb[..., 0] * C.LUMA_RED + rgb[..., 1] * C.LUMA_GREEN + rgb[..., 2] * C.LUMA_BLUE).astype(np.float32)


def _gaussian_ev(ev: np.ndarray, center: float) -> np.ndarray:
    z = (ev - center) / C.TONE_EV_SIGMA
    return np.exp(-0.5 * z * z).astype(np.float32)


def _soft_clamp(value: np.ndarray) -> np.ndarray:
    above = value > 1.0
    return np.where(value < 0.0, 0.0, np.where(above, 1.0 + (value - 1.0) / (1.0 + C.SOFT_CLAMP_FACTOR * (value - 1.0)), value)).astype(np.float32)


def _oklab_to_linear(lab: np.ndarray) -> np.ndarray:
    lms = lab @ np.asarray(C.OKLAB_M2_INV, dtype=np.float32).T
    return ((lms * lms * lms) @ np.asarray(C.OKLAB_M1_INV, dtype=np.float32).T).astype(np.float32)


def applyLocalCorrection(
    rgbLinear: np.ndarray,
    luma: np.ndarray,
    blurs: Mapping[str, np.ndarray],
    local: Mapping[str, object],
    m: np.ndarray,
) -> np.ndarray:
    """Apply one reduced local stack and return gamma-domain sRGB.

    ``rgbLinear`` is the current sequential correction result decoded to linear;
    luma/blurs are the shared pre-local gamma fields, as in the GLSL twin.
    """
    rgb = np.asarray(rgbLinear, dtype=np.float32).copy()
    strength = np.clip(np.asarray(m, dtype=np.float32), 0.0, 2.0)
    rgb *= np.exp2(localToSlider(local, "LocalExposure2012") * strength)[..., None]

    mired = localToSlider(local, "LocalTemperature") * strength
    tint = localToSlider(local, "LocalTint") * strength
    rgb[..., 0] *= np.exp2(mired * C.LOCAL_WB_TEMP_FACTOR)
    rgb[..., 2] *= np.exp2(-mired * C.LOCAL_WB_TEMP_FACTOR)
    rgb[..., 1] *= np.exp2(-tint * C.LOCAL_WB_TINT_FACTOR)

    y = _luma(rgb)
    ev = np.log2(np.maximum(y, C.TONE_EPSILON))
    highlights = localToSlider(local, "LocalHighlights2012") / C.LOCAL_SLIDER_SCALE
    hl_scale = 1.0 if highlights < 0.0 else C.TONE_HIGHLIGHTS_POS_SCALE
    delta_ev = strength * (
        C.TONE_HIGHLIGHTS_FACTOR * highlights * hl_scale * _gaussian_ev(ev, C.TONE_EV_HIGHLIGHTS_CENTER)
        + C.TONE_SHADOWS_FACTOR * localToSlider(local, "LocalShadows2012") / C.LOCAL_SLIDER_SCALE * _gaussian_ev(ev, C.TONE_EV_SHADOWS_CENTER)
        + C.TONE_WHITES_FACTOR * localToSlider(local, "LocalWhites2012") / C.LOCAL_SLIDER_SCALE * _gaussian_ev(ev, C.TONE_EV_WHITES_CENTER)
        + C.TONE_BLACKS_FACTOR * localToSlider(local, "LocalBlacks2012") / C.LOCAL_SLIDER_SCALE * _gaussian_ev(ev, C.TONE_EV_BLACKS_CENTER)
    )
    rgb *= np.exp2(delta_ev)[..., None]
    y2 = _luma(rgb)
    tone = np.power(np.clip(y2, 0.0, 1.0), 1.0 / C.TONE_GAMMA)
    contrast = localToSlider(local, "LocalContrast2012") / C.LOCAL_SLIDER_SCALE
    tone = _soft_clamp(0.5 + (tone - 0.5) * (1.0 + C.CONTRAST_FACTOR * contrast * strength))
    rgb *= (np.power(tone, C.TONE_GAMMA) / np.maximum(y2, C.TONE_EPSILON))[..., None]

    dehaze = localToSlider(local, "LocalDehaze") / C.LOCAL_SLIDER_SCALE * strength
    rgb = np.maximum((rgb - C.DEHAZE_AIRLIGHT_FACTOR * dehaze[..., None]) / (1.0 - C.DEHAZE_AIRLIGHT_FACTOR * dehaze[..., None]), 0.0)
    srgb = _linear_to_srgb(rgb)

    saturation = localToSlider(local, "LocalSaturation") / C.LOCAL_SLIDER_SCALE
    hue = math.radians(localToSlider(local, "LocalHue") / C.LOCAL_SLIDER_SCALE * C.LOCAL_HUE_DEGREES) * strength
    lab = _linear_to_oklab(_srgb_to_linear(srgb))
    cosine, sine = np.cos(hue), np.sin(hue)
    a, b = lab[..., 1].copy(), lab[..., 2].copy()
    chroma_scale = np.maximum(1.0 + saturation * strength, 0.0)
    lab[..., 1] = (a * cosine - b * sine) * chroma_scale
    lab[..., 2] = (a * sine + b * cosine) * chroma_scale
    srgb = _linear_to_srgb(np.clip(_oklab_to_linear(lab), 0.0, 1.0))

    clarity = localToSlider(local, "LocalClarity2012") / C.LOCAL_SLIDER_SCALE
    texture = localToSlider(local, "LocalTexture") / C.LOCAL_SLIDER_SCALE
    large = np.asarray(blurs.get("large", luma), dtype=np.float32)
    small = np.asarray(blurs.get("small", luma), dtype=np.float32)
    midtones = np.clip(4.0 * luma * (1.0 - luma), 0.0, 1.0)
    residual_large = np.clip(luma - large, -C.CLARITY_RESIDUAL_MAX, C.CLARITY_RESIDUAL_MAX)
    residual_small = np.clip(luma - small, -C.CLARITY_RESIDUAL_MAX, C.CLARITY_RESIDUAL_MAX)
    srgb += (residual_large * C.CLARITY_FACTOR * clarity * midtones * strength)[..., None]
    srgb += (residual_small * C.TEXTURE_FACTOR * texture * strength)[..., None]
    return np.clip(srgb, 0.0, 1.0).astype(np.float32)


apply_local_correction = applyLocalCorrection


def apply_local_corrections(
    srgb: np.ndarray,
    settings: Mapping[str, object],
    *,
    luma: np.ndarray,
    blurs: Mapping[str, np.ndarray],
    pixel_offset: tuple[int, int] = (0, 0),
    canvas_size: tuple[int, int] | None = None,
) -> np.ndarray:
    height, width = srgb.shape[:2]
    rasters = dict(rasterize_corrections(settings, width, height, image=srgb, pixel_offset=pixel_offset, canvas_size=canvas_size))
    corrections = settings.get("MaskGroupBasedCorrections")
    if not isinstance(corrections, Sequence) or isinstance(corrections, (str, bytes)):
        return srgb
    result = np.asarray(srgb, dtype=np.float32)
    for index, correction in enumerate(corrections[: C.LOCAL_RENDER_CAP]):
        if not isinstance(correction, Mapping) or not _truth(correction.get("CorrectionActive"), True):
            continue
        mask = rasters.get(index)
        if mask is None or not np.any(mask):
            continue
        result = applyLocalCorrection(_srgb_to_linear(result), luma, blurs, correction, upsample_mask(mask, width, height))
    return result


def has_local_adjustments(settings: Mapping[str, object]) -> bool:
    corrections = settings.get("MaskGroupBasedCorrections")
    return isinstance(corrections, Sequence) and not isinstance(corrections, (str, bytes)) and any(
        isinstance(correction, Mapping)
        and _truth(correction.get("CorrectionActive"), True)
        and any(abs(_number(correction, key)) > C.LOCAL_RANGE_EPSILON for key in _LOCAL_KEYS)
        for correction in corrections[: C.LOCAL_RENDER_CAP]
    )
