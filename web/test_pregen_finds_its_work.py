"""A photo with no thumbnail must be offered for generation.

The hub sat with 81,090 photos lacking a small thumbnail — room in every tier,
226GB on the volume, 9GB of RAM, half the cores idle — and its generator scanned
roughly two pages a second, forever, finding nothing. This pins which branch of
the candidate filter can swallow a photo that genuinely needs work, so the
condition can be recognised in seconds instead of by watching production.
"""

import unittest

from thumbnails.pregen_candidates import bulk_candidate_signatures

TIERS = ("sm", "md", "lg")
GB = 1024 ** 3


class Row(dict):
    """Rows arrive as sqlite3.Row; only item access is used."""


def row(image_id=1, filepath="/lib/a.jpg", file_size=5_000_000, modified=1_700_000_000.0):
    return Row(id=image_id, filepath=filepath, file_size=file_size, file_modified_at=modified)


def signatures(
    *,
    budgets=None,
    room=None,
    on_disk=(),
    retry_after=None,
    replace_stale=False,
):
    """Call the real filter with the conditions under test."""

    budgets = {size: GB for size in TIERS} if budgets is None else budgets
    room = {size: GB for size in TIERS} if room is None else room
    return bulk_candidate_signatures(
        row(),
        room,
        budgets,
        thumb_tiers=TIERS,
        replace_stale_thumbnails=replace_stale,
        fast_disk_has=lambda size, image_id, sig=None: size in on_disk,
        build_catalog_source_signature=lambda path, size, image_id, fsize, mtime: (
            f"sig-{size}", fsize, False,
        ),
        estimated_tier_bytes=lambda size: 100_000,
        retry_after=retry_after or {},
        now=1_700_000_000.0,
    )


class PregenFindsItsWorkTests(unittest.TestCase):
    def test_a_photo_with_nothing_cached_is_a_candidate(self):
        needed, _size = signatures()
        self.assertEqual(set(needed), set(TIERS), "every tier is missing, so every tier is work")

    def test_a_photo_missing_only_the_small_tier_is_still_a_candidate(self):
        """The hub's actual shape: 66k had md and lg, 81k lacked sm."""

        needed, _size = signatures(on_disk=("md", "lg"))
        self.assertIn("sm", needed, "a missing small thumbnail is work even when the others exist")

    def test_a_fully_cached_photo_is_not_a_candidate(self):
        needed, _size = signatures(on_disk=TIERS)
        self.assertEqual(needed, {})

    def test_no_tier_has_budget_swallows_a_photo_that_needs_work(self):
        """The failure mode that produces an endless empty scan.

        With every budget at zero there are no active tiers, so the filter
        returns nothing for a photo with nothing cached — indistinguishable, to
        the scan loop, from a photo that is already done.
        """

        needed, _size = signatures(budgets={size: 0 for size in TIERS})
        self.assertEqual(needed, {}, "documents the swallow; the loop cannot tell this apart")

    def test_no_room_left_swallows_a_photo_that_needs_work(self):
        needed, _size = signatures(room={size: 0 for size in TIERS})
        self.assertEqual(needed, {}, "budget above zero but no room reads as 'nothing to do'")

    def test_a_tier_in_retry_backoff_is_skipped_but_others_still_offered(self):
        backoff = {("sm", 1, "sig-sm"): 1_700_000_600.0}
        needed, _size = signatures(retry_after=backoff)
        self.assertNotIn("sm", needed)
        self.assertIn("md", needed, "one tier backing off must not hide the rest")


if __name__ == "__main__":
    unittest.main()
