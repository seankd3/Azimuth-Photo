"""Ranking: you make comparisons, everything else is computed from them.

Two pure functions and no state. `ratings()` folds the comparison log into Elo;
`propagate()` spreads those ratings through the embedding space to photographs
that *look like* the ones you judged. Ranking is the second applied to the
first, and it is a **cache kind** — recomputed, never stored as if it were
judgement.

That last word is the whole change. The old propagation ran as a side effect of
recording a comparison: it wrote scaled deltas straight onto neighbours' `elo`
columns, under a lock, with retries, guarded by a cap on how many direct
comparisons a photo could have before it stopped listening. Every one of those
mechanisms exists to make an *in-place mutation* safe. Fold the same idea into a
function of its inputs and they all evaporate:

* no lock and no retry, because nothing is mutated while you are using the app;
* no `propagation_updates` ledger and no `propagated_updates` column, because
  there is no partial state to reconcile — the answer is recomputed whole;
* no cap protecting real comparisons from being swamped, because a direct
  comparison wins **by construction**: judged photographs take their rating
  from the fold and never from their neighbours.

And it gains the property that mattered most and was missing: **the ranking
improves on its own.** One more comparison, or one more embedding off the owed
queue, re-ranks everything that resembles it. The old design froze its output
the moment it wrote it.

Measured against the live library, which is the clearest picture of what
propagation is worth:

| | |
|---|---|
| comparisons the owner made | 2,532 |
| photographs those pairs name | 665 |
| of those, ones that have a vector | **310** |
| photographs reached by propagation | **8,617** |
| time to compute the whole thing | **0.4 s** |

So 310 judged photographs currently carry an opinion to 8,617 others — and the
first row of that table is the interesting one. **Fewer than half the judged
photographs have a vector yet.** Every embedding off the owed queue does not
merely make one more photo searchable; it may connect a judgement the owner
already made to a part of the library it cannot presently reach. That is the
sense in which the owed embeddings are load-bearing for ranking.

The constants were recovered from the ledger rather than from the code, by
reading `elo_before` down consecutive rows: an even matchup moved the loser
6.0 points, which is K=12 against the standard 400-point logistic from a 1200
base.

Replaying the archive's own 2,532 pairs against those constants is also the
neatest demonstration of why ranking had to become a derivation:

| Pairs replayed | Disagreement with the recorded `elo_before` |
|---|---|
| 1 – 41 | **0.000000** |
| 500 | 0.081 |
| 1,000 | 0.401 |
| 2,532 | 11.407 |

The maths is exact and stays exact until comparison **#42**, which found a
photograph sitting at 1205.4989 that had never been compared with anything.
Propagation had written to it in place. From there the stored numbers are no
longer a function of the ledger, and cannot be recovered from it — which is
what happens when a derivation is kept in the same column as the judgement.
Recomputing gives an answer that is explainable at every point; the old one was
only reproducible by replaying its side effects in the order they happened.
"""

from __future__ import annotations

from model import decisions

BASE = 1200.0
K = 12.0

# Below this cosine similarity two photographs are not "the same sort of
# picture" and a judgement about one says nothing about the other.
SIMILARITY_THRESHOLD = 0.70
# The long tail is near-zero after the cubic remap; this only bounds the work.
MAX_NEIGHBOURS = 100
# A propagated opinion is worth less than a judged one, and this says how much.
DECAY = 0.3


def _expected(a: float, b: float) -> float:
    return 1.0 / (1.0 + 10.0 ** ((b - a) / 400.0))


def ratings(conn) -> dict[str, float]:
    """Replay every comparison into Elo. Same input, same answer, always.

    Read in the order they were made, because Elo is path-dependent: the same
    pairs in a different order give different numbers. `id` breaks ties within
    a second, which is the same rule the log itself reads by.
    """

    scores: dict[str, float] = {}
    rows = conn.execute(
        "SELECT subject, value FROM decisions WHERE family = ? ORDER BY at ASC, id ASC",
        (decisions.COMPARE,),
    ).fetchall()

    import json

    for row in rows:
        try:
            beaten = json.loads(row["value"])["beat"]
        except (TypeError, ValueError, KeyError):
            continue
        winner, loser = row["subject"], beaten
        won, lost = scores.get(winner, BASE), scores.get(loser, BASE)
        change = K * (1.0 - _expected(won, lost))
        scores[winner] = won + change
        scores[loser] = lost - change
    return scores


def _weight(similarity: float) -> float:
    """How much one photograph's rating should say about another's.

    Cubic, so that near-identical frames carry an opinion and barely-qualifying
    ones carry almost none: 0.75 → 0.00, 0.90 → 0.22, 0.99 → 0.89. Linear
    weighting made everything above the threshold count nearly the same, which
    is how a whole shoot inherits one frame's luck.
    """

    t = (similarity - SIMILARITY_THRESHOLD) / (1.0 - SIMILARITY_THRESHOLD)
    return t * t * t if t > 0.0 else 0.0


def propagate(judged: dict[str, float], subjects: list[str], vectors) -> dict[str, float]:
    """Spread judged ratings to look-alikes. One matrix multiply, no writes.

    `vectors` is a row-per-subject unit-normalised matrix aligned to `subjects`.

    The result is a **weighted average** of the neighbours' deviations from
    base, not a sum. Summing is what forced the old code to cap how much
    propagation a photo could receive: a frame resembling fifty judged
    photographs collected fifty nudges and drifted away from all of them. An
    average is bounded by construction, so the cap stops being necessary.
    """

    import numpy as np

    index = {subject: i for i, subject in enumerate(subjects)}
    known = [(index[s], score - BASE) for s, score in judged.items() if s in index]
    if not known:
        return dict(judged)

    rows = np.array([i for i, _ in known], dtype=np.int64)
    deviation = np.array([d for _, d in known], dtype=np.float32)

    # similarity[i, j] = how much photo i looks like judged photo j
    similarity = np.asarray(vectors) @ np.asarray(vectors)[rows].T
    if similarity.ndim == 1:
        similarity = similarity.reshape(len(subjects), -1)

    weights = np.where(similarity >= SIMILARITY_THRESHOLD, similarity, 0.0)
    t = (weights - SIMILARITY_THRESHOLD) / (1.0 - SIMILARITY_THRESHOLD)
    weights = np.where(weights > 0.0, t * t * t, 0.0).astype(np.float32)

    if len(known) > MAX_NEIGHBOURS:
        # Keep each photo's strongest neighbours; the rest are ~0 after the
        # cubic anyway, and dropping them keeps this one multiply.
        cut = np.argpartition(weights, -MAX_NEIGHBOURS, axis=1)[:, :-MAX_NEIGHBOURS]
        np.put_along_axis(weights, cut, 0.0, axis=1)

    total = weights.sum(axis=1)
    nudge = np.zeros(len(subjects), dtype=np.float32)
    reached = total > 0.0
    nudge[reached] = (weights[reached] @ deviation) / total[reached]

    out = {subject: BASE + DECAY * float(nudge[i]) for i, subject in enumerate(subjects)}
    # A judged photograph takes its own rating, never its neighbours'. This is
    # the guarantee the old cap was trying to buy with a threshold.
    out.update(judged)
    return out


def _somewhere(upper: int) -> int:
    """A random offset. Its own function so a test can hold it still."""

    import random

    return random.randint(0, upper) if upper > 0 else 0


def seen(conn) -> dict[str, int]:
    """How many comparisons each photograph has been in, winning or losing."""

    import json

    counts: dict[str, int] = {}
    for row in conn.execute(
        "SELECT subject, value FROM decisions WHERE family = ?", (decisions.COMPARE,)
    ):
        counts[row["subject"]] = counts.get(row["subject"], 0) + 1
        try:
            beaten = json.loads(row["value"])["beat"]
        except (TypeError, ValueError, KeyError):
            continue
        counts[beaten] = counts.get(beaten, 0) + 1
    return counts


def candidates(conn, n: int = 12, *, folder: str | None = None) -> list[dict]:
    """Photographs worth comparing next.

    The mosaic is a candidate query, and the query is one sentence: **show the
    least-judged photographs, and around them the ones closest in rating.** A
    comparison between two photographs you already know the order of teaches
    nothing; a comparison between two that are close teaches the most.

    That replaces a route with 22 parameters and a strategy engine. Filters are
    not an argument here — narrowing the library is `library.photos`'s job, and
    a mosaic over a folder is this query over that folder.
    """

    experience = seen(conn)
    scores = ratings(conn)

    where = "i.status != 'trashed' AND i.tail IS NOT NULL AND i.content_hash IS NOT NULL"
    args: list = []
    if folder:
        prefix = folder.replace("\\", "/").rstrip("/") + "/"
        where += " AND substr(i.tail, 1, ?) = ?"
        args += [len(prefix), prefix]

    # A window at a random offset, not the top of the library. Ordering the
    # pool by rating looked reasonable and was wrong: 127,219 photographs sit
    # at exactly the 1200 default, so "the best 4,000" contains only ones
    # already judged, and the mosaic would never show you an unrated photograph
    # again. A window costs the same and reaches everything.
    # Seek to a random id rather than paging with OFFSET. OFFSET has to walk
    # every row it skips, which on this library cost 850 ms; `id >= ?` is an
    # index seek and costs nothing. Wrapping to the start when the window falls
    # off the end keeps the last few thousand photographs reachable.
    window = 4000
    highest = conn.execute("SELECT MAX(id) FROM images").fetchone()[0] or 0
    pool = [dict(row) for row in conn.execute(
        f"SELECT i.id, i.tail, i.content_hash AS hash, i.elo, i.width, i.height"
        f" FROM images i WHERE {where} AND i.id >= ? ORDER BY i.id LIMIT ?",
        (*args, _somewhere(highest), window),
    )]
    if len(pool) < window:
        pool += [dict(row) for row in conn.execute(
            f"SELECT i.id, i.tail, i.content_hash AS hash, i.elo, i.width, i.height"
            f" FROM images i WHERE {where} ORDER BY i.id LIMIT ?",
            (*args, window - len(pool)),
        )]
    if not pool:
        return []

    for photo in pool:
        photo["comparisons"] = experience.get(photo["hash"], 0)
        photo["rating"] = scores.get(photo["hash"], float(photo["elo"] or BASE))

    # The least-judged photograph anchors the set; the rest are its nearest
    # neighbours by rating, which is what makes the answer informative.
    anchor = min(pool, key=lambda p: (p["comparisons"], -p["rating"]))
    pool.sort(key=lambda p: abs(p["rating"] - anchor["rating"]))
    return pool[:max(2, int(n))]


def ranking(conn, subjects: list[str] | None = None, vectors=None) -> dict[str, float]:
    """The whole ranking: fold the log, then spread it through the vectors.

    With no vectors it degrades to plain Elo over what you actually compared —
    which is correct, not a fallback. Ranking works at zero embedding coverage
    and sharpens as the owed queue drains.
    """

    judged = ratings(conn)
    if not subjects or vectors is None:
        return judged
    return propagate(judged, subjects, vectors)
