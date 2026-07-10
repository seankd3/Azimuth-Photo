"""Load fitted per-camera color profiles for the Develop render twins."""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from . import ops_constants as C

PROFILES_DIR = Path(__file__).with_name("profiles")


def slugify_model(model: object) -> str:
    """Return the filename-safe camera model key used by the fitter."""
    return re.sub(r"[^a-z0-9]+", "-", str(model or "").lower()).strip("-")


def _finite_list(values: object, length: int) -> list[float] | None:
    if not isinstance(values, list) or len(values) != length:
        return None
    try:
        result = [float(value) for value in values]
    except (TypeError, ValueError):
        return None
    return result if all(value == value and abs(value) != float("inf") for value in result) else None


def _validated_profile(payload: object) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    nodes = _finite_list(payload.get("tone_nodes"), C.CAMERA_PROFILE_TONE_NODES)
    values = _finite_list(payload.get("tone_values"), C.CAMERA_PROFILE_TONE_NODES)
    edges = _finite_list(payload.get("chroma_edges"), C.CAMERA_PROFILE_CHROMA_BINS + 1)
    table = payload.get("oklab_ab_delta")
    if nodes is None or values is None or edges is None or not isinstance(table, list) or len(table) != C.CAMERA_PROFILE_HUE_BINS:
        return None
    deltas: list[list[list[float]]] = []
    for hue_row in table:
        if not isinstance(hue_row, list) or len(hue_row) != C.CAMERA_PROFILE_CHROMA_BINS:
            return None
        chroma_row: list[list[float]] = []
        for delta in hue_row:
            pair = _finite_list(delta, 2)
            if pair is None:
                return None
            chroma_row.append(pair)
        deltas.append(chroma_row)
    if any(right <= left for left, right in zip(nodes, nodes[1:])) or any(
        right <= left for left, right in zip(edges, edges[1:])
    ):
        return None
    tone_trusted = payload.get("tone_trusted") is True
    if not tone_trusted:
        # Only use fitted tone after the fitter's per-camera 20% holdout gate.
        # Color-table fitting remains useful independently of that decision.
        xs = [p[0] / 255.0 for p in C.BASE_PROFILE_POINTS]
        ys = [p[1] / 255.0 for p in C.BASE_PROFILE_POINTS]

        def _interp(v: float) -> float:
            if v <= xs[0]:
                return ys[0]
            for left, right, lo, hi in zip(xs, xs[1:], ys, ys[1:]):
                if v <= right:
                    span = right - left
                    return lo if span <= 0 else lo + (hi - lo) * (v - left) / span
            return ys[-1]

        values = [_interp(n) for n in nodes]
        values = [max(values[i], max(values[:i + 1])) for i in range(len(values))]
    return {
        **payload,
        "model": str(payload.get("model") or ""),
        "tone_trusted": tone_trusted,
        "tone_nodes": nodes,
        "tone_values": values,
        "oklab_ab_delta": deltas,
        "chroma_edges": edges,
    }


@lru_cache(maxsize=32)
def load_camera_profile(model: str) -> dict[str, Any] | None:
    """Load and validate one fitted profile, caching misses as well as hits."""
    slug = slugify_model(model)
    if not slug:
        return None
    path = PROFILES_DIR / f"{slug}.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError):
        return None
    profile = _validated_profile(payload)
    if profile is None:
        return None
    profile["slug"] = slug
    return profile
