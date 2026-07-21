"""Linear base-preview decode and cache helpers for Develop.

The cache lives on the expansion volume: large Develop previews must never consume
the nearly-full root disk.  The `.bin.gz` payload starts with a 16-byte PABASE1
header and then little-endian interleaved uint16 RGB pixels.
"""

from __future__ import annotations

import gzip
import json
import logging
import math
import os
import struct
import threading
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageOps
from core import pil_limits  # noqa: F401  # disables the decompression-bomb limit process-wide

from core.runtime_paths import resolve_runtime_paths

try:
    import rawpy
except ImportError:  # pragma: no cover - rawpy is an application dependency.
    rawpy = None

from . import ops_constants as C
from .camera_profile import load_camera_profile
from .highlights_recon import reconstruct_highlights

log = logging.getLogger(__name__)
from .lens import normalized_source_metadata, read_exif, resolve_lens_correction


RAW_EXTENSIONS = {".dng", ".cr2", ".cr3"}
DISPLAY_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp"}
OPTIONAL_DISPLAY_EXTENSIONS = {".heic"}
BASE_CACHE_ROOT = Path(resolve_runtime_paths().develop_cache_dir)
# v3 remains the compatible display-image cache. Raw bases use the separately
# versioned directory below because their decode pixels change more frequently.
BASE_CACHE_DIR = BASE_CACHE_ROOT / "base" / "v3"
# v5: all raw bases reconstruct clipped channels before their final hard clip.
RAW_BASE_CACHE_VERSION = 5
BASE_MAGIC = b"PABASE1\0"
BASE_HEADER = struct.Struct("<8sII")
MAX_BASE_EDGE = 2048
MEMORY_BASE_LIMIT = 2
# v2: native EXIF fallback fills camera_model on machines without ExifTool.
# v3: cached metadata carries ISO so NR defaults never shell out per request.
SOURCE_META_VERSION = 3
UINT16_FULL_SCALE = np.float32(np.iinfo(np.uint16).max)


class RawDecodeError(RuntimeError):
    """An image exists but could not be decoded into a usable Develop base."""


@dataclass(frozen=True)
class BasePaths:
    binary: Path
    metadata: Path
    preview: Path


_locks_guard = threading.Lock()
_image_locks: dict[int, threading.Lock] = {}
_recent_decodes: OrderedDict[int, tuple[np.ndarray, dict[str, Any]]] = OrderedDict()


def is_raw_path(path: str | os.PathLike[str]) -> bool:
    return Path(path).suffix.lower() in RAW_EXTENSIONS


def is_display_path(path: str | os.PathLike[str]) -> bool:
    suffix = Path(path).suffix.lower()
    if suffix in DISPLAY_EXTENSIONS:
        return True
    return suffix in OPTIONAL_DISPLAY_EXTENSIONS and suffix in Image.registered_extensions()


def is_hdr_merge_path(path: str | os.PathLike[str]) -> bool:
    candidate = Path(path)
    if candidate.suffix.lower() != ".exr":
        return False
    from features.develop import hdr

    return candidate.parent == hdr.HDR_CACHE_DIR


def is_pano_merge_path(path: str | os.PathLike[str]) -> bool:
    candidate = Path(path)
    if candidate.suffix.lower() != ".exr":
        return False
    from features.develop import pano

    return candidate.parent == pano.PANO_CACHE_DIR


def is_develop_path(path: str | os.PathLike[str]) -> bool:
    return is_raw_path(path) or is_display_path(path) or is_hdr_merge_path(path) or is_pano_merge_path(path)


def _raw_base_cache_dir() -> Path:
    default_v3 = BASE_CACHE_ROOT / "base" / "v3"
    if BASE_CACHE_DIR != default_v3:
        # Tests and isolated probes override BASE_CACHE_DIR as a complete cache
        # seam; keep honoring that override instead of escaping to a sibling.
        return BASE_CACHE_DIR
    return BASE_CACHE_ROOT / "base" / f"v{RAW_BASE_CACHE_VERSION}"


def _uses_raw_base_cache(path: str | os.PathLike[str]) -> bool:
    source = Path(path)
    # HDR/pano merges write their bases via the merge pipeline into the
    # default cache dir; only true camera raws move to the raw v5 dir.
    if is_hdr_merge_path(source) or is_pano_merge_path(source):
        return False
    return is_raw_path(source)


def base_paths(image_id: int, source_path: str | os.PathLike[str] | None = None) -> BasePaths:
    cache_dir = _raw_base_cache_dir() if source_path is not None and _uses_raw_base_cache(source_path) else BASE_CACHE_DIR
    stem = cache_dir / str(int(image_id))
    return BasePaths(binary=stem.with_suffix(".bin.gz"), metadata=stem.with_suffix(".json"), preview=stem.with_suffix(".jpg"))


def cached_base_paths(image_id: int, path: str | os.PathLike[str]) -> BasePaths | None:
    """Return a complete, source-matching base cache without decoding anything.

    Develop's first paint must never wait on a cold RAW read.  Route handlers use
    this inexpensive probe before deciding whether to serve an artifact or kick
    off the existing single-flight base generator.
    """
    paths = base_paths(image_id, path)
    if not (paths.binary.exists() and paths.metadata.exists() and paths.preview.exists()):
        return None
    return paths if _cached_source_matches(paths, path) else None


def _image_lock(image_id: int) -> threading.Lock:
    with _locks_guard:
        return _image_locks.setdefault(int(image_id), threading.Lock())


def _finite_positive(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def derive_libraw_clip_levels(
    white_levels: Any,
    black_levels: Any,
    camera_whitebalance: Any,
    *,
    saturation_level: Any,
) -> np.ndarray | None:
    """Return RGB saturation boundaries after LibRaw's restored WB gain.

    LibRaw scales each black-subtracted sensor channel to ``user_sat`` and
    normalizes WB by its largest coefficient. ``decode_base`` restores the
    discarded common gain, leaving each boundary proportional to WB/green.
    """

    saturation = _finite_positive(saturation_level)
    try:
        whites = list(white_levels or [])
        blacks = list(black_levels or [])
        wb = list(camera_whitebalance or [])
    except TypeError:
        return None
    if saturation is None or len(whites) < 3 or len(wb) < 3:
        return None
    green_gain = _finite_positive(wb[1])
    if green_gain is None:
        return None

    clips = np.empty(3, dtype=np.float32)
    for channel in range(3):
        white = _finite_positive(whites[channel])
        gain = _finite_positive(wb[channel])
        try:
            black = float(blacks[channel]) if channel < len(blacks) else 0.0
        except (TypeError, ValueError):
            black = 0.0
        if white is None or gain is None or not math.isfinite(black):
            return None
        sensor_range = max(white - black, 1.0)
        saturation_range = max(saturation - black, 1.0)
        clips[channel] = UINT16_FULL_SCALE * np.float32(sensor_range / saturation_range) * np.float32(
            gain / green_gain
        )
    return clips


def _matrix_3x3(values: Any) -> np.ndarray | None:
    if values is None:
        return None
    try:
        flat = [float(x) for x in list(values)]
    except (TypeError, ValueError):
        return None
    if len(flat) != 9 or not all(math.isfinite(x) for x in flat):
        return None
    return np.asarray(flat, dtype=np.float64).reshape(3, 3)


def _planckian_uv_prime(cct: float) -> tuple[float, float]:
    """Approximate Planckian locus in CIE 1976 u'v' (Krystek 1985 → u'v')."""
    t = float(np.clip(cct, 1000.0, 20000.0))
    u = (0.860117757 + 1.54118254e-4 * t + 1.28641212e-7 * t * t) / (
        1.0 + 8.42420235e-4 * t + 7.08145163e-7 * t * t
    )
    v = (0.317398726 + 4.22806245e-5 * t + 4.20481691e-8 * t * t) / (
        1.0 - 2.89741816e-5 * t + 1.61456053e-7 * t * t
    )
    return float(u), float(1.5 * v)


def estimate_temp_tint_from_neutral(
    as_shot_neutral: Any, color_matrix: Any, color_matrix_a: Any = None
) -> dict[str, float] | None:
    """DNG-correct CCT/tint from AsShotNeutral + ColorMatrix (camera←XYZ).

    When both matrices are present (color_matrix = D65/6504K ColorMatrix2,
    color_matrix_a = StdA/2856K ColorMatrix1) the matrix is interpolated by
    inverse mired and the CCT solved iteratively, per the DNG spec — a single
    fixed matrix can misplace dusk/tungsten whites by >1500K.
    """
    asn_list = list(as_shot_neutral or [])
    if len(asn_list) < 3:
        return None
    asn = np.asarray([float(asn_list[0]), float(asn_list[1]), float(asn_list[2])], dtype=np.float64)
    if not np.all(np.isfinite(asn)) or np.any(asn <= 0):
        return None
    matrix_d65 = _matrix_3x3(color_matrix)
    matrix_a = _matrix_3x3(color_matrix_a)
    if matrix_d65 is None and matrix_a is None:
        return None

    def interpolated(cct_guess: float):
        if matrix_d65 is None:
            return matrix_a
        if matrix_a is None:
            return matrix_d65
        mired = 1_000_000.0 / min(max(cct_guess, 2000.0), 50000.0)
        weight_a = (mired - 1_000_000.0 / 6504.0) / (1_000_000.0 / 2856.0 - 1_000_000.0 / 6504.0)
        weight_a = min(max(weight_a, 0.0), 1.0)
        return matrix_d65 + weight_a * (matrix_a - matrix_d65)

    x = y = None
    cct_guess = 5000.0
    for _ in range(6):
        try:
            xyz = np.linalg.inv(interpolated(cct_guess)) @ asn
        except np.linalg.LinAlgError:
            return None
        if not np.all(np.isfinite(xyz)) or float(xyz.sum()) <= 0:
            return None
        x = float(xyz[0] / xyz.sum())
        y = float(xyz[1] / xyz.sum())
        denom_i = 0.1858 - y
        if abs(denom_i) < 1e-9:
            return None
        n_i = (x - 0.3320) / denom_i
        new_guess = 449.0 * n_i**3 + 3525.0 * n_i**2 + 6823.3 * n_i + 5520.33
        new_guess = float(np.clip(new_guess, 2000.0, 12000.0))
        if abs(new_guess - cct_guess) < 1.0:
            cct_guess = new_guess
            break
        cct_guess = new_guess
    denom = 0.1858 - y
    if abs(denom) < 1e-9:
        return None
    n = (x - 0.3320) / denom
    cct = 449.0 * n**3 + 3525.0 * n**2 + 6823.3 * n + 5520.33
    cct = float(np.clip(cct, 2000.0, 12000.0))
    d = -2.0 * x + 12.0 * y + 3.0
    if abs(d) < 1e-9:
        return None
    _up, vp = 4.0 * x / d, 9.0 * y / d
    up_p, vp_p = _planckian_uv_prime(cct)
    # Positive tint = greener = above the locus in v' (Lightroom convention).
    tint = float(np.clip((vp - vp_p) * C.TINT_UV_SCALE, -150.0, 150.0))
    return {"temperature": cct, "tint": tint}


def estimate_as_shot_white_balance_mired(camera_whitebalance: Any, daylight_whitebalance: Any) -> dict[str, Any]:
    """Fallback UI temp/tint from camera gains using an explicitly mired model.

    RAW camera gains are relative multipliers.  Let q be the camera R/B gain
    ratio divided by the daylight R/B ratio.  We estimate
    ``mired = 1e6/5500 + 285*log2(q)`` and ``temperature = 1e6/mired``.  More
    red gain therefore means a cooler scene and a larger mired value.  Tint is
    the green residual in stops relative to the red/blue geometric mean,
    scaled to Lightroom's approximately +/-150 control range.
    """

    camera = list(camera_whitebalance or [])
    daylight = list(daylight_whitebalance or [])
    if len(camera) < 3:
        return {
            "temperature": 5500,
            "tint": 0,
            "camera_whitebalance": [],
            "daylight_whitebalance": [],
            "method": "default",
        }

    red = _finite_positive(camera[0])
    green = _finite_positive(camera[1])
    blue = _finite_positive(camera[2])
    day_red = _finite_positive(daylight[0]) if len(daylight) >= 3 else None
    day_blue = _finite_positive(daylight[2]) if len(daylight) >= 3 else None
    if red is None or green is None or blue is None:
        return {
            "temperature": 5500,
            "tint": 0,
            "camera_whitebalance": [float(x) for x in camera if _finite_positive(x)],
            "daylight_whitebalance": [float(x) for x in daylight if _finite_positive(x)],
            "method": "default",
        }

    daylight_ratio = (day_red / day_blue) if day_red and day_blue else 1.0
    relative_rb = (red / blue) / daylight_ratio
    mired = (1_000_000.0 / 5500.0) + 285.0 * math.log2(max(relative_rb, 1e-6))
    temperature = int(round(max(2000.0, min(12000.0, 1_000_000.0 / max(mired, 1e-6)))))
    green_residual_stops = math.log2(green / math.sqrt(red * blue))
    tint = int(round(max(-150.0, min(150.0, -green_residual_stops * 100.0))))
    return {
        "temperature": temperature,
        "tint": tint,
        "camera_whitebalance": [float(value) for value in camera[:4]],
        "daylight_whitebalance": [float(value) for value in daylight[:4]],
        "method": "mired",
    }


def estimate_as_shot_white_balance(
    camera_whitebalance: Any = None,
    daylight_whitebalance: Any = None,
    *,
    as_shot_neutral: Any = None,
    color_matrix: Any = None,
    color_matrix2: Any = None,
) -> dict[str, Any]:
    """Prefer DNG ColorMatrix/McCamy; fall back to the crude mired gain model."""
    asn = as_shot_neutral
    if asn is None and camera_whitebalance is not None:
        camera = list(camera_whitebalance or [])
        if len(camera) >= 3:
            red = _finite_positive(camera[0])
            green = _finite_positive(camera[1])
            blue = _finite_positive(camera[2])
            if red and green and blue:
                # Camera multipliers are relative to green; ASN ∝ G / gains.
                asn = [green / red, 1.0, green / blue]
    dng = estimate_temp_tint_from_neutral(asn, color_matrix2, color_matrix)
    if dng is not None:
        fallback = estimate_as_shot_white_balance_mired(camera_whitebalance, daylight_whitebalance)
        return {
            "temperature": int(round(dng["temperature"])),
            "tint": int(round(dng["tint"])),
            "camera_whitebalance": fallback.get("camera_whitebalance") or [],
            "daylight_whitebalance": fallback.get("daylight_whitebalance") or [],
            "as_shot_neutral": [float(x) for x in list(asn)[:3]],
            "method": "dng_mccamy",
        }
    return estimate_as_shot_white_balance_mired(camera_whitebalance, daylight_whitebalance)


def _resize_linear_uint16(rgb: np.ndarray, max_edge: int | None = MAX_BASE_EDGE) -> np.ndarray:
    if max_edge is None:
        return np.ascontiguousarray(rgb, dtype=np.uint16)
    height, width = rgb.shape[:2]
    longest = max(width, height)
    if longest <= max_edge:
        return np.ascontiguousarray(rgb, dtype=np.uint16)
    scale = max_edge / float(longest)
    target = (max(1, round(width * scale)), max(1, round(height * scale)))
    # Pillow has no reliable LANCZOS path for 16-bit RGB arrays. Resize each
    # linear channel as float32 and convert back only after filtering.
    channels = [
        np.asarray(
            Image.fromarray(rgb[..., channel].astype(np.float32), mode="F").resize(
                target,
                Image.Resampling.LANCZOS,
            ),
            dtype=np.float32,
        )
        for channel in range(3)
    ]
    resized = np.stack(channels, axis=-1)
    return np.ascontiguousarray(np.clip(np.rint(resized), 0, np.iinfo(np.uint16).max), dtype=np.uint16)


def _rawpy_color_matrix(raw: Any) -> list[float] | None:
    for attr in ("color_matrix", "rgb_xyz_matrix"):
        matrix = _matrix_3x3(getattr(raw, attr, None))
        if matrix is None:
            continue
        # rawpy rgb_xyz_matrix is camera→XYZ; DNG ColorMatrix is XYZ→camera.
        if attr == "rgb_xyz_matrix":
            try:
                matrix = np.linalg.inv(matrix)
            except np.linalg.LinAlgError:
                continue
        return [float(x) for x in matrix.reshape(-1)]
    return None


def _srgb_to_linear(srgb: np.ndarray) -> np.ndarray:
    clipped = np.clip(np.asarray(srgb, dtype=np.float32), 0.0, 1.0)
    return np.where(
        clipped <= C.SRGB_DECODE_THRESHOLD,
        clipped * C.SRGB_DECODE_SCALE,
        np.power((clipped + C.SRGB_DECODE_A) / C.SRGB_ENCODE_A, C.SRGB_DECODE_GAMMA),
    ).astype(np.float32)


def decode_display_image(path: str | os.PathLike[str], *, max_edge: int | None = MAX_BASE_EDGE) -> np.ndarray:
    """Decode a display-referred image into linear uint16 sRGB-primary pixels."""

    source = Path(path)
    if not source.exists():
        raise FileNotFoundError(source)
    if not is_display_path(source):
        raise RawDecodeError(
            "Develop supports JPEG, PNG, TIFF, and WebP; HEIC requires Pillow codec support"
        )
    try:
        with Image.open(source) as image:
            display_rgb = np.asarray(ImageOps.exif_transpose(image).convert("RGB"), dtype=np.float32)
    except (OSError, ValueError) as exc:
        raise RawDecodeError(f"Image decode failed: {exc}") from exc
    linear = _srgb_to_linear(display_rgb / np.float32(255.0))
    rgb = np.asarray(np.clip(np.rint(linear * 65535.0), 0, 65535), dtype=np.uint16)
    return _resize_linear_uint16(rgb, max_edge=max_edge)


def decode_base(path: str | os.PathLike[str]) -> tuple[np.ndarray, dict[str, Any]]:
    """Decode a linear uint16 sRGB-primary Develop base image."""

    source = Path(path)
    if not source.exists():
        raise FileNotFoundError(source)
    if is_display_path(source):
        rgb = decode_display_image(source)
        return rgb, {
            "as_shot": {"temperature": 6500, "tint": 0.0, "method": "display"},
            "base_kind": "display",
            "width": int(rgb.shape[1]),
            "height": int(rgb.shape[0]),
            "dtype": "uint16",
            "linear": True,
            "source_meta_version": SOURCE_META_VERSION,
        }
    if rawpy is None:
        raise RawDecodeError("rawpy is unavailable")
    if not is_raw_path(source):
        raise RawDecodeError("Develop supports DNG, CR2, CR3, JPEG, PNG, TIFF, and WebP files")
    as_shot_neutral = None
    color_matrix = None
    color_matrix2 = None
    forward_matrix = None
    iso = None
    from features.develop import lossydng

    if source.suffix.lower() == ".dng" and lossydng.is_linear_dng(str(source)):
        try:
            rgb, lossy_meta = lossydng.decode_lossy_dng(str(source), max_px=MAX_BASE_EDGE)
        except Exception as exc:
            raise RawDecodeError(f"RAW decode failed: {exc}") from exc
        camera_wb = list(lossy_meta.get("cam_mul") or [])
        daylight_wb = []
        as_shot_neutral = lossy_meta.get("as_shot_neutral")
        color_matrix = lossy_meta.get("color_matrix1")
        color_matrix2 = lossy_meta.get("color_matrix2")
        forward_matrix = lossy_meta.get("forward_matrix")
        iso = _finite_positive(lossy_meta.get("iso"))
    else:
        try:
            with rawpy.imread(str(source)) as raw:
                camera_wb = list(raw.camera_whitebalance or [])
                daylight_wb = list(raw.daylight_whitebalance or [])
                color_matrix = _rawpy_color_matrix(raw)
                saturation_level = _finite_positive(raw.white_level)
                white_levels = [saturation_level] * 4 if saturation_level is not None else []
                iso = _finite_positive(getattr(getattr(raw, "metadata", None), "iso_speed", None))
                postprocess_args = {
                    "use_camera_wb": True,
                    "output_bps": 16,
                    "no_auto_bright": True,
                    "adjust_maximum_thr": 0.0,
                    "gamma": (1, 1),
                    "output_color": rawpy.ColorSpace.sRGB,
                    "highlight_mode": rawpy.HighlightMode.Blend,
                    "half_size": True,
                }
                camera_white = raw.camera_white_level_per_channel
                if camera_white:
                    valid_white = [int(value) for value in camera_white if int(value) > 0]
                    if valid_white:
                        saturation_level = float(max(valid_white))
                        postprocess_args["user_sat"] = int(saturation_level)
                        white_levels = [
                            float(value) if _finite_positive(value) is not None else saturation_level
                            for value in camera_white
                        ]
                rgb = raw.postprocess(**postprocess_args)
                valid_wb = [float(value) for value in camera_wb[:4] if _finite_positive(value)]
                if valid_wb:
                    # LibRaw normalizes camera WB so its largest multiplier is
                    # one. DNG reference-neutral semantics divide by the
                    # unnormalized AsShotNeutral, so restore that discarded
                    # common gain at the linear decode boundary.
                    green_wb = _finite_positive(camera_wb[1]) if len(camera_wb) > 1 else None
                    wb_scale = np.float32(max(valid_wb) / (green_wb or min(valid_wb)))
                    # In-place ops keep dev-perf's no-temporaries win while the
                    # float frame is still linear for highlight reconstruction.
                    linear_rgb = np.asarray(rgb, dtype=np.float32)
                    np.multiply(linear_rgb, wb_scale, out=linear_rgb)
                    clip_levels = derive_libraw_clip_levels(
                        white_levels,
                        raw.black_level_per_channel,
                        camera_wb,
                        saturation_level=saturation_level,
                    )
                    if clip_levels is not None:
                        linear_rgb = reconstruct_highlights(linear_rgb, clip_levels)
                    np.rint(linear_rgb, out=linear_rgb)
                    np.clip(linear_rgb, 0, UINT16_FULL_SCALE, out=linear_rgb)
                    rgb = linear_rgb.astype(np.uint16)
        except Exception as exc:
            raise RawDecodeError(f"RAW decode failed: {exc}") from exc
    rgb = _resize_linear_uint16(np.asarray(rgb, dtype=np.uint16))
    meta = {
        "as_shot": estimate_as_shot_white_balance(
            camera_wb,
            daylight_wb,
            as_shot_neutral=as_shot_neutral,
            color_matrix=color_matrix,
            color_matrix2=color_matrix2,
        ),
        "width": int(rgb.shape[1]),
        "height": int(rgb.shape[0]),
        "dtype": "uint16",
        "linear": True,
        "base_kind": "raw",
    }
    if iso is not None:
        meta["iso"] = iso
    if as_shot_neutral and forward_matrix and (color_matrix or color_matrix2):
        meta["color"] = {
            "as_shot_neutral": [float(v) for v in as_shot_neutral],
            "forward_matrix": [float(v) for v in forward_matrix],
            "color_matrix1": [float(v) for v in color_matrix] if color_matrix else None,
            "color_matrix2": [float(v) for v in color_matrix2] if color_matrix2 else None,
        }
    return rgb, _enrich_source_metadata(meta, source)


def _enrich_source_metadata(meta: dict[str, Any], path: str | os.PathLike[str]) -> dict[str, Any]:
    """Attach camera/profile/lens data, including to already-cached bases."""
    if meta.get("base_kind") == "display":
        meta["source_meta_version"] = SOURCE_META_VERSION
        return meta
    if int(meta.get("source_meta_version") or 0) >= SOURCE_META_VERSION:
        return meta
    source = dict(read_exif(str(path)))
    source.update({key: value for key, value in meta.items() if value not in (None, "")})
    normalized = normalized_source_metadata(source)
    for key, value in normalized.items():
        if value not in (None, ""):
            meta[key] = value
    fitted = load_camera_profile(normalized.get("camera_model") or "")
    correction = resolve_lens_correction(normalized)
    color = dict(meta.get("color") or {})
    if fitted is not None:
        meta["camera_profile"] = fitted
        color["camera_profile"] = fitted
    if correction is not None:
        meta["lens_correction"] = correction
        color["lens_correction"] = correction
    if color:
        meta["color"] = color
    meta["source_meta_version"] = SOURCE_META_VERSION
    return meta


def _linear_to_srgb(linear: np.ndarray) -> np.ndarray:
    clipped = np.clip(linear, 0.0, 1.0)
    return np.where(clipped <= 0.0031308, clipped * 12.92, 1.055 * np.power(clipped, 1.0 / 2.4) - 0.055)


def _write_base_cache(paths: BasePaths, rgb: np.ndarray, meta: dict[str, Any]) -> None:
    paths.binary.parent.mkdir(parents=True, exist_ok=True)
    height, width = rgb.shape[:2]
    payload = BASE_HEADER.pack(BASE_MAGIC, width, height) + np.ascontiguousarray(rgb.astype("<u2", copy=False)).tobytes()
    binary_temp = paths.binary.with_suffix(".bin.gz.tmp")
    with gzip.open(binary_temp, "wb", compresslevel=1) as handle:
        handle.write(payload)
    os.replace(binary_temp, paths.binary)

    # The base preview must match the library thumbnail: develop the linear
    # base through the default pipeline (WB + color matrices + tone), not a bare
    # sRGB gamma of scene-linear data — which reads dark, flat, and desaturated
    # ("vomit") the moment you open Develop. Falls back to the raw encode only
    # if the pipeline is somehow unavailable.
    try:
        from features.develop.render import develop_default_render

        developed = develop_default_render(rgb.astype(np.float32) / 65535.0, meta)
        encoded = np.asarray(np.clip(developed * 255.0 + 0.5, 0, 255), dtype=np.uint8)
    except Exception:
        log.exception("base preview pipeline render failed; using raw sRGB encode")
        encoded = np.rint(_linear_to_srgb(rgb.astype(np.float32) / 65535.0) * 255.0).astype(np.uint8)
    preview_temp = paths.preview.with_suffix(".jpg.tmp")
    Image.fromarray(encoded, mode="RGB").save(preview_temp, format="JPEG", quality=88)
    os.replace(preview_temp, paths.preview)

    _write_base_metadata(paths.metadata, meta)


def _write_base_metadata(path: Path, meta: dict[str, Any]) -> None:
    metadata_temp = path.with_suffix(".json.tmp")
    metadata_temp.write_text(json.dumps(meta, separators=(",", ":")), encoding="utf-8")
    os.replace(metadata_temp, path)


def _read_base_metadata_path(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None


def read_base_metadata(image_id: int) -> dict[str, Any] | None:
    native = _raw_base_cache_dir() / f"{int(image_id)}.json"
    legacy = base_paths(image_id).metadata
    for candidate in dict.fromkeys((native, legacy)):
        meta = _read_base_metadata_path(candidate)
        if meta is not None:
            return meta
    return None


def _upgrade_cached_metadata(paths: BasePaths, source_path: str | os.PathLike[str]) -> dict[str, Any]:
    meta = _read_base_metadata_path(paths.metadata) or {}
    before = json.dumps(meta, sort_keys=True, separators=(",", ":"))
    meta = _enrich_source_metadata(meta, source_path)
    if json.dumps(meta, sort_keys=True, separators=(",", ":")) != before:
        _write_base_metadata(paths.metadata, meta)
    return meta


def parse_base_payload(payload: bytes) -> tuple[np.ndarray, int, int]:
    """Parse an uncompressed PABASE1 stream; used by tests and future streams."""

    if len(payload) < BASE_HEADER.size:
        raise ValueError("PABASE1 payload is truncated")
    magic, width, height = BASE_HEADER.unpack_from(payload)
    if magic != BASE_MAGIC or width <= 0 or height <= 0:
        raise ValueError("Invalid PABASE1 header")
    expected = BASE_HEADER.size + width * height * 3 * np.dtype("<u2").itemsize
    if len(payload) != expected:
        raise ValueError("PABASE1 payload has the wrong pixel length")
    rgb = np.frombuffer(payload, dtype="<u2", offset=BASE_HEADER.size).reshape(height, width, 3)
    return rgb, width, height


def _cached_source_matches(paths: BasePaths, path: str | os.PathLike[str]) -> bool:
    """Reject a cached base generated from a different source file (id reuse)."""
    meta = _read_base_metadata_path(paths.metadata) or {}
    recorded = meta.get("source_path")
    return not recorded or str(recorded) == str(path)


def ensure_base_cache(image_id: int, path: str | os.PathLike[str]) -> tuple[BasePaths, dict[str, Any]]:
    """Generate missing base artifacts once per image, even under concurrent hits."""

    paths = base_paths(image_id, path)
    cached = cached_base_paths(image_id, path)
    if cached is not None:
        return cached, _upgrade_cached_metadata(cached, path)
    if paths.binary.exists() and paths.metadata.exists() and paths.preview.exists():
        for stale in (paths.binary, paths.metadata, paths.preview):
            stale.unlink(missing_ok=True)
    if is_hdr_merge_path(path):
        raise RawDecodeError("HDR merge base cache is unavailable")
    lock = _image_lock(image_id)
    with lock:
        cached = cached_base_paths(image_id, path)
        if cached is not None:
            return cached, _upgrade_cached_metadata(cached, path)
        if paths.binary.exists() and paths.metadata.exists() and paths.preview.exists():
            for stale in (paths.binary, paths.metadata, paths.preview):
                stale.unlink(missing_ok=True)
        if not Path(path).is_file():
            from features.sync import readthrough

            if readthrough.can_read_through():
                import db

                try:
                    meta = readthrough.fetch_base_cache_for_image(
                        image_id,
                        paths,
                        db_path=db.DB_PATH,
                        source_path=str(path),
                        blocking=True,
                    )
                except readthrough.BaseReadthroughError as exc:
                    raise RawDecodeError(str(exc)) from exc
                if meta is not None:
                    return paths, meta
        recent = _recent_decodes.pop(int(image_id), None)
        if recent is None:
            rgb, meta = decode_base(path)
        else:
            rgb, meta = recent
        meta.setdefault("source_path", str(path))
        _write_base_cache(paths, rgb, meta)
        _recent_decodes[int(image_id)] = (rgb, meta)
        while len(_recent_decodes) > MEMORY_BASE_LIMIT:
            _recent_decodes.popitem(last=False)
        return paths, meta
