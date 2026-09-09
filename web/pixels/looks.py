"""Imported Lightroom Look helpers shared by render and import seams.

Looks are profile parameters, not user edits.  The stored settings object stays
verbatim; renderers ask :func:`effective_settings` for the small subset we can
faithfully apply without Adobe's unavailable 3D LookTable.
"""

from __future__ import annotations

from .numbers import spelled as _normalize_value

from .numbers import number as _number

import re
import xml.etree.ElementTree as etree
from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np

from . import ops_constants as C


_NUMBER = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$")
_CRS_NAMESPACE_MARKER = "camera-raw-settings"


def _split_name(name: str) -> tuple[str, str]:
    if name.startswith("{"):
        namespace, _, local = name[1:].partition("}")
        return namespace, local
    return "", name.split(":", 1)[-1]


def _is_crs_name(name: str) -> bool:
    namespace, _local = _split_name(name)
    return _CRS_NAMESPACE_MARKER in namespace.lower() or name.startswith("crs:")


def _crs_attributes(element: etree.Element) -> dict[str, Any]:
    return {
        _split_name(name)[1]: _normalize_value(value)
        for name, value in element.attrib.items()
        if _is_crs_name(name)
    }


def _curve_values(element: etree.Element) -> list[str]:
    return [text.strip() for text in element.itertext() if text and text.strip()]


def extract_xmp_look(root: etree.Element) -> dict[str, Any] | None:
    """Extract the first nested ``crs:Look`` RDF object without flattening it."""

    look_element = next(
        (
            element
            for element in root.iter()
            if _is_crs_name(element.tag) and _split_name(element.tag)[1] == "Look"
        ),
        None,
    )
    if look_element is None:
        return None

    look: dict[str, Any] = _crs_attributes(look_element)
    parameters_element: etree.Element | None = None
    for element in look_element.iter():
        local = _split_name(element.tag)[1]
        if local == "Parameters" and _is_crs_name(element.tag):
            parameters_element = element
            break
        if local == "Description":
            look.update(_crs_attributes(element))

    if parameters_element is None:
        return look or None

    parameters = _crs_attributes(parameters_element)
    for element in parameters_element.iter():
        local = _split_name(element.tag)[1]
        if local == "Description":
            parameters.update(_crs_attributes(element))
        elif _is_crs_name(element.tag) and local.startswith("ToneCurvePV2012"):
            parameters[local] = _curve_values(element)
        elif _is_crs_name(element.tag) and element is not parameters_element:
            text = "".join(element.itertext()).strip()
            if text:
                parameters[local] = _normalize_value(text)
    look["Parameters"] = parameters
    return look


def extract_look(settings: Mapping[str, object] | None) -> Mapping[str, object] | None:
    """Return a canonical imported Look, rejecting legacy flattened strings."""

    look = settings.get("Look") if isinstance(settings, Mapping) else None
    return look if isinstance(look, Mapping) else None


def look_parameters(settings: Mapping[str, object] | None) -> Mapping[str, object]:
    look = extract_look(settings)
    parameters = look.get("Parameters") if look else None
    return parameters if isinstance(parameters, Mapping) else {}


def look_amount(settings: Mapping[str, object] | None) -> float:
    """Normalize Adobe Look amount to 0..1 (catalogs use either 1 or 100)."""

    look = extract_look(settings)
    if not look:
        return 0.0
    try:
        amount = float(look.get("Amount", 1.0))
    except (TypeError, ValueError):
        amount = 1.0
    if abs(amount) > 1.0:
        amount /= 100.0
    return float(np.clip(amount, 0.0, 1.0))


def look_curve(settings: Mapping[str, object] | None) -> object:
    return look_parameters(settings).get("ToneCurvePV2012")



def _bool(value: object) -> bool:
    return value is True or value == 1 or str(value).strip().lower() == "true"


def effective_settings(settings: Mapping[str, object] | None) -> dict[str, object]:
    """Return render-only settings with supported Look parameters merged.

    Look clarity is additive to the photo slider and scaled by Amount.  A
    monochrome Look enables the pipeline's B&W path whenever its amount is
    non-zero.  LookTable/RGBTable data is intentionally retained but ignored:
    Adobe's corresponding 3D tables are not present in the imported catalog.
    """

    source = settings or {}
    effective = dict(source)
    amount = look_amount(source)
    if amount <= 0.0:
        return effective
    parameters = look_parameters(source)
    if "Clarity2012" in parameters:
        clarity = _number(source.get("Clarity2012")) + amount * _number(parameters.get("Clarity2012"))
        effective["Clarity2012"] = float(np.clip(clarity, -100.0, 100.0))
    if _bool(parameters.get("ConvertToGrayscale")):
        effective["ConvertToGrayscale"] = True
    return effective


def _sample_lut(lut: np.ndarray, values: np.ndarray) -> np.ndarray:
    positions = np.linspace(0.0, 1.0, C.CURVE_LUT_SIZE, dtype=np.float32)
    return np.interp(np.clip(values, 0.0, 1.0), positions, lut).astype(np.float32)


def compose_curve_luts(base_lut: Sequence[float], look_lut: Sequence[float], amount: float = 1.0) -> np.ndarray:
    """Return ``LookCurve(BaseCurve(x))``, blended by normalized Look amount."""

    base = np.asarray(base_lut, dtype=np.float32)
    after = np.asarray(look_lut, dtype=np.float32)
    expected = (C.CURVE_LUT_SIZE,)
    if base.shape != expected or after.shape != expected:
        raise ValueError(f"curve LUTs must both have shape {expected}")
    strength = float(np.clip(amount, 0.0, 1.0))
    composed = _sample_lut(after, base)
    return (base + (composed - base) * strength).astype(np.float32)
