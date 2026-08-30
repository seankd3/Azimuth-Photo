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

    rows = conn.execute(
        "SELECT hash, value FROM cache WHERE kind = 'embedding' AND recipe = ?"
        " AND state = 'ready' AND value IS NOT NULL",
        (embed.RECIPE,),
    ).fetchall()
    if not rows:
        return [], None
    subjects = [str(row["hash"]) for row in rows]
    return subjects, np.stack([np.frombuffer(row["value"], dtype=np.float32) for row in rows])


def _somewhere(upper: int) -> int:
    """A random offset. Its own function so a test can hold it still."""

    import random

    return random.randint(0, upper) if upper > 0 else 0


def _finding_round() -> bool:
    """Learn's coin: one round in three finds, the rest teach. Its own
    function so a test can hold it still."""

    import random

    return random.random() < 1 / 3


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


def candidates(conn, n: int = 12, *, scope: Scope = EVERYTHING, avoid=(),
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
    window = 4000
    highest = conn.execute("SELECT MAX(id) FROM images").fetchone()[0] or 0
    select = "SELECT i.id, i.content_hash AS hash, i.width, i.height, i.rotate, i.elo FROM images i"
    pool = [dict(row) for row in conn.execute(
        f"{select} WHERE {where} AND i.id >= ? ORDER BY i.id LIMIT ?",
        (*args, _somewhere(highest), window),
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
    ordered = _ordered(pool, n, str(mode), space)
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


def _ordered(pool: list[dict], n: int, mode: str, space) -> list[dict]:
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
        return _learn(pool, n)

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


def _learn(pool: list[dict], n: int) -> list[dict]:
    """The simulation's winner: the fastest route to the best ranking.

    Two kinds of round, mixed one-in-three once everything has been seen:

    * **Teaching** — the most uncertain photographs that are most evenly
      matched: a window slid over the rating-sorted pool to where total
      uncertainty (1/sqrt(1+rounds)) is greatest. Information theory's
      answer for a softmax model — a round teaches most when its outcome
      is least foretold — and before anything is judged the flat-rating
      crowd is one huge uncertain window, so coverage falls out free.
    * **Finding** — the least-worn of the current top band (15%), spread
      across it, because pure uncertainty stops visiting the leaders once
      their ratings separate, and the stars read the top.

    Measured (scripts/sim_learn.py, 3 seeds, 600 rounds of 9 over 2,000):
    this mixture dominates every other mode on both answers at once —
    whole-order rho .382 and top-decile recall .457, against close's
    .371/.397, random's .365/.418 and diverse's .363/.455. Pure teaching
    reaches rho .406 but finds only .348 of the true top; the third round
    buys the top back for a twentieth of the order.
    """

    import math
    import random

    def wear(photo):
        return 1.0 / math.sqrt(1.0 + photo["comparisons"])

    import numpy as np

    mu = np.asarray([float(p["rating"]) for p in pool])
    sigma = np.asarray([wear(p) for p in pool])

    covered = sum(1 for p in pool if p["comparisons"] > 0) >= 0.95 * len(pool)
    if covered and _finding_round():
        # The least-worn nearest the band's cut: the bubble. Membership of
        # the top is decided at its boundary — the extreme leaders are
        # already safely in, so rounds there change nothing the stars read.
        # (Learned from the simulation the hard way: spreading over the
        # band's top instead cost seven points of top-decile recall.)
        cut = float(np.quantile(mu, 0.85))
        band = np.flatnonzero(mu >= cut)
        if len(band) < n:
            band = np.argsort(-mu)[: max(n * 2, 16)]
        order = band[np.lexsort((mu[band], -sigma[band]))]     # least-worn first
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

    order = np.argsort(mu, kind="stable")
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

    Without vectors the spread is by rating — even steps across the least-
    worn slice's whole range."""

    import random

    placed = {}
    if space is not None and space[1] is not None and len(space[0]):
        placed = {subject: i for i, subject in enumerate(space[0])}
    seen_in_space = [p for p in pool if p["hash"] in placed]
    if len(seen_in_space) < max(4, n):
        fresh = sorted(pool, key=lambda p: p["comparisons"])[: max(4 * n, 48)]
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
