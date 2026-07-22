from __future__ import annotations

import unittest

import numpy as np

from features.library.preference_model import fit_pairwise_preference, preference_confidence


class PreferenceModelTests(unittest.TestCase):
    def test_pairwise_model_learns_winner_direction(self):
        winners = [np.asarray([1.0, 0.0], dtype=np.float32) for _ in range(8)]
        losers = [np.asarray([-1.0, 0.0], dtype=np.float32) for _ in range(8)]

        vector, accuracy = fit_pairwise_preference(winners, losers)

        self.assertIsNotNone(vector)
        self.assertGreater(float(np.dot(vector, winners[0])), float(np.dot(vector, losers[0])))
        self.assertEqual(accuracy, 1.0)

    def test_repeated_evidence_increases_confidence(self):
        self.assertGreater(preference_confidence(100, 0.9), preference_confidence(5, 0.9))

    def test_chance_agreement_has_no_confidence(self):
        self.assertEqual(preference_confidence(500, 0.5), 0.0)


if __name__ == "__main__":
    unittest.main()
