"""Taste: one direction in the embedding space, fitted to what you picked.

Two numbers exist for a photograph and they answer different questions.

**Strength** is what you measured. It comes only from rounds the photograph was
actually in, and it knows things no model can: the frame where the eyes are
open, the one that is a hair sharper, the better expression. Measured on this
archive, a vector explains about a third of the variance in the owner's
ranking — so two thirds of it lives here and nowhere else.

**Taste** is what the direction predicts. `w` is one vector of the same length
as an embedding, fitted so that `features . w` tracks strength, and it covers
every photograph including the 136,000 never seen. It cannot know the things
above; it knows what the features can express.

So the score is neither alone:

    weight = seen / (seen + TRUST)
    score  = weight * strength + (1 - weight) * predicted

A photograph you have never judged is entirely the prediction. One you have
judged twenty times is almost entirely your own verdict. Nothing is stored, so
nothing can be stale, and one more round re-fits `w` and moves all 157,000 at
once — which is the "rank ten centroids and thousands move" behaviour, arrived
at by not building it.

Measured decisions behind the shape, none of them guesses:

* **Linear, not an MLP.** A 64-unit and a 256x64 head both lose to ridge
  (+0.560 and +0.577 against +0.588) on 16,436 photographs. Taste, as far as
  these features express it, is one axis.
* **The encoder is not the bottleneck.** Qwen3-VL 8B, nine times the size and
  3.6x the dimensions, ties SigLIP-2 on the owner's own duels (67.9% against
  68.3%) and loses on Elo (+0.687 against +0.714).
* **Genre is roughly half of it.** Overall the direction reaches +0.598, but
  ranking like-for-like inside a cluster it reaches +0.350. It separates
  portraits from flat documentation more confidently than it separates two
  portraits — which is exactly why strength has to keep its half of the job.
"""

from __future__ import annotations

# How many rounds before your own verdict outweighs the prediction. At `seen ==
# TRUST` they count equally. Six is deliberately small: the direction explains
# about a third of the variance, so a handful of real rounds is worth more than
# it is, and every round after that only widens the gap.
TRUST = 6

# Ridge penalty. The features are unit vectors of 1,152 numbers fitted against
# a few thousand strengths, so without it the direction memorises rather than
# generalises.
PENALTY = 1.0


def direction(strengths: dict[str, float], subjects: list[str], vectors):
    """`w`, from the photographs that have both a strength and a vector.

    Returns None when there is nothing to fit — no judgements yet, or no
    embeddings yet. That is a real state on a fresh library and not a failure:
    the caller falls back to strength alone, which is correct rather than
    degraded.
    """

    import numpy as np

    known = [(i, strengths[h]) for i, h in enumerate(subjects) if h in strengths]
    if len(known) < 8:
        return None
    rows = np.asarray([i for i, _ in known])
    target = np.asarray([s for _, s in known], dtype=np.float64)
    features = np.asarray(vectors[rows], dtype=np.float64)

    centre = target.mean()
    gram = features.T @ features + PENALTY * np.eye(features.shape[1])
    return np.linalg.solve(gram, features.T @ (target - centre)), centre


def scores(strengths: dict[str, float], seen: dict[str, int],
           subjects: list[str], vectors) -> dict[str, float]:
    """Every photograph's score: measured where you measured, predicted elsewhere.

    One matrix multiply for the whole library. There is no propagation pass, no
    queue and no per-photograph write, because there is nothing to propagate
    *to* — the prediction is a function of the vector, so a photograph that has
    never been near a judgement still has a score the moment it has a vector.
    """

    import numpy as np

    fitted = direction(strengths, subjects, vectors)
    if fitted is None:
        return dict(strengths)
    w, centre = fitted

    predicted = np.asarray(vectors, dtype=np.float64) @ w + centre
    out = dict(strengths)
    for i, subject in enumerate(subjects):
        rounds = int(seen.get(subject, 0))
        weight = rounds / (rounds + TRUST)
        measured = strengths.get(subject)
        if measured is None:
            out[subject] = float(predicted[i])
        else:
            out[subject] = float(weight * measured + (1.0 - weight) * predicted[i])
    return out
