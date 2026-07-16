"""Full-resolution Develop exports.

Color operations live in :mod:`features.develop.pipeline`; this module only
decodes, applies display geometry, optional post-resize output sharpening
(§24 — export-only, no GL twin), encodes a selected file format, and keeps
the blocking work in a small thread pool.  v1 deliberately does not render
local masks, lens/CA correction, noise reduction, color grading, spot removal,
or pano/HDR settings.
"""

from __future__ import annotations

import asyncio
import io
import math
import re
import shutil
import struct
import time
import uuid
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Mapping

import numpy as np
from PIL import Image
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from core.runtime_paths import resolve_runtime_paths

from . import ops_constants as C, rawproc
from .film import load_stock
from .pipeline import apply_pipeline, gaussian_blur, luma
from .transform import apply_transform


def _resolved_export_dirs() -> tuple[Path, Path]:
    paths = resolve_runtime_paths()
    return Path(paths.temporary_export_dir), Path(paths.library_export_dir)


EXPORT_DIRECTORY, LIBRARY_EXPORT_DIRECTORY = _resolved_export_dirs()
LIBRARY_SOURCE_NAME = "Develop Exports"
# A native RAW render has a substantial working set; serialize exports rather
# than allowing two 45 MP pipelines to contend for memory.
_EXPORT_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="develop-export")
_PROOF_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="develop-proof")
PIPELINE_TILE_HEIGHT = 192
DEFAULT_FILENAME_PATTERN = "{stem}-develop"
_TOKEN_RE = re.compile(r"\{(stem|filename|id|ext|date)\}")
PROOF_DECODE_CACHE_SIZE = 2
_PROOF_DECODE_CACHE: OrderedDict[tuple[str, int, int], np.ndarray] = OrderedDict()
_PROOF_DECODE_LOCK = RLock()


class RenderError(RuntimeError):
    """A source image could not be decoded or rendered for export."""


@dataclass(frozen=True)
class ProofTile:
    png: bytes
    left: int
    top: int
    width: int
    height: int
    source_width: int
    source_height: int


def _number(settings: Mapping[str, object], key: str, default: float) -> float:
    try:
        return float(settings.get(key, default))
    except (TypeError, ValueError):
        return default


def decode_full_resolution(path: str | Path) -> np.ndarray:
    """Decode a full-resolution source into linear sRGB-primary floats."""
    if rawproc.is_display_path(path):
        try:
            decoded = rawproc.decode_display_image(path, max_edge=None)
        except (rawproc.RawDecodeError, OSError) as exc:
            raise RenderError(f"Image export decode failed: {exc}") from exc
        return np.asarray(decoded, dtype=np.float32) / np.float32(65535.0)

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


def clear_proof_decode_cache() -> None:
    """Release native decodes (public for focused tests and memory-pressure hooks)."""
    with _PROOF_DECODE_LOCK:
        _PROOF_DECODE_CACHE.clear()


def _proof_decode(path: str | Path) -> np.ndarray:
    source = Path(path)
    try:
        stat = source.stat()
    except OSError as exc:
        raise RenderError(f"Original proof source is unavailable: {exc}") from exc
    key = (str(source.resolve()), int(stat.st_mtime_ns), int(stat.st_size))
    with _PROOF_DECODE_LOCK:
        cached = _PROOF_DECODE_CACHE.pop(key, None)
        if cached is not None:
            _PROOF_DECODE_CACHE[key] = cached
            return cached
        decoded = np.ascontiguousarray(decode_full_resolution(source), dtype=np.float32)
        decoded.setflags(write=False)
        _PROOF_DECODE_CACHE[key] = decoded
        while len(_PROOF_DECODE_CACHE) > PROOF_DECODE_CACHE_SIZE:
            _PROOF_DECODE_CACHE.popitem(last=False)
        return decoded


def render_proof_tile(
    raw_path: str | Path,
    settings: Mapping[str, object],
    *,
    u: float,
    v: float,
    edge: int = 1024,
    asshot_temperature: float | None = None,
    asshot_tint: float | None = None,
    color_profile=None,
) -> ProofTile:
    """Render one native-resolution source tile through the shared NumPy look.

    v1 intentionally decodes the full original, then runs only the requested
    crop (plus blur overlap) through the pipeline. Geometry remains represented
    by the client's image-space location; no full-resolution canvas is built.
    """
    linear = _proof_decode(raw_path)
    source_height, source_width = linear.shape[:2]
    tile_edge = int(np.clip(int(edge), 128, 2048))
    tile_width = min(tile_edge, source_width)
    tile_height = min(tile_edge, source_height)
    center_x = int(round(np.clip(float(u), 0.0, 1.0) * max(0, source_width - 1)))
    center_y = int(round(np.clip(float(v), 0.0, 1.0) * max(0, source_height - 1)))
    left = int(np.clip(center_x - tile_width // 2, 0, source_width - tile_width))
    top = int(np.clip(center_y - tile_height // 2, 0, source_height - tile_height))
    overlap = _pipeline_overlap(settings, min(source_width, source_height))
    source_left = max(0, left - overlap)
    source_top = max(0, top - overlap)
    source_right = min(source_width, left + tile_width + overlap)
    source_bottom = min(source_height, top + tile_height + overlap)
    developed = apply_pipeline(
        linear[source_top:source_bottom, source_left:source_right],
        settings,
        asshot_temperature=asshot_temperature,
        asshot_tint=asshot_tint,
        color_profile=color_profile,
        pixel_offset=(source_left, source_top),
        canvas_size=(source_width, source_height),
        blur_min_dimension=min(source_width, source_height),
    )
    retained_left = left - source_left
    retained_top = top - source_top
    developed = developed[
        retained_top:retained_top + tile_height,
        retained_left:retained_left + tile_width,
    ]
    encoded = np.asarray(np.clip(developed * 255.0 + 0.5, 0, 255), dtype=np.uint8)
    output = io.BytesIO()
    Image.fromarray(encoded, mode="RGB").save(output, format="PNG", optimize=False)
    return ProofTile(
        png=output.getvalue(),
        left=left,
        top=top,
        width=tile_width,
        height=tile_height,
        source_width=source_width,
        source_height=source_height,
    )


async def render_proof_tile_async(*args, **kwargs) -> ProofTile:
    """Keep native decode and NumPy work off FastAPI's event loop."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_PROOF_EXECUTOR, lambda: render_proof_tile(*args, **kwargs))


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
    rgb = apply_transform(rgb, settings)
    if max_px is not None and max_px > 0:
        height, width = rgb.shape[:2]
        largest = max(width, height)
        if largest > max_px:
            scale = max_px / largest
            rgb = _resize_bilinear(rgb, max(1, round(width * scale)), max(1, round(height * scale)))
    return rgb


def resolve_output_sharpen(sharpen: str | None) -> tuple[float, float]:
    """Return (amount, radius) for an export sharpening preset name."""
    key = str(sharpen or "none").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "screen": "screen_standard",
        "print": "print_standard",
        "low": "screen_low",
        "standard": "screen_standard",
        "high": "screen_high",
        "off": "none",
        "": "none",
    }
    key = aliases.get(key, key)
    if key not in C.OUTPUT_SHARPEN_PRESETS:
        raise ValueError(
            "sharpen must be one of: none, screen_low, screen_standard, screen_high, "
            "print_low, print_standard, print_high"
        )
    return C.OUTPUT_SHARPEN_PRESETS[key]


def apply_output_sharpen(rgb: np.ndarray, sharpen: str | None) -> np.ndarray:
    """Post-resize unsharp mask on gamma-domain RGB (export-only, §24)."""
    amount, radius = resolve_output_sharpen(sharpen)
    if amount <= 0.0 or radius <= 0.0:
        return rgb
    lightness = luma(rgb)
    blurred = gaussian_blur(lightness, radius)
    residual = (lightness - blurred) * amount
    return np.clip(rgb + residual[..., None], 0.0, 1.0).astype(np.float32)


def format_export_filename(
    *,
    raw_path: str | Path,
    image_id: int | None = None,
    output_format: str = "jpeg",
    pattern: str | None = None,
) -> str:
    """Expand a Lightroom-ish filename pattern into a download basename."""
    path = Path(raw_path)
    stem = path.stem or "developed"
    filename = path.name or stem
    ext = "jpg" if str(output_format).lower() == "jpeg" else "tiff"
    tokens = {
        "stem": stem,
        "filename": filename,
        "id": str(image_id or ""),
        "ext": ext,
        "date": time.strftime("%Y%m%d"),
    }
    template = (pattern or DEFAULT_FILENAME_PATTERN).strip() or DEFAULT_FILENAME_PATTERN
    rendered = _TOKEN_RE.sub(lambda match: tokens.get(match.group(1), ""), template)
    rendered = re.sub(r"[^\w.\-+=() ]+", "_", rendered).strip(" ._") or stem
    if not rendered.lower().endswith(f".{ext}"):
        rendered = f"{rendered}.{ext}"
    return rendered


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
    film_stock = load_stock(str(settings.get("pa_FilmStock") or "").strip())
    if film_stock and _number(settings, "pa_FilmHalation", 100.0) > 0.0:
        halation = film_stock.get("halation") or {}
        if _number(halation, "amount", 0.0) > 0.0:
            sigma = max(sigma, _number(halation, "radius_frac", 0.015) * minimum_dimension)
    return int(math.ceil(3.0 * sigma))


def _apply_pipeline_tiled(
    linear: np.ndarray,
    settings: Mapping[str, object],
    asshot_temperature: float | None,
    asshot_tint: float | None = None,
    color_profile=None,
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
            asshot_tint=asshot_tint,
            color_profile=color_profile,
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
    sharpen: str | None = None,
    filename_pattern: str | None = None,
    image_id: int | None = None,
    asshot_temperature: float | None = None,
    asshot_tint: float | None = None,
    color_profile=None,
) -> Path:
    """Render one source image to the expansion-disk export folder and return its path."""
    normalized_format = output_format.lower()
    if normalized_format not in {"jpeg", "tiff16"}:
        raise ValueError("output_format must be 'jpeg' or 'tiff16'")
    resolve_output_sharpen(sharpen)  # validate early
    EXPORT_DIRECTORY.mkdir(parents=True, exist_ok=True)
    linear = decode_full_resolution(raw_path)
    developed = _apply_pipeline_tiled(linear, settings, asshot_temperature, asshot_tint, color_profile)
    developed = apply_geometry(developed, settings, max_px=max_px)
    developed = apply_output_sharpen(developed, sharpen)
    download_name = format_export_filename(
        raw_path=raw_path,
        image_id=image_id,
        output_format=normalized_format,
        pattern=filename_pattern,
    )
    suffix = Path(download_name).suffix or (".jpg" if normalized_format == "jpeg" else ".tiff")
    output_path = EXPORT_DIRECTORY / f"develop-{uuid.uuid4().hex}{suffix}"
    if normalized_format == "jpeg":
        encoded = np.asarray(np.clip(developed * 255.0 + 0.5, 0, 255), dtype=np.uint8)
        Image.fromarray(encoded, mode="RGB").save(
            output_path, format="JPEG", quality=int(np.clip(quality, 1, 100)), subsampling=0
        )
    else:
        _write_tiff16(output_path, developed)
    # Stash the intended download name beside the file for the response layer.
    output_path.with_suffix(output_path.suffix + ".name").write_text(download_name, encoding="utf-8")
    return output_path


def save_export_to_library(
    export_path: Path,
    *,
    db_path: str,
    source_image: Mapping[str, object],
    download_name: str | None = None,
) -> dict:
    """Register an exported JPEG/TIFF under library-exports (§20 / §24).

    Stack join for kind ``version`` is owned by the VERSTACKS lane; this helper
    only registers the catalog row so Save-to-library works immediately.
    """
    import sqlite3

    LIBRARY_EXPORT_DIRECTORY.mkdir(parents=True, exist_ok=True)
    name = download_name or export_path.name
    safe = re.sub(r"[^\w.\-+=() ]+", "_", name).strip(" ._") or export_path.name
    destination = LIBRARY_EXPORT_DIRECTORY / safe
    if destination.exists():
        destination = LIBRARY_EXPORT_DIRECTORY / f"{destination.stem}-{uuid.uuid4().hex[:8]}{destination.suffix}"
    shutil.copy2(export_path, destination)
    now = time.time()
    width = height = None
    try:
        with Image.open(destination) as image:
            width, height = image.size
    except Exception:
        pass
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute(
            "INSERT INTO catalog_sources (path, display_name, included, online, created_at, last_scan_at, last_seen_at) "
            "VALUES (?, ?, 1, 1, ?, ?, ?) "
            "ON CONFLICT(path) DO UPDATE SET included=1, online=1, last_seen_at=excluded.last_seen_at, removed_at=NULL",
            (str(LIBRARY_EXPORT_DIRECTORY), LIBRARY_SOURCE_NAME, now, now, now),
        )
        source_id = int(
            conn.execute(
                "SELECT id FROM catalog_sources WHERE path = ?",
                (str(LIBRARY_EXPORT_DIRECTORY),),
            ).fetchone()["id"]
        )
        cursor = conn.execute(
            "INSERT INTO images (source_id, filename, filepath, status, file_ext, file_size, file_modified_at, "
            "width, height, date_taken, camera_make, camera_model, lens) "
            "VALUES (?, ?, ?, 'kept', ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                source_id,
                destination.name,
                str(destination),
                destination.suffix.lower(),
                int(destination.stat().st_size),
                destination.stat().st_mtime,
                width,
                height,
                source_image.get("date_taken"),
                source_image.get("camera_make"),
                source_image.get("camera_model"),
                source_image.get("lens") or source_image.get("lens_model"),
            ),
        )
        library_id = int(cursor.lastrowid)
        conn.execute(
            "UPDATE catalog_sources SET image_count=(SELECT COUNT(*) FROM images WHERE source_id=?), "
            "active_image_count=(SELECT COUNT(*) FROM images WHERE source_id=? AND status IN ('kept', 'maybe') "
            "AND missing_at IS NULL) WHERE id=?",
            (source_id, source_id, source_id),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        destination.unlink(missing_ok=True)
        raise
    finally:
        conn.close()
    return {
        "library_image_id": library_id,
        "filepath": str(destination),
        "filename": destination.name,
        "source_image_id": int(source_image.get("id") or 0) or None,
    }


async def render_export_async(*args, **kwargs) -> Path:
    """Run the full RAW render off FastAPI's event loop."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_EXPORT_EXECUTOR, lambda: render_export(*args, **kwargs))


def _download_name_for(path: Path, fallback: str) -> str:
    sidecar = path.with_suffix(path.suffix + ".name")
    try:
        if sidecar.is_file():
            return sidecar.read_text(encoding="utf-8").strip() or fallback
    except OSError:
        pass
    return fallback


def _cleanup_export(path: Path) -> None:
    path.unlink(missing_ok=True)
    path.with_suffix(path.suffix + ".name").unlink(missing_ok=True)


async def render_export_response(
    raw_path: str | Path,
    settings: Mapping[str, object],
    *,
    output_format: str,
    quality: int = 90,
    max_px: int | None = None,
    sharpen: str | None = None,
    filename_pattern: str | None = None,
    image_id: int | None = None,
    asshot_temperature: float | None = None,
    asshot_tint: float | None = None,
    color_profile=None,
) -> FileResponse:
    """Render then construct the cleanup-safe response used by the develop route."""
    output_path = await render_export_async(
        raw_path,
        settings,
        output_format=output_format,
        quality=quality,
        max_px=max_px,
        sharpen=sharpen,
        filename_pattern=filename_pattern,
        image_id=image_id,
        asshot_temperature=asshot_temperature,
        asshot_tint=asshot_tint,
        color_profile=color_profile,
    )
    is_jpeg = output_format.lower() == "jpeg"
    fallback = format_export_filename(
        raw_path=raw_path,
        image_id=image_id,
        output_format=output_format,
        pattern=filename_pattern,
    )
    filename = _download_name_for(output_path, fallback)
    return FileResponse(
        output_path,
        media_type="image/jpeg" if is_jpeg else "image/tiff",
        filename=filename,
        background=BackgroundTask(_cleanup_export, output_path),
    )


def default_render_color_profile(meta: Mapping[str, object] | None) -> dict[str, object]:
    """Return the cache-native color payload used by the default renderer.

    Request-time metadata may also contain a resolved Adobe profile for the
    profile caption and explicit profile workflows.  The known-good library
    preview is intentionally based on the cache's as-shot decode plus its
    fitted camera profile, so the interactive canvas must receive this exact
    subset instead of silently switching renderers when Develop opens.
    """
    color = dict(meta.get("color") or {}) if isinstance(meta, Mapping) else {}
    if isinstance(meta, Mapping) and meta.get("base_kind"):
        color["base_kind"] = meta["base_kind"]
    return color


def develop_default_render(
    linear01: np.ndarray, meta: Mapping[str, object] | None, settings: Mapping[str, object] | None = None
) -> np.ndarray:
    """Develop a linear base (float [0,1]) through the shared pipeline.

    Single source of truth for "what an image looks like": the same
    default WB + dual-illuminant color matrices + tone the library thumbnail
    uses, so the Develop base preview never diverges into a flat, dark,
    desaturated render. Returns developed float RGB in [0,1] (pre-geometry).
    """
    settings = settings or {}
    as_shot = meta.get("as_shot") if isinstance(meta, Mapping) else None
    color = default_render_color_profile(meta)
    return apply_pipeline(
        linear01,
        settings,
        asshot_temperature=(as_shot or {}).get("temperature") if isinstance(as_shot, Mapping) else None,
        asshot_tint=(as_shot or {}).get("tint") if isinstance(as_shot, Mapping) else None,
        color_profile=color or None,
    )


def render_display_preview(image_id: int, raw_path: str | Path, max_px: int | None = None) -> Image.Image | None:
    """Develop-quality sRGB preview for library full views.

    Renders the cached linear base through the shared pipeline with the
    image's saved develop settings (camera-space WB, dual-illuminant color
    matrices, fitted profile), so the library loupe shows the same color
    truth as the Develop canvas instead of LibRaw's default look.
    Returns None on any failure so callers can fall back.
    """
    import gzip as _gzip
    import json as _json
    import sqlite3 as _sqlite3

    from features.develop import rawproc

    try:
        paths, meta = rawproc.ensure_base_cache(int(image_id), raw_path)
        linear16, _width, _height = rawproc.parse_base_payload(_gzip.decompress(paths.binary.read_bytes()))
        linear = linear16.astype(np.float32) / 65535.0
        # HDR-merged bases are stored normalized; restore the scene headroom the
        # same way develop.js parseBase does so the sigmoid view transform sees
        # real >1 values (twin contract).
        hdr_info = meta.get("hdr") if isinstance(meta, dict) else None
        if isinstance(hdr_info, dict):
            try:
                hdr_scale = float(hdr_info.get("scale") or 1.0)
            except (TypeError, ValueError):
                hdr_scale = 1.0
            if hdr_scale > 1.0:
                linear *= np.float32(hdr_scale)
        settings: dict[str, object] = {}
        try:
            from core import db as _db

            conn = _sqlite3.connect(_db.DB_PATH)
            try:
                row = conn.execute(
                    "SELECT settings FROM develop_settings WHERE image_id = ?", (int(image_id),)
                ).fetchone()
            finally:
                conn.close()
            if row and row[0]:
                settings = _json.loads(row[0]) or {}
        except Exception:
            settings = {}
        developed = develop_default_render(linear, meta, settings)
        developed = apply_geometry(developed, settings, max_px=max_px)
        encoded = np.asarray(np.clip(developed * 255.0 + 0.5, 0, 255), dtype=np.uint8)
        return Image.fromarray(encoded, mode="RGB")
    except Exception:
        return None
