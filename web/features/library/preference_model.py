"""Regularized pairwise preference learning over photo embeddings."""

from __future__ import annotations

import math

import numpy as np


def _sigmoid(values: np.ndarray) -> np.ndarray:
    clipped = np.clip(values, -30.0, 30.0)
    return 1.0 / (1.0 + np.exp(-clipped))


def fit_pairwise_preference(
    winner_vectors: list[np.ndarray],
    loser_vectors: list[np.ndarray],
    *,
    epochs: int = 4,
    batch_size: int = 512,
    learning_rate: float = 0.2,
    l2: float = 0.02,
) -> tuple[np.ndarray | None, float]:
    """Fit logistic preference direction without materializing the full dataset."""

    if not winner_vectors or len(winner_vectors) != len(loser_vectors):
        return None, 0.0
    dimension = int(np.asarray(winner_vectors[0]).size)
    if dimension <= 0:
        return None, 0.0
    weights = np.zeros(dimension, dtype=np.float32)
    count = len(winner_vectors)
    batch_size = max(1, int(batch_size))
    for epoch in range(max(1, int(epochs))):
        step = float(learning_rate) / math.sqrt(epoch + 1.0)
        for start in range(0, count, batch_size):
            winners = np.stack(winner_vectors[start:start + batch_size]).astype(np.float32)
            losers = np.stack(loser_vectors[start:start + batch_size]).astype(np.float32)
            differences = winners - losers
            probabilities = _sigmoid(differences @ weights)
            gradient = ((probabilities - 1.0)[:, None] * differences).mean(axis=0)
            gradient += float(l2) * weights
            weights -= step * gradient.astype(np.float32)

    norm = float(np.linalg.norm(weights))
    if not np.isfinite(norm) or norm <= 0:
        return None, 0.0
    normalized = (weights / norm).astype(np.float32)
    correct = 0
    for start in range(0, count, batch_size):
        winners = np.stack(winner_vectors[start:start + batch_size]).astype(np.float32)
        losers = np.stack(loser_vectors[start:start + batch_size]).astype(np.float32)
        correct += int(np.count_nonzero((winners - losers) @ normalized > 0.0))
    return normalized, correct / count


def preference_confidence(pair_count: int, pairwise_accuracy: float) -> float:
    """Calibrate trust from evidence volume and learned pair agreement."""

    volume = min(1.0, math.log1p(max(0, int(pair_count))) / math.log1p(500))
    skill = max(0.0, min(1.0, (float(pairwise_accuracy) - 0.5) * 2.0))
    return round(volume * skill, 4)
