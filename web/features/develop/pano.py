"""Panorama sequence discovery and OpenCV stitch merge for Develop.

Mirrors ``hdr.py``: detect → threaded merge → status, with the result registered
as a library image backed by float32 EXR + the normal PABASE1 cache artifact.

Precision tradeoff (documented, intentional): OpenCV's stitcher only accepts
8-bit images. We gamma-encode our decoded linear floats to sRGB uint8, stitch,
then invert the sRGB curve back to linear. That round-trip:

* quantizes to ~1/255 in midtones (worse in shadows where the curve is steeper
  in linear space),
* permanently clips any linear headroom above 1.0,
* is *not* a bit-exact recovery of the decoded bases.

The stored EXR / PABASE is this recovered linear estimate. Kind tag: ``pano``.
"""

from __future__ import annotations

import gzip
import json
import math
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Callable, Iterable

import cv2
import imagecodecs
import numpy as np
from PIL import Image

from core.runtime_paths import resolve_runtime_paths
from features.develop import hdr, rawproc


PANO_CACHE_DIR = Path(resolve_runtime_paths().develop_cache_dir) / "pano"
PANO_SOURCE_NAME = "Panorama Merges"
PANO_MAX_EDGE = 3000
PANO_MIN_FRAMES = 2
PANO_MAX_FRAMES = 8
PANO_MAX_GAP_SECONDS = 10.0

# OpenCV stitcher status → honest failure labels (cv2 4/5 share these ints).
_STITCH_STATUS_LABELS = {
    int(getattr(cv2, "STITCHER_OK", 0)): "ok",
    int(getattr(cv2, "STITCHER_ERR_NEED_MORE_IMGS", 1)): "need_more_images",
    int(getattr(cv2, "STITCHER_ERR_HOMOGRAPHY_EST_FAIL", 2)): "homography_estimate_failed",
    int(getattr(cv2, "STITCHER_ERR_CAMERA_PARAMS_ADJUST_FAIL", 3)): "camera_params_adjust_failed",
}

_status_lock = threading.Lock()
_status: dict[str, Any] = {
    "running": False,
    "phase": "idle",
    "image_ids": [],
    "result_id": None,
    "error": None,
    "started_at": None,
    "finished_at": None,
    "seconds": None,
}


class PanoError(RuntimeError):
    """A requested sequence cannot produce a usable panorama merge."""


def pano_status() -> dict[str, Any]:
    with _status_lock:
        return dict(_status)


def _set_status(**changes: Any) -> None:
    with _status_lock:
        _status.update(changes)


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _same_optical_setup(first: dict[str, Any], second: dict[str, Any]) -> bool:
    first_lens, second_lens = first.get("lens_model"), second.get("lens_model")
    first_focal, second_focal = first.get("focal_length"), second.get("focal_length")
    if not first_lens or not second_lens or first_lens.casefold() != second_lens.casefold():
        return False
    if first_focal is None or second_focal is None:
        return False
    return abs(float(first_focal) - float(second_focal)) < 0.01


def _shutter_seconds(row: dict[str, Any]) -> float | None:
    direct = _number(row.get("ExposureTime"))
    if direct and direct > 0:
        return direct
    apex = _number(row.get("ShutterSpeedValue"))
    return 2.0 ** (-apex) if apex is not None else None


def _exposure_signature(row: dict[str, Any]) -> tuple[str, float] | None:
    """Stable exposure key so differing brackets are excluded from pano runs."""

    bias = _number(row.get("ExposureBiasValue"))
    if bias is not None:
        return ("bias", round(bias, 3))
    shutter = _shutter_seconds(row)
    if shutter is not None and shutter > 0:
        return ("shutter", round(shutter, 6))
    relative = _number(row.get("relative_ev"))
    if relative is not None:
        return ("ev", round(relative, 3))
    return None


def _uniform_exposure(frames: list[dict[str, Any]]) -> bool:
    signatures = {_exposure_signature(frame) for frame in frames}
    signatures.discard(None)
    return len(signatures) <= 1


def _is_hdr_like_exposure_spread(frames: list[dict[str, Any]]) -> bool:
    """True when the run has distinct shutters/biases (an HDR bracket, not a pano)."""

    shutters = {_shutter_seconds(frame) for frame in frames}
    biases = {_number(frame.get("ExposureBiasValue")) for frame in frames}
    return len(shutters - {None}) >= 2 or len(biases - {None}) >= 2


def detect_sequences(db_path: str, image_ids: Iterable[int] | None = None) -> list[dict[str, Any]]:
    """Return 2–8 frame runs: same lens/focal, ≤10s apart, not an HDR bracket."""

    frames = [
        row
        for row in hdr.image_exposure_rows(db_path, image_ids)
        if row.get("capture_timestamp") is not None
        and row.get("lens_model")
        and row.get("focal_length") is not None
    ]
    frames.sort(key=lambda row: float(row["capture_timestamp"]))

    hdr_sets = {frozenset(bracket["image_ids"]) for bracket in hdr.detect_brackets(db_path, image_ids)}

    runs: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for frame in frames:
        if current and (
            float(frame["capture_timestamp"]) - float(current[-1]["capture_timestamp"]) <= PANO_MAX_GAP_SECONDS
            and _same_optical_setup(current[-1], frame)
        ):
            current.append(frame)
        else:
            if PANO_MIN_FRAMES <= len(current) <= PANO_MAX_FRAMES:
                runs.append(current)
            elif len(current) > PANO_MAX_FRAMES:
                for start in range(0, len(current) - PANO_MIN_FRAMES + 1, PANO_MAX_FRAMES):
                    chunk = current[start : start + PANO_MAX_FRAMES]
                    if len(chunk) >= PANO_MIN_FRAMES:
                        runs.append(chunk)
            current = [frame]
    if PANO_MIN_FRAMES <= len(current) <= PANO_MAX_FRAMES:
        runs.append(current)
    elif len(current) > PANO_MAX_FRAMES:
        for start in range(0, len(current) - PANO_MIN_FRAMES + 1, PANO_MAX_FRAMES):
            chunk = current[start : start + PANO_MAX_FRAMES]
            if len(chunk) >= PANO_MIN_FRAMES:
                runs.append(chunk)

    sequences: list[dict[str, Any]] = []
    for run in runs:
        ids = [int(frame["id"]) for frame in run]
        if frozenset(ids) in hdr_sets:
            continue
        if _is_hdr_like_exposure_spread(run) or not _uniform_exposure(run):
            continue
        sequences.append(
            {
                "image_ids": ids,
                "capture_timestamp": run[0]["capture_timestamp"],
                "lens": run[0]["lens_model"],
                "focal_length": run[0]["focal_length"],
                "frame_count": len(run),
                "span_seconds": float(run[-1]["capture_timestamp"]) - float(run[0]["capture_timestamp"]),
                "frames": [
                    {
                        "image_id": int(frame["id"]),
                        "capture_timestamp": frame["capture_timestamp"],
                        "exposure_bias": _number(frame.get("ExposureBiasValue")),
                    }
                    for frame in run
                ],
            }
        )
    return sequences


def linear_to_srgb_u8(linear_rgb: np.ndarray) -> np.ndarray:
    """Encode linear RGB (0..1+) to 8-bit sRGB for the OpenCV stitcher."""

    x = np.clip(np.asarray(linear_rgb, dtype=np.float32), 0.0, 1.0)
    encoded = np.where(x <= 0.0031308, x * 12.92, 1.055 * np.power(np.maximum(x, 0.0), 1.0 / 2.4) - 0.055)
    return np.rint(np.clip(encoded, 0.0, 1.0) * 255.0).astype(np.uint8)


def srgb_u8_to_linear(srgb_u8: np.ndarray) -> np.ndarray:
    """Inverse of ``linear_to_srgb_u8`` — recovers an approximate linear float."""

    x = np.asarray(srgb_u8, dtype=np.float32) / 255.0
    return np.where(x <= 0.04045, x / 12.92, np.power((x + 0.055) / 1.055, 2.4)).astype(np.float32)


def _downscale_max_edge(rgb: np.ndarray, max_edge: int = PANO_MAX_EDGE) -> np.ndarray:
    source = np.asarray(rgb, dtype=np.float32)
    height, width = source.shape[:2]
    edge = max(height, width)
    if edge <= max_edge:
        return source
    scale = max_edge / float(edge)
    new_w = max(1, int(round(width * scale)))
    new_h = max(1, int(round(height * scale)))
    channels = [
        np.asarray(
            Image.fromarray(np.asarray(source[..., channel], dtype=np.float32), mode="F").resize(
                (new_w, new_h), Image.Resampling.LANCZOS
            ),
            dtype=np.float32,
        )
        for channel in range(3)
    ]
    return np.stack(channels, axis=-1)


def stitch_linear_arrays(arrays: list[np.ndarray], *, max_edge: int = PANO_MAX_EDGE) -> tuple[np.ndarray, dict[str, Any]]:
    """Stitch linear RGB frames via 8-bit sRGB OpenCV stitcher; return linear float."""

    if not (PANO_MIN_FRAMES <= len(arrays) <= PANO_MAX_FRAMES):
        raise PanoError(f"Panorama merge needs {PANO_MIN_FRAMES}–{PANO_MAX_FRAMES} frames")
    prepared_u8: list[np.ndarray] = []
    source_sizes: list[tuple[int, int]] = []
    for array in arrays:
        linear = _downscale_max_edge(np.asarray(array, dtype=np.float32), max_edge=max_edge)
        if linear.ndim != 3 or linear.shape[2] != 3:
            raise PanoError("Panorama decoder returned invalid RGB data")
        source_sizes.append((int(linear.shape[1]), int(linear.shape[0])))
        # OpenCV expects BGR uint8; convert after gamma encode.
        srgb = linear_to_srgb_u8(linear)
        prepared_u8.append(np.ascontiguousarray(srgb[..., ::-1]))

    stitcher = cv2.Stitcher_create(cv2.Stitcher_PANORAMA)
    status, bgr = stitcher.stitch(prepared_u8)
    label = _STITCH_STATUS_LABELS.get(int(status), f"unknown_status_{status}")
    if int(status) != int(getattr(cv2, "STITCHER_OK", 0)) or bgr is None:
        raise PanoError(f"Stitch failed ({label})")

    srgb = np.ascontiguousarray(bgr[..., ::-1])
    linear = srgb_u8_to_linear(srgb)
    info = {
        "stitch_status": label,
        "stitch_confidence": "ok" if label == "ok" else label,
        "source_sizes": source_sizes,
        "result_size": [int(linear.shape[1]), int(linear.shape[0])],
        "max_edge": max_edge,
        "precision": "srgb-u8-roundtrip",
    }
    return np.ascontiguousarray(np.maximum(linear, 0.0), dtype=np.float32), info


def _write_pano_base(image_id: int, merged: np.ndarray, meta: dict[str, Any]) -> None:
    """Write PABASE1 uint16 artifacts (same shape as HDR, scale retained for headroom)."""

    paths = rawproc.base_paths(image_id)
    paths.binary.parent.mkdir(parents=True, exist_ok=True)
    scale = max(1.0, float(np.max(merged)) if merged.size else 1.0)
    encoded = np.rint(np.clip(merged / scale, 0.0, 1.0) * 65535.0).astype("<u2")
    height, width = encoded.shape[:2]
    payload = rawproc.BASE_HEADER.pack(rawproc.BASE_MAGIC, width, height) + encoded.tobytes()
    temp_binary = paths.binary.with_suffix(".bin.gz.tmp")
    with gzip.open(temp_binary, "wb") as handle:
        handle.write(payload)
    os.replace(temp_binary, paths.binary)
    preview_linear = np.clip(merged / scale, 0.0, 1.0)
    preview = np.where(
        preview_linear <= 0.0031308,
        preview_linear * 12.92,
        1.055 * np.power(preview_linear, 1.0 / 2.4) - 0.055,
    )
    temp_preview = paths.preview.with_suffix(".jpg.tmp")
    Image.fromarray(np.rint(np.clip(preview, 0.0, 1.0) * 255.0).astype(np.uint8), mode="RGB").save(
        temp_preview, format="JPEG", quality=88
    )
    os.replace(temp_preview, paths.preview)
    meta = {
        **meta,
        "width": width,
        "height": height,
        "dtype": "uint16",
        "linear": True,
        "pano": {**(meta.get("pano") or {}), "scale": scale, "format": "float32-exr", "kind": "pano"},
    }
    temp_meta = paths.metadata.with_suffix(".json.tmp")
    temp_meta.write_text(json.dumps(meta, separators=(",", ":")), encoding="utf-8")
    os.replace(temp_meta, paths.metadata)


def _register_merge(db_path: str, frames: list[dict[str, Any]], merged: np.ndarray, stitch_info: dict[str, Any]) -> int:
    PANO_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    token = f"PANO_{stamp}_{int(time.time_ns() % 1_000_000):06d}"
    exr_path = PANO_CACHE_DIR / f"{token}.exr"
    temp_exr = exr_path.with_suffix(".exr.tmp")
    temp_exr.write_bytes(imagecodecs.exr_encode(np.ascontiguousarray(merged, dtype=np.float32)))
    os.replace(temp_exr, exr_path)
    anchor = frames[len(frames) // 2]
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        now = time.time()
        conn.execute(
            "INSERT INTO catalog_sources (path, display_name, included, online, created_at, last_scan_at, last_seen_at) VALUES (?, ?, 1, 1, ?, ?, ?) "
            "ON CONFLICT(path) DO UPDATE SET included=1, online=1, last_seen_at=excluded.last_seen_at, removed_at=NULL",
            (str(PANO_CACHE_DIR), PANO_SOURCE_NAME, now, now, now),
        )
        source_id = int(conn.execute("SELECT id FROM catalog_sources WHERE path = ?", (str(PANO_CACHE_DIR),)).fetchone()["id"])
        cursor = conn.execute(
            "INSERT INTO images (source_id, filename, filepath, status, file_ext, file_size, file_modified_at, width, height, date_taken, camera_make, camera_model, lens) "
            "VALUES (?, ?, ?, 'kept', '.exr', ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                source_id,
                exr_path.name,
                str(exr_path),
                int(exr_path.stat().st_size),
                exr_path.stat().st_mtime,
                int(merged.shape[1]),
                int(merged.shape[0]),
                anchor.get("date_taken"),
                anchor.get("camera_make"),
                anchor.get("camera_model"),
                anchor.get("lens") or anchor.get("lens_model"),
            ),
        )
        image_id = int(cursor.lastrowid)
        conn.execute(
            "UPDATE catalog_sources SET image_count=(SELECT COUNT(*) FROM images WHERE source_id=?), "
            "active_image_count=(SELECT COUNT(*) FROM images WHERE source_id=? AND status IN ('kept', 'maybe') AND missing_at IS NULL) WHERE id=?",
            (source_id, source_id, source_id),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        exr_path.unlink(missing_ok=True)
        raise
    finally:
        conn.close()
    _write_pano_base(
        image_id,
        merged,
        {
            "pano_sources": [int(frame["id"]) for frame in frames],
            "pano": {"stitch": stitch_info, "kind": "pano"},
            "as_shot": {"temperature": 5500, "tint": 0},
        },
    )
    return image_id


def merge_images(
    db_path: str,
    image_ids: Iterable[int],
    *,
    decode: Callable[[str], tuple[np.ndarray, dict[str, Any]]] = rawproc.decode_base,
) -> dict[str, Any]:
    ids = list(dict.fromkeys(int(image_id) for image_id in image_ids if int(image_id) > 0))
    if not (PANO_MIN_FRAMES <= len(ids) <= PANO_MAX_FRAMES):
        raise PanoError(f"Panorama merge needs {PANO_MIN_FRAMES}–{PANO_MAX_FRAMES} selected RAW images")
    by_id = {int(row["id"]): row for row in hdr.image_exposure_rows(db_path, ids)}
    if len(by_id) != len(ids):
        raise PanoError("One or more selected images are unavailable RAW files")
    frames = [by_id[image_id] for image_id in ids]
    if _is_hdr_like_exposure_spread(frames) or not _uniform_exposure(frames):
        raise PanoError("Selected images look like an HDR bracket (differing exposures); use HDR merge instead")
    arrays = [decode(frame["filepath"])[0].astype(np.float32) / 65535.0 for frame in frames]
    merged, stitch_info = stitch_linear_arrays(arrays)
    image_id = _register_merge(db_path, frames, merged, stitch_info)
    return {"image_id": image_id, "image_ids": ids, "stitch": stitch_info}


def begin_merge(db_path: str, image_ids: Iterable[int]) -> bool:
    ids = list(dict.fromkeys(int(image_id) for image_id in image_ids if int(image_id) > 0))
    with _status_lock:
        if _status["running"]:
            return False
        _status.update(
            running=True,
            phase="queued",
            image_ids=ids,
            result_id=None,
            error=None,
            started_at=time.time(),
            finished_at=None,
            seconds=None,
        )

    def worker() -> None:
        started = time.monotonic()
        try:
            _set_status(phase="decoding")
            result = merge_images(db_path, ids)
            _set_status(phase="complete", result_id=result["image_id"])
        except Exception as exc:
            _set_status(phase="error", error=str(exc))
        finally:
            _set_status(running=False, finished_at=time.time(), seconds=round(time.monotonic() - started, 3))

    threading.Thread(target=worker, name="photoarchive-pano-merge", daemon=True).start()
    return True
