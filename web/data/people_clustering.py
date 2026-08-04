"""Order-independent face grouping and conservative identity matching."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class FaceVector:
    face_id: int
    image_id: int
    vector: np.ndarray


def _normalized_mean(vectors: list[np.ndarray]) -> np.ndarray:
    centroid = np.mean(np.stack(vectors), axis=0).astype(np.float32)
    norm = float(np.linalg.norm(centroid))
    return centroid / norm if norm > 0 else centroid


def group_face_batch(
    faces: list[FaceVector],
    *,
    similarity_threshold: float,
    strong_link_margin: float = 0.08,
) -> list[list[FaceVector]]:
    """Group a whole batch using mutual-neighbor links, independent of row order."""

    if not faces:
        return []
    matrix = np.stack([face.vector for face in faces])
    similarities = matrix @ matrix.T
    np.fill_diagonal(similarities, -1.0)
    nearest = similarities.argmax(axis=1)
    parents = list(range(len(faces)))
    images = [{face.image_id} for face in faces]

    def root(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    def union(left: int, right: int) -> None:
        left_root, right_root = root(left), root(right)
        if left_root == right_root or images[left_root] & images[right_root]:
            return
        parents[right_root] = left_root
        images[left_root].update(images[right_root])

    threshold = float(similarity_threshold)
    strong_threshold = min(0.99, threshold + float(strong_link_margin))
    for left in range(len(faces)):
        for right in range(left + 1, len(faces)):
            if faces[left].image_id == faces[right].image_id:
                continue
            similarity = float(similarities[left, right])
            mutual = int(nearest[left]) == right and int(nearest[right]) == left
            if similarity >= strong_threshold or (similarity >= threshold and mutual):
                union(left, right)

    grouped: dict[int, list[FaceVector]] = {}
    for index, face in enumerate(faces):
        grouped.setdefault(root(index), []).append(face)
    return sorted(grouped.values(), key=lambda group: min(face.face_id for face in group))


def group_centroid(group: list[FaceVector]) -> np.ndarray:
    return _normalized_mean([face.vector for face in group])


def confident_person_match(
    centroid: np.ndarray,
    person_centroids: dict[int, np.ndarray],
    *,
    similarity_threshold: float,
    ambiguity_margin: float = 0.04,
    excluded_person_ids: set[int] | None = None,
) -> tuple[int, float]:
    """Match only when the best identity is both strong and unambiguous."""

    excluded = excluded_person_ids or set()
    ranked = sorted(
        (
            (person_id, float(np.dot(centroid, person_centroid)))
            for person_id, person_centroid in person_centroids.items()
            if person_id not in excluded
        ),
        key=lambda item: item[1],
        reverse=True,
    )
    if not ranked or ranked[0][1] < float(similarity_threshold):
        return 0, ranked[0][1] if ranked else -1.0
    if len(ranked) > 1 and ranked[0][1] - ranked[1][1] < float(ambiguity_margin):
        return 0, ranked[0][1]
    return ranked[0]
