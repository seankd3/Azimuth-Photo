"""The embedded facts a photograph carries, read from its bytes alone.

The shape it is shown at, the camera, the lens, the capture date, the place,
and the free-text description a lab or a scanning tool may have written. A
header read for any format: bounded EXIF for raws, Pillow for the rest. Folder
names, mtimes and sidecars are deliberately absent -- each can change while
the bytes stay the same.
"""

from __future__ import annotations

import datetime as dt
import struct

from PIL import ExifTags, Image

from photo import exif as raw_exif
from photo import kind


def dimensions(path: str) -> tuple[int, int]:
    """The shape the photograph is *shown* at. A header read, not a picture.

    Shown, not stored, because every caller wants the former: the grid sizes
    each cell from these numbers, and a cell that disagrees with its tile is
    the letterboxed-portrait bug. Keeping the sensor's shape here would mean
    each caller had to re-derive the turn, which is how two decoders start.

    A raw's flip is the camera's own, already honoured by `postprocess`, so it
    has to be honoured here too or the two disagree. Measured across this
    archive: `flip=5` (8224x5490 sensor) and `flip=6` (8191x5463) both decode
    portrait, `flip=0` decodes landscape, and `flip=3` is a half turn that
    swaps nothing.
    """

    if kind.is_raw(path):
        import rawpy

        with rawpy.imread(path) as raw:
            width, height = int(raw.sizes.width), int(raw.sizes.height)
            return (height, width) if raw.sizes.flip in (5, 6) else (width, height)
    with Image.open(path) as image:
        width, height = int(image.width), int(image.height)
        orientation = int(image.getexif().get(0x0112, 1) or 1)
        return (height, width) if orientation in (5, 6, 7, 8) else (width, height)


def read(path: str, *, description: bool = False) -> dict:
    """Read the six embedded fields the library indexes (and, when asked, the
    free-text description a lab or a scanning tool may have written)."""

    width, height = dimensions(path)
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
