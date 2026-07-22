from __future__ import annotations

import unittest

from features.collections.suggestions import _public_suggestion


class SuggestionContractTests(unittest.TestCase):
    def _candidate(self, ids: list[int], *, query=None) -> dict:
        return {
            "kind": "theme" if query else "shoot",
            "title": "Sunday mornings",
            "subtitle": f"{len(ids)} photos",
            "reason": "Shared caption tags" if query else "Same shoot folder",
            "count": len(ids),
            "cover_image_id": ids[0],
            "image_ids": ids,
            "_all_ids": ids,
            "_key": "sunday-mornings",
            "_coherence": 0.84,
            "query": query,
        }

    def test_smart_contract_extends_legacy_fields(self):
        suggestion = _public_suggestion(
            self._candidate(list(range(1, 13)), query={"tag": "breakfast"}),
            generated_at=123.0,
        )

        for field in (
            "fingerprint", "kind", "title", "reason", "subtitle", "count",
            "cover_image_id", "image_ids", "query",
        ):
            self.assertIn(field, suggestion)
        self.assertEqual(suggestion["mode"], "smart")
        self.assertEqual(suggestion["generated_at"], 123.0)
        self.assertTrue(suggestion["evidence"])
        self.assertGreater(suggestion["confidence"], 0.0)

    def test_fingerprint_is_stable_as_membership_grows(self):
        before = _public_suggestion(self._candidate(list(range(1, 9))), generated_at=100.0)
        after = _public_suggestion(
            self._candidate(list(range(1, 12))),
            generated_at=200.0,
            previous=before,
        )

        self.assertEqual(before["fingerprint"], after["fingerprint"])
        self.assertEqual(after["change"], {"added": 3, "removed": 0})
        self.assertEqual(after["change_summary"], "3 new photos")

    def test_static_suggestion_never_pretends_to_be_live(self):
        suggestion = _public_suggestion(self._candidate(list(range(1, 9))))

        self.assertEqual(suggestion["mode"], "static")
        self.assertNotIn("query", suggestion)


if __name__ == "__main__":
    unittest.main()
