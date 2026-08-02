"""A library on this machine does not stop responding — it gets busy.

The desktop app showed "The library isn't responding." whenever a read took
longer than ten seconds. That is a network app's excuse, and this library is on
the same machine as the person reading it. What is actually happening during a
first sync is knowable — the app polls it every three seconds for the sync chip
— so it should be said.
"""

import pathlib
import re
import unittest

API = pathlib.Path(__file__).with_name("static") / "js" / "desktop" / "api.js"
CHIP = pathlib.Path(__file__).with_name("static") / "js" / "desktop" / "sync_chip.js"


class HonestFailureMessageTests(unittest.TestCase):
    def setUp(self):
        self.api = API.read_text(encoding="utf-8")
        self.chip = CHIP.read_text(encoding="utf-8")

    def test_the_network_excuse_is_gone(self):
        self.assertNotIn("isn't responding", self.api)

    def test_a_busy_library_says_it_is_busy(self):
        self.assertIn("busy", self.api.lower())

    def test_a_catching_up_library_says_how_much_is_left(self):
        self.assertIn("still catching up", self.api)
        self.assertIn("photos to go", self.api)

    def test_the_count_comes_from_what_the_app_already_polls(self):
        self.assertIn("noteLibraryCatchingUp", self.chip)
        self.assertIn("library_total", self.chip)

    def test_a_real_http_error_still_reports_its_status(self):
        self.assertRegex(self.api, r"Request failed \(\$\{status\}\)")

    def test_the_catching_up_note_goes_stale_rather_than_lying_forever(self):
        """A count from ten minutes ago is not the truth either."""

        self.assertRegex(self.api, r"catchingUpUntil\s*=\s*Date\.now\(\)\s*\+")

    def test_nothing_left_to_catch_up_clears_the_note(self):
        clearing = re.search(r"if \(!left \|\| !total\) \{(.+?)\}", self.api, re.S)
        self.assertIsNotNone(clearing, "the note must be clearable")
        self.assertIn("catchingUpText = ''", clearing.group(1))


if __name__ == "__main__":
    unittest.main()
