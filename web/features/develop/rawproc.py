"""Linear RAW base-preview decode and cache helpers for Develop.

The cache lives on the expansion volume: large RAW previews must never consume
the nearly-full root disk.  The `.bin.gz` payload starts with a 16-byte PABASE1
header and then little-endian interleaved uint16 RGB pixels.
"""

from __future__ import annotations

import gzip
import json
import math
import os
import struct
import threading
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

try:
    import rawpy
except ImportError:  # pragma: no cover - rawpy is an application dependency.
    rawpy = None


RAW_EXTENSIONS = {".dng", ".cr2", ".cr3"}
BASE_CACHE_ROOT = Path(os.environ.get("PHOTOARCHIVE_DEVELOP_CACHE_DIR", "/mnt/expansion/PhotoArchiveCache/develop"))
BASE_CACHE_DIR = BASE_CACHE_ROOT / "base"
BASE_MAGIC = b"PABASE1\0"
BASE_HEADER = struct.Struct("<8sII")
MAX_BASE_EDGE = 2048
MEMORY_BASE_LIMIT = 2


class RawDecodeError(RuntimeError):
    """A RAW exists but could not be decoded into a usable preview."""


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


def base_paths(image_id: int) -> BasePaths:
    stem = BASE_CACHE_DIR / str(int(image_id))
    return BasePaths(binary=stem.with_suffix(".bin.gz"), metadata=stem.with_suffix(".json"), preview=stem.with_suffix(".jpg"))


def _image_lock(image_id: int) -> threading.Lock:
    with _locks_guard:
        return _image_locks.setdefault(int(image_id), threading.Lock())


def _finite_positive(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def estimate_as_shot_white_balance(camera_whitebalance: Any, daylight_whitebalance: Any) -> dict[str, Any]:
    """Estimate UI temp/tint from camera gains using an explicitly mired model.

    RAW camera gains are relative multipliers.  Let q be the camera R/B gain
    ratio divided by the daylight R/B ratio.  We estimate
    ``mired = 1e6/5500 + 285*log2(q)`` and ``temperature = 1e6/mired``.  More
    red gain therefore means a cooler scene and a larger mired value.  Tint is
    the green residual in stops relative to the red/blue geometric mean,
    scaled to Lightroom's approximately +/-150 control range.  This is an
    as-shot UI estimate, not a color-science replacement for a camera matrix.
    """

    camera = list(camera_whitebalance or [])
    daylight = list(daylight_whitebalance or [])
    if len(camera) < 3:
        return {"temperature": 5500, "tint": 0, "camera_whitebalance": [], "daylight_whitebalance": []}

    red = _finite_positive(camera[0])
    green = _finite_positive(camera[1])
    blue = _finite_positive(camera[2])
    day_red = _finite_positive(daylight[0]) if len(daylight) >= 3 else None
    day_blue = _finite_positive(daylight[2]) if len(daylight) >= 3 else None
    if red is None or green is None or blue is None:
        return {"temperature": 5500, "tint": 0, "camera_whitebalance": [float(x) for x in camera if _finite_positive(x)], "daylight_whitebalance": [float(x) for x in daylight if _finite_positive(x)]}

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
    }


def _resize_linear_uint16(rgb: np.ndarray, max_edge: int = MAX_BASE_EDGE) -> np.ndarray:
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


def decode_base(path: str | os.PathLike[str]) -> tuple[np.ndarray, dict[str, Any]]:
    """Decode a half-size, linear uint16 sRGB-primary Develop base image."""

    if rawpy is None:
        raise RawDecodeError("rawpy is unavailable")
    source = Path(path)
    if not source.exists():
        raise FileNotFoundError(source)
    if not is_raw_path(source):
        raise RawDecodeError("Develop supports DNG, CR2, and CR3 files only")
    try:
        with rawpy.imread(str(source)) as raw:
            camera_wb = list(raw.camera_whitebalance or [])
            daylight_wb = list(raw.daylight_whitebalance or [])
            rgb = raw.postprocess(
                use_camera_wb=True,
                output_bps=16,
                no_auto_bright=True,
                gamma=(1, 1),
                output_color=rawpy.ColorSpace.sRGB,
                highlight_mode=rawpy.HighlightMode.Blend,
                half_size=True,
            )
    except Exception as exc:
        raise RawDecodeError(f"RAW decode failed: {exc}") from exc
    rgb = _resize_linear_uint16(np.asarray(rgb, dtype=np.uint16))
    meta = {
        "as_shot": estimate_as_shot_white_balance(camera_wb, daylight_wb),
        "width": int(rgb.shape[1]),
        "height": int(rgb.shape[0]),
        "dtype": "uint16",
        "linear": True,
    }
    return rgb, meta


def _linear_to_srgb(linear: np.ndarray) -> np.ndarray:
    clipped = np.clip(linear, 0.0, 1.0)
    return np.where(clipped <= 0.0031308, clipped * 12.92, 1.055 * np.power(clipped, 1.0 / 2.4) - 0.055)


def _write_base_cache(paths: BasePaths, rgb: np.ndarray, meta: dict[str, Any]) -> None:
    paths.binary.parent.mkdir(parents=True, exist_ok=True)
    height, width = rgb.shape[:2]
    payload = BASE_HEADER.pack(BASE_MAGIC, width, height) + np.ascontiguousarray(rgb.astype("<u2", copy=False)).tobytes()
    binary_temp = paths.binary.with_suffix(".bin.gz.tmp")
    with gzip.open(binary_temp, "wb") as handle:
        handle.write(payload)
    os.replace(binary_temp, paths.binary)

    encoded = np.rint(_linear_to_srgb(rgb.astype(np.float32) / 65535.0) * 255.0).astype(np.uint8)
    preview_temp = paths.preview.with_suffix(".jpg.tmp")
    Image.fromarray(encoded, mode="RGB").save(preview_temp, format="JPEG", quality=88)
    os.replace(preview_temp, paths.preview)

    metadata_temp = paths.metadata.with_suffix(".json.tmp")
    metadata_temp.write_text(json.dumps(meta, separators=(",", ":")), encoding="utf-8")
    os.replace(metadata_temp, paths.metadata)


def read_base_metadata(image_id: int) -> dict[str, Any] | None:
    try:
        return json.loads(base_paths(image_id).metadata.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None


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


def ensure_base_cache(image_id: int, path: str | os.PathLike[str]) -> tuple[BasePaths, dict[str, Any]]:
    """Generate missing base artifacts once per image, even under concurrent hits."""

    paths = base_paths(image_id)
    if paths.binary.exists() and paths.metadata.exists() and paths.preview.exists():
        return paths, read_base_metadata(image_id) or {}
    lock = _image_lock(image_id)
    with lock:
        if paths.binary.exists() and paths.metadata.exists() and paths.preview.exists():
            return paths, read_base_metadata(image_id) or {}
        recent = _recent_decodes.pop(int(image_id), None)
        if recent is None:
            rgb, meta = decode_base(path)
        else:
            rgb, meta = recent
        _write_base_cache(paths, rgb, meta)
        _recent_decodes[int(image_id)] = (rgb, meta)
        while len(_recent_decodes) > MEMORY_BASE_LIMIT:
            _recent_decodes.popitem(last=False)
        return paths, meta
