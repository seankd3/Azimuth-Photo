"""Ranking: you pick photographs out of sets, everything else is computed.

**A round is the atomic act — this one, out of these.** Everything here follows
from taking that literally.

`fit()` is one Plackett–Luce fit over the rounds — a direction in the
embedding space and each photograph's own residual, learned together — and it
is **recomputed, never stored as if it were judgement**.

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



# The parsed compare log, per catalog file: (highest decision id seen, the
# triples). The log is append-only, so a memo extends instead of reparsing —
# every candidate ask used to JSON-parse the whole log twice (once for
# `seen`, once for `judged`), a cost that grew with every round judged and
# sat squarely on the click path.
_PARSED: dict[str, tuple[int, list[tuple[int, str, dict]]]] = {}


def _triples(conn) -> list[tuple[int, str, dict]]:
    """Every compare decision, parsed once: (id, subject, value)."""

    import json

    try:
        path = conn.execute("PRAGMA database_list").fetchone()[2]
        top = int(conn.execute(
            "SELECT COALESCE(MAX(id), 0) FROM decisions WHERE family = ?",
            (decisions.COMPARE,)).fetchone()[0])
    except (AttributeError, TypeError, IndexError):
        # A connection that cannot say what file it is (a test's wrapper, a
        # bare in-memory db) reads whole and skips the memo.
        path, top = "", 0
    held = _PARSED.get(path) if path else None
    since = held[0] if held and held[0] <= top else 0
    parsed = list(held[1]) if since else []
    for row in conn.execute(
        "SELECT id, subject, value FROM decisions WHERE family = ? AND id > ?"
        " ORDER BY at ASC, id ASC",
        (decisions.COMPARE, since),
    ):
        try:
            value = json.loads(row["value"])
        except (TypeError, ValueError):
            continue
        if isinstance(value, dict):
            parsed.append((int(row["id"]), row["subject"], value))
    if path:
        _PARSED[path] = (top, parsed)
    return parsed


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

    kept: list[tuple[int, str, list[str]]] = []
    retracted: set[int] = set()
    for number, subject, value in _triples(conn):
        if value.get("undo"):
            retracted.add(int(value["undo"]))
            continue
        over = value.get("over")
        if over is None and value.get("beat"):
            over = [value["beat"]]
        over = [h for h in (over or []) if h and h != subject]
        if over:
            kept.append((number, subject, over))
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


LAM_W = 1.0     # the ridge on the direction
LAM_B = 1.0     # the pull on a photograph's own residual
STEPS = 300     # Adam steps; the fit is convex, this is past convergence


def fit(log: list[tuple[str, list[str]]], subjects: list[str] | None = None, vectors=None) -> dict[str, float]:
    """The whole ranking, from one fit on the rounds: `fitted`'s scores."""

    return fitted(log, subjects, vectors)[0]


def fitted(log: list[tuple[str, list[str]]], subjects: list[str] | None = None,
           vectors=None) -> tuple[dict[str, float], dict[str, float]]:
    """The whole ranking, from one fit on the rounds -- and how unsure the
    fit is of each judged photograph's own place: one over the square root
    of its evidence, the Fisher information (`sum p(1 - p)` over its rounds,
    at the fitted scores) plus the pull. A photograph whose rounds were all
    foregone conclusions is as unsure as one never judged; one that keeps
    landing in close calls is pinned. Learn draws by it.

    Every photograph's score is `x . w + b`: what its vector says a photograph
    like it is worth, and what its own rounds say beyond that. One
    Plackett-Luce likelihood over every round -- the chance you picked `p`
    out of the set `S` is `softmax(score)[p]`, so a duel and a grid of twelve
    are the same statement at different sizes -- fitted for `w` and `b`
    together, so the direction is learned from the rounds themselves rather
    than from strengths fitted first. Measured (E1, five-fold over 4,308 of
    the owner's rounds): the two-stage head it replaces placed the picked
    photograph first in 45.3% of held-out rounds, this one in 51.6%;
    pairwise 80.4% to 82.2%.

    Fitted, not folded. Elo folds the log in order and is path-dependent by
    construction; the same rounds in any order give this the same answer,
    and one more round re-fits everything rather than appending to a history
    you cannot audit.

    The pull on `b` is what stops a photograph that only ever won from
    walking off to infinity (nothing in the data ever pulls it back); the
    ridge on `w` is what keeps a direction from being read off a handful of
    rounds. Both are fixed while the evidence grows, so the more you rank
    the more it means. With no vectors it is the fit over `b` alone, which
    is correct on a fresh library, not a fallback: ranking works at zero
    embedding coverage and reaches every photograph the moment it has a
    vector.

    Reported on the scale the app already speaks (`BASE + SPREAD * score`),
    a display choice, not a model one.
    """

    if not log:
        return {}, {}

    import numpy as np

    judged = sorted({h for picked, over in log for h in (picked, *over)})
    order = list(subjects or [])
    known = set(order)
    order += [h for h in judged if h not in known]
    index = {h: i for i, h in enumerate(order)}
    n = len(order)
    if vectors is not None and len(order) > len(judged) - len(known & set(judged)) and len(subjects or []):
        held = np.asarray(vectors, dtype=np.float32)
        x = np.zeros((n, held.shape[1]), dtype=np.float32)
        x[:len(subjects)] = held
    else:
        x = np.zeros((n, 0), dtype=np.float32)

    # Every round flattened into one long list of members, with a parallel
    # list saying which round each belongs to: the whole fit is array
    # arithmetic and `bincount`, and rounds of different sizes need no
    # special case.
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
    onehot = np.zeros(len(member), dtype=np.float32)
    onehot[picked_at] = 1.0
    rounds_n = float(len(log))
    xm = x[member]
    width = x.shape[1]

    w = np.zeros(width, dtype=np.float32)
    b = np.zeros(n, dtype=np.float32)
    mw, vw = np.zeros(width, dtype=np.float32), np.zeros(width, dtype=np.float32)
    # Each photograph's residual steps by its own evidence: one in twenty
    # rounds is pinned twenty times as firmly as one in a single round,
    # and the pull does not scale with appearances -- evidence does, belief
    # does not. The direction, shared by every round, steps by Adam.
    appearances = np.bincount(member, minlength=n).astype(np.float32)
    pace = 1.0 / np.maximum(appearances, 1.0)
    rate = 0.05
    for t in range(1, STEPS + 1):
        s = (xm @ w if width else 0.0) + b[member]
        top = np.maximum.reduceat(s, picked_at)
        e = np.exp(s - top[belongs])
        prob = e / np.bincount(belongs, weights=e)[belongs]
        g = (prob - onehot).astype(np.float32)
        gb = np.bincount(member, weights=g, minlength=n).astype(np.float32)
        b -= pace * (gb + LAM_B * b) / (1.0 + t / 120.0)
        if width:
            gw = (xm.T @ g) / rounds_n + LAM_W * w / rounds_n
            mw = 0.9 * mw + 0.1 * gw
            vw = 0.999 * vw + 0.001 * gw * gw
            w -= rate * (mw / (1 - 0.9 ** t)) / (np.sqrt(vw / (1 - 0.999 ** t)) + 1e-8)
    scores = (x @ w if width else 0.0) + b
    final = scores[member]
    top = np.maximum.reduceat(final, picked_at)
    e = np.exp(final - top[belongs])
    prob = e / np.bincount(belongs, weights=e)[belongs]
    evidence = np.bincount(member, weights=prob * (1.0 - prob), minlength=n)
    unsure = {h: float(1.0 / np.sqrt(evidence[i] + LAM_B)) for h, i in index.items() if appearances[i] > 0}
    return {h: BASE + SPREAD * float(scores[i]) for h, i in index.items()}, unsure


def ranking(conn, subjects: list[str] | None = None, vectors=None) -> dict[str, float]:
    """The ranking from the log: `fit` over every round you have not retracted."""

    return fit(rounds(conn), subjects, vectors)




# A star is the ranking's readable face, and it is earned: only a photograph
# seen in at least this many rounds carries one. One round is luck, two a
# coincidence, three a pattern.
EARNED = 3
# Cumulative shares of the ranked photographs, best first. One sentence
# rules the ladder: **each additional star halves the set** — the top third
# wears a star at all, and every step up is another halving (33% → 16% →
# 8% → 4% → 2%). Everything below the top third is ranked and unstarred:
# stars are rare on purpose ("deff not more than half", 08-29). A display
# choice, not a model one -- the order is the ranking's, this only names it.
BANDS = ((5, 0.02), (4, 0.04), (3, 0.08), (2, 0.16), (1, 1 / 3))
# The shoot's grace: its top tenth — never fewer than its single best
# frame — reads three stars even when the world said less.
GRACE = 0.10


def stars(scores: dict[str, float], seen: dict[str, int],
          shoots: dict[str, str] | None = None) -> dict[str, int]:
    """Every starred photograph's star: earned in the world, or in the shoot.

    Each additional star halves the set, and only the top third wears one
    at all. Five and four are absolute — the top 2% and 4% of everything
    ranked — because they are portfolio currency and travel to Lightroom
    as plain Ratings; the best of a weak shoot must never mint one. Three
    is where context belongs: the global top 8%, *or* the top tenth of its
    own shoot (`shoots` maps subject to its folder), so every shoot keeps
    a keeper. Below the top third a photograph is ranked and unstarred —
    stars are rare on purpose. There is no manual star anywhere -- the
    keys 1 to 5 filter, they do not rate -- so the star cannot disagree
    with the order.
    """

    import math

    ranked = sorted(
        (h for h, n in seen.items() if n >= EARNED and h in scores),
        key=lambda h: (-scores[h], h),
    )
    out: dict[str, int] = {}
    for place, subject in enumerate(ranked):
        # `place < count * share`, so the best of even a handful is five: a
        # small library has a best frame too.
        out[subject] = next((star for star, share in BANDS if place < len(ranked) * share), 0)
    if shoots:
        gathered: dict[str, list[str]] = {}
        for subject in ranked:              # ranking order carries into each shoot
            where = shoots.get(subject)
            if where:
                gathered.setdefault(where, []).append(subject)
        for members in gathered.values():
            for subject in members[: max(1, math.ceil(len(members) * GRACE))]:
                if out[subject] < 3:
                    out[subject] = 3
    return out


def space(conn) -> tuple[list[str], object]:
    """Every ready vector for the model in use, as one matrix.

    This is the whole input the prediction needs, read from the same cache
    the worker and the backfill write. Row order is the subject list; the
    matrix is float32, unit rows, one width -- a second width under the same
    recipe would be corruption and is allowed to fail loudly. Empty is a
    normal answer on a fresh library, and the ranking is then the fit alone.
    """

    import numpy as np

    import embed

    # Counted first and filled in place from the cursor: a list of the rows
    # and then a stack of them peaked at twice the matrix (1.4 GB at 150k).
    ready = ("FROM cache WHERE kind = 'embedding' AND recipe = ?"
             " AND state = 'ready' AND value IS NOT NULL")
    count = int(conn.execute(f"SELECT COUNT(*) {ready}", (embed.RECIPE,)).fetchone()[0])
    if not count:
        return [], None
    subjects: list[str] = []
    matrix = None
    for row in conn.execute(f"SELECT hash, value {ready}", (embed.RECIPE,)):
        if len(subjects) == count:
            break
        vector = np.frombuffer(row["value"], dtype=np.float32)
        if matrix is None:
            matrix = np.empty((count, vector.shape[0]), dtype=np.float32)
        matrix[len(subjects)] = vector   # a second width fails here, loudly
        subjects.append(str(row["hash"]))
    if matrix is None:
        return [], None
    # Rows that left between the count and the walk: the buffer is trimmed
    # for real, not viewed, since the space is held for the whole session.
    return subjects, matrix if len(subjects) == count else matrix[:len(subjects)].copy()


def _somewhere(upper: int) -> int:
    """A random offset. Its own function so a test can hold it still."""

    import random

    return random.randint(0, upper) if upper > 0 else 0


def _finding_round() -> bool:
    """Learn's coin: half the rounds find among the leaders, half teach.
    Its own function so a test can hold it still."""

    import random

    return random.random() < 0.5


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


def uncertain(conn) -> dict[str, float]:
    """Learn's uncertainty when no lane has fitted yet: the fit over the
    rounds alone (310 ms on 4,308 rounds). The rank lane's own fit, with
    the vectors, replaces it when it lands."""

    return fitted(rounds(conn))[1]


def candidates(conn, n: int = 12, *, scope: Scope = EVERYTHING, avoid=(), unsure: dict[str, float] | None = None,
               mode: str = "close", space=None) -> list[dict]:
    """Photographs worth comparing next.

    The mosaic is a candidate query, and the default is one sentence: **show
    the least-judged photographs, and around them the ones closest in
    rating.** A comparison between two photographs you already know the order
    of teaches nothing; a comparison between two that are close teaches the
    most.

    `mode` is a small pure ordering over the same pool — never a strategy
    engine (the thing with 22 parameters this replaced):

    - ``learn``       the simulation's winner: teaching windows over the
                      rating-sorted pool where uncertainty is greatest,
                      one round in three finding among the top band.
    - ``close``       the default above.
    - ``random``      the pool shuffled — a walk with no opinion.
    - ``diverse``     spread apart in the embedding space (by rating when
                      no vectors have been made), so one round looks across
                      the scope instead of within one look.
    - ``tournament``  the leaders meet: the highest-rated already-judged
                      photographs face each other, which is how a top
                      settles. Falls back to ``close`` until enough have
                      been judged to have leaders.

    Narrowing is not an argument here -- it is the `scope`, the same one
    every surface takes. `avoid` is what is on screen or was a moment ago,
    by identity; a set is drawn from the rest so the same frame does not
    come straight back.

    A pair is one orientation. Two photographs side by side are judged by
    shape before they are judged by anything else -- a portrait against a
    landscape is a comparison of frames, not photographs -- so the companion
    in a pair shares the anchor's orientation when one exists. A larger set
    is mixed and shown at equal area, which is what takes shape out of it
    there.
    """

    experience = seen(conn)

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
    # Several windows, not one: ids are handed out in import order, so one
    # window of four thousand is one or two shoots, and a "diverse" round
    # drawn from it could only be as diverse as one afternoon. Eight seeks
    # at random ids span the scope for the price of eight index seeks.
    window = 4000
    windows = 8
    highest = conn.execute("SELECT MAX(id) FROM images").fetchone()[0] or 0
    select = ("SELECT i.id, i.content_hash AS hash, i.width, i.height, i.rotate, i.elo,"
              " i.date_taken FROM images i")
    pool: list[dict] = []
    for _ in range(windows):
        pool += [dict(row) for row in conn.execute(
            f"{select} WHERE {where} AND i.id >= ? ORDER BY i.id LIMIT ?",
            (*args, _somewhere(highest), window // windows),
        )]
    if len(pool) < window:
        pool += [dict(row) for row in conn.execute(
            f"{select} WHERE {where} ORDER BY i.id LIMIT ?", (*args, window - len(pool)),
        )]
    unwanted = set(avoid)
    # The wrap can re-take rows the first read already holds; one row per
    # photograph, or a window could seat the same frame against itself.
    pool = list({p["id"]: p for p in pool}.values())
    pool = [p for p in pool if p["hash"] not in unwanted]
    if not pool:
        return []

    for photo in pool:
        photo["comparisons"] = experience.get(photo["hash"], 0)
        photo["rating"] = float(photo["elo"] or BASE)

    n = max(2, int(n))
    if str(mode) == "learn" and unsure is None:
        unsure = uncertain(conn)
    ordered = _ordered(pool, n, str(mode), space, unsure)
    if ordered:
        # A set wears its anchor's orientation when the scope can dress it:
        # two shapes side by side are judged as frames before photographs,
        # at any set size. The partition is stable, so each mode's own
        # order holds within the shape — and when the scope runs thin the
        # set tops up with the rest rather than refusing to deal.
        like = _orientation(ordered[0])
        ordered.sort(key=lambda p: _orientation(p) != like)
    chosen: list[dict] = []
    identities: set[str] = set()
    for photo in ordered:
        if photo["hash"] in identities:
            continue          # one frame on two drives is one member
        identities.add(photo["hash"])
        chosen.append(photo)
        if len(chosen) == n:
            break
    return chosen


def _ordered(pool: list[dict], n: int, mode: str, space, unsure: dict[str, float] | None = None) -> list[dict]:
    """The pool, in the order one mode would take from it.

    One law over every mode: **a round spends its seats on the least-judged
    photographs the mode's own idea can find.** Wear — how many rounds a
    photograph has been in — rises the moment it is shown, so the walk
    progresses through the scope in epochs instead of resampling the same
    faces. Simulated (300 rounds of 9 over 2,000): repeats fell from 45% to
    ~0 for random, 42% to ~0 for diverse, and tournament stopped showing the
    same nine leaders fifty times each.
    """

    if mode == "random":
        import random

        # A walk with no opinion about which — but a firm one about whom:
        # freshest first, shuffled within equal wear (the sort is stable).
        shuffled = list(pool)
        random.shuffle(shuffled)
        shuffled.sort(key=lambda p: p["comparisons"])
        return shuffled

    if mode == "tournament":
        leaders = sorted((p for p in pool if p["comparisons"] > 0),
                         key=lambda p: -p["rating"])
        if len(leaders) >= n:
            # The court is the top of the table; the crown circulates through
            # it rather than the same n faces meeting forever.
            import random

            court = leaders[: 3 * n]
            random.shuffle(court)
            court.sort(key=lambda p: p["comparisons"])
            return court + leaders[3 * n:]
        # Not enough judged to have leaders yet: fall through to close.

    if mode == "learn":
        return _learn(pool, n, unsure)

    if mode == "diverse":
        return _spread(pool, n, space)

    # close: the least-judged photograph anchors the set; the rest are its
    # nearest neighbours by rating — wear breaks the tie, so the flat crowd
    # at the default rating rotates instead of repeating. (Orientation is
    # not this mode's business: every set is dressed in its anchor's shape
    # by the one partition in `candidates`.)
    anchor = min(pool, key=lambda p: (p["comparisons"], -p["rating"]))
    return sorted(pool, key=lambda p: (
        p is not anchor, abs(p["rating"] - anchor["rating"]), p["comparisons"]))


def _learn(pool: list[dict], n: int, unsure: dict[str, float]) -> list[dict]:
    """The simulation's winner: the fastest route to the best ranking.

    Two kinds of round, mixed one-in-three once everything has been seen:

    * **Teaching** — the most uncertain photographs that are most evenly
      matched: a window slid over the rating-sorted pool to where total
      uncertainty is greatest. Information theory's answer for a softmax
      model — a round teaches most when its outcome is least foretold —
      and before anything is judged the flat-rating crowd is one huge
      uncertain window, so coverage falls out free. The uncertainty is the
      fit's own (`fitted`: what a photograph's rounds could still teach),
      not a count of its rounds. Measured with this very mode, five seeds,
      600 rounds of 9 over 2,000 (E4): top-decile recall .473 to .502 on
      every seed; the whole order unchanged (.342 to .340). The finding
      round still keys on wear -- at the top every round is a foregone
      conclusion and the fit's uncertainty ties.
    * **Finding** — the least-worn of the current top band (15%), spread
      across it, because pure uncertainty stops visiting the leaders once
      their ratings separate, and the stars read the top. Half the rounds,
      as soon as there are leaders to find among: the person is here to
      find the best, and a sitting that only teaches the floor feels like
      being shown the worst of the library.

    Among equally uncertain windows the highest-rated one is taught first:
    the unjudged crowd all wears the same uncertainty, and the window that
    happened to come first in rating order was the lowest-predicted -- the
    bottom of the library, dealt round after round.

    Measured when the mixture was chosen (scripts/sim_learn.py, an earlier
    build of the simulation, 3 seeds, 600 rounds of 9 over 2,000): this
    mixture dominated every other mode on both answers at once —
    whole-order rho .382 and top-decile recall .457, against close's
    .371/.397, random's .365/.418 and diverse's .363/.455. Pure teaching
    reaches rho .406 but finds only .348 of the true top; the third round
    buys the top back for a twentieth of the order.
    """

    import math
    import random

    import numpy as np

    never = 1.0 / math.sqrt(LAM_B)      # unjudged: only the pull pins it
    mu = np.asarray([float(p["rating"]) for p in pool])
    sigma = np.asarray([unsure.get(p["hash"], never) for p in pool])

    judged = sum(1 for p in pool if p["comparisons"] > 0)
    if judged >= 2 * n and _finding_round():
        # The least-worn nearest the band's cut: the bubble. Membership of
        # the top is decided at its boundary — the extreme leaders are
        # already safely in, so rounds there change nothing the stars read.
        # (Learned from the simulation the hard way: spreading over the
        # band's top instead cost seven points of top-decile recall.)
        cut = float(np.quantile(mu, 0.85))
        band = np.flatnonzero(mu >= cut)
        if len(band) < n:
            band = np.argsort(-mu)[: max(n * 2, 16)]
        worn = np.asarray([p["comparisons"] for p in pool])
        order = band[np.lexsort((mu[band], worn[band]))]        # least-worn first
        fresh = order[: max(3 * n, 12)]
        fresh = fresh[np.argsort(mu[fresh], kind="stable")]
        if len(fresh) > n:
            step = (len(fresh) - 1) / (n - 1)
            fresh = [int(fresh[round(i * step)]) for i in range(n)]
        chosen = [pool[i] for i in fresh]
        taken = set(fresh)
        rest = [pool[i] for i in range(len(pool)) if i not in taken]
        random.shuffle(rest)
        return chosen + rest

    # Descending, so among tied windows argmax lands on the highest rated.
    order = np.argsort(-mu, kind="stable")
    if len(order) <= n:
        return [pool[int(i)] for i in order]
    sums = np.convolve(sigma[order], np.ones(n), mode="valid")
    start = int(np.argmax(sums))
    window = [int(i) for i in order[start: start + n]]
    taken = set(window)
    return [pool[i] for i in window] + [
        pool[int(i)] for i in order if int(i) not in taken]


def _spread(pool: list[dict], n: int, space) -> list[dict]:
    """Farthest-point sampling over the least-worn tier of the embedding
    space: each next member is the photograph least like everything already
    in the set, drawn from the freshest photographs first.

    The tier is the fix for the repeats the unrestricted version had: the
    hull of an embedding space has corners, and farthest-point walks to the
    same corners every round. Confined to the least-judged slice, a shown
    photograph's wear rises and it leaves the tier — the spread progresses
    through the scope instead of orbiting its extremes.

    Without vectors the spread is across capture time -- even steps through
    the least-worn slice's days, which is across shoots -- and by rating
    when the dates are not there to spread by."""

    import random

    placed = {}
    if space is not None and space[1] is not None and len(space[0]):
        placed = {subject: i for i, subject in enumerate(space[0])}
    seen_in_space = [p for p in pool if p["hash"] in placed]
    if len(seen_in_space) < max(4, n):
        fresh = sorted(pool, key=lambda p: p["comparisons"])[: max(4 * n, 48)]
        by_day: dict[str, list[dict]] = {}
        for p in fresh:
            if p.get("date_taken"):
                by_day.setdefault(str(p["date_taken"])[:10], []).append(p)
        if len(by_day) >= n:
            # One frame per day in turn, the days in order: a round is as
            # many shoots as it has seats, never one evening six times.
            picked: list[dict] = []
            lanes = [sorted(frames, key=lambda p: p["comparisons"]) for _day, frames in sorted(by_day.items())]
            while len(picked) < len(fresh):
                for lane in lanes:
                    if lane:
                        picked.append(lane.pop(0))
            picked += [p for p in fresh if not p.get("date_taken")]
            return picked
        ranked = sorted(fresh, key=lambda p: p["rating"])
        if len(ranked) <= n:
            return ranked
        step = (len(ranked) - 1) / (n - 1)
        picked = [ranked[round(i * step)] for i in range(n)]
        rest = [p for p in ranked if p not in picked]
        return picked + rest

    import numpy as np

    random.shuffle(seen_in_space)
    seen_in_space.sort(key=lambda p: p["comparisons"])
    tier = seen_in_space[: max(4 * n, 48)]
    matrix = space[1]
    vectors = matrix[[placed[p["hash"]] for p in tier]]
    chosen = [0]                       # the least-worn opens the set
    nearest = vectors @ vectors[0]
    while len(chosen) < min(n, len(tier)):
        far = int(np.argmin(nearest))
        chosen.append(far)
        nearest = np.maximum(nearest, vectors @ vectors[far])
    rest = [i for i in range(len(tier)) if i not in set(chosen)]
    beyond = seen_in_space[len(tier):]
    others = [p for p in pool if p["hash"] not in placed]
    return [tier[i] for i in (*chosen, *rest)] + beyond + others


def _orientation(photo: dict) -> str:
    """Landscape, portrait or square, as the photograph is shown -- a turn
    swaps the sides. Unread dimensions count as landscape, the common case."""

    width, height = photo.get("width") or 3, photo.get("height") or 2
    if int(photo.get("rotate") or 0) % 180:
        width, height = height, width
    return "landscape" if width > height else "portrait" if height > width else "square"


# Rounds a photograph has been in before its place is earned rather than
# guessed: the same three `stars` waits for.
EARNED = 3


_PROGRESS: dict[tuple, dict[str, int]] = {}


def progress(conn, scope: Scope = EVERYTHING) -> dict[str, int]:
    """How much of a scope has been looked at: `judged` have been in a round,
    `earned` in three or more, which is when a place stops being a guess.

    Not a percentage of anything invented, just how much of what you are
    looking at you have looked at, and how much of that is settled.
    Remembered per scope until the round log moves: two DISTINCT counts
    over the whole scope were ~260 ms of every draw.
    """

    import json

    counts = seen(conn)
    if not counts:
        return {"judged": 0, "earned": 0}
    clause, args = scope_where(scope)
    head = conn.execute("SELECT COALESCE(MAX(id), 0) FROM decisions WHERE family = ?",
                        (decisions.COMPARE,)).fetchone()[0]
    key = (clause, tuple(args), int(head), len(counts))
    held = _PROGRESS.get(key)
    if held is not None:
        return dict(held)

    def within(members) -> int:
        return int(conn.execute(
            f"SELECT COUNT(DISTINCT i.content_hash) FROM images i"
            f" WHERE i.status != 'trashed' AND i.tail IS NOT NULL AND ({clause})"
            f" AND i.content_hash IN (SELECT value FROM json_each(?))",
            (*args, json.dumps(sorted(members))),
        ).fetchone()[0])

    answer = {"judged": within(counts),
              "earned": within([h for h, n in counts.items() if n >= EARNED])}
    _PROGRESS.clear()
    _PROGRESS[key] = dict(answer)
    return answer


def judged(conn, scope: Scope = EVERYTHING) -> int:
    return progress(conn, scope)["judged"]
