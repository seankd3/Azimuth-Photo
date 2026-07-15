"""Google Timeline export readers, intentionally tolerant of both export eras."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from core.dates import parse_taken_timestamp
from features.library.geodata import validate_coordinates


def _timestamp(value: Any) -> float | None:
    if value is None:
        return None
    try:
        numeric = float(value)
        return numeric / 1000 if numeric > 10_000_000_000 else numeric
    except (TypeError, ValueError):
        pass
    return parse_taken_timestamp(value)


def _geo_point(value: Any) -> tuple[float, float] | None:
    if not isinstance(value, str) or not value.startswith("geo:"):
        return None
    try:
        latitude, longitude = value[4:].split(",", 1)
    except ValueError:
        return None
    return validate_coordinates(latitude, longitude)


def _modern_points(payload: dict[str, Any]) -> Iterable[tuple[float, float, float]]:
    for segment in payload.get("semanticSegments", []):
        for point in segment.get("timelinePath", []) or []:
            coordinates = _geo_point(point.get("point"))
            timestamp = _timestamp(point.get("time") or point.get("timestamp") or segment.get("startTime"))
            if coordinates and timestamp is not None:
                yield timestamp, *coordinates


def _legacy_points(payload: dict[str, Any]) -> Iterable[tuple[float, float, float]]:
    for point in payload.get("locations", []):
        coordinates = validate_coordinates(
            (point.get("latitudeE7") or 0) / 10_000_000,
            (point.get("longitudeE7") or 0) / 10_000_000,
        )
        timestamp = _timestamp(point.get("timestampMs") or point.get("timestamp"))
        if coordinates and timestamp is not None:
            yield timestamp, *coordinates


def parse_timeline_payload(payload: dict[str, Any]) -> list[tuple[float, float, float]]:
    points = list(_modern_points(payload)) + list(_legacy_points(payload))
    # A Timeline export can repeat points between segments; keep DB inserts stable.
    return sorted(set(points), key=lambda point: point[0])


def parse_timeline_file(path: str) -> list[tuple[float, float, float]]:
    with Path(path).open(encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError("Google Timeline export must contain a JSON object")
    return parse_timeline_payload(payload)
