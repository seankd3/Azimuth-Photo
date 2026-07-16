import os
import struct
import tempfile
import unittest
from unittest import mock

from PIL import Image

import image_headers


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

        with tempfile.NamedTemporaryFile(suffix=".cr3") as image:
            image.write(data)
            image.flush()
            dimensions = image_headers.read_header_dimensions(image.name)

        self.assertEqual(dimensions, (4000, 6000))

    def test_heic_ispe_rotation_swaps_dimensions(self):
        ftyp = struct.pack(">I4s4sI4s", 20, b"ftyp", b"heic", 0, b"heic")
        ispe = struct.pack(">I4sIII", 20, b"ispe", 0, 4032, 3024)
        irot = struct.pack(">I4sB", 9, b"irot", 1)
        with tempfile.NamedTemporaryFile(suffix=".heic") as image:
            image.write(ftyp + ispe + irot)
            image.flush()
            dimensions = image_headers.read_header_dimensions(image.name)

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
