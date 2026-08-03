"""GPS extraction, conservative location updates, and trail interpolation."""

from __future__ import annotations

import os
from datetime import datetime
from fractions import Fraction
from typing import Any

from core.dates import parse_taken_timestamp as _parse_taken_timestamp


GPS_IFD = 0x8825
GPS_LAT_REF, GPS_LAT, GPS_LON_REF, GPS_LON = 1, 2, 3, 4
BACKFILL_BATCH_SIZE = 500
BACKFILL_THROTTLE_SECONDS = 0.05


def _text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="ignore").replace("\x00", "").strip()
    return str(value or "").replace("\x00", "").strip()


def _number(value: Any) -> float | None:
    if isinstance(value, tuple) and len(value) == 2:
        try:
            return float(Fraction(value[0], value[1] or 1))
        except (TypeError, ValueError, ZeroDivisionError):
            return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _dms(value: Any, ref: Any) -> float | None:
    try:
        degrees, minutes, seconds = (_number(part) for part in value[:3])
    except (TypeError, IndexError):
        return None
    if None in (degrees, minutes, seconds):
        return None
    decimal = degrees + minutes / 60 + seconds / 3600
    hemisphere = _text(ref).upper()
    if hemisphere not in {"N", "S", "E", "W"}:
        return None  # missing/corrupt ref: sign would be a guess
    if hemisphere in {"S", "W"}:
        decimal = -decimal
    return decimal


def validate_coordinates(latitude: Any, longitude: Any) -> tuple[float, float] | None:
    lat, lon = _number(latitude), _number(longitude)
    if lat is None or lon is None or (lat == 0 and lon == 0):
        return None
    if abs(lat) > 90 or abs(lon) > 180:
        return None
    return round(lat, 6), round(lon, 6)


def parse_gps_ifd(gps: dict[Any, Any] | None) -> tuple[float, float] | None:
    """Parse Pillow's numeric GPS IFD without relying on exiftool."""
    if not gps:
        return None
    lat = _dms(gps.get(GPS_LAT), gps.get(GPS_LAT_REF))
    lon = _dms(gps.get(GPS_LON), gps.get(GPS_LON_REF))
    return validate_coordinates(lat, lon)


def _parse_exif_date(value: Any) -> str | None:
    raw = _text(value)
    if not raw:
        return None
    for fmt in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(raw[:19], fmt).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
    return None


def _pillow_metadata(filepath: str) -> dict[str, Any]:
    from PIL import Image
    from core import pil_limits  # noqa: F401  # disables the decompression-bomb limit process-wide

    with Image.open(filepath) as image:
        exif = image.getexif()
        gps = exif.get_ifd(GPS_IFD) if exif else {}
        exif_ifd = exif.get_ifd(0x8769) if exif else {}
        make = _text(exif.get(271))
        model = _text(exif.get(272))
        if make and model.startswith(make):
            model = model[len(make):].strip()
        return {
            "latitude_longitude": parse_gps_ifd(gps),
            "camera_make": make or None,
            "camera_model": model or None,
            "lens": _text(exif_ifd.get(42036)) or None,
            "software": _text(exif.get(305)) or None,
            "date_taken": _parse_exif_date(exif_ifd.get(36867) or exif_ifd.get(36868) or exif.get(306)),
        }


def _tifffile_metadata(filepath: str) -> dict[str, Any]:
    """Best-effort DNG/TIFF metadata. tifffile varies by maker, so never fail a scan."""
    try:
        import tifffile
        with tifffile.TiffFile(filepath) as tif:
            tags = tif.pages[0].tags
            get = lambda key: tags[key].value if key in tags else None
            gps = get("GPSInfo") or get("GPSIFD") or {}
            if not isinstance(gps, dict):
                gps = {}
            return {
                "latitude_longitude": parse_gps_ifd(gps),
                "camera_make": _text(get("Make")) or None,
                "camera_model": _text(get("Model")) or None,
                "lens": _text(get("LensModel")) or None,
                "date_taken": _parse_exif_date(get("DateTimeOriginal") or get("DateTime")),
            }
    except Exception:
        return {}


def extract_file_metadata(filepath: str) -> dict[str, Any]:
    """Read only the fields geo backfill owns; invalid/missing GPS is omitted."""
    result: dict[str, Any] = {}
    try:
        result.update(_pillow_metadata(filepath))
    except Exception:
        pass
    if os.path.splitext(filepath)[1].lower() in {".dng", ".tif", ".tiff"}:
        raw = _tifffile_metadata(filepath)
        for key, value in raw.items():
            if value and not result.get(key):
                result[key] = value
    coordinates = result.pop("latitude_longitude", None)
    if coordinates:
        result["latitude"], result["longitude"] = coordinates
    return {key: value for key, value in result.items() if value is not None}


def parse_taken_timestamp(value: Any) -> float | None:
    return _parse_taken_timestamp(_text(value))


