"""GPS extraction, conservative location updates, and trail interpolation."""

from __future__ import annotations

import asyncio
import math
import os
import time
from datetime import datetime, timezone
from fractions import Fraction
from typing import Any

from core.dates import parse_taken_timestamp as _parse_taken_timestamp
from data import connection


GPS_IFD = 0x8825
GPS_LAT_REF, GPS_LAT, GPS_LON_REF, GPS_LON = 1, 2, 3, 4
LOCATION_PRIORITY = {"inferred": 1, "timeline": 2, "xmp": 3, "exif": 4}
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


def location_can_replace(existing_source: str | None, new_source: str, *, has_coordinates: bool) -> bool:
    """Protect existing coordinates unless the incoming provenance is stronger."""
    if not has_coordinates:
        return True
    if not existing_source:
        return False
    return LOCATION_PRIORITY.get(new_source, 0) > LOCATION_PRIORITY.get(existing_source, 0)


def parse_taken_timestamp(value: Any) -> float | None:
    return _parse_taken_timestamp(_text(value))


def distance_km(left: tuple[float, float], right: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (*left, *right))
    a = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 6371.0088 * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _derived_source(neighbor_source: str) -> str:
    """A location copied or interpolated from a neighbor is never 'exif':
    provenance must not outrank a future real EXIF value for the same photo."""
    return "timeline" if neighbor_source == "timeline" else "inferred"


def infer_location(timestamp: float, trail: list[dict[str, Any]]) -> tuple[float, float, str] | None:
    """Infer from a sorted trail according to the deliberately tight safety windows."""
    before = next((point for point in reversed(trail) if point["ts"] <= timestamp), None)
    after = next((point for point in trail if point["ts"] >= timestamp), None)
    if before and after and before is after:
        return before["lat"], before["lon"], _derived_source(before["source"])
    if before and after:
        left_delta, right_delta = timestamp - before["ts"], after["ts"] - timestamp
        if left_delta <= 45 * 60 and right_delta <= 45 * 60 and distance_km((before["lat"], before["lon"]), (after["lat"], after["lon"])) < 50:
            if left_delta == 0:
                return before["lat"], before["lon"], _derived_source(before["source"])
            if right_delta == 0:
                return after["lat"], after["lon"], _derived_source(after["source"])
            left_weight, right_weight = 1 / left_delta, 1 / right_delta
            lat = (before["lat"] * left_weight + after["lat"] * right_weight) / (left_weight + right_weight)
            lon = (before["lon"] * left_weight + after["lon"] * right_weight) / (left_weight + right_weight)
            source = "timeline" if "timeline" in {before["source"], after["source"]} else "inferred"
            return round(lat, 6), round(lon, 6), source
        if left_delta <= 45 * 60 and right_delta <= 45 * 60:
            # Both neighbors are close in time but far apart in space: the
            # trail contradicts itself here — refuse to guess.
            return None
    one_side = before or after
    if one_side and abs(timestamp - one_side["ts"]) <= 15 * 60:
        return one_side["lat"], one_side["lon"], _derived_source(one_side["source"])
    return None


async def ensure_geo_schema(db_path: str) -> None:
    conn = await connection.open_async(db_path)
    try:
        columns = {row[1] for row in await (await conn.execute("PRAGMA table_info(images)")).fetchall()}
        if "location_source" not in columns:
            try:
                await conn.execute("ALTER TABLE images ADD COLUMN location_source TEXT DEFAULT NULL")
            except Exception as exc:  # concurrent caller won the ALTER race
                if "duplicate column" not in str(exc).lower():
                    raise
        await conn.execute("CREATE TABLE IF NOT EXISTS geo_trail (ts REAL, lat REAL, lon REAL, source TEXT)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_geo_trail_ts ON geo_trail(ts)")
        await conn.commit()
    finally:
        await connection.close_async(conn, db_path=db_path)


async def geo_status(db_path: str) -> dict[str, Any]:
    await ensure_geo_schema(db_path)
    conn = await connection.open_async(db_path)
    try:
        sources = await (await conn.execute(
            "SELECT COALESCE(location_source, 'none') AS source, COUNT(*) AS count FROM images "
            "WHERE latitude IS NOT NULL AND longitude IS NOT NULL GROUP BY location_source"
        )).fetchall()
        remaining = await (await conn.execute(
            "SELECT COUNT(*) AS count FROM images WHERE latitude IS NULL OR longitude IS NULL"
        )).fetchone()
        return {"by_source": {row["source"]: int(row["count"]) for row in sources}, "remaining": int(remaining["count"])}
    finally:
        await connection.close_async(conn, db_path=db_path)


async def backfill_batch(db_path: str, *, after_id: int = 0, limit: int = BACKFILL_BATCH_SIZE) -> tuple[int, dict[str, int]]:
    await ensure_geo_schema(db_path)
    conn = await connection.open_async(db_path)
    try:
        rows = await (await conn.execute(
            "SELECT id, filepath, latitude, longitude, location_source, date_taken, camera_make, camera_model, lens "
            "FROM images WHERE id > ? AND (latitude IS NULL OR longitude IS NULL OR date_taken IS NULL "
            "OR camera_make IS NULL OR camera_model IS NULL OR lens IS NULL) ORDER BY id LIMIT ?",
            (after_id, limit),
        )).fetchall()
        changes = {"gps": 0, "date_taken": 0, "camera_make": 0, "camera_model": 0, "lens": 0}
        pending_updates: list[tuple[str, tuple]] = []
        for row in rows:
            # File reads happen before any UPDATE so the write transaction is
            # only held for the fast SQL tail of the batch, not the slow IO.
            metadata = await asyncio.to_thread(extract_file_metadata, row["filepath"])
            coords = validate_coordinates(metadata.get("latitude"), metadata.get("longitude"))
            has_coords = row["latitude"] is not None and row["longitude"] is not None
            can_write_coords = coords and location_can_replace(row["location_source"], "exif", has_coordinates=has_coords)
            assignments: dict[str, Any] = {}
            if can_write_coords:
                assignments.update(latitude=coords[0], longitude=coords[1], location_source="exif")
                changes["gps"] += 1
            for field in ("date_taken", "camera_make", "camera_model", "lens"):
                if row[field] in (None, "") and metadata.get(field):
                    assignments[field] = metadata[field]
                    changes[field] += 1
            if assignments:
                clause = ", ".join(f"{field} = ?" for field in assignments)
                pending_updates.append((f"UPDATE images SET {clause} WHERE id = ?", (*assignments.values(), row["id"])))
        for statement, params in pending_updates:
            await conn.execute(statement, params)
        await conn.commit()
        return (int(rows[-1]["id"]) if rows else after_id), changes
    finally:
        await connection.close_async(conn, db_path=db_path)


async def run_backfill(db_path: str, status: dict[str, Any]) -> None:
    status.update(state="running", started_at=time.time(), cursor=0, counts={"gps": 0, "date_taken": 0, "camera_make": 0, "camera_model": 0, "lens": 0}, error="")
    try:
        while True:
            cursor, changes = await backfill_batch(db_path, after_id=int(status["cursor"]))
            if cursor == status["cursor"]:
                break
            status["cursor"] = cursor
            for field, count in changes.items():
                status["counts"][field] += count
            await asyncio.sleep(BACKFILL_THROTTLE_SECONDS)
        status.update(state="complete", finished_at=time.time())
    except Exception as exc:
        status.update(state="error", error=str(exc), finished_at=time.time())


async def infer_locations(db_path: str) -> dict[str, int]:
    await ensure_geo_schema(db_path)
    conn = await connection.open_async(db_path)
    try:
        photo_rows = await (await conn.execute(
            "SELECT date_taken, latitude, longitude, location_source FROM images "
            "WHERE date_taken IS NOT NULL AND latitude IS NOT NULL AND longitude IS NOT NULL "
            "AND location_source = 'exif'"
        )).fetchall()
        timeline_rows = await (await conn.execute("SELECT ts, lat, lon, source FROM geo_trail")).fetchall()
        trail = [
            {"ts": ts, "lat": float(row["latitude"]), "lon": float(row["longitude"]), "source": "exif"}
            for row in photo_rows if (ts := parse_taken_timestamp(row["date_taken"])) is not None
        ]
        trail += [{"ts": float(row["ts"]), "lat": float(row["lat"]), "lon": float(row["lon"]), "source": "timeline"} for row in timeline_rows]
        trail.sort(key=lambda point: point["ts"])
        candidates = await (await conn.execute(
            "SELECT id, date_taken, latitude, longitude, location_source FROM images WHERE date_taken IS NOT NULL "
            "AND (latitude IS NULL OR longitude IS NULL OR location_source = 'inferred')"
        )).fetchall()
        changes = {"inferred": 0, "timeline": 0}
        for row in candidates:
            ts = parse_taken_timestamp(row["date_taken"])
            if ts is None:
                continue
            inferred = infer_location(ts, trail)
            if not inferred:
                continue
            lat, lon, source = inferred
            has_coords = row["latitude"] is not None and row["longitude"] is not None
            if not location_can_replace(row["location_source"], source, has_coordinates=has_coords):
                continue
            await conn.execute("UPDATE images SET latitude = ?, longitude = ?, location_source = ? WHERE id = ?", (lat, lon, source, row["id"]))
            changes[source] += 1
        await conn.commit()
        return changes
    finally:
        await connection.close_async(conn, db_path=db_path)


async def run_inference(db_path: str, status: dict[str, Any]) -> None:
    status.update(state="running", started_at=time.time(), counts={"inferred": 0, "timeline": 0}, error="")
    try:
        status["counts"] = await infer_locations(db_path)
        status.update(state="complete", finished_at=time.time())
    except Exception as exc:
        status.update(state="error", error=str(exc), finished_at=time.time())
