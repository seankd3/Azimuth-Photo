"""Full-resolution RAW develop exports.

Color operations live in :mod:`features.develop.pipeline`; this module only
decodes, applies display geometry, encodes a selected file format, and keeps
the blocking work in a small thread pool.  v1 deliberately does not render
local masks, lens/CA correction, noise reduction, color grading, spot removal,
or pano/HDR settings.
"""

from __future__ import annotations

import asyncio
import math
import struct
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Mapping

import numpy as np
from PIL import Image
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from .pipeline import apply_pipeline


EXPORT_DIRECTORY = Path("/mnt/expansion/PhotoArchiveCache/develop/exports")
# A native RAW render has a substantial working set; serialize exports rather
# than allowing two 45 MP pipelines to contend for memory.
_EXPORT_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="develop-export")
PIPELINE_TILE_HEIGHT = 192


class RenderError(RuntimeError):
    """A RAW could not be decoded or rendered for export."""


def _number(settings: Mapping[str, object], key: str, default: float) -> float:
    try:
        return float(settings.get(key, default))
    except (TypeError, ValueError):
        return default


def decode_full_resolution(path: str | Path) -> np.ndarray:
    """Decode linear 16-bit sRGB-primary RAW data without automatic brightening."""
    import rawpy

    try:
        with rawpy.imread(str(path)) as raw:
            decoded = raw.postprocess(
                use_camera_wb=True,
                output_bps=16,
                no_auto_bright=True,
                gamma=(1, 1),
                output_color=rawpy.ColorSpace.sRGB,
                highlight_mode=rawpy.HighlightMode.Blend,
                half_size=False,
            )
    except Exception as exc:
        from features.develop import lossydng

        if lossydng.is_lossy_dng(str(path)):
            try:
                decoded, _meta = lossydng.decode_lossy_dng(str(path), max_px=None)
            except Exception as lossy_exc:
                raise RenderError(f"RAW export decode failed: {lossy_exc}") from lossy_exc
        else:
            raise RenderError(f"RAW export decode failed: {exc}") from exc
    return np.asarray(decoded, dtype=np.float32) / np.float32(65535.0)


def _crop(rgb: np.ndarray, settings: Mapping[str, object]) -> np.ndarray:
    height, width = rgb.shape[:2]
    left = np.clip(_number(settings, "CropLeft", 0.0), 0.0, 1.0)
    top = np.clip(_number(settings, "CropTop", 0.0), 0.0, 1.0)
    right = np.clip(_number(settings, "CropRight", 1.0), left + 1.0 / width, 1.0)
    bottom = np.clip(_number(settings, "CropBottom", 1.0), top + 1.0 / height, 1.0)
    x0, x1 = int(math.floor(left * width)), int(math.ceil(right * width))
    y0, y1 = int(math.floor(top * height)), int(math.ceil(bottom * height))
    return rgb[y0:max(y0 + 1, y1), x0:max(x0 + 1, x1)]


def _resize_bilinear(rgb: np.ndarray, width: int, height: int) -> np.ndarray:
    source_height, source_width = rgb.shape[:2]
    if (source_width, source_height) == (width, height):
        return rgb
    x = np.linspace(0.0, source_width - 1, width, dtype=np.float32)
    y = np.linspace(0.0, source_height - 1, height, dtype=np.float32)
    x0 = np.floor(x).astype(np.intp)
    y0 = np.floor(y).astype(np.intp)
    x1 = np.minimum(x0 + 1, source_width - 1)
    y1 = np.minimum(y0 + 1, source_height - 1)
    wx, wy = (x - x0)[None, :, None], (y - y0)[:, None, None]
    top = rgb[y0[:, None], x0[None, :]] * (1.0 - wx) + rgb[y0[:, None], x1[None, :]] * wx
    bottom = rgb[y1[:, None], x0[None, :]] * (1.0 - wx) + rgb[y1[:, None], x1[None, :]] * wx
    return (top * (1.0 - wy) + bottom * wy).astype(np.float32)


def _rotate(rgb: np.ndarray, angle_degrees: float) -> np.ndarray:
    if abs(angle_degrees) < 1e-7:
        return rgb
    height, width = rgb.shape[:2]
    angle = math.radians(angle_degrees)
    cos_angle, sin_angle = math.cos(angle), math.sin(angle)
    output_width = int(math.ceil(abs(width * cos_angle) + abs(height * sin_angle)))
    output_height = int(math.ceil(abs(width * sin_angle) + abs(height * cos_angle)))
    out_x, out_y = np.meshgrid(np.arange(output_width, dtype=np.float32), np.arange(output_height, dtype=np.float32))
    out_x -= (output_width - 1) * 0.5
    out_y -= (output_height - 1) * 0.5
    input_x = cos_angle * out_x + sin_angle * out_y + (width - 1) * 0.5
    input_y = -sin_angle * out_x + cos_angle * out_y + (height - 1) * 0.5
    valid = (input_x >= 0.0) & (input_x <= width - 1) & (input_y >= 0.0) & (input_y <= height - 1)
    x0 = np.clip(np.floor(input_x).astype(np.intp), 0, width - 1)
    y0 = np.clip(np.floor(input_y).astype(np.intp), 0, height - 1)
    x1, y1 = np.minimum(x0 + 1, width - 1), np.minimum(y0 + 1, height - 1)
    wx, wy = (input_x - x0)[..., None], (input_y - y0)[..., None]
    top = rgb[y0, x0] * (1.0 - wx) + rgb[y0, x1] * wx
    bottom = rgb[y1, x0] * (1.0 - wx) + rgb[y1, x1] * wx
    result = top * (1.0 - wy) + bottom * wy
    result[~valid] = 0.0
    return result.astype(np.float32)


def apply_geometry(rgb: np.ndarray, settings: Mapping[str, object], max_px: int | None = None) -> np.ndarray:
    """Apply orientation, crop, angle, then optional export-size reduction."""
    orientation = int(_number(settings, "Orientation", 1.0))
    if orientation == 3:
        rgb = np.rot90(rgb, 2)
    elif orientation == 6:
        rgb = np.rot90(rgb, -1)
    elif orientation == 8:
        rgb = np.rot90(rgb, 1)
    rgb = _crop(rgb, settings)
    rgb = _rotate(rgb, _number(settings, "CropAngle", 0.0))
    if max_px is not None and max_px > 0:
        height, width = rgb.shape[:2]
        largest = max(width, height)
        if largest > max_px:
            scale = max_px / largest
            rgb = _resize_bilinear(rgb, max(1, round(width * scale)), max(1, round(height * scale)))
    return rgb


def _write_tiff16(path: Path, rgb: np.ndarray) -> None:
    """Write an uncompressed little-endian RGB16 TIFF without another dependency."""
    array = np.ascontiguousarray(np.clip(rgb, 0.0, 1.0) * 65535.0 + 0.5, dtype="<u2")
    height, width = array.shape[:2]
    entries = 11
    ifd_offset = 8
    auxiliary_offset = ifd_offset + 2 + entries * 12 + 4
    bits_offset = auxiliary_offset
    pixels_offset = bits_offset + 6
    byte_count = int(array.nbytes)
    fields = (
        (256, 4, 1, width),
        (257, 4, 1, height),
        (258, 3, 3, bits_offset),
        (259, 3, 1, 1),
        (262, 3, 1, 2),
        (273, 4, 1, pixels_offset),
        (277, 3, 1, 3),
        (278, 4, 1, height),
        (279, 4, 1, byte_count),
        (284, 3, 1, 1),
        (339, 3, 1, 1),
    )
    with path.open("wb") as output:
        output.write(struct.pack("<2sHI", b"II", 42, ifd_offset))
        output.write(struct.pack("<H", entries))
        for tag, field_type, count, value in fields:
            if field_type == 3 and count == 1:
                output.write(struct.pack("<HHIHH", tag, field_type, count, value, 0))
            else:
                output.write(struct.pack("<HHII", tag, field_type, count, value))
        output.write(struct.pack("<I", 0))
        output.write(struct.pack("<HHH", 16, 16, 16))
        output.write(array.tobytes())


def _pipeline_overlap(settings: Mapping[str, object], minimum_dimension: int) -> int:
    """Rows of Gaussian context required around one independent render tile."""
    sigma = 0.0
    if _number(settings, "Clarity2012", 0.0) != 0.0:
        sigma = max(sigma, 0.02 * minimum_dimension)
    if _number(settings, "Texture", 0.0) != 0.0:
        sigma = max(sigma, 0.004 * minimum_dimension)
    if _number(settings, "Sharpness", 0.0) != 0.0:
        sigma = max(sigma, np.clip(_number(settings, "SharpenRadius", 1.0), 0.5, 3.0))
    return int(math.ceil(3.0 * sigma))


def _apply_pipeline_tiled(
    linear: np.ndarray, settings: Mapping[str, object], asshot_temperature: float | None
) -> np.ndarray:
    """Keep a native RAW export below the process memory ceiling.

    Color stages are pixel-local. Detail stages receive a 3-sigma vertical
    overlap, making the retained center rows equivalent to one full-frame
    separable blur while avoiding a multi-gigabyte transient working set.
    """
    height, width = linear.shape[:2]
    minimum_dimension = min(width, height)
    overlap = _pipeline_overlap(settings, minimum_dimension)
    output = np.empty_like(linear, dtype=np.float32)
    for core_top in range(0, height, PIPELINE_TILE_HEIGHT):
        core_bottom = min(height, core_top + PIPELINE_TILE_HEIGHT)
        source_top = max(0, core_top - overlap)
        source_bottom = min(height, core_bottom + overlap)
        tile = apply_pipeline(
            linear[source_top:source_bottom],
            settings,
            asshot_temperature=asshot_temperature,
            pixel_offset=(0, source_top),
            canvas_size=(width, height),
            blur_min_dimension=minimum_dimension,
        )
        retained_top = core_top - source_top
        retained_bottom = retained_top + (core_bottom - core_top)
        output[core_top:core_bottom] = tile[retained_top:retained_bottom]
    return output


def render_export(
    raw_path: str | Path,
    settings: Mapping[str, object],
    *,
    output_format: str,
    quality: int = 90,
    max_px: int | None = None,
    asshot_temperature: float | None = None,
) -> Path:
    """Render one RAW to the expansion-disk export folder and return its path."""
    normalized_format = output_format.lower()
    if normalized_format not in {"jpeg", "tiff16"}:
        raise ValueError("output_format must be 'jpeg' or 'tiff16'")
    EXPORT_DIRECTORY.mkdir(parents=True, exist_ok=True)
    linear = decode_full_resolution(raw_path)
    developed = _apply_pipeline_tiled(linear, settings, asshot_temperature)
    developed = apply_geometry(developed, settings, max_px=max_px)
    suffix = ".jpg" if normalized_format == "jpeg" else ".tiff"
    output_path = EXPORT_DIRECTORY / f"develop-{uuid.uuid4().hex}{suffix}"
    if normalized_format == "jpeg":
        encoded = np.asarray(np.clip(developed * 255.0 + 0.5, 0, 255), dtype=np.uint8)
        Image.fromarray(encoded, mode="RGB").save(output_path, format="JPEG", quality=int(np.clip(quality, 1, 100)), subsampling=0)
    else:
        _write_tiff16(output_path, developed)
    return output_path


async def render_export_async(*args, **kwargs) -> Path:
    """Run the full RAW render off FastAPI's event loop."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_EXPORT_EXECUTOR, lambda: render_export(*args, **kwargs))


async def render_export_response(
    raw_path: str | Path,
    settings: Mapping[str, object],
    *,
    output_format: str,
    quality: int = 90,
    max_px: int | None = None,
    asshot_temperature: float | None = None,
) -> FileResponse:
    """Render then construct the cleanup-safe response used by the develop route."""
    output_path = await render_export_async(
        raw_path,
        settings,
        output_format=output_format,
        quality=quality,
        max_px=max_px,
        asshot_temperature=asshot_temperature,
    )
    is_jpeg = output_format.lower() == "jpeg"
    filename = f"{Path(raw_path).stem}-develop.{'jpg' if is_jpeg else 'tiff'}"
    return FileResponse(
        output_path,
        media_type="image/jpeg" if is_jpeg else "image/tiff",
        filename=filename,
        background=BackgroundTask(output_path.unlink, missing_ok=True),
    )
