"""A photo missing only its end marker is still a photo.

Measured on the owner's archive: 2,480 of 44,521 JPEGs end without the two-byte
end-of-image marker, almost all of them Google Takeout exports. Pillow refused
every one with "image file is truncated (0 bytes not processed)" — the picture
was entirely there, only the terminator was absent — so those photos could never
get a thumbnail and the bulk generator rediscovered that on every pass.
"""

import io
import unittest

from core import pil_limits  # noqa: F401  # the policy under test
from PIL import Image


def _complete_jpeg(size=(320, 240)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, (120, 90, 60)).save(buffer, format="JPEG", quality=90)
    return buffer.getvalue()


class TruncatedJpegTests(unittest.TestCase):
    def test_a_jpeg_missing_only_its_end_marker_still_decodes(self):
        data = _complete_jpeg()
        self.assertEqual(data[-2:], b"\xff\xd9", "fixture should be a complete JPEG")

        without_marker = data[:-2]
        with Image.open(io.BytesIO(without_marker)) as image:
            image.load()
            self.assertEqual(image.size, (320, 240))

    def test_a_jpeg_cut_off_mid_scan_still_yields_what_arrived(self):
        data = _complete_jpeg(size=(640, 480))
        with Image.open(io.BytesIO(data[: int(len(data) * 0.75)])) as image:
            image.load()
            self.assertEqual(image.size, (640, 480), "the header survives, so the size is known")

    def test_something_that_is_not_an_image_still_fails(self):
        with self.assertRaises(Exception):
            with Image.open(io.BytesIO(b"this is not a jpeg at all")) as image:
                image.load()

    def test_a_jpeg_with_no_scan_data_still_fails(self):
        # Header only: there is no picture here, and pretending otherwise would
        # hide genuine corruption behind a blank tile.
        data = _complete_jpeg()
        with self.assertRaises(Exception):
            with Image.open(io.BytesIO(data[:20])) as image:
                image.load()

    def test_the_policy_is_actually_on(self):
        from PIL import ImageFile

        self.assertTrue(ImageFile.LOAD_TRUNCATED_IMAGES)
        self.assertIsNone(Image.MAX_IMAGE_PIXELS, "panoramas must not trip the bomb guard")


if __name__ == "__main__":
    unittest.main()
