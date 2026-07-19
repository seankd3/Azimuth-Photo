"""Dependency-free EXIF reader for the fields Develop needs (Make/Model/Lens/
Focal/FNumber). Handles TIFF-family containers (CR2, DNG, TIFF) and Canon CR3
(ISO-BMFF: CMT1 box = IFD0, CMT2 box = Exif IFD). Fallback when ExifTool is absent.
"""
from __future__ import annotations

import struct
from typing import Any

_TAGS_IFD0 = {0x010F: "Make", 0x0110: "Model"}
_TAGS_EXIF = {0x829D: "FNumber", 0x920A: "FocalLength", 0xA434: "LensModel", 0x8769: None}
_TYPE_SIZES = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 7: 1, 9: 4, 10: 8}


def _parse_ifd(data: bytes, base: int, offset: int, byteorder: str, wanted: dict[int, str | None], out: dict[str, Any], depth: int = 0) -> None:
    if depth > 2 or offset <= 0 or base + offset + 2 > len(data):
        return
    endian = "<" if byteorder == "II" else ">"
    (count,) = struct.unpack_from(endian + "H", data, base + offset)
    pos = base + offset + 2
    for _ in range(count):
        if pos + 12 > len(data):
            return
        tag, dtype, n = struct.unpack_from(endian + "HHI", data, pos)
        value_bytes = data[pos + 8: pos + 12]
        size = _TYPE_SIZES.get(dtype, 1) * n
        if size > 4:
            (value_offset,) = struct.unpack_from(endian + "I", value_bytes)
            start = base + value_offset
        else:
            start = pos + 8
        if tag in wanted and start + size <= len(data):
            name = wanted[tag]
            if tag == 0x8769:  # Exif IFD pointer
                (sub,) = struct.unpack_from(endian + "I", data, pos + 8)
                _parse_ifd(data, base, sub, byteorder, _TAGS_EXIF, out, depth + 1)
            elif dtype == 2:  # ASCII
                out[name] = data[start:start + size].split(b"\0")[0].decode("ascii", "ignore").strip()
            elif dtype in (5, 10) and size >= 8:  # RATIONAL
                num, den = struct.unpack_from(endian + ("II" if dtype == 5 else "ii"), data, start)
                if den:
                    out[name] = num / den
            elif dtype in (3, 4):
                fmt = "H" if dtype == 3 else "I"
                out[name] = struct.unpack_from(endian + fmt, data, start)[0]
        pos += 12


def _parse_tiff(data: bytes, base: int, out: dict[str, Any]) -> bool:
    if base + 8 > len(data):
        return False
    byteorder = data[base:base + 2].decode("ascii", "ignore")
    if byteorder not in ("II", "MM"):
        return False
    endian = "<" if byteorder == "II" else ">"
    magic, first_ifd = struct.unpack_from(endian + "HI", data, base + 2)
    if magic != 42:
        return False
    wanted = dict(_TAGS_IFD0)
    wanted[0x8769] = None  # follow Exif IFD for lens/focal/aperture
    _parse_ifd(data, base, first_ifd, byteorder, wanted, out)
    return True


def read_native_exif(path: str, head_bytes: int = 4 << 20) -> dict[str, Any]:
    """Best-effort Make/Model/LensModel/FocalLength/FNumber from the file header."""
    try:
        with open(path, "rb") as handle:
            data = handle.read(head_bytes)
    except OSError:
        return {}
    out: dict[str, Any] = {}
    if data[:2] in (b"II", b"MM"):  # TIFF family: CR2, DNG, TIFF
        _parse_tiff(data, 0, out)
        return out
    # ISO-BMFF (CR3): CMT1 = IFD0-style TIFF, CMT2 = Exif IFD TIFF
    for box in (b"CMT1", b"CMT2"):
        idx = data.find(box)
        if idx >= 0:
            tiff_at = idx + 4
            sub: dict[str, Any] = {}
            if _parse_tiff(data, tiff_at, sub):
                out.update({k: v for k, v in sub.items() if v not in (None, "")})
            elif box == b"CMT2":
                # CMT2's IFD holds Exif tags directly at IFD0 position
                byteorder = data[tiff_at:tiff_at + 2].decode("ascii", "ignore")
                if byteorder in ("II", "MM"):
                    endian = "<" if byteorder == "II" else ">"
                    _, first = struct.unpack_from(endian + "HI", data, tiff_at + 2)
                    _parse_ifd(data, tiff_at, first, byteorder, _TAGS_EXIF, out)
    return out


if __name__ == "__main__":
    import sys
    import glob
    for pattern in sys.argv[1:]:
        for p in glob.glob(pattern):
            print(p, "->", read_native_exif(p))
