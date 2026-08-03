"""Measured camera-noise lookup and conservative Develop NR defaults.

The shipped data is a small, generated distillation of darktable calibration
measurements.  It estimates noise at scene-linear mid-grey with the standard
Poissonian-Gaussian model: variance = a * x + b.  Those variance-stabilizing
(a, b) coefficients drive the existing NR slider strength — not a new engine.
"""

from __future__ import annotations

import json
import math
import re
from functools import lru_cache
from os import PathLike
from pathlib import Path
from typing import Mapping


NOISE_PROFILES_PATH = Path(__file__).with_name("noise_profiles.json")
NR_KEYS = ("LuminanceSmoothing", "ColorNoiseReduction")

# A deliberately small table so visual tuning stays localized.  The R5
# green-channel sigma at scene-linear 18% grey maps from ISO 100 -> 0 to ISO
# 6400 -> these slider values.  Color uses the same sensor-noise estimate but
# a gentler target because the current late bilateral's chroma control is more
# visually aggressive than its luminance control.
NR_CALIBRATION = {
    "mid_grey": 0.18,
    "anchor_camera": "Canon EOS R5",
    "anchor_isos": (100.0, 6400.0),
    "slider_values": {
        "LuminanceSmoothing": (0.0, 35.0),
        "ColorNoiseReduction": (0.0, 20.0),
    },
}


def _text(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace").strip("\0 ")
    return str(value or "").strip("\0 ")


def _model_key(value: object) -> str:
    """Normalize the common Canon / EXIF spelling variants without fuzzy IDs."""
    model = _text(value).casefold()
    model = re.sub(r"^canon\s+", "", model)
    model = re.sub(r"^sony\s+", "", model)
    model = re.sub(r"^dji\s+", "", model)
    return re.sub(r"[^a-z0-9]+", "", model)


@lru_cache(maxsize=1)
def _payload() -> dict[str, object]:
    try:
        payload = json.loads(NOISE_PROFILES_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


@lru_cache(maxsize=1)
def _aliases() -> dict[str, str]:
    raw = _payload().get("aliases")
    if not isinstance(raw, dict):
        return {}
    return {
        _model_key(source): _text(target)
        for source, target in raw.items()
        if _text(source) and _text(target)
    }


def canonicalize_model(camera_model: object) -> str:
    """Map catalog/EXIF model strings onto the shipped profile model name."""
    text = _text(camera_model)
    if not text:
        return ""
    aliased = _aliases().get(_model_key(text))
    return aliased or re.sub(r"^(Canon|Sony|DJI)\s+", "", text, flags=re.IGNORECASE)


def _model_matches(camera_model: object, profile_model: object) -> bool:
    """Match maker-prefixed, alias, and punctuation-variant model names safely."""
    requested = canonicalize_model(camera_model)
    return bool(_model_key(requested)) and _model_key(requested) == _model_key(profile_model)


def _coefficient_vector(value: object) -> tuple[float, float, float] | None:
    if not isinstance(value, list) or len(value) != 3:
        return None
    try:
        vector = tuple(float(item) for item in value)
    except (TypeError, ValueError):
        return None
    return vector if all(math.isfinite(item) for item in vector) else None


@lru_cache(maxsize=1)
def _profiles() -> tuple[dict[str, object], ...]:
    entries = _payload().get("profiles")
    if not isinstance(entries, list):
        return ()
    result: list[dict[str, object]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        try:
            iso = float(entry.get("iso"))
        except (TypeError, ValueError):
            continue
        a = _coefficient_vector(entry.get("a"))
        b = _coefficient_vector(entry.get("b"))
        if (
            iso > 0.0
            and math.isfinite(iso)
            and a is not None
            and b is not None
            and _text(entry.get("model"))
        ):
            result.append(
                {
                    "maker": _text(entry.get("maker")),
                    "model": _text(entry["model"]),
                    "iso": iso,
                    "a": a,
                    "b": b,
                }
            )
    return tuple(result)


def _interpolate(lower: Mapping[str, object], upper: Mapping[str, object], iso: float) -> dict[str, list[float]]:
    lower_iso, upper_iso = float(lower["iso"]), float(upper["iso"])
    if lower_iso == upper_iso:
        return {key: list(lower[key]) for key in ("a", "b")}
    fraction = (math.log(iso) - math.log(lower_iso)) / (math.log(upper_iso) - math.log(lower_iso))
    return {
        key: [
            float(left) + fraction * (float(right) - float(left))
            for left, right in zip(lower[key], upper[key])
        ]
        for key in ("a", "b")
    }


def lookup(camera_model: str, iso: float) -> dict[str, list[float]] | None:
    """Return measured/interpolated ``a`` and ``b`` coefficients for a camera.

    Coefficients clamp to the nearest measured ISO.  Intermediate ISO values
    interpolate linearly in log-ISO space, matching exposure-stop spacing.
    Unknown cameras return ``None`` (generic fallback = today's NR behavior).
    """
    try:
        requested_iso = float(iso)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(requested_iso) or requested_iso <= 0.0:
        return None
    candidates = sorted(
        (entry for entry in _profiles() if _model_matches(camera_model, entry["model"])),
        key=lambda entry: float(entry["iso"]),
    )
    if not candidates:
        return None
    if requested_iso <= float(candidates[0]["iso"]):
        return {key: list(candidates[0][key]) for key in ("a", "b")}
    if requested_iso >= float(candidates[-1]["iso"]):
        return {key: list(candidates[-1][key]) for key in ("a", "b")}
    for lower, upper in zip(candidates, candidates[1:]):
        if requested_iso <= float(upper["iso"]):
            return _interpolate(lower, upper, requested_iso)
    return None


def _green_sigma(coefficients: Mapping[str, object]) -> float | None:
    try:
        a = float(coefficients["a"][1])
        b = float(coefficients["b"][1])
    except (KeyError, IndexError, TypeError, ValueError):
        return None
    variance = a * float(NR_CALIBRATION["mid_grey"]) + b
    return math.sqrt(max(variance, 0.0)) if math.isfinite(variance) else None


def vst_parameters(camera_model: str, iso: float) -> dict[str, object] | None:
    """Variance-stabilizing parameters that drive existing NR strength.

    Returns Poisson–Gaussian ``a`` / ``b``, mid-grey variance, and green-channel
    sigma.  ``None`` means no profile — callers must keep today's behavior.
    """
    coefficients = lookup(camera_model, iso)
    if coefficients is None:
        return None
    sigma = _green_sigma(coefficients)
    if sigma is None:
        return None
    a_g = float(coefficients["a"][1])
    b_g = float(coefficients["b"][1])
    mid = float(NR_CALIBRATION["mid_grey"])
    return {
        "model": canonicalize_model(camera_model),
        "iso": float(iso),
        "a": list(coefficients["a"]),
        "b": list(coefficients["b"]),
        "mid_grey": mid,
        "variance": a_g * mid + b_g,
        "sigma": sigma,
    }


def nr_strength_from_vst(parameters: Mapping[str, object]) -> dict[str, float] | None:
    """Map VST mid-grey sigma onto our current NR slider units."""
    low_iso, high_iso = NR_CALIBRATION["anchor_isos"]
    low = lookup(NR_CALIBRATION["anchor_camera"], low_iso)
    high = lookup(NR_CALIBRATION["anchor_camera"], high_iso)
    if low is None or high is None:
        return None
    try:
        sigma = float(parameters["sigma"])
    except (KeyError, TypeError, ValueError):
        return None
    low_sigma, high_sigma = (_green_sigma(value) for value in (low, high))
    if low_sigma is None or high_sigma is None or high_sigma <= low_sigma:
        return None
    if not math.isfinite(sigma):
        return None
    # The R5 anchors establish the slider scale, while noisier bodies and
    # ISOs continue above that reference until the slider's own hard cap.
    fraction = max(0.0, (sigma - low_sigma) / (high_sigma - low_sigma))
    return {
        key: round(min(100.0, max(0.0, minimum + fraction * (maximum - minimum))), 3)
        for key, (minimum, maximum) in NR_CALIBRATION["slider_values"].items()
    }


def suggested_defaults(camera_model: str, iso: float) -> dict[str, float] | None:
    """Map measured mid-grey noise to our current NR slider units."""
    parameters = vst_parameters(camera_model, iso)
    return nr_strength_from_vst(parameters) if parameters is not None else None


def resolve_defaults(
    settings: Mapping[str, object] | None, metadata: Mapping[str, object] | None
) -> dict[str, object]:
    """Return render settings with camera defaults only for untouched NR sliders.

    Any explicit NR setting suppresses this feature completely, preserving the
    existing pipeline input (and therefore pixels) for edited and imported
    photos.  Unknown cameras leave settings unchanged (identical to today).
    """
    resolved = dict(settings or {})
    if any(key in resolved for key in NR_KEYS) or not isinstance(metadata, Mapping):
        return resolved
    camera_model = (
        metadata.get("camera_model") or metadata.get("UniqueCameraModel") or metadata.get("Model")
    )
    iso = (
        metadata.get("iso") or metadata.get("ISO") or metadata.get("PhotographicSensitivity")
    )
    defaults = suggested_defaults(_text(camera_model), iso)
    return {**resolved, **defaults} if defaults is not None else resolved


def resolve_file_defaults(
    settings: Mapping[str, object] | None,
    metadata: Mapping[str, object] | None,
    source_path: str | PathLike[str] | None = None,
) -> dict[str, object]:
    """Resolve defaults from cached base metadata without source I/O."""
    return resolve_defaults(settings, metadata)
