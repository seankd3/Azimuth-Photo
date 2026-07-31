import os
import struct
import tempfile
import unittest
from unittest import mock

from PIL import Image

import image_headers


def _write_ifd(data: bytearray, offset: int, entries) -> None:
    """Write a little-endian TIFF IFD (count + 12-byte entries, no next pointer)."""
    struct.pack_into("<H", data, offset, len(entries))
    for index, (tag, value_type, value) in enumerate(entries):
        entry = offset + 2 + index * 12
        struct.pack_into("<HHI", data, entry, tag, value_type, 1)
        if value_type == 3:
            struct.pack_into("<H", data, entry + 8, value)
        else:
            struct.pack_into("<I", data, entry + 8, value)


def _stub_dimensions(suffix: str, payload: bytes):
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as stub:
        stub.write(payload)
        stub.flush()
    # Windows cannot reopen a held NamedTemporaryFile; close first. No budget:
    # these tests prove geometry parsing, not this machine's file-open latency.
    try:
        return image_headers.read_header_dimensions(stub.name, budget_seconds=None)
    finally:
        os.unlink(stub.name)


class HeaderGeometryTests(unittest.TestCase):
    def test_cr3_cmt1_dimensions_honor_tiff_orientation(self):
        data = bytearray(256)
        data[20:24] = b"CMT1"
        data[24:32] = b"II*\x00\x08\x00\x00\x00"
        struct.pack_into("<H", data, 32, 3)
        entries = ((0x0100, 6000), (0x0101, 4000), (0x0112, 6))
        for index, (tag, value) in enumerate(entries):
            offset = 34 + index * 12
            struct.pack_into("<HHI", data, offset, tag, 3, 1)
            struct.pack_into("<H", data, offset + 8, value)

        with tempfile.NamedTemporaryFile(suffix=".cr3", delete=False) as image:
            image.write(data)
            image.flush()
        # Windows cannot reopen a held NamedTemporaryFile; close first.
        try:
            dimensions = image_headers.read_header_dimensions(image.name)
        finally:
            os.unlink(image.name)

        self.assertEqual(dimensions, (4000, 6000))

    def test_arw_tiff_header_reads_full_frame_dimensions(self):
        data = bytearray(128)
        data[0:8] = b"II*\x00\x08\x00\x00\x00"
        _write_ifd(data, 8, ((0x0100, 4, 7008), (0x0101, 4, 4672), (0x0112, 3, 1)))

        self.assertEqual(_stub_dimensions(".arw", bytes(data)), (7008, 4672))

    def test_nef_subifd_full_frame_beats_ifd0_thumbnail(self):
        data = bytearray(192)
        data[0:8] = b"II*\x00\x08\x00\x00\x00"
        _write_ifd(
            data,
            8,
            ((0x0100, 4, 160), (0x0101, 4, 120), (0x0112, 3, 8), (0x014A, 4, 96)),
        )
        _write_ifd(data, 96, ((0x0100, 4, 8256), (0x0101, 4, 5504)))

        # Orientation 8 comes from IFD0; dimensions from the full-frame SubIFD.
        self.assertEqual(_stub_dimensions(".nef", bytes(data)), (5504, 8256))

    def test_orf_vendor_magic_parses_as_tiff(self):
        data = bytearray(128)
        data[0:8] = b"IIRO\x08\x00\x00\x00"
        _write_ifd(data, 8, ((0x0100, 4, 5240), (0x0101, 4, 3912)))

        self.assertEqual(_stub_dimensions(".orf", bytes(data)), (5240, 3912))

    def test_rw2_sensor_borders_give_frame_dimensions(self):
        data = bytearray(128)
        data[0:8] = b"IIU\x00\x08\x00\x00\x00"
        _write_ifd(
            data,
            8,
            ((0x0004, 3, 4), (0x0005, 3, 6), (0x0006, 3, 3476), (0x0007, 3, 4646)),
        )

        self.assertEqual(_stub_dimensions(".rw2", bytes(data)), (4640, 3472))

    def test_raf_embedded_jpeg_offset_supplies_dimensions(self):
        jpeg = (
            b"\xff\xd8\xff\xc0" + struct.pack(">H", 11) + b"\x08"
            + struct.pack(">HH", 3296, 4416) + b"\x01\x01\x11\x00"
        )
        data = bytearray(100)
        data[0:15] = b"FUJIFILMCCD-RAW"
        struct.pack_into(">I", data, 84, 100)

        self.assertEqual(_stub_dimensions(".raf", bytes(data) + jpeg), (4416, 3296))

    def test_heic_ispe_rotation_swaps_dimensions(self):
        ftyp = struct.pack(">I4s4sI4s", 20, b"ftyp", b"heic", 0, b"heic")
        ispe = struct.pack(">I4sIII", 20, b"ispe", 0, 4032, 3024)
        irot = struct.pack(">I4sB", 9, b"irot", 1)
        with tempfile.NamedTemporaryFile(suffix=".heic", delete=False) as image:
            image.write(ftyp + ispe + irot)
            image.flush()
        # Windows cannot reopen a held NamedTemporaryFile; close first.
        try:
            dimensions = image_headers.read_header_dimensions(image.name)
        finally:
            os.unlink(image.name)

        self.assertEqual(dimensions, (3024, 4032))

    def test_scan_budget_discards_a_slow_header_result(self):
        with tempfile.TemporaryDirectory() as directory:
            filepath = os.path.join(directory, "slow.png")
            Image.new("RGB", (1200, 800)).save(filepath, "PNG")
            with mock.patch.object(
                image_headers.time,
                "perf_counter",
                side_effect=(0.0, 0.006),
            ):
                dimensions = image_headers.read_header_dimensions(
                    filepath,
                    budget_seconds=0.005,
                )

        self.assertIsNone(dimensions)

    def test_uncached_rotational_header_skips_without_parsing(self):
        with tempfile.TemporaryDirectory() as directory:
            filepath = os.path.join(directory, "cold.png")
            Image.new("RGB", (1200, 800)).save(filepath, "PNG")
            with (
                mock.patch.object(image_headers, "_is_rotational_device", return_value=True),
                mock.patch.object(image_headers, "_first_page_resident", return_value=False),
                mock.patch.object(image_headers, "_parse_header_geometry") as parse,
            ):
                dimensions = image_headers.read_header_dimensions(filepath)

        self.assertIsNone(dimensions)
        parse.assert_not_called()


if __name__ == "__main__":
    unittest.main()
