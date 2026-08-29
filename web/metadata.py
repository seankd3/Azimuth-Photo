"""Embedded facts used to browse a photograph.

The answer is keyed only by the photograph's content identity, so it contains
only facts embedded in those bytes. Folder names, mtimes, JSON companions and
XMP sidecars are deliberately absent: each can change while the content hash
stays the same and therefore cannot honestly share this cache key.
"""

from __future__ import annotations

import datetime as dt
import json
import struct

from PIL import ExifTags, Image

import render
from model import cache, decisions
from photo import exif as raw_exif
from photo import kind


def read(path: str, *, description: bool = False) -> dict:
    """Read the six embedded fields the library indexes (and, when asked, the
    free-text description a lab or a scanning tool may have written)."""

    width, height = render.dimensions(path)
    tags = _raw_tags(path) if kind.is_raw(path) else _display_tags(path)
    make = _text(tags.get("make"))
    model = _text(tags.get("model"))
    if make and model.casefold().startswith(make.casefold()):
        model = model[len(make):].strip()

    answer = {"width": int(width), "height": int(height)}
    for name, value in (
        ("date_taken", normalize_date(tags.get("date_taken") or tags.get("date_digitized"))),
        ("camera_make", make),
        ("camera_model", model),
        ("lens", _text(tags.get("lens"))),
    ):
        if value:
            answer[name] = value
    lat = _degrees(tags.get("gps_lat"), _text(tags.get("gps_lat_ref")), "S")
    lon = _degrees(tags.get("gps_lon"), _text(tags.get("gps_lon_ref")), "W")
    if lat is not None and lon is not None:
        answer["lat"] = lat
        answer["lon"] = lon
    if description:
        answer["description"] = _text(tags.get("description"))
    return answer


def _degrees(parts, ref: str, negative: str) -> float | None:
    """Degrees-minutes-seconds rationals as one signed decimal degree."""

    try:
        d, m, s = (float(p) for p in (*parts, 0, 0)[:3])
    except (TypeError, ValueError):
        return None
    value = d + m / 60.0 + s / 3600.0
    if not 0.0 <= value <= 180.0:
        return None
    return round(-value if ref.upper().startswith(negative) else value, 6)


def make(path: str, _digest: str) -> cache.Made:
    value = json.dumps(read(path), separators=(",", ":"), sort_keys=True)
    return cache.Made(value=value, bytes=len(value.encode("utf-8")))


def project(conn, photo_id: int, entry: dict) -> None:
    """Maintain the query columns derived from one ready metadata answer."""

    answer = decoded(entry)
    row = conn.execute(
        "SELECT content_hash FROM images WHERE id = ?", (int(photo_id),)
    ).fetchone()
    if row is None:
        return
    values = _values(conn, row["content_hash"], answer)
    conn.execute(
        "UPDATE images SET date_taken = ?, camera_make = ?, camera_model = ?, "
        "lens = ?, width = ?, height = ? WHERE content_hash = ?",
        (*values, row["content_hash"]),
    )


def reindex(conn) -> dict[str, int]:
    """Rebuild query columns from ready metadata rows after opening a catalog."""

    plans = []
    discarded = 0
    rows = conn.execute(
        "SELECT c.hash, c.value, c.state, MIN(i.id) AS photo_id FROM cache c "
        "JOIN images i ON i.content_hash = c.hash "
        "WHERE c.kind = ? AND c.recipe = '{}' AND c.state = ? GROUP BY c.hash",
        (KIND.name, cache.READY),
    ).fetchall()
    for row in rows:
        entry = {"state": row["state"], "value": row["value"]}
        try:
            answer = decoded(entry)
        except ValueError:
            conn.execute(
                "DELETE FROM cache WHERE hash = ? AND kind = ? AND recipe = '{}'",
                (row["hash"], KIND.name),
            )
            discarded += 1
            continue
        plans.append((row["hash"], _values(conn, row["hash"], answer)))

    projected = 0
    for digest, values in plans:
        cursor = conn.execute(
            "UPDATE images SET date_taken = ?, camera_make = ?, camera_model = ?, "
            "lens = ?, width = ?, height = ? WHERE content_hash = ?",
            (*values, digest),
        )
        projected += cursor.rowcount
    conn.commit()
    return {"projected": projected, "discarded": discarded}


def _values(conn, digest: str, answer: dict) -> tuple:
    chosen_date = decisions.latest(conn, digest, decisions.DATE)
    date_taken = answer.get("date_taken")
    if chosen_date is not None:
        date_taken = normalize_date(chosen_date)
        if date_taken is None:
            raise ValueError(f"invalid date decision: {chosen_date!r}")
    return (
        date_taken,
        answer.get("camera_make"),
        answer.get("camera_model"),
        answer.get("lens"),
        answer["width"],
        answer["height"],
    )


def decoded(entry: dict) -> dict:
    if not entry or entry.get("state") != cache.READY:
        raise ValueError("metadata projection requires a ready cache entry")
    try:
        answer = json.loads(entry["value"])
    except (KeyError, TypeError, json.JSONDecodeError) as error:
        raise ValueError("invalid cached metadata") from error
    if not isinstance(answer, dict) or not all(
        isinstance(answer.get(name), int) and answer[name] > 0
        for name in ("width", "height")
    ):
        raise ValueError("cached metadata has no valid dimensions")
    return answer


KIND = cache.Kind(
    name="metadata",
    compute=make,
    cost=0.02,
    project=project,
)


def _raw_tags(path: str) -> dict:
    try:
        return raw_exif.read(path)
    except (OSError, ValueError, struct.error):
        return {}


def _display_tags(path: str) -> dict:
    try:
        with Image.open(path) as image:
            embedded = image.getexif()
            values = dict(embedded.items())
            try:
                values.update(embedded.get_ifd(ExifTags.IFD.Exif))
            except (AttributeError, KeyError, TypeError, ValueError):
                pass
    except (OSError, SyntaxError, TypeError, ValueError):
        return {}
    try:
        gps = dict(embedded.get_ifd(ExifTags.IFD.GPSInfo))
    except (AttributeError, KeyError, TypeError, ValueError):
        gps = {}
    return {
        "make": values.get(0x010F),
        "model": values.get(0x0110),
        "date_taken": values.get(0x9003) or values.get(0x0132),
        "date_digitized": values.get(0x9004),
        "lens": values.get(0xA434),
        "description": values.get(0x010E) or values.get(0x9286),
        "gps_lat_ref": gps.get(1),
        "gps_lat": gps.get(2),
        "gps_lon_ref": gps.get(3),
        "gps_lon": gps.get(4),
    }


def _text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        value = value.decode("utf-8", "ignore")
    return str(value).replace("\0", "").strip()


def normalize_date(value) -> str | None:
    raw = _text(value)
    if not raw:
        return None
    raw = raw[:19].replace("T", " ")
    if len(raw) >= 10 and raw[4] == ":" and raw[7] == ":":
        raw = f"{raw[:4]}-{raw[5:7]}-{raw[8:]}"
    for form in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return dt.datetime.strptime(raw, form).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
    return None
