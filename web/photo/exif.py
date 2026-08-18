"""Bounded EXIF reads for TIFF-family RAW files and Canon CR3.

JPEG and display formats use Pillow's EXIF reader. This module exists for RAW
containers Pillow does not open: it reads at most four MiB and returns only the
four fields the library actually queries.
"""

from __future__ import annotations

import struct

MAX_HEADER_BYTES = 4 * 1024 * 1024

IFD0 = {
    0x010F: "make",
    0x0110: "model",
    0x8769: None,
}
EXIF = {
    0x9003: "date_taken",
    0x9004: "date_digitized",
    0xA434: "lens",
}
TYPE_BYTES = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 7: 1, 9: 4, 10: 8}


def read(path: str, *, limit: int = MAX_HEADER_BYTES) -> dict[str, object]:
    """Return the useful embedded fields that fit in a bounded header read."""

    with open(path, "rb") as handle:
        data = handle.read(int(limit))

    found: dict[str, object] = {}
    if _tiff(data, 0, found):
        return found

    # CR3 is ISO-BMFF. Canon's CMT1 and CMT2 boxes carry TIFF IFDs.
    for box in (b"CMT1", b"CMT2"):
        start = data.find(box)
        while start >= 0:
            _tiff(data, start + len(box), found)
            start = data.find(box, start + len(box))
    return found


def _tiff(data: bytes, base: int, found: dict[str, object]) -> bool:
    if base < 0 or base + 8 > len(data):
        return False
    order = data[base:base + 2]
    if order not in (b"II", b"MM"):
        return False
    endian = "<" if order == b"II" else ">"
    magic, first = struct.unpack_from(endian + "HI", data, base + 2)
    if magic != 42:
        return False
    _ifd(data, base, first, endian, IFD0, found, depth=0)
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
    return None
