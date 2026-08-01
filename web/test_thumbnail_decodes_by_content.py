"""A file name is not a format.

This archive holds 1,306 files named .CR2 that are really full-resolution
JPEGs — they open perfectly in the viewer, and LibRaw refuses them as "not a raw
file", so the thumbnail path could never produce anything for them. Picking the
decoder by extension made them permanently blank.
"""

import io
import os
import tempfile
import unittest

from PIL import Image

from thumbnails.generation import (
    is_raw_original,
    load_source_image,
    load_source_image_from_bytes,
)

JPEG_EXTENSIONS = {".jpg", ".jpeg"}
RAW_EXTENSIONS = {".cr2", ".cr3", ".nef", ".arw", ".dng"}


class DecodeByContentTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = self.tempdir.name

    def tearDown(self):
        self.tempdir.cleanup()

    def _write(self, name: str, image: Image.Image | None = None, payload: bytes | None = None) -> str:
        path = os.path.join(self.root, name)
        if payload is not None:
            with open(path, "wb") as handle:
                handle.write(payload)
            return path
        buffer = io.BytesIO()
        (image or Image.new("RGB", (64, 48), (10, 120, 200))).save(buffer, format="JPEG")
        with open(path, "wb") as handle:
            handle.write(buffer.getvalue())
        return path

    def _load(self, path: str):
        return load_source_image(
            path, 256, False,
            jpeg_extensions=JPEG_EXTENSIONS,
            raw_extensions=RAW_EXTENSIONS,
        )

    def test_a_jpeg_named_cr2_still_produces_an_image(self):
        """The 1,306 files. LibRaw refuses them; the bytes are a plain JPEG."""

        path = self._write("IMG_2347.CR2")
        with self._load(path) as image:
            self.assertEqual(image.size, (64, 48))

    def test_an_ordinary_jpeg_is_unaffected(self):
        path = self._write("holiday.jpg")
        with self._load(path) as image:
            self.assertEqual(image.size, (64, 48))

    def test_a_file_that_is_neither_still_raises(self):
        """Falling back must widen what can be shown, not hide real failures."""

        path = self._write("broken.CR2", payload=b"this is not an image at all")
        with self.assertRaises(Exception):
            self._load(path)

    def test_the_raw_error_survives_when_nothing_can_decode_it(self):
        """A genuinely broken RAW should report the RAW failure, not a JPEG one."""

        path = self._write("truncated.CR2", payload=b"\x00" * 512)
        with self.assertRaises(Exception) as caught:
            self._load(path)
        self.assertNotIsInstance(caught.exception, SystemExit)


class IsRawOriginalTests(unittest.TestCase):
    """The shared rule. Both decoders and both branch selectors ask this.

    Fixing only the decoders was not enough: the bulk generator picks a whole
    different pipeline (embedded preview + process-pool demosaic) before any
    decoder is called, and that choice was made on the extension.
    """

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tempdir.cleanup()

    def _file(self, name: str, payload: bytes) -> str:
        path = os.path.join(self.tempdir.name, name)
        with open(path, "wb") as handle:
            handle.write(payload)
        return path

    def _is_raw(self, path: str, **kwargs) -> bool:
        return is_raw_original(
            path, os.path.splitext(path)[1].lower(), RAW_EXTENSIONS, **kwargs
        )

    def test_a_jpeg_named_cr2_does_not_take_the_raw_pipeline(self):
        self.assertFalse(self._is_raw(self._file("IMG_2347.CR2", b"\xff\xd8\xff\xe0rest")))

    def test_a_real_raw_still_takes_the_raw_pipeline(self):
        self.assertTrue(self._is_raw(self._file("IMG_2348.CR2", b"II*\x00\x10\x00\x00\x00CR")))

    def test_an_ordinary_jpeg_was_never_raw(self):
        self.assertFalse(self._is_raw(self._file("holiday.jpg", b"\xff\xd8\xff\xe0rest")))

    def test_bytes_in_hand_are_used_instead_of_touching_the_disk(self):
        path = os.path.join(self.tempdir.name, "never-written.CR2")
        self.assertFalse(self._is_raw(path, source_data=b"\xff\xd8\xff\xe0rest"))
        self.assertTrue(self._is_raw(path, source_data=b"II*\x00"))

    def test_an_unreadable_file_stays_raw_so_the_real_error_surfaces(self):
        missing = os.path.join(self.tempdir.name, "gone.CR2")
        self.assertTrue(self._is_raw(missing))


class DecodeBytesByContentTests(unittest.TestCase):
    """The bulk generator reads originals into RAM, so it uses the other door."""

    def _jpeg_bytes(self) -> bytes:
        buffer = io.BytesIO()
        Image.new("RGB", (48, 32), (200, 60, 10)).save(buffer, format="JPEG")
        return buffer.getvalue()

    def _load(self, name: str, data: bytes):
        return load_source_image_from_bytes(
            name, data, 256, False,
            jpeg_extensions=JPEG_EXTENSIONS,
            raw_extensions=RAW_EXTENSIONS,
        )

    def test_a_jpeg_named_cr2_decodes_from_memory_too(self):
        with self._load("IMG_2347.CR2", self._jpeg_bytes()) as image:
            self.assertEqual(image.size, (48, 32))

    def test_an_ordinary_jpeg_from_memory_is_unaffected(self):
        with self._load("holiday.jpg", self._jpeg_bytes()) as image:
            self.assertEqual(image.size, (48, 32))

    def test_bytes_that_are_not_an_image_still_raise(self):
        with self.assertRaises(Exception):
            self._load("broken.CR2", b"not an image")


if __name__ == "__main__":
    unittest.main()
