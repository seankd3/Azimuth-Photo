"""A full tier must not hide the work of the tiers that still have room.

The hub generated nothing for hours with 81,090 photos missing a small
thumbnail. Its large tier was full — room 0 — while small and medium had 2.8GB
and 30GB free. Candidate selection asked for rows missing any tier *with a
budget*, and a full tier still has a budget, so every page filled with rows that
lacked only the large tier. The filter then dropped all of them for having
nowhere to put the result, the wave gave up after 64 empty pages, and the
cursor never reached the photos that genuinely needed a small thumbnail.
"""

import unittest

TIERS = ("sm", "md", "lg")
GB = 1024 ** 3


def tiers_to_ask_for(budgets: dict, room: dict, estimate: int = 100_000) -> list[str]:
    """The selection rule under test, in the same shape as the worker."""

    return [
        size for size in TIERS
        if budgets.get(size, 0) > 0 and room.get(size, 0) >= estimate
    ]


class AskForTiersWithRoomTests(unittest.TestCase):
    def test_a_full_tier_is_not_asked_for(self):
        """The hub's exact shape: large full, small and medium with room."""

        asked = tiers_to_ask_for(
            budgets={"sm": 5 * GB, "md": 54 * GB, "lg": 11 * GB},
            room={"sm": 2_781_668_635, "md": 30_386_816_146, "lg": 0},
        )
        self.assertEqual(asked, ["sm", "md"], "a full tier has budget but nowhere to write")

    def test_tiers_with_room_are_all_asked_for(self):
        asked = tiers_to_ask_for(
            budgets={size: GB for size in TIERS},
            room={size: GB for size in TIERS},
        )
        self.assertEqual(asked, list(TIERS))

    def test_a_tier_without_room_for_even_one_thumbnail_is_dropped(self):
        asked = tiers_to_ask_for(
            budgets={size: GB for size in TIERS},
            room={"sm": GB, "md": 50_000, "lg": GB},
            estimate=100_000,
        )
        self.assertNotIn("md", asked, "half a thumbnail of room is no room")

    def test_every_tier_full_asks_for_nothing(self):
        """Which is the honest answer, and lets the caller fall back rather than spin."""

        asked = tiers_to_ask_for(
            budgets={size: GB for size in TIERS},
            room={size: 0 for size in TIERS},
        )
        self.assertEqual(asked, [])

    def test_the_old_rule_would_have_asked_for_the_full_tier(self):
        """Guards the regression rather than just the fix."""

        budgets = {"sm": 5 * GB, "md": 54 * GB, "lg": 11 * GB}
        old_rule = [size for size in TIERS if budgets.get(size, 0) > 0]
        self.assertIn("lg", old_rule, "the old rule selected on budget alone")
        self.assertNotIn(
            "lg",
            tiers_to_ask_for(budgets, {"sm": 2 * GB, "md": 30 * GB, "lg": 0}),
        )


if __name__ == "__main__":
    unittest.main()
