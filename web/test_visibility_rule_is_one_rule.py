"""One answer to "is this photo in the library", in one place.

The rule — source included, not trashed, file where the catalog says it is —
was hand-written in 192 places across twenty files, in three different
strengths, while a helper holding the correct version sat unused. A rule
written in two hundred places is two hundred chances to disagree, and it cannot
be repaired or reasoned about.

Adoption is only safe if the helper emits exactly what it replaces, so that is
what these assert.
"""

import unittest

from data.repositories.catalog import active_image_condition, visible_image_condition


class VisibilityRuleTests(unittest.TestCase):
    def test_the_whole_rule_is_what_the_queries_used_to_spell_out(self):
        self.assertEqual(
            active_image_condition(),
            "s.included = 1 AND i.status IN ('kept', 'maybe') AND i.missing_at IS NULL",
        )

    def test_the_photo_half_matches_a_scoped_query(self):
        self.assertEqual(
            visible_image_condition(),
            "i.status IN ('kept', 'maybe') AND i.missing_at IS NULL",
        )

    def test_a_single_table_query_can_name_columns_bare(self):
        self.assertEqual(
            visible_image_condition(""),
            "status IN ('kept', 'maybe') AND missing_at IS NULL",
        )

    def test_aliases_are_honoured(self):
        self.assertEqual(
            active_image_condition("img", "src"),
            "src.included = 1 AND img.status IN ('kept', 'maybe') AND img.missing_at IS NULL",
        )

    def test_the_whole_rule_contains_the_photo_half(self):
        """They must never drift apart, whatever either is edited to say."""

        self.assertIn(visible_image_condition(), active_image_condition())

    def test_maybe_counts_as_in_the_library(self):
        """A photo the owner has not decided on is still theirs to see."""

        for rule in (active_image_condition(), visible_image_condition()):
            self.assertIn("'maybe'", rule)

    def test_the_rule_still_excludes_the_two_things_it_must(self):
        for rule in (active_image_condition(), visible_image_condition()):
            self.assertIn("missing_at IS NULL", rule, "a lost file is not in the library")
        self.assertIn("included = 1", active_image_condition(), "an excluded source is not either")


if __name__ == "__main__":
    unittest.main()
