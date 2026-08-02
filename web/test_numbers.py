"""One rule for making a number out of whatever arrived.

Values reach this app as strings from XMP, as None from a half-filled catalog
row, as NaN from a pipeline that divided by nothing. Eleven modules had each
written their own four lines to cope, in three shapes that were one shape.

A non-finite number is not a number here: it cannot be drawn, and `json.dumps`
writes it as a bare `NaN` that a strict client refuses to parse.
"""

import unittest

from core import numbers
from core.numbers import field, integer, number, setting


class NumberTests(unittest.TestCase):
    def test_a_number_is_itself(self):
        self.assertEqual(number(1.5), 1.5)

    def test_a_number_written_as_text_is_still_a_number(self):
        """XMP and Lightroom hand these over as strings."""

        self.assertEqual(number("2.25"), 2.25)

    def test_something_that_is_not_a_number_falls_back(self):
        self.assertEqual(number("later", 0.0), 0.0)
        self.assertIsNone(number(object()))

    def test_missing_falls_back(self):
        self.assertIsNone(number(None))
        self.assertEqual(number(None, 3.0), 3.0)

    def test_a_number_that_is_not_finite_is_not_a_number(self):
        """A NaN through a pipeline is a black frame; treat it as absent."""

        for broken in (float("nan"), float("inf"), float("-inf")):
            self.assertEqual(number(broken, 1.0), 1.0)

    def test_zero_survives(self):
        """Zero is a real value; a falsy check here would eat it."""

        self.assertEqual(number(0, 9.0), 0.0)
        self.assertEqual(number("0", 9.0), 0.0)


class SettingTests(unittest.TestCase):
    def test_it_reads_by_key(self):
        self.assertEqual(setting({"Exposure2012": "1.25"}, "Exposure2012"), 1.25)

    def test_a_missing_key_falls_back(self):
        self.assertEqual(setting({}, "Contrast2012", 4.0), 4.0)

    def test_no_settings_at_all_falls_back(self):
        self.assertEqual(setting(None, "Contrast2012", 4.0), 4.0)

    def test_it_is_the_same_rule(self):
        """Defined in terms of `number`, so the two can never disagree."""

        self.assertEqual(setting({"k": float("nan")}, "k", 5.0), number(float("nan"), 5.0))


class IntegerTests(unittest.TestCase):
    def test_it_reads_whole_numbers(self):
        self.assertEqual(integer("7"), 7)
        self.assertEqual(integer(7.9), 7)

    def test_anything_else_falls_back(self):
        self.assertEqual(integer(None, 3), 3)
        self.assertEqual(integer(float("nan"), 3), 3)


class FieldTests(unittest.TestCase):
    """Rows arrive as dicts from JSON and as Row objects from SQLite."""

    class _RowLike:
        def __getitem__(self, key):
            if key != "elo":
                raise KeyError(key)
            return 1300

    def test_it_reads_a_mapping(self):
        self.assertEqual(field({"elo": 1200}, "elo"), 1200)

    def test_it_reads_something_that_only_supports_indexing(self):
        self.assertEqual(field(self._RowLike(), "elo"), 1300)
        self.assertIsNone(field(self._RowLike(), "missing"))

    def test_a_missing_field_falls_back(self):
        self.assertEqual(field({}, "elo", 1200), 1200)
        self.assertEqual(field(None, "elo", 1200), 1200)


class NobodyKeepsAPrivateCopyTests(unittest.TestCase):
    def test_no_module_redefines_the_rule(self):
        import pathlib

        web = pathlib.Path(__file__).parent
        candidates = list((web / "features" / "develop").glob("*.py"))
        candidates += [web / "helpers.py", web / "core" / "responses.py"]
        strays = [
            path.name
            for path in candidates
            if any(
                f"\ndef {name}(" in path.read_text(encoding="utf-8", errors="replace")
                for name in ("_number", "_as_float", "_as_int", "_get")
            )
        ]
        self.assertEqual(strays, [], f"private copies remain: {strays}")

    def test_a_row_field_can_never_reach_a_client_as_nan(self):
        """json.dumps writes a bare NaN, which strict parsers refuse."""

        import json

        payload = json.dumps({"aspect_ratio": numbers.number(float("nan"), 1.5)})
        self.assertNotIn("NaN", payload)


if __name__ == "__main__":
    unittest.main()
