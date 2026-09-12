"""EXIF reads for TIFF-family RAW files and Canon CR3.

JPEG and display formats use Pillow's EXIF reader. This module exists for RAW
containers Pillow does not open, and it returns only the four fields the
library actually queries.

A TIFF IFD is a pointer graph over the whole file — Lightroom's DNG writer
puts the Exif IFD *after* 38 MB of image data, so "read a bounded header"
is the wrong model for it and left every converted DNG undated. The file is
mapped instead: the walk follows pointers anywhere while the OS pages in
only the few KiB each IFD touches. The bound survives where the format
earns it: CR3's CMT boxes live in the head, so their scan stops at 4 MiB.
"""

from __future__ import annotations

import mmap
import struct

MAX_SCAN_BYTES = 4 * 1024 * 1024

IFD0 = {
    0x010F: "make",
    0x0110: "model",
    0x8769: None,
    0x8825: None,
}
EXIF = {
    0x9003: "date_taken",
    0x9004: "date_digitized",
    0xA434: "lens",
    0x8827: "iso",
    0x829D: "f_number",
    0x829A: "exposure_time",
    0x920A: "focal_length",
}
GPS = {
    0x0001: "gps_lat_ref",
    0x0002: "gps_lat",
    0x0003: "gps_lon_ref",
    0x0004: "gps_lon",
}
TYPE_BYTES = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 7: 1, 9: 4, 10: 8}


def read(path: str, *, limit: int = MAX_SCAN_BYTES) -> dict[str, object]:
    """Return the useful embedded fields of one RAW container."""

    with open(path, "rb") as handle:
        try:
            data = mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ)
        except (ValueError, OSError):
            return {}
        with data:
            return _read(data, int(limit))


def _read(data, limit: int) -> dict[str, object]:
    found: dict[str, object] = {}
    if _tiff(data, 0, found, IFD0):
        return found

    # CR3 is ISO-BMFF. Canon's CMT1 box carries IFD0 (make, model); CMT2 *is*
    # the Exif IFD itself — its top-level entries are the Exif-namespace tags,
    # with no 0x8769 pointer to follow. Reading it with IFD0's map is how
    # every CR3 in the library sat "undated" while its stage preview knew
    # the date perfectly well.
    for box, wanted in ((b"CMT1", IFD0), (b"CMT2", EXIF)):
        start = data.find(box, 0, limit)
        while 0 <= start < limit:
            _tiff(data, start + len(box), found, wanted)
            start = data.find(box, start + len(box), limit)
    return found


def _tiff(data: bytes, base: int, found: dict[str, object], wanted: dict[int, str | None]) -> bool:
    if base < 0 or base + 8 > len(data):
        return False
    order = data[base:base + 2]
    if order not in (b"II", b"MM"):
        return False
    endian = "<" if order == b"II" else ">"
    magic, first = struct.unpack_from(endian + "HI", data, base + 2)
    if magic != 42:
        return False
    _ifd(data, base, first, endian, wanted, found, depth=0)
    return True


def _ifd(
    data: bytes,
    base: int,
    offset: int,
    endian: str,
    wanted: dict[int, str | None],
    found: dict[str, object],
    *,
    depth: int,
) -> None:
    position = base + int(offset)
    if depth > 2 or offset <= 0 or position + 2 > len(data):
        return
    count = struct.unpack_from(endian + "H", data, position)[0]
    position += 2
    for _ in range(count):
        if position + 12 > len(data):
            return
        tag, data_type, number = struct.unpack_from(endian + "HHI", data, position)
        unit = TYPE_BYTES.get(data_type)
        if unit is None or number > len(data):
            position += 12
            continue
        size = unit * number
        if size <= 4:
            start = position + 8
        else:
            relative = struct.unpack_from(endian + "I", data, position + 8)[0]
            start = base + relative

        if tag == 0x8769:
            child = struct.unpack_from(endian + "I", data, position + 8)[0]
            _ifd(data, base, child, endian, EXIF, found, depth=depth + 1)
        elif tag == 0x8825 and wanted is IFD0:
            child = struct.unpack_from(endian + "I", data, position + 8)[0]
            _ifd(data, base, child, endian, GPS, found, depth=depth + 1)
        elif tag in wanted and start >= base and start + size <= len(data):
            name = wanted[tag]
            if name:
                value = _value(data, start, data_type, number, endian)
                if value not in (None, ""):
                    found[name] = value
        position += 12


def _value(data: bytes, start: int, data_type: int, number: int, endian: str):
    if data_type == 2:
        return data[start:start + number].split(b"\0", 1)[0].decode("utf-8", "ignore").strip()
    if data_type == 3:
        return struct.unpack_from(endian + "H", data, start)[0]
    if data_type == 4:
        return struct.unpack_from(endian + "I", data, start)[0]
    if data_type == 5:
        # Unsigned rationals — a GPS coordinate is three of them (d, m, s).
        parts = []
        for i in range(min(number, 3)):
            top, bottom = struct.unpack_from(endian + "II", data, start + 8 * i)
            parts.append(top / bottom if bottom else 0.0)
        return tuple(parts)
    return None
