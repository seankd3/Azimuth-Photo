"""One set of answers for what a visitor sees.

A published site, a client gallery and a share link each showed photos to a
stranger, and each had grown its own copy of the same three answers. One had
drifted: a second `attachment_name` split extensions differently, so the same
photo could download under two names depending on which surface served it.
"""

import pathlib
import unittest
from unittest import mock

from fastapi import Response

from features.share import visitor


class SiteLabelTests(unittest.TestCase):
    def test_it_is_the_bare_hostname(self):
        self.assertEqual(visitor.site_label("https://www.example.com/gallery"), "example.com")
        self.assertEqual(visitor.site_label("example.com"), "example.com")

    def test_no_site_is_no_label(self):
        self.assertEqual(visitor.site_label(""), "")


class BrandTests(unittest.TestCase):
    def _with(self, **config):
        return mock.patch.object(visitor.settings, "get_settings", return_value=config)

    def test_a_configured_name_wins(self):
        with self._with(share_brand_name="Sean Doherty", publish_site_base_url="https://x.com/"):
            self.assertEqual(visitor.brand()["name"], "Sean Doherty")

    def test_the_site_stands_in_for_a_missing_name(self):
        with self._with(publish_site_base_url="https://www.azimuthphoto.com"):
            self.assertEqual(visitor.brand()["name"], "azimuthphoto.com")

    def test_there_is_always_something_to_show(self):
        with self._with():
            self.assertEqual(visitor.brand()["name"], "Your photographer")


class NoLeakTests(unittest.TestCase):
    def test_a_public_page_is_never_cached_and_never_refers(self):
        headers = visitor.no_leak(Response()).headers
        self.assertEqual(headers["Cache-Control"], "private, no-store")
        self.assertEqual(headers["Referrer-Policy"], "no-referrer")


class AttachmentNameTests(unittest.TestCase):
    def test_it_keeps_the_photographer_s_filename(self):
        self.assertEqual(visitor.attachment_name(1, "IMG_1234.CR2"), "IMG_1234.cr2")

    def test_it_keeps_only_the_last_extension(self):
        self.assertEqual(visitor.attachment_name(1, "beach.2024.jpg"), "beach.2024.jpg")

    def test_a_requested_suffix_replaces_the_original(self):
        self.assertEqual(visitor.attachment_name(1, "IMG.CR2", suffix=".jpg"), "IMG.jpg")

    def test_it_strips_anything_a_filesystem_would_refuse(self):
        self.assertEqual(visitor.attachment_name(1, "a/b:c*d.jpg"), "b_c_d.jpg")

    def test_there_is_always_a_name(self):
        self.assertEqual(visitor.attachment_name(42, ""), "photo-42")
        self.assertEqual(visitor.attachment_name(42, "***"), "photo-42")

    def test_a_long_name_is_bounded(self):
        self.assertLessEqual(len(visitor.attachment_name(1, "x" * 500 + ".jpg")), 184)


class NobodyKeepsAPrivateCopyTests(unittest.TestCase):
    def test_no_surface_answers_these_for_itself(self):
        features = pathlib.Path(__file__).with_name("features")
        strays = [
            f"{path.parent.name}/{path.name}"
            for package in ("publish", "publishing", "share")
            for path in (features / package).glob("*.py")
            if path.name != "visitor.py"
            and any(
                f"\ndef {name}(" in path.read_text(encoding="utf-8", errors="replace")
                for name in ("_brand_payload", "_site_label", "_public_response", "_attachment_name")
            )
        ]
        self.assertEqual(strays, [], f"private copies remain: {strays}")


if __name__ == "__main__":
    unittest.main()
