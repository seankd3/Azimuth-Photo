from __future__ import annotations

import unittest

from core.search_fusion import candidate_evidence, fused_candidate_scores
from core.search_planning import plan_search


class SearchPlanningTests(unittest.TestCase):
    def test_visual_language_favors_visual_similarity(self):
        plan = plan_search("moody golden hour silhouettes")

        self.assertEqual(plan.intent, "visual")
        self.assertGreater(plan.source_weights["embedding"], plan.source_weights["captions"])
        self.assertEqual(plan.recency_weight, 0.0)

    def test_text_in_photo_favors_understanding_channel(self):
        plan = plan_search("the receipt with handwriting")

        self.assertEqual(plan.intent, "textual")
        self.assertGreater(plan.source_weights["captions"], plan.source_weights["metadata"])

    def test_recency_is_only_a_prior_when_requested(self):
        self.assertEqual(plan_search("beach vacation").recency_weight, 0.0)
        self.assertGreater(plan_search("recent beach vacation").recency_weight, 0.0)

    def test_weighted_fusion_and_evidence_are_stable(self):
        sources = {
            "metadata": [1, 2],
            "captions": [2, 3],
            "embedding": [3, 2, 4],
        }
        scores, used = fused_candidate_scores(
            ranked_sources=sources,
            embedding_scores={3: 0.9, 2: 0.7, 4: 0.6},
            source_weights=plan_search("colorful sunset").source_weights,
        )
        evidence = candidate_evidence(sources, scores)

        self.assertEqual(used, ["captions", "embedding", "metadata"])
        self.assertEqual(next(iter(scores)), 3)
        self.assertEqual(evidence[2]["signals"], ["captions", "embedding", "metadata"])
        self.assertEqual(evidence[1]["signals"], ["metadata"])


if __name__ == "__main__":
    unittest.main()
