"""Bracket discovery and translation-only HDR merge for Develop.

The catalog intentionally keeps only broadly useful image metadata, so exposure
fields are read from catalog columns when they exist and otherwise from the
installed ExifTool.  HDR output is a float32 EXR plus the normal PABASE1 cache
artifact used by Develop; its ``hdr.scale`` metadata restores values above one
when the base is uploaded to the renderer.
"""

from __future__ import annotations

import gzip
import json
import math
import os
import sqlite3
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable

import imagecodecs
import numpy as np
from PIL import Image

from features.develop import rawproc


EXIFTOOL = "/usr/bin/vendor_perl/exiftool"
HDR_CACHE_DIR = Path(os.environ.get("PHOTOARCHIVE_DEVELOP_CACHE_DIR", "/mnt/expansion/PhotoArchiveCache/develop")) / "hdr"
HDR_SOURCE_NAME = "HDR Merges"
RAW_EXTENSIONS = frozenset({".dng", ".cr2", ".cr3"})
_EXIF_FIELDS = (
    "ExposureTime", "ShutterSpeedValue", "FNumber", "ApertureValue", "ISO",
    "ExposureBiasValue", "DateTimeOriginal", "SubSecDateTimeOriginal", "CreateDate",
    "FocalLength", "LensModel",
)
_status_lock = threading.Lock()
_status: dict[str, Any] = {"running": False, "phase": "idle", "image_ids": [], "result_id": None, "error": None, "started_at": None, "finished_at": None, "seconds": None}


class HdrError(RuntimeError):
    """A requested bracket cannot produce a usable HDR merge."""


def hdr_status() -> dict[str, Any]:
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


def _timestamp(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value or "").strip()
    if not text:
        return None
    text = text.replace(":", "-", 2).replace("Z", "+00:00")
    for candidate in (text, text.replace(" ", "T", 1)):
        try:
            return datetime.fromisoformat(candidate).timestamp()
        except ValueError:
            pass
    return None


def _shutter_seconds(row: dict[str, Any]) -> float | None:
    direct = _number(row.get("ExposureTime"))
    if direct and direct > 0:
        return direct
    apex = _number(row.get("ShutterSpeedValue"))
    return 2.0 ** (-apex) if apex is not None else None


def _aperture(row: dict[str, Any]) -> float | None:
    direct = _number(row.get("FNumber"))
    if direct and direct > 0:
        return direct
    apex = _number(row.get("ApertureValue"))
    return 2.0 ** (apex / 2.0) if apex is not None else None


def exposure_signal(row: dict[str, Any]) -> float | None:
    """Relative sensor exposure: shutter * ISO / aperture squared."""

    shutter, aperture, iso = _shutter_seconds(row), _aperture(row), _number(row.get("ISO"))
    if not shutter or not aperture or not iso or shutter <= 0 or aperture <= 0 or iso <= 0:
        return None
    return shutter * iso / (aperture * aperture)


def exposure_ev(row: dict[str, Any]) -> float | None:
    signal = exposure_signal(row)
    return math.log2(signal) if signal and signal > 0 else None


def _catalog_rows(db_path: str, image_ids: Iterable[int] | None) -> list[dict[str, Any]]:
    """Read all useful image columns, including future exposure columns if added."""

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(images)")}
        optional = [name for name in _EXIF_FIELDS if name in columns]
        selected = ["id", "filepath", "filename", "date_taken", "camera_make", "camera_model", "lens", "file_ext", *optional]
        where = "status IN ('kept', 'maybe') AND missing_at IS NULL"
        params: list[Any] = []
        if image_ids is not None:
            ids = list(dict.fromkeys(int(image_id) for image_id in image_ids if int(image_id) > 0))
            if not ids:
                return []
            where += " AND id IN (" + ",".join("?" for _ in ids) + ")"
            params.extend(ids)
        rows = conn.execute(f"SELECT {', '.join(selected)} FROM images WHERE {where}", params).fetchall()
        return [dict(row) for row in rows if Path(row["filepath"]).suffix.lower() in RAW_EXTENSIONS and os.path.exists(row["filepath"])]
    finally:
        conn.close()


def _exiftool_rows(paths: list[str]) -> dict[str, dict[str, Any]]:
    """Read the missing exposure fields in batches, avoiding per-image forks."""

    result: dict[str, dict[str, Any]] = {}
    if not paths:
        return result
    if not os.path.exists(EXIFTOOL):
        return result
    for start in range(0, len(paths), 200):
        command = [EXIFTOOL, "-j", "-n", "-api", "LargeFileSupport=1", *[f"-{field}" for field in _EXIF_FIELDS], *paths[start:start + 200]]
        try:
            completed = subprocess.run(command, check=True, capture_output=True, text=True, timeout=180)
            payload = json.loads(completed.stdout)
        except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
            continue
        for row in payload:
            source = row.pop("SourceFile", None)
            if source:
                result[os.path.realpath(source)] = row
    return result


def image_exposure_rows(db_path: str, image_ids: Iterable[int] | None = None) -> list[dict[str, Any]]:
    rows = _catalog_rows(db_path, image_ids)
    exif = _exiftool_rows([row["filepath"] for row in rows])
    for row in rows:
        # Existing catalog values win; no current schema exposes these fields,
        # but this keeps future scanner metadata a fast path.
        row.update({key: value for key, value in exif.get(os.path.realpath(row["filepath"]), {}).items() if row.get(key) is None})
        row["capture_timestamp"] = _timestamp(row.get("SubSecDateTimeOriginal") or row.get("DateTimeOriginal") or row.get("CreateDate") or row.get("date_taken"))
        row["focal_length"] = _number(row.get("FocalLength"))
        row["lens_model"] = str(row.get("LensModel") or row.get("lens") or "").strip()
        row["relative_ev"] = exposure_ev(row)
    return rows


def _same_optical_setup(first: dict[str, Any], second: dict[str, Any]) -> bool:
    first_lens, second_lens = first.get("lens_model"), second.get("lens_model")
    first_focal, second_focal = first.get("focal_length"), second.get("focal_length")
    if not first_lens or not second_lens or first_lens.casefold() != second_lens.casefold():
        return False
    if first_focal is None or second_focal is None:
        return False
    return abs(float(first_focal) - float(second_focal)) < 0.01


def detect_brackets(db_path: str, image_ids: Iterable[int] | None = None) -> list[dict[str, Any]]:
    """Return contiguous 3+ frame runs shot within two seconds on one setup."""

    frames = [row for row in image_exposure_rows(db_path, image_ids) if row.get("capture_timestamp") is not None and row.get("relative_ev") is not None]
    frames.sort(key=lambda row: float(row["capture_timestamp"]))
    runs: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for frame in frames:
        if current and (float(frame["capture_timestamp"]) - float(current[-1]["capture_timestamp"]) <= 2.0 and _same_optical_setup(current[-1], frame)):
            current.append(frame)
        else:
            if len(current) >= 3:
                runs.append(current)
            current = [frame]
    if len(current) >= 3:
        runs.append(current)
    brackets = []
    for run in runs:
        shutters = {_shutter_seconds(frame) for frame in run}
        biases = {_number(frame.get("ExposureBiasValue")) for frame in run}
        if len(shutters - {None}) < 2 and len(biases - {None}) < 2:
            continue
        brackets.append({
            "image_ids": [int(frame["id"]) for frame in run],
            "capture_timestamp": run[0]["capture_timestamp"],
            "lens": run[0]["lens_model"],
            "focal_length": run[0]["focal_length"],
            "ev_span": max(float(frame["relative_ev"]) for frame in run) - min(float(frame["relative_ev"]) for frame in run),
            "frames": [{"image_id": int(frame["id"]), "relative_ev": frame["relative_ev"], "exposure_bias": _number(frame.get("ExposureBiasValue"))} for frame in run],
        })
    return brackets


def phase_correlation_shift(reference_luma: np.ndarray, source_luma: np.ndarray) -> tuple[int, int]:
    """Return integer (dy, dx) translation needed to move source onto reference."""

    reference = np.asarray(reference_luma, dtype=np.float32)
    source = np.asarray(source_luma, dtype=np.float32)
    if reference.shape != source.shape or reference.ndim != 2:
        raise HdrError("HDR alignment needs same-size luma previews")
    reference = reference - float(reference.mean())
    source = source - float(source.mean())
    spectrum = np.fft.fft2(reference) * np.conj(np.fft.fft2(source))
    normalized = spectrum / np.maximum(np.abs(spectrum), 1e-12)
    peak = np.unravel_index(int(np.argmax(np.abs(np.fft.ifft2(normalized)))), reference.shape)
    dy, dx = int(peak[0]), int(peak[1])
    if dy > reference.shape[0] // 2:
        dy -= reference.shape[0]
    if dx > reference.shape[1] // 2:
        dx -= reference.shape[1]
    return dy, dx


def _downsample_luma(rgb: np.ndarray, max_edge: int = 512) -> np.ndarray:
    luma = np.asarray(rgb, dtype=np.float32) @ np.asarray((0.2126, 0.7152, 0.0722), dtype=np.float32)
    stride = max(1, int(math.ceil(max(luma.shape) / max_edge)))
    return luma[::stride, ::stride]


def translate_rgb(rgb: np.ndarray, dy: int, dx: int) -> np.ndarray:
    """Translate without wraparound; uncovered pixels remain black."""

    source = np.asarray(rgb, dtype=np.float32)
    output = np.zeros_like(source)
    height, width = source.shape[:2]
    src_y0, src_y1 = max(0, -dy), min(height, height - dy)
    src_x0, src_x1 = max(0, -dx), min(width, width - dx)
    if src_y0 < src_y1 and src_x0 < src_x1:
        output[src_y0 + dy:src_y1 + dy, src_x0 + dx:src_x1 + dx] = source[src_y0:src_y1, src_x0:src_x1]
    return output


def _resize_rgb(rgb: np.ndarray, shape: tuple[int, int]) -> np.ndarray:
    if rgb.shape[:2] == shape:
        return np.asarray(rgb, dtype=np.float32)
    height, width = shape
    channels = [np.asarray(Image.fromarray(np.asarray(rgb[..., channel], dtype=np.float32), mode="F").resize((width, height), Image.Resampling.LANCZOS), dtype=np.float32) for channel in range(3)]
    return np.stack(channels, axis=-1)


def hat_weights(linear_rgb: np.ndarray) -> np.ndarray:
    """Triangular validity: black and clipped samples contribute zero."""

    luma = np.asarray(linear_rgb, dtype=np.float32) @ np.asarray((0.2126, 0.7152, 0.0722), dtype=np.float32)
    return np.clip(1.0 - np.abs(2.0 * np.clip(luma, 0.0, 1.0) - 1.0), 0.0, 1.0)


def merge_linear_arrays(arrays: list[np.ndarray], relative_evs: list[float]) -> tuple[np.ndarray, list[tuple[int, int]]]:
    """Align and radiance-average linear sources using exposure validity hats."""

    if len(arrays) < 3 or len(arrays) != len(relative_evs):
        raise HdrError("HDR merge needs at least three exposures")
    reference_index = int(np.argsort(np.asarray(relative_evs))[len(relative_evs) // 2])
    reference = np.asarray(arrays[reference_index], dtype=np.float32)
    if reference.ndim != 3 or reference.shape[2] != 3:
        raise HdrError("HDR decoder returned invalid RGB data")
    reference_shape = reference.shape[:2]
    baseline_ev = float(np.median(np.asarray(relative_evs, dtype=np.float32)))
    numerator = np.zeros_like(reference, dtype=np.float32)
    denominator = np.zeros(reference_shape, dtype=np.float32)
    shifts: list[tuple[int, int]] = []
    for array, ev in zip(arrays, relative_evs):
        linear = _resize_rgb(np.asarray(array, dtype=np.float32), reference_shape)
        small_ref, small_source = _downsample_luma(reference), _downsample_luma(linear)
        small_dy, small_dx = phase_correlation_shift(small_ref, small_source)
        stride_y = max(1, round(reference_shape[0] / small_ref.shape[0]))
        stride_x = max(1, round(reference_shape[1] / small_ref.shape[1]))
        dy, dx = small_dy * stride_y, small_dx * stride_x
        aligned = translate_rgb(linear, dy, dx)
        scale = float(2.0 ** (float(ev) - baseline_ev))
        weights = hat_weights(aligned)
        numerator += weights[..., None] * (aligned / scale)
        denominator += weights
        shifts.append((dy, dx))
    fallback = reference / float(2.0 ** (float(relative_evs[reference_index]) - baseline_ev))
    merged = np.where((denominator > 1e-6)[..., None], numerator / np.maximum(denominator[..., None], 1e-6), fallback)
    return np.ascontiguousarray(np.maximum(merged, 0.0), dtype=np.float32), shifts


def _write_hdr_base(image_id: int, merged: np.ndarray, meta: dict[str, Any]) -> None:
    """Write PABASE1 uint16 artifacts while retaining HDR scale in metadata."""

    paths = rawproc.base_paths(image_id)
    paths.binary.parent.mkdir(parents=True, exist_ok=True)
    scale = max(1.0, float(np.max(merged)))
    encoded = np.rint(np.clip(merged / scale, 0.0, 1.0) * 65535.0).astype("<u2")
    height, width = encoded.shape[:2]
    payload = rawproc.BASE_HEADER.pack(rawproc.BASE_MAGIC, width, height) + encoded.tobytes()
    temp_binary = paths.binary.with_suffix(".bin.gz.tmp")
    with gzip.open(temp_binary, "wb") as handle:
        handle.write(payload)
    os.replace(temp_binary, paths.binary)
    # A compressive preview remains useful before WebGL uploads the scaled base.
    preview_linear = merged / (1.0 + merged)
    preview = np.where(preview_linear <= 0.0031308, preview_linear * 12.92, 1.055 * np.power(preview_linear, 1.0 / 2.4) - 0.055)
    temp_preview = paths.preview.with_suffix(".jpg.tmp")
    Image.fromarray(np.rint(np.clip(preview, 0.0, 1.0) * 255.0).astype(np.uint8), mode="RGB").save(temp_preview, format="JPEG", quality=88)
    os.replace(temp_preview, paths.preview)
    meta = {**meta, "width": width, "height": height, "dtype": "uint16", "linear": True, "hdr": {"scale": scale, "format": "float32-exr"}}
    temp_meta = paths.metadata.with_suffix(".json.tmp")
    temp_meta.write_text(json.dumps(meta, separators=(",", ":")), encoding="utf-8")
    os.replace(temp_meta, paths.metadata)


def _register_merge(db_path: str, frames: list[dict[str, Any]], merged: np.ndarray, shifts: list[tuple[int, int]]) -> int:
    HDR_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    token = f"HDR_{stamp}_{int(time.time_ns() % 1_000_000):06d}"
    exr_path = HDR_CACHE_DIR / f"{token}.exr"
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
            (str(HDR_CACHE_DIR), HDR_SOURCE_NAME, now, now, now),
        )
        source_id = int(conn.execute("SELECT id FROM catalog_sources WHERE path = ?", (str(HDR_CACHE_DIR),)).fetchone()["id"])
        cursor = conn.execute(
            "INSERT INTO images (source_id, filename, filepath, status, file_ext, file_size, file_modified_at, width, height, date_taken, camera_make, camera_model, lens) "
            "VALUES (?, ?, ?, 'kept', '.exr', ?, ?, ?, ?, ?, ?, ?, ?)",
            (source_id, exr_path.name, str(exr_path), int(exr_path.stat().st_size), exr_path.stat().st_mtime, int(merged.shape[1]), int(merged.shape[0]), anchor.get("date_taken"), anchor.get("camera_make"), anchor.get("camera_model"), anchor.get("lens") or anchor.get("lens_model")),
        )
        image_id = int(cursor.lastrowid)
        conn.execute("UPDATE catalog_sources SET image_count=(SELECT COUNT(*) FROM images WHERE source_id=?), active_image_count=(SELECT COUNT(*) FROM images WHERE source_id=? AND status IN ('kept', 'maybe') AND missing_at IS NULL) WHERE id=?", (source_id, source_id, source_id))
        conn.commit()
    except Exception:
        conn.rollback()
        exr_path.unlink(missing_ok=True)
        raise
    finally:
        conn.close()
    _write_hdr_base(image_id, merged, {"hdr_sources": [int(frame["id"]) for frame in frames], "hdr_shifts": shifts, "as_shot": {"temperature": 5500, "tint": 0}})
    return image_id


def merge_images(db_path: str, image_ids: Iterable[int], *, decode: Callable[[str], tuple[np.ndarray, dict[str, Any]]] = rawproc.decode_base) -> dict[str, Any]:
    ids = list(dict.fromkeys(int(image_id) for image_id in image_ids if int(image_id) > 0))
    if len(ids) < 3:
        raise HdrError("HDR merge needs at least three selected RAW images")
    by_id = {int(row["id"]): row for row in image_exposure_rows(db_path, ids)}
    if len(by_id) != len(ids):
        raise HdrError("One or more selected images are unavailable RAW files")
    frames = [by_id[image_id] for image_id in ids]
    if any(frame.get("relative_ev") is None for frame in frames):
        raise HdrError("Selected images need shutter, ISO, and aperture metadata")
    arrays = [decode(frame["filepath"])[0].astype(np.float32) / 65535.0 for frame in frames]
    merged, shifts = merge_linear_arrays(arrays, [float(frame["relative_ev"]) for frame in frames])
    image_id = _register_merge(db_path, frames, merged, shifts)
    return {"image_id": image_id, "image_ids": ids, "shifts": shifts}


def begin_merge(db_path: str, image_ids: Iterable[int]) -> bool:
    ids = list(dict.fromkeys(int(image_id) for image_id in image_ids if int(image_id) > 0))
    with _status_lock:
        if _status["running"]:
            return False
        _status.update(running=True, phase="queued", image_ids=ids, result_id=None, error=None, started_at=time.time(), finished_at=None, seconds=None)

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

    threading.Thread(target=worker, name="photoarchive-hdr-merge", daemon=True).start()
    return True
