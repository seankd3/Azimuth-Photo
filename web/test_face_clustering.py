from __future__ import annotations

import unittest

import numpy as np

from features.people.clustering import FaceVector, confident_person_match, group_centroid, group_face_batch


def _vector(*values: float) -> np.ndarray:
    value = np.asarray(values, dtype=np.float32)
    return value / np.linalg.norm(value)


class FaceClusteringTests(unittest.TestCase):
    def test_batch_grouping_is_order_independent(self):
        faces = [
            FaceVector(1, 10, _vector(1.0, 0.02)),
            FaceVector(2, 11, _vector(0.99, 0.04)),
            FaceVector(3, 12, _vector(0.01, 1.0)),
        ]

        forward = [{face.face_id for face in group} for group in group_face_batch(faces, similarity_threshold=0.8)]
        reverse = [{face.face_id for face in group} for group in group_face_batch(list(reversed(faces)), similarity_threshold=0.8)]

        self.assertEqual({frozenset(group) for group in forward}, {frozenset(group) for group in reverse})
        self.assertIn({1, 2}, forward)

    def test_faces_from_same_photo_never_join(self):
        faces = [
            FaceVector(1, 10, _vector(1.0, 0.0)),
            FaceVector(2, 10, _vector(1.0, 0.001)),
        ]

        groups = group_face_batch(faces, similarity_threshold=0.5)

        self.assertEqual(len(groups), 2)

    def test_ambiguous_identity_match_creates_reviewable_new_person(self):
        centroid = _vector(1.0, 0.0)
        people = {1: _vector(0.99, 0.05), 2: _vector(0.99, -0.05)}

        person_id, similarity = confident_person_match(
            centroid, people, similarity_threshold=0.8, ambiguity_margin=0.04
        )

        self.assertEqual(person_id, 0)
        self.assertGreater(similarity, 0.9)

    def test_existing_identity_in_same_photo_is_excluded(self):
        centroid = group_centroid([FaceVector(1, 10, _vector(1.0, 0.0))])
        person_id, _similarity = confident_person_match(
            centroid,
            {1: _vector(1.0, 0.0), 2: _vector(0.9, 0.2)},
            similarity_threshold=0.8,
            excluded_person_ids={1},
        )

        self.assertEqual(person_id, 2)


if __name__ == "__main__":
    unittest.main()
