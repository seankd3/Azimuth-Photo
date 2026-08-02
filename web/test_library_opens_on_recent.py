"""The library opens on the photos you just took.

Rating order is something you choose. Opening on it means every session starts
by undoing a decision nobody made — and on a library still learning its ratings,
the top of that order is close to arbitrary. Newest first is the answer to
"what did I just shoot", which is why the app is open.
"""

import pathlib
import re
import unittest

STATE = pathlib.Path(__file__).with_name("static") / "js" / "desktop" / "state.js"
PANEL = pathlib.Path(__file__).with_name("static") / "js" / "desktop" / "panel.js"


class DefaultViewTests(unittest.TestCase):
    def setUp(self):
        self.state = STATE.read_text(encoding="utf-8")
        self.panel = PANEL.read_text(encoding="utf-8")

    def test_the_library_opens_newest_first(self):
        self.assertRegex(self.state, r"DEFAULT_SORT\s*=\s*'date_taken'")

    def test_the_opening_scope_uses_that_name(self):
        opening = re.search(r"sort:\s*(\w+),", self.state)
        self.assertIsNotNone(opening)
        self.assertEqual(opening.group(1), "DEFAULT_SORT")

    def test_the_default_is_named_once(self):
        """One value in one place, not a literal repeated down the file."""

        self.assertEqual(len(re.findall(r"DEFAULT_SORT\s*=\s*'", self.state)), 1)

    def test_no_stray_rating_default_is_left_behind(self):
        """The restores fall back to the app default, not to a hard-coded sort."""

        self.assertNotIn("|| 'elo'", self.state)

    def test_best_of_still_ranks_by_rating(self):
        """Best of is a rating question; only the opening view changed."""

        self.assertIn("patchScope({ sort: 'elo' })", self.state)

    def test_the_sidebar_marks_recent_as_where_you_are(self):
        self.assertIn("recentActive", self.panel)
        self.assertIn("sortBase() === 'date_taken'", self.panel)


if __name__ == "__main__":
    unittest.main()
