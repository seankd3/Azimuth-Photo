"""EXIF and Lensfun resolution for Develop lens corrections.

The returned payload is renderer-neutral JSON.  Lensfun supplies interpolated
calibration terms; the NumPy and WebGL twins apply those terms with shared math.
"""

from __future__ import annotations

from features.develop.numbers import number as _number

import json
import math
import shutil
import os
import subprocess
from functools import lru_cache
from typing import Any, Mapping

from . import ops_constants as C


EXIFTOOL = shutil.which("exiftool") or "/usr/bin/vendor_perl/exiftool"
EXIF_FIELDS = (
    "UniqueCameraModel",
    "Model",
    "Make",
    "LensModel",
    "Lens",
    "FocalLength",
    "FNumber",
    "Aperture",
    "FocusDistance",
    "ISO",
)


def distortion_radial_scale(radius: float, distortion: Mapping[str, object] | None) -> float:
    """Lensfun's inverse-remap radial multiplier for one normalized radius."""
    if not distortion:
        return 1.0
    try:
        terms = [float(value) for value in distortion.get("terms", ())]
    except (AttributeError, TypeError, ValueError):
        return 1.0
    model = str(distortion.get("model") or "").lower()
    r2 = float(radius) ** 2
    if model == "poly3" and terms:
        return 1.0 - terms[0] + terms[0] * r2
    if model == "poly5" and len(terms) >= 2:
        return 1.0 + terms[0] * r2 + terms[1] * r2 * r2
    if model == "ptlens" and len(terms) >= 3:
        a, b, c = terms[:3]
        return a * radius * r2 + b * r2 + c * radius + 1.0 - a - b - c
    return 1.0


def distortion_auto_crop_scale(
    distortion: Mapping[str, object] | None,
    width: float,
    height: float,
    crop_ratio: float = 1.0,
) -> float:
    """Largest centered UV rectangle whose radial remap stays in-frame."""
    if not distortion or width <= 0 or height <= 0:
        return 1.0
    half_min = min(width, height) / C.LENS_NORMALIZED_HALF_MIN
    maximum = 1.0
    for index in range(C.LENS_AUTO_CROP_EDGE_SAMPLES + 1):
        t = index / C.LENS_AUTO_CROP_EDGE_SAMPLES
        for u, v in ((t, 0.0), (t, 1.0), (0.0, t), (1.0, t)):
            px = (u * width - width * C.LENS_IMAGE_CENTER) / half_min * crop_ratio
            py = (v * height - height * C.LENS_IMAGE_CENTER) / half_min * crop_ratio
            maximum = max(maximum, distortion_radial_scale(math.hypot(px, py), distortion))
    return min(1.0, 1.0 / max(maximum, C.TONE_EPSILON))



@lru_cache(maxsize=256)
def read_exif(path: str) -> dict[str, Any]:
    """Read only the source metadata required for camera/lens resolution.

    Prefers ExifTool when installed; otherwise falls back to the dependency-free
    native reader so camera identification (and therefore Adobe profile
    resolution) never silently degrades on machines without ExifTool.
    """
    if not os.path.exists(EXIFTOOL):
        from features.develop.native_exif import read_native_exif
        return read_native_exif(path)
    try:
        result = subprocess.run(
            [EXIFTOOL, "-j", "-n", *[f"-{field}" for field in EXIF_FIELDS], path],
            capture_output=True,
            check=False,
            text=True,
            timeout=30,
        )
        payload = json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, TypeError, ValueError):
        return {}
    return payload[0] if isinstance(payload, list) and payload and isinstance(payload[0], dict) else {}


def normalized_source_metadata(metadata: Mapping[str, object] | None) -> dict[str, Any]:
    """Normalize catalog, TIFF, or ExifTool field names to the base-meta contract."""
    source = metadata or {}
    camera_model = str(
        source.get("camera_model") or source.get("UniqueCameraModel") or source.get("Model") or ""
    ).strip()
    camera_make = str(source.get("camera_make") or source.get("Make") or "").strip()
    if not camera_make and camera_model:
        camera_make = camera_model.split(None, 1)[0]
    lens_model = str(source.get("lens_model") or source.get("LensModel") or source.get("lens") or source.get("Lens") or "").strip()
    return {
        "camera_model": camera_model,
        "camera_make": camera_make,
        "lens_model": lens_model,
        "iso": _number(
            source.get("iso")
            or source.get("ISO")
            or source.get("PhotographicSensitivity")
            or source.get("ISOSpeedRatings")
        ),
        "focal_length": _number(source.get("focal_length") or source.get("FocalLength")),
        "aperture": _number(source.get("aperture") or source.get("FNumber") or source.get("Aperture")),
        "focus_distance": _number(source.get("focus_distance") or source.get("FocusDistance"), 1000.0),
    }


@lru_cache(maxsize=1)
def _database():
    try:
        import lensfunpy
        return lensfunpy.Database()
    except (ImportError, RuntimeError):
        return None


def _model_name(model: object) -> str:
    return str(getattr(model, "name", model)).lower()


def _label(maker: object, model: object) -> str:
    make = str(maker or "").strip()
    name = str(model or "").strip()
    return name if name.lower().startswith(make.lower()) else f"{make} {name}".strip()


def resolve_lens_correction(metadata: Mapping[str, object] | None) -> dict[str, Any] | None:
    """Resolve one camera/lens and interpolate its distortion/vignetting data."""
    source = normalized_source_metadata(metadata)
    if not source["camera_model"] or not source["lens_model"] or source["focal_length"] is None:
        return None
    database = _database()
    if database is None:
        return None
    cameras = database.find_cameras(source["camera_make"] or None, source["camera_model"])
    if not cameras and source["camera_make"]:
        cameras = database.find_cameras(None, source["camera_model"])
    if not cameras:
        return None
    camera = cameras[0]
    lenses = database.find_lenses(camera, None, source["lens_model"])
    if not lenses:
        return None
    matched = lenses[0]
    focal = float(source["focal_length"])
    aperture = float(source["aperture"] or matched.min_aperture or 1.0)
    distance = float(source["focus_distance"] or 1000.0)
    try:
        distortion = matched.interpolate_distortion(focal)
    except (RuntimeError, ValueError):
        distortion = None
    try:
        vignetting = matched.interpolate_vignetting(focal, aperture, distance)
    except (RuntimeError, ValueError):
        vignetting = None
    payload: dict[str, Any] = {
        "camera": _label(camera.maker, camera.model),
        "lens": _label(matched.maker, matched.model),
        "focal_length": focal,
        "aperture": aperture,
        "focus_distance": distance,
        "camera_crop_factor": float(camera.crop_factor),
        "lens_crop_factor": float(matched.crop_factor),
        "center": [float(matched.center_x), float(matched.center_y)],
    }
    if distortion is not None and _model_name(distortion.model) != "none":
        payload["distortion"] = {
            "model": _model_name(distortion.model),
            "terms": [float(value) for value in distortion.terms],
        }
    if vignetting is not None and _model_name(vignetting.model) != "none":
        payload["vignetting"] = {
            "model": _model_name(vignetting.model),
            "terms": [float(value) for value in vignetting.terms],
        }
    return payload if "distortion" in payload or "vignetting" in payload else None
