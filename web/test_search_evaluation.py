import json
import tempfile
import unittest
from pathlib import Path

from features.search import evaluation


class SearchEvaluationTests(unittest.TestCase):
    def test_loads_versioned_graded_judgments(self):
        payload = {
            "version": 1,
            "queries": [{
                "query": "  Maya   laughing ",
                "category": "people_action",
                "judgments": {"10": 3, "11": 1, "12": 0},
            }],
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "quality.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            loaded = evaluation.load_evaluation_set(path)

        self.assertEqual(loaded[0].query, "Maya laughing")
        self.assertEqual(loaded[0].judgments, {10: 3, 11: 1, 12: 0})
        self.assertTrue(loaded[0].judged)

    def test_legacy_query_hints_are_not_mistaken_for_ground_truth(self):
        loaded = evaluation.parse_evaluation_set([{
            "query": "sunset water",
            "relevant_hints": {"folders": ["*lake*"]},
        }])

        self.assertFalse(loaded[0].judged)
        self.assertEqual(loaded[0].judgments, {})

    def test_metrics_reward_graded_relevance_and_measure_recall(self):
        judgments = {1: 3, 2: 2, 3: 1}

        perfect = evaluation.ndcg_at([1, 2, 3], judgments, 3)
        reversed_score = evaluation.ndcg_at([3, 2, 1], judgments, 3)

        self.assertAlmostEqual(perfect, 1.0)
        self.assertLess(reversed_score, perfect)
        self.assertAlmostEqual(evaluation.recall_at([1, 9, 2], judgments, 3), 2 / 3)
        self.assertAlmostEqual(evaluation.reciprocal_rank([9, 2, 1], judgments), 0.5)

    def test_aggregate_excludes_unjudged_queries_from_quality_scores(self):
        judged = evaluation.EvaluationQuery("cat", "object", {7: 3})
        draft = evaluation.EvaluationQuery("concert", "event", {})
        rows = [
            evaluation.evaluate_query(
                judged,
                [7, 8],
                latency_ms=100,
                search_mode="fused",
                search_sources=["embedding", "captions"],
            ),
            evaluation.evaluate_query(
                draft,
                [9],
                latency_ms=300,
                search_mode="metadata",
                search_sources=["metadata"],
            ),
        ]

        report = evaluation.aggregate_evaluations(rows)

        self.assertEqual(report["query_count"], 2)
        self.assertEqual(report["judged_query_count"], 1)
        self.assertEqual(report["unjudged_query_count"], 1)
        self.assertEqual(report["overall"]["ndcg@10"], 1.0)
        self.assertEqual(report["latency_ms"]["p50"], 200.0)
        self.assertEqual(report["search_modes"], {"fused": 1, "metadata": 1})


if __name__ == "__main__":
    unittest.main()
