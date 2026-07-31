"""Cheap, bounded image-header geometry reads for catalog registration."""

import ctypes
import mmap
import os
import struct
import time


# Also the scanner's catalog acceptance list (scanner.SUPPORTED_EXTENSIONS).
HEADER_GEOMETRY_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".dng",
    ".cr2",
    ".cr3",
    ".arw",
    ".nef",
    ".orf",
    ".raf",
    ".rw2",
    ".tif",
    ".tiff",
    ".webp",
    ".heic",
    ".heif",
    ".bmp",
    ".gif",
}
INITIAL_HEADER_BYTES = 1024
MAX_HEADER_BYTES = 128 * 1024
_TIFF_TYPE_SIZES = {1: 1, 2: 1, 3: 2, 4: 4, 7: 1, 9: 4}
_JPEG_SOF_MARKERS = {
    0xC0,
    0xC1,
    0xC2,
    0xC3,
    0xC5,
    0xC6,
    0xC7,
    0xC9,
    0xCA,
    0xCB,
    0xCD,
    0xCE,
    0xCF,
}
_rotational_devices: dict[int, bool] = {}
# CDLL(None) means "this process's libc" — a POSIX-only idiom; the frozen
# Windows loader turns it into a fatal import error. Rotational detection is
# a Linux sysfs optimization anyway.
_libc = ctypes.CDLL(None, use_errno=True) if os.name != "nt" else None


def _is_rotational_device(filepath: str) -> bool:
    """Return Linux's cached rotational-media flag for the source device."""

    if _libc is None:
        return False
    try:
        device = int(os.stat(filepath).st_dev)
    except OSError:
        return False
    cached = _rotational_devices.get(device)
    if cached is not None:
        return cached
    rotational = False
    try:
        node = os.path.realpath(f"/sys/dev/block/{os.major(device)}:{os.minor(device)}")
    except (AttributeError, OSError):
        _rotational_devices[device] = False
        return False
    while node.startswith("/sys/"):
        path = os.path.join(node, "queue", "rotational")
        try:
            with open(path, encoding="ascii") as handle:
                rotational = handle.read(1) == "1"
            break
        except OSError:
            parent = os.path.dirname(node)
            if parent == node:
                break
            node = parent
    _rotational_devices[device] = rotational
    return rotational


def _first_page_resident(handle) -> bool | None:
    """Check Linux page-cache residency without faulting slow source media."""

    try:
        size = min(mmap.PAGESIZE, os.fstat(handle.fileno()).st_size)
        if size <= 0:
            return False
        mapping = mmap.mmap(
            handle.fileno(),
            size,
            flags=mmap.MAP_PRIVATE,
            prot=mmap.PROT_READ | mmap.PROT_WRITE,
        )
        anchor = None
        try:
            anchor = ctypes.c_char.from_buffer(mapping)
            vector = ctypes.c_ubyte()
            result = _libc.mincore(
                ctypes.c_void_p(ctypes.addressof(anchor)),
                ctypes.c_size_t(size),
                ctypes.byref(vector),
            )
            return bool(vector.value & 1) if result == 0 else None
        finally:
            if anchor is not None:
                del anchor
            mapping.close()
    except (AttributeError, BufferError, OSError, ValueError):
        return None


def _corrected_dimensions(width: int, height: int, orientation: int = 1):
    if width <= 0 or height <= 0:
        return None
    if orientation in {5, 6, 7, 8}:
        width, height = height, width
    return int(width), int(height)


def _tiff_values(data: bytes, base: int, endian: str, entry_offset: int):
    tag, value_type, count = struct.unpack_from(f"{endian}HHI", data, entry_offset)
    unit_size = _TIFF_TYPE_SIZES.get(value_type)
    if unit_size is None or count <= 0 or count > 4096:
        return tag, []
    byte_count = unit_size * count
    if byte_count <= 4:
        value_offset = entry_offset + 8
    else:
        relative_offset = struct.unpack_from(f"{endian}I", data, entry_offset + 8)[0]
        value_offset = base + relative_offset
    if value_offset < 0 or value_offset + byte_count > len(data):
        return tag, []
    if value_type == 3:
        values = struct.unpack_from(f"{endian}{count}H", data, value_offset)
    elif value_type in {4, 9}:
        code = "I" if value_type == 4 else "i"
        values = struct.unpack_from(f"{endian}{count}{code}", data, value_offset)
    else:
        values = tuple(data[value_offset:value_offset + byte_count])
    return tag, list(values)


def _tiff_ifd(data: bytes, base: int, endian: str, relative_offset: int):
    offset = base + relative_offset
    if offset < 0 or offset + 2 > len(data):
        return {}
    count = struct.unpack_from(f"{endian}H", data, offset)[0]
    if count > 1024 or offset + 2 + count * 12 > len(data):
        return {}
    fields = {}
    for index in range(count):
        tag, values = _tiff_values(data, base, endian, offset + 2 + index * 12)
        if values:
            fields[tag] = values
    return fields


def _tiff_root(data: bytes, base: int):
    if base < 0 or base + 8 > len(data):
        return None
    marker = data[base:base + 4]
    # Olympus ORF is TIFF under vendor magics (IIRO/IIRS little, MMOR big).
    if marker in {b"II*\x00", b"IIRO", b"IIRS"}:
        endian = "<"
    elif marker in {b"MM\x00*", b"MMOR"}:
        endian = ">"
    else:
        return None
    first_ifd = struct.unpack_from(f"{endian}I", data, base + 4)[0]
    fields = _tiff_ifd(data, base, endian, first_ifd)
    return endian, fields


def _tiff_geometry(data: bytes, base: int = 0):
    root = _tiff_root(data, base)
    if root is None:
        return None
    endian, fields = root
    orientation = int((fields.get(0x0112) or [1])[0])
    width = (fields.get(0x0100) or [None])[0]
    height = (fields.get(0x0101) or [None])[0]

    exif_pointer = (fields.get(0x8769) or [None])[0]
    if exif_pointer is not None:
        exif_fields = _tiff_ifd(data, base, endian, int(exif_pointer))
        width = (exif_fields.get(0xA002) or [width])[0]
        height = (exif_fields.get(0xA003) or [height])[0]
    # NEF/DNG-style raws describe a small preview in IFD0 and keep the full
    # frame in a SubIFD, so prefer the largest frame any SubIFD offers.
    for pointer in (fields.get(0x014A) or [])[:8]:
        sub_fields = _tiff_ifd(data, base, endian, int(pointer))
        sub_width = (sub_fields.get(0x0100) or [None])[0]
        sub_height = (sub_fields.get(0x0101) or [None])[0]
        if not sub_width or not sub_height:
            continue
        if not width or not height or int(sub_width) * int(sub_height) > int(width) * int(height):
            width, height = sub_width, sub_height
    if width is None or height is None:
        return None
    return int(width), int(height), orientation


def _jpeg_geometry(data: bytes):
    if not data.startswith(b"\xff\xd8"):
        return None
    offset = 2
    dimensions = None
    orientation = 1
    while offset + 4 <= len(data):
        if data[offset] != 0xFF:
            offset += 1
            continue
        while offset < len(data) and data[offset] == 0xFF:
            offset += 1
        if offset >= len(data):
            break
        marker = data[offset]
        offset += 1
        if marker in {0xD8, 0xD9}:
            continue
        if marker == 0xDA or offset + 2 > len(data):
            break
        segment_length = struct.unpack_from(">H", data, offset)[0]
        if segment_length < 2 or offset + segment_length > len(data):
            break
        payload = offset + 2
        if marker == 0xE1 and data[payload:payload + 6] == b"Exif\x00\x00":
            root = _tiff_root(data, payload + 6)
            if root is not None:
                orientation = int((root[1].get(0x0112) or [1])[0])
        elif marker in _JPEG_SOF_MARKERS and payload + 5 <= len(data):
            height, width = struct.unpack_from(">HH", data, payload + 1)
            dimensions = (int(width), int(height))
        offset += segment_length
    if dimensions is None:
        return None
    return dimensions[0], dimensions[1], orientation


def _iso_bmff_geometry(data: bytes):
    marker = data.find(b"ispe")
    if marker < 4 or marker + 16 > len(data):
        return None
    width, height = struct.unpack_from(">II", data, marker + 8)
    rotation = 0
    rotation_marker = data.find(b"irot")
    if rotation_marker >= 4 and rotation_marker + 5 <= len(data):
        rotation = data[rotation_marker + 4] & 0x03
    orientation = 6 if rotation % 2 else 1
    return int(width), int(height), orientation


def _rw2_geometry(data: bytes):
    """Panasonic RW2: TIFF layout under an IIU magic; the visible frame is
    recorded as sensor crop borders instead of ImageWidth/ImageLength."""
    if len(data) < 8:
        return None
    first_ifd = struct.unpack_from("<I", data, 4)[0]
    fields = _tiff_ifd(data, 0, "<", first_ifd)
    if not fields:
        return None
    top, left, bottom, right = (
        int((fields.get(tag) or [0])[0]) for tag in (0x0004, 0x0005, 0x0006, 0x0007)
    )
    width = right - left
    height = bottom - top
    if width <= 0 or height <= 0:
        return None
    orientation = int((fields.get(0x0112) or [1])[0])
    return width, height, orientation


def _raf_geometry(data: bytes):
    """Fujifilm RAF: geometry lives in the embedded JPEG addressed by a
    big-endian offset at byte 84 of the fixed header."""
    if len(data) < 92:
        return None
    jpeg_offset = struct.unpack_from(">I", data, 84)[0]
    if jpeg_offset < 92 or jpeg_offset >= len(data):
        return None
    return _jpeg_geometry(data[jpeg_offset:])


def _webp_geometry(data: bytes):
    if data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        return None
    marker = data.find(b"VP8X", 12)
    if marker < 0 or marker + 14 > len(data):
        return None
    width = int.from_bytes(data[marker + 8:marker + 11], "little") + 1
    height = int.from_bytes(data[marker + 11:marker + 14], "little") + 1
    return width, height, 1


def _parse_header_geometry(data: bytes):
    if data.startswith((b"II*\x00", b"MM\x00*", b"IIRO", b"IIRS", b"MMOR")):
        return _tiff_geometry(data)
    if data.startswith(b"IIU\x00"):
        return _rw2_geometry(data)
    if data.startswith(b"FUJIFILMCCD-RAW"):
        return _raf_geometry(data)
    if data.startswith(b"\xff\xd8"):
        return _jpeg_geometry(data)
    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
        width, height = struct.unpack_from(">II", data, 16)
        return int(width), int(height), 1
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return _webp_geometry(data)
    cr3_marker = data.find(b"CMT1")
    if cr3_marker >= 4:
        geometry = _tiff_geometry(data, cr3_marker + 4)
        if geometry is not None:
            return geometry
    if b"ftyp" in data[:32]:
        return _iso_bmff_geometry(data)
    return None


def read_header_dimensions(
    filepath: str,
    *,
    budget_seconds: float | None = 0.005,
):
    """Return orientation-corrected dimensions, or ``None`` when over budget."""

    started = time.perf_counter()
    try:
        with open(filepath, "rb") as handle:
            if budget_seconds is not None and _is_rotational_device(filepath):
                resident = _first_page_resident(handle)
                if resident is False:
                    return None
            data = handle.read(INITIAL_HEADER_BYTES)
            geometry = _parse_header_geometry(data)
            if geometry is None:
                if budget_seconds is not None and time.perf_counter() - started > budget_seconds:
                    return None
                data += handle.read(MAX_HEADER_BYTES - len(data))
                geometry = _parse_header_geometry(data)
        if geometry is None and budget_seconds is None:
            from PIL import Image as PILImage
            from core import pil_limits  # noqa: F401  # disables the decompression-bomb limit process-wide

            with PILImage.open(filepath) as image:
                orientation = int(image.getexif().get(274, 1) or 1)
                geometry = (int(image.width), int(image.height), orientation)
    except (OSError, ValueError, struct.error):
        return None
    if budget_seconds is not None and time.perf_counter() - started > budget_seconds:
        return None
    if geometry is None:
        return None
    return _corrected_dimensions(*geometry)
