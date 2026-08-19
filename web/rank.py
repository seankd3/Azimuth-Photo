"""Ranking: you pick photographs out of sets, everything else is computed.

**A round is the atomic act — this one, out of these.** Everything here follows
from taking that literally.

`strength()` fits the round log — what you measured. `taste` predicts the
rest. Ranking is the blend, and it is **recomputed, never stored as if it were
judgement**.

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

Nothing is mutated to produce a ranking, and that deletes the machinery that
existed to make mutation safe: no lock, no retry, no `propagation_updates`
ledger, no `propagated_updates` column, no cap protecting real comparisons from
being swamped. The answer is recomputed whole, so there is no partial state to
reconcile.

And it gains the property that mattered most and was missing: **the ranking
improves on its own.** One more round, or one more embedding off the owed
queue, re-ranks the whole library. The old design froze its output the moment
it wrote it.

**What the log is, measured 2026-08-17.** 2,532 rounds naming 665 photographs —
and every one of them a pair, because the write path shredded grids into pairs
and then stopped working entirely. `images.comparisons` sums to 415,360 over
21,376 photographs, which is what the judging actually was: roughly 22,400
mosaic rounds crediting every photograph shown, ~19 at a time. The counters
survived; the rounds behind them did not, and are not recoverable from any
backup. That number is the reason a round is now stored whole.

The strength scale is fitted, so there are no recovered constants to justify.
`SPREAD` is a display choice and `pull` is a prior; neither changes an ordering.

A judged photograph keeps its own verdict: the blend weights strength by how
many rounds it has been in, so the prediction only fills the space where you
have not looked. That is the guarantee the old `MAX_DIRECT_COMPARISONS` cap was
trying to buy with a threshold.
"""

from __future__ import annotations

from model import decisions
from model.scope import EVERYTHING, Scope, where as scope_where

BASE = 1200.0
# Strength is fitted in log-odds; this is only how it is spoken aloud. A
# photograph one unit above another is `e` times more likely to be picked over
# it, and 400 makes that read like the numbers the app has always shown.
SPREAD = 400.0



def rounds(conn) -> list[tuple[str, list[str]]]:
    """Every round: what you picked, and what it was shown against.

    A round is the atomic act — *this one, out of these* — and it is stored
    whole. It used to be shredded on the way in: one mosaic click became N
    separate pairwise decisions, which threw away which photographs were on
    screen together and made undo remove one eighteenth of a click.

    Pairs still read: a duel is a round whose set has one member, so `{"beat":
    x}` and `{"over": [x, y, z]}` are the same shape at different sizes.

    A round taken back is still in the log -- the log says what happened --
    as a later row naming it: `{"undo": id}`. Both rows drop out here, so the
    fit never sees a round you retracted, and nothing was deleted to manage
    that.
    """

    import json

    kept: list[tuple[int, str, list[str]]] = []
    retracted: set[int] = set()
    for row in conn.execute(
        "SELECT id, subject, value FROM decisions WHERE family = ? ORDER BY at ASC, id ASC",
        (decisions.COMPARE,),
    ):
        try:
            value = json.loads(row["value"])
        except (TypeError, ValueError):
            continue
        if not isinstance(value, dict):
            continue
        if value.get("undo"):
            retracted.add(int(value["undo"]))
            continue
        over = value.get("over")
        if over is None and value.get("beat"):
            over = [value["beat"]]
        over = [h for h in (over or []) if h and h != row["subject"]]
        if over:
            kept.append((int(row["id"]), row["subject"], over))
    return [(picked, over) for number, picked, over in kept if number not in retracted]


def record(conn, winner_id: int, over_ids) -> dict:
    """One round, whole: this photograph, out of these. Returns what `retract`
    needs to take it back.

    Ids become identities here, because a round is about photographs and not
    about rows: the same frame on two drives is one member. A photograph with
    no identity yet cannot be in a round -- the window does not offer it --
    so asking is refused rather than recorded half.
    """

    wanted = [int(winner_id), *(int(i) for i in over_ids)]
    holes = ",".join("?" for _ in wanted)
    known = {
        int(row["id"]): row["hash"] for row in conn.execute(
            f"SELECT id, content_hash AS hash FROM images WHERE id IN ({holes})", wanted,
        ) if row["hash"]
    }
    winner = known.get(int(winner_id))
    over = sorted({known[i] for i in wanted[1:] if i in known and known[i] != winner})
    if winner is None or not over:
        raise ValueError("every photograph in a round needs an identity first")
    number = decisions.decide(conn, winner, decisions.COMPARE, {"over": over})
    conn.commit()
    return {"decision": number, "subject": winner, "over": over}


def retract(conn, decision: int) -> dict:
    """Take one round back, by saying so.

    Not a delete: the log's job is to say what happened, and a redo has
    nothing to read from a deletion. The retraction names the round, and
    `rounds()` drops both.
    """

    row = conn.execute(
        "SELECT subject FROM decisions WHERE id = ? AND family = ?",
        (int(decision), decisions.COMPARE),
    ).fetchone()
    if row is None:
        raise ValueError("no such round")
    number = decisions.decide(conn, row["subject"], decisions.COMPARE, {"undo": int(decision)})
    conn.commit()
    return {"decision": number, "retracted": int(decision)}


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


def candidates(conn, n: int = 12, *, scope: Scope = EVERYTHING, avoid=()) -> list[dict]:
    """Photographs worth comparing next.

    The mosaic is a candidate query, and the query is one sentence: **show the
    least-judged photographs, and around them the ones closest in rating.** A
    comparison between two photographs you already know the order of teaches
    nothing; a comparison between two that are close teaches the most.

    That replaces a route with 22 parameters and a strategy engine. Narrowing
    is not an argument here -- it is the `scope`, the same one every surface
    takes, so a mosaic over a folder is this query over that folder and a
    mosaic over what can be shown right now is this query over that.

    `avoid` is what is on screen or was a moment ago, by identity; a set is
    drawn from the rest so the same frame does not come straight back.

    A pair is one orientation. Two photographs side by side are judged by
    shape before they are judged by anything else -- a portrait against a
    landscape is a comparison of frames, not photographs -- so the companion
    in a pair shares the anchor's orientation when one exists. A larger set is
    mixed and shown at equal area, which is what takes shape out of it there.
    """

    experience = seen(conn)
    scores = strength(conn)

    clause, args = scope_where(scope)
    where = f"i.status != 'trashed' AND i.tail IS NOT NULL AND i.content_hash IS NOT NULL AND ({clause})"

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
    select = "SELECT i.id, i.content_hash AS hash, i.width, i.height, i.rotate FROM images i"
    pool = [dict(row) for row in conn.execute(
        f"{select} WHERE {where} AND i.id >= ? ORDER BY i.id LIMIT ?",
        (*args, _somewhere(highest), window),
    )]
    if len(pool) < window:
        pool += [dict(row) for row in conn.execute(
            f"{select} WHERE {where} ORDER BY i.id LIMIT ?", (*args, window - len(pool)),
        )]
    unwanted = set(avoid)
    pool = [p for p in pool if p["hash"] not in unwanted]
    if not pool:
        return []

    for photo in pool:
        photo["comparisons"] = experience.get(photo["hash"], 0)
        photo["rating"] = scores.get(photo["hash"], BASE)

    # The least-judged photograph anchors the set; the rest are its nearest
    # neighbours by rating, which is what makes the answer informative.
    anchor = min(pool, key=lambda p: (p["comparisons"], -p["rating"]))
    n = max(2, int(n))
    if n == 2:
        pool.sort(key=lambda p: (_orientation(p) != _orientation(anchor), abs(p["rating"] - anchor["rating"])))
    else:
        pool.sort(key=lambda p: abs(p["rating"] - anchor["rating"]))
    chosen: list[dict] = []
    identities: set[str] = set()
    for photo in pool:
        if photo["hash"] in identities:
            continue          # one frame on two drives is one member
        identities.add(photo["hash"])
        chosen.append(photo)
        if len(chosen) == n:
            break
    return chosen


def _orientation(photo: dict) -> str:
    """Landscape, portrait or square, as the photograph is shown -- a turn
    swaps the sides. Unread dimensions count as landscape, the common case."""

    width, height = photo.get("width") or 3, photo.get("height") or 2
    if int(photo.get("rotate") or 0) % 180:
        width, height = height, width
    return "landscape" if width > height else "portrait" if height > width else "square"


def judged(conn, scope: Scope = EVERYTHING) -> int:
    """How many photographs in a scope have been in at least one round.

    The progress a ranking surface shows: not a percentage of anything
    invented, just how much of what you are looking at you have looked at.
    """

    import json

    members = {h for picked, over in rounds(conn) for h in (picked, *over)}
    if not members:
        return 0
    clause, args = scope_where(scope)
    return int(conn.execute(
        f"SELECT COUNT(DISTINCT i.content_hash) FROM images i"
        f" WHERE i.status != 'trashed' AND i.tail IS NOT NULL AND ({clause})"
        f" AND i.content_hash IN (SELECT value FROM json_each(?))",
        (*args, json.dumps(sorted(members))),
    ).fetchone()[0])


def ranking(conn, subjects: list[str] | None = None, vectors=None) -> dict[str, float]:
    """The whole ranking: what you measured, and what the direction predicts.

    With no vectors it degrades to strength over what you actually compared —
    which is correct, not a fallback. Ranking works at zero embedding coverage
    and sharpens as the owed queue drains.

    This used to spread judged ratings to look-alikes by cosine similarity: a
    threshold, a cubic weighting, a neighbour cap and a decay factor, all of
    them dials. One fitted direction beats the lot on held-out photographs
    (+0.600 against +0.533 over 16,436 with both a strength and a vector) and
    reaches every photograph rather than only those near something judged. Four
    constants and a hundred lines went with it.
    """

    import taste

    measured = strength(conn)
    if not subjects or vectors is None:
        return measured
    return taste.scores(measured, seen(conn), subjects, vectors)
