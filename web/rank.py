"""Ranking: you pick photographs out of sets, everything else is computed.

**A round is the atomic act — this one, out of these.** Everything here follows
from taking that literally.

Two pure functions and no state. `strength()` fits the round log;
`propagate()` spreads those ratings through the embedding space to photographs
that *look like* the ones you judged. Ranking is the second applied to the
first, and it is a **cache kind** — recomputed, never stored as if it were
judgement.

Three things fall out of storing the round whole rather than shredding it:

* **Set size stops being a mode.** The chance you picked `p` out of `S` is
  `softmax(s)[p]`, so a duel and a grid of twelve are one statement at two
  sizes. The mosaic can show any number without the ranking growing a branch,
  and beating eleven counts for more than beating one because the model says
  so, not because a rule does.
* **Undo is one row.** A mosaic click used to become N pairwise decisions, so
  undo removed one Nth of it and left the rest.
* **The fit is order-independent**, which the fold it replaces could not be.
  Elo depends on the sequence, and this archive is the proof: replaying its own
  ledger matched the stored numbers exactly for 41 comparisons and then
  diverged for good, because propagation had written to a photograph in place.
  Same rounds in any order, same answer — asserted in `test_core`.

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

**What the log is, measured 2026-08-17.** 2,532 rounds naming 665 photographs —
and every one of them a pair, because the write path shredded grids into pairs
and then stopped working entirely. `images.comparisons` sums to 415,360 over
21,376 photographs, which is what the judging actually was: roughly 22,400
mosaic rounds crediting every photograph shown, ~19 at a time. The counters
survived; the rounds behind them did not, and are not recoverable from any
backup. That number is the reason a round is now stored whole.

The strength scale is fitted, so there are no recovered constants to justify.
`SPREAD` is a display choice and `pull` is a prior; neither changes an ordering.

Two properties worth keeping in mind when reading `propagate`:

* a judged photograph takes its rating from the fit and **never** from its
  neighbours, which is the guarantee the old `MAX_DIRECT_COMPARISONS` cap was
  trying to buy with a threshold;
* propagation averages rather than sums, so a frame resembling fifty judged
  photographs cannot drift past all of them.
"""

from __future__ import annotations

from model import decisions

BASE = 1200.0
# Strength is fitted in log-odds; this is only how it is spoken aloud. A
# photograph one unit above another is `e` times more likely to be picked over
# it, and 400 makes that read like the numbers the app has always shown.
SPREAD = 400.0

# Below this cosine similarity two photographs are not "the same sort of
# picture" and a judgement about one says nothing about the other.
SIMILARITY_THRESHOLD = 0.70
# The long tail is near-zero after the cubic remap; this only bounds the work.
MAX_NEIGHBOURS = 100
# A propagated opinion is worth less than a judged one, and this says how much.
DECAY = 0.3


def rounds(conn) -> list[tuple[str, list[str]]]:
    """Every round: what you picked, and what it was shown against.

    A round is the atomic act — *this one, out of these* — and it is stored
    whole. It used to be shredded on the way in: one mosaic click became N
    separate pairwise decisions, which threw away which photographs were on
    screen together and made undo remove one eighteenth of a click.

    Pairs still read: a duel is a round whose set has one member, so `{"beat":
    x}` and `{"over": [x, y, z]}` are the same shape at different sizes.
    """

    import json

    out: list[tuple[str, list[str]]] = []
    for row in conn.execute(
        "SELECT subject, value FROM decisions WHERE family = ? ORDER BY at ASC, id ASC",
        (decisions.COMPARE,),
    ):
        try:
            value = json.loads(row["value"])
        except (TypeError, ValueError):
            continue
        over = value.get("over") if isinstance(value, dict) else None
        if over is None and isinstance(value, dict) and value.get("beat"):
            over = [value["beat"]]
        over = [h for h in (over or []) if h and h != row["subject"]]
        if over:
            out.append((row["subject"], over))
    return out


def strength(conn, *, steps: int = 300, rate: float = 1.0, pull: float = 1.0) -> dict[str, float]:
    """How good each photograph is, from the rounds alone.

    One model, and set size is not a parameter of it: the chance you picked `p`
    out of the set `S` is `softmax(s)[p]`, so a duel and a grid of twelve are
    the same statement at different sizes. That is why the mosaic can be
    flexible about numbers without the ranking growing a mode.

    Fitted, not replayed. Elo folds the log in order and is path-dependent by
    construction — its own predecessor here admitted as much, and the archive
    proved it: replaying the recorded pairs matched the stored numbers exactly
    for 41 comparisons and then diverged for good, because something had
    written to a photograph in place. A fit has no such memory. The same rounds
    in any order give the same answer, and one more round re-fits everything
    rather than appending to a history you cannot audit.

    `pull` holds the scale still. Softmax only ever sees differences, so
    nothing anchors the numbers; without it a photograph that only ever won
    walks off to infinity. It is the same job Elo's fixed 1200 base did, doing
    it honestly.
    """

    log = rounds(conn)
    if not log:
        return {}

    import numpy as np

    subjects = sorted({h for picked, over in log for h in (picked, *over)})
    index = {h: i for i, h in enumerate(subjects)}

    # Every round flattened into one long list of members, with a parallel list
    # saying which round each belongs to. That turns the whole fit into array
    # arithmetic and `bincount`, so a hundred thousand rounds costs about what
    # a thousand did in Python -- and rounds of different sizes need no
    # special case, which is the point.
    member, belongs, picked_at = [], [], []
    for number, (picked, over) in enumerate(log):
        picked_at.append(len(member))
        member.append(index[picked])
        belongs.append(number)
        for photo in over:
            member.append(index[photo])
            belongs.append(number)
    member = np.asarray(member)
    belongs = np.asarray(belongs)
    picked_at = np.asarray(picked_at)

    scores = np.zeros(len(subjects))
    # Each photograph steps by its own evidence rather than the library's. A
    # photograph in twenty rounds should be pinned twenty times as firmly as
    # one in a single round -- dividing by the total number of rounds instead
    # would shrink every strength toward the middle as the log grew, so the
    # more you ranked the less any of it would mean.
    appearances = np.bincount(member, minlength=len(subjects)).astype(float)
    pace = rate / np.maximum(appearances, 1.0)

    for step in range(steps):
        values = scores[member]
        top = np.maximum.reduceat(values, picked_at)          # per-round max
        weights = np.exp(values - top[belongs])
        share = weights / np.bincount(belongs, weights=weights)[belongs]
        gradient = -np.bincount(member, weights=share, minlength=len(subjects))
        np.add.at(gradient, member[picked_at], 1.0)           # the one you picked
        # The pull does NOT scale with appearances. Evidence does; belief does
        # not. A photograph that won its only round has, strictly, unbounded
        # strength -- nothing in the data ever pulls it back -- so the prior is
        # what stops one lucky frame outranking a photograph that won twenty
        # times. Scaling the prior by appearances would have applied it most
        # weakly exactly where it is needed most.
        scores += pace * (gradient - pull * scores) / (1.0 + step / 120.0)

    # Reported on the scale the app already speaks, so nothing downstream has
    # to learn a new one. The mapping is a display choice, not a model one.
    return {h: BASE + SPREAD * float(scores[index[h]]) for h in subjects}


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
    """How many rounds each photograph has appeared in, winning or losing.

    This is the confidence behind a strength, and so the weight in any blend
    with a predicted score: a photograph seen twenty times speaks for itself, a
    photograph seen once barely does.
    """

    counts: dict[str, int] = {}
    for picked, over in rounds(conn):
        for photo in (picked, *over):
            counts[photo] = counts.get(photo, 0) + 1
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
    scores = strength(conn)

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

    With no vectors it degrades to strength over what you actually compared —
    which is correct, not a fallback. Ranking works at zero embedding coverage
    and sharpens as the owed queue drains.
    """

    judged = strength(conn)
    if not subjects or vectors is None:
        return judged
    return propagate(judged, subjects, vectors)
