"""Embedded and harvested Adobe DNG camera profiles.

The DNG 1.7 profile tags are the authoritative camera-default styling data.  A
profile embedded in the file being developed wins; otherwise the harvested
library is selected by ``UniqueCameraModel``.  ``BaselineExposure`` remains a
per-file tag, even when the profile itself comes from the library.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Mapping


ADOBE_PROFILES_DIR = Path(__file__).with_name("profiles") / "adobe"
_RATIONAL_DTYPES = {5, 10}  # TIFF RATIONAL and SRATIONAL.


def slugify(value: object) -> str:
    """Return the stable filename portion for a camera model or profile name."""
    return re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")


def _text(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace").strip("\0 ")
    return str(value or "").strip("\0 ")


def _tag(pages: Iterable[object], name: str) -> object | None:
    for page in pages:
        tags = getattr(page, "tags", None)
        if tags is None:
            continue
        tag = tags.get(name)
        if tag is not None:
            return tag
    return None


def _flat_values(value: object) -> list[object]:
    if hasattr(value, "ravel"):
        return list(value.ravel())
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def _numbers(tag: object | None) -> list[float] | None:
    """Decode TIFF numerics, including flattened RATIONAL/SRATIONAL pairs."""
    if tag is None:
        return None
    values = _flat_values(getattr(tag, "value", None))
    try:
        dtype = int(getattr(tag, "dtype", 0))
    except (TypeError, ValueError):
        dtype = 0
    try:
        if dtype in _RATIONAL_DTYPES:
            if len(values) % 2:
                return None
            result = [float(values[index]) / float(values[index + 1]) for index in range(0, len(values), 2)]
        else:
            result = [float(value) for value in values]
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    return result if all(math.isfinite(value) for value in result) else None


def _integer_values(tag: object | None, length: int) -> list[int] | None:
    values = _numbers(tag)
    if values is None or len(values) != length or any(value != int(value) for value in values):
        return None
    return [int(value) for value in values]


def _scalar(tag: object | None) -> float | None:
    values = _numbers(tag)
    return values[0] if values and len(values) == 1 else None


def _profile_content(profile: Mapping[str, Any]) -> dict[str, Any]:
    """Return the portable profile fields used for identity hashing."""
    return {
        key: profile[key]
        for key in (
            "camera_model",
            "profile_name",
            "matrices",
            "illuminants",
            "baseline_exposure",
            "baseline_exposure_offset",
            "tone_curve",
            "hue_sat_map",
            "look_table",
        )
        if key in profile
    }


def profile_content_hash(profile: Mapping[str, Any]) -> str:
    """Stable SHA-256 for the DNG profile payload, excluding harvest provenance."""
    encoded = json.dumps(_profile_content(profile), sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _profile_from_pages(pages: Iterable[object], source_file: str) -> dict[str, Any] | None:
    model = _text(getattr(_tag(pages, "UniqueCameraModel"), "value", ""))
    profile_name = _text(getattr(_tag(pages, "ProfileName"), "value", ""))
    if not model or not profile_name:
        return None

    matrices = {
        name: _numbers(_tag(pages, tag_name))
        for name, tag_name in (
            ("color_matrix1", "ColorMatrix1"),
            ("color_matrix2", "ColorMatrix2"),
            ("forward_matrix1", "ForwardMatrix1"),
            ("forward_matrix2", "ForwardMatrix2"),
        )
    }
    if any(values is None or len(values) != 9 for values in matrices.values()):
        return None

    illuminant1 = _scalar(_tag(pages, "CalibrationIlluminant1"))
    illuminant2 = _scalar(_tag(pages, "CalibrationIlluminant2"))
    baseline_exposure = _scalar(_tag(pages, "BaselineExposure"))
    baseline_exposure_offset = _scalar(_tag(pages, "BaselineExposureOffset"))
    if illuminant1 is None or illuminant2 is None:
        return None

    profile: dict[str, Any] = {
        "schema_version": 1,
        "camera_model": model,
        "profile_name": profile_name,
        "matrices": matrices,
        "illuminants": {
            "calibration_illuminant1": int(illuminant1),
            "calibration_illuminant2": int(illuminant2),
        },
        "source_file": source_file,
    }
    if baseline_exposure is not None:
        profile["baseline_exposure"] = baseline_exposure
    if baseline_exposure_offset is not None:
        profile["baseline_exposure_offset"] = baseline_exposure_offset

    tone = _numbers(_tag(pages, "ProfileToneCurve"))
    if tone is not None and len(tone) % 2 == 0:
        profile["tone_curve"] = [[tone[index], tone[index + 1]] for index in range(0, len(tone), 2)]

    hue_dims = _integer_values(_tag(pages, "ProfileHueSatMapDims"), 3)
    hue_data1 = _numbers(_tag(pages, "ProfileHueSatMapData1"))
    hue_data2 = _numbers(_tag(pages, "ProfileHueSatMapData2"))
    if hue_dims is not None and hue_data1 is not None and hue_data2 is not None:
        expected = hue_dims[0] * hue_dims[1] * hue_dims[2] * 3
        if len(hue_data1) == expected and len(hue_data2) == expected:
            profile["hue_sat_map"] = {"dims": hue_dims, "data1": hue_data1, "data2": hue_data2}

    look_dims = _integer_values(_tag(pages, "ProfileLookTableDims"), 3)
    look_data = _numbers(_tag(pages, "ProfileLookTableData"))
    if look_dims is not None and look_data is not None:
        expected = look_dims[0] * look_dims[1] * look_dims[2] * 3
        if len(look_data) == expected:
            profile["look_table"] = {"dims": look_dims, "data": look_data}

    profile["content_hash"] = profile_content_hash(profile)
    return profile


def extract_embedded_profile(path: str | Path) -> dict[str, Any] | None:
    """Read an embedded DNG profile with tifffile, returning JSON-safe values."""
    try:
        import tifffile

        source = str(Path(path))
        with tifffile.TiffFile(source) as tif:
            return _profile_from_pages((tif.pages[0],), source)
    except (OSError, RuntimeError, ValueError, IndexError):
        return None


def read_baseline_exposure(path: str | Path) -> float | None:
    """Return the per-file DNG BaselineExposure, without requiring a profile."""
    try:
        import tifffile

        with tifffile.TiffFile(str(path)) as tif:
            return _scalar(_tag((tif.pages[0],), "BaselineExposure"))
    except (OSError, RuntimeError, ValueError, IndexError):
        return None


def _valid_profile(payload: object) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    model = _text(payload.get("camera_model"))
    name = _text(payload.get("profile_name"))
    matrices = payload.get("matrices")
    illuminants = payload.get("illuminants")
    if not model or not name or not isinstance(matrices, dict) or not isinstance(illuminants, dict):
        return None
    for key in ("color_matrix1", "color_matrix2", "forward_matrix1", "forward_matrix2"):
        values = matrices.get(key)
        if not isinstance(values, list) or len(values) != 9 or not all(isinstance(value, (int, float)) and math.isfinite(value) for value in values):
            return None
    profile = copy.deepcopy(payload)
    profile["camera_model"] = model
    profile["profile_name"] = name
    return profile


@lru_cache(maxsize=1)
def _library_profiles() -> tuple[dict[str, Any], ...]:
    profiles: list[dict[str, Any]] = []
    try:
        paths = sorted(ADOBE_PROFILES_DIR.glob("*.json"))
    except OSError:
        return ()
    for path in paths:
        try:
            profile = _valid_profile(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
        if profile is not None:
            profile["library_file"] = str(path)
            profiles.append(profile)
    return tuple(profiles)


def clear_adobe_profile_cache() -> None:
    """Clear the in-process library cache (tests and rerunnable harvests)."""
    _library_profiles.cache_clear()


def _model_key(value: object) -> str:
    """Normalize a camera model for matching: EXIF often prefixes the make
    ("Canon EOS R5") while catalogs store the bare model ("EOS R5")."""
    return "".join(ch for ch in str(value or "").casefold() if ch.isalnum())


def _model_matches(profile_model: object, requested_model: object) -> bool:
    a, b = _model_key(profile_model), _model_key(requested_model)
    return bool(a) and bool(b) and (a == b or a.endswith(b) or b.endswith(a))


def load_adobe_profile(camera_model: object, profile_name: object | None = None) -> dict[str, Any] | None:
    """Load one harvested profile, preferring Adobe Standard for default renders."""
    model = _text(camera_model)
    requested_name = _text(profile_name)
    candidates = [profile for profile in _library_profiles() if _model_matches(profile["camera_model"], model)]
    if requested_name:
        candidates = [profile for profile in candidates if profile["profile_name"] == requested_name]
    if not candidates:
        return None
    candidates.sort(key=lambda profile: (not profile["profile_name"].casefold().startswith("adobe standard"), profile["profile_name"].casefold()))
    return copy.deepcopy(candidates[0])


def resolve_adobe_profile(path: str | Path | None, camera_model: object) -> dict[str, Any] | None:
    """Resolve DNG embedded profile -> harvested camera profile -> no profile."""
    source_path = Path(path) if path else None
    profile = None
    if source_path is not None and source_path.suffix.casefold() == ".dng":
        profile = extract_embedded_profile(source_path)
        if profile is not None:
            profile["resolution"] = "embedded"
    if profile is None:
        profile = load_adobe_profile(camera_model)
        if profile is None:
            return None
        profile["resolution"] = "library"
    if source_path is not None and source_path.suffix.casefold() == ".dng":
        per_file_baseline = read_baseline_exposure(source_path)
        if per_file_baseline is not None:
            profile["baseline_exposure"] = per_file_baseline
    return profile
