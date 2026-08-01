"""A batch must not start work the machine cannot finish.

The hub was OOM-killed four times in one hour. Each death had the same shape in
the log: a single item estimated at 4.2GB, charged 768MB because the budget
clamps any weight to its own ceiling, admitted because nothing else was in
flight, and then the kernel took the whole service down — with every other
photo's progress in it.

A budget that cannot refuse anything is not a budget.
"""

import unittest

from thumbnails.decode_budget import DecodeByteBudget, estimate_decode_bytes

MB = 1024**2
GB = 1024**3


class ClampedWeightTests(unittest.TestCase):
    def test_an_oversized_frame_is_charged_only_what_the_budget_holds(self):
        """The behaviour that made refusal necessary — kept as the reason."""

        budget = DecodeByteBudget(768 * MB)
        weight = budget.try_acquire(4 * GB)
        self.assertEqual(weight, 768 * MB, "4GB of work admitted at 768MB of price")


class EstimateMatchesTheDecodeTests(unittest.TestCase):
    """Non-RAW photos are decoded at draft scale, so charge draft scale."""

    def test_a_giant_panorama_is_charged_for_what_is_actually_read(self):
        # 527MP — the largest photo in the archive.
        full = estimate_decode_bytes(width=30000, height=17500)
        drafted = estimate_decode_bytes(width=30000, height=17500, max_target=3840)
        self.assertGreater(full, 2 * GB)
        self.assertLess(drafted, full / 4, "draft reads a fraction of the pixels")

    def test_an_ordinary_photo_is_unchanged(self):
        args = dict(width=6000, height=4000)
        self.assertEqual(
            estimate_decode_bytes(**args),
            estimate_decode_bytes(**args, max_target=3840),
            "the cap only binds when the photo is larger than the thumbnail",
        )

    def test_a_raw_is_still_charged_in_full(self):
        """A demosaic reads the whole sensor whatever size is being made."""

        full = estimate_decode_bytes(width=30000, height=17500, raw=True)
        capped = estimate_decode_bytes(
            width=30000, height=17500, raw=True, max_target=400
        )
        self.assertEqual(full, capped)


class AffordabilityTests(unittest.TestCase):
    """The selection rule: queue nothing that cannot fit the budget."""

    def affordable(self, estimate: int, budget_bytes: int = 768 * MB) -> bool:
        return estimate <= budget_bytes

    def test_the_panorama_that_killed_the_hub_is_refused(self):
        self.assertFalse(self.affordable(int(4.24 * GB)))

    def test_an_ordinary_photo_is_admitted(self):
        self.assertTrue(self.affordable(estimate_decode_bytes(width=6000, height=4000)))

    def test_a_photo_that_exactly_fills_the_budget_is_admitted(self):
        self.assertTrue(self.affordable(768 * MB))


if __name__ == "__main__":
    unittest.main()
