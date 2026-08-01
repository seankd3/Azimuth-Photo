"""A backfill must be able to find its own work.

Measured on the hub: 79,000 images still had no small thumbnail, yet the
generator reported `pending_total=0` and sat idle on a half-idle machine. It
scans eight rows a page and gives up after sixty-four empty pages — 512 rows
out of 147,482 — so a warm stretch of the library stranded it.

Searching narrowly is only needed while pages are productive, because the
cursor advances by the whole page and unconsumed rows would be skipped. A page
that yields nothing contains only already-warm rows, so there is nothing to
skip and the search can widen.
"""

import unittest


def widened(scan_batch: int, need: int, empty_scan_batches: int, ceiling: int = 4096) -> int:
    """The page-size rule under test, in the same shape as the worker."""

    candidate = max(1, min(scan_batch, need))
    if empty_scan_batches:
        candidate = min(ceiling, max(candidate, scan_batch * (1 << min(empty_scan_batches, 9))))
    return candidate


class WarmRegionScanTests(unittest.TestCase):
    def test_a_productive_page_stays_narrow(self):
        """While rows are actionable the cursor must not outrun what is consumed."""

        self.assertEqual(widened(scan_batch=8, need=8, empty_scan_batches=0), 8)
        self.assertEqual(widened(scan_batch=8, need=3, empty_scan_batches=0), 3,
                         "never fetch past the room left to generate")

    def test_the_search_widens_once_pages_come_back_empty(self):
        first = widened(scan_batch=8, need=8, empty_scan_batches=1)
        later = widened(scan_batch=8, need=8, empty_scan_batches=5)
        self.assertGreater(first, 8)
        self.assertGreater(later, first, "a longer desert should be crossed faster")

    def test_it_is_bounded(self):
        self.assertLessEqual(widened(scan_batch=8, need=8, empty_scan_batches=64), 4096)
        self.assertLessEqual(widened(scan_batch=512, need=512, empty_scan_batches=64), 4096)

    def test_the_old_behaviour_could_not_cross_the_library(self):
        """The regression this guards: 64 empty pages of 8 rows is 512 rows."""

        old_rows_examined = 8 * 64
        self.assertLess(old_rows_examined, 1000)

        widened_total = sum(
            widened(scan_batch=8, need=8, empty_scan_batches=n) for n in range(1, 65)
        )
        self.assertGreater(
            widened_total, 147_482,
            "one wave must be able to cross a 147k-photo catalog looking for work",
        )


if __name__ == "__main__":
    unittest.main()
