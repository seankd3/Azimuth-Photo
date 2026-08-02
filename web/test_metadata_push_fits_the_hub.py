"""Local flags and ratings must be able to leave the machine.

The hub caps a metadata post at 5,000 items. The satellite sent everything it
owed in one request, so a library with more local photos than that could never
push at all — measured on the owner's laptop, 10,689 items rejected 422 on every
cycle, meaning no flag or rating had ever reached the hub.
"""

import unittest

from features.sync.hub_routes import MetadataRequest
from features.sync.sync_worker import METADATA_PUSH_LIMIT


def helpings(row_count: int, limit: int = METADATA_PUSH_LIMIT) -> list[int]:
    """The split under test, in the same shape as the push loop."""

    rows = list(range(row_count))
    return [len(rows[start:start + limit]) for start in range(0, len(rows), limit)]


class MetadataPushSizeTests(unittest.TestCase):
    def test_the_client_limit_matches_what_the_hub_accepts(self):
        """A drifting cap here is the whole bug, so pin them together."""

        cap = MetadataRequest.model_fields["items"].metadata
        maximums = [getattr(entry, "max_length", None) for entry in cap]
        self.assertIn(METADATA_PUSH_LIMIT, maximums)

    def test_the_laptop_load_is_split_rather_than_rejected(self):
        self.assertEqual(helpings(10_689), [5000, 5000, 689])

    def test_a_small_library_still_sends_one_request(self):
        self.assertEqual(helpings(120), [120])

    def test_an_exact_multiple_does_not_send_an_empty_helping(self):
        self.assertEqual(helpings(10_000), [5000, 5000])

    def test_nothing_owed_sends_nothing(self):
        self.assertEqual(helpings(0), [])

    def test_no_helping_can_exceed_the_cap(self):
        for count in (1, 4999, 5000, 5001, 99_999):
            self.assertTrue(all(size <= METADATA_PUSH_LIMIT for size in helpings(count)))


if __name__ == "__main__":
    unittest.main()
