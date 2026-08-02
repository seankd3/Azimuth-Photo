"""Filling the grid first must not mean never filling anything else.

The browse tier can be permanently short: a hub still working through its own
backlog has no small preview to send yet, and some photos will never have one —
videos, frames too large for the decoder. Requiring 100% before touching the
loupe tier left the previews that make opening a photo instant half-empty
forever. Measured on the owner's laptop: 38,262 medium previews held locally
against 72,844 sitting on the hub, while the browse tier spun.
"""

import asyncio
import unittest


class FakePrefetcher:
    """The decision under test, with the two fetches recorded."""

    BROWSE_SIZE = "sm"
    LOUPE_SIZE = "md"

    def __init__(self, *, browse_complete: bool, browse_stores: int, state: str = "idle"):
        self._browse_complete = browse_complete
        self._browse_stores = browse_stores
        self._state = state
        self.asked: list[str] = []

    async def browse_tier_complete(self) -> bool:
        return self._browse_complete

    async def prefetch_once(self, *, size: str, limit: int = 500) -> dict:
        self.asked.append(size)
        if size == self.BROWSE_SIZE:
            return {"stored_last_pass": self._browse_stores, "state": self._state}
        return {"stored_last_pass": 0, "state": "idle"}

    prefetch_browse_first = None  # bound below


from features.sync.prefetch import ThumbPrefetcher  # noqa: E402

FakePrefetcher.prefetch_browse_first = ThumbPrefetcher.prefetch_browse_first


class BrowseFirstTests(unittest.TestCase):
    def _run(self, prefetcher: FakePrefetcher) -> list[str]:
        asyncio.run(prefetcher.prefetch_browse_first())
        return prefetcher.asked

    def test_a_browse_pass_that_found_work_keeps_the_pass(self):
        asked = self._run(FakePrefetcher(browse_complete=False, browse_stores=500))
        self.assertEqual(asked, ["sm"], "the grid still wins while there is work")

    def test_a_browse_pass_the_hub_cannot_feed_moves_to_the_loupe_tier(self):
        """The laptop's case: sm short, but the hub has nothing more to send."""

        asked = self._run(FakePrefetcher(browse_complete=False, browse_stores=0))
        self.assertEqual(asked, ["sm", "md"])

    def test_a_complete_browse_tier_goes_straight_to_the_loupe_tier(self):
        asked = self._run(FakePrefetcher(browse_complete=True, browse_stores=0))
        self.assertEqual(asked, ["md"])

    def test_a_full_disk_is_not_treated_as_nothing_to_fetch(self):
        """At budget, moving to a bigger tier would only make it worse."""

        asked = self._run(
            FakePrefetcher(browse_complete=False, browse_stores=0, state="budget")
        )
        self.assertEqual(asked, ["sm"])


if __name__ == "__main__":
    unittest.main()
