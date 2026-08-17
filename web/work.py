"""Everything the machine owes you, as one query and one worker.

> **Owed is what should exist, minus what is cached.** An anti-join, not a queue.

That sentence is worth more than any code in this file. A queue is a second
copy of the truth: it has to be filled when a photo arrives, drained as work
completes, repaired when a worker dies mid-item, migrated when a kind changes,
and reconciled when someone edits the library behind its back. Every one of
those obligations was met here by its own table — a 93,220-row face backlog
with six triggers to keep it fed, three scan ledgers, five cursor tables and
seven preview schedulers. A `LEFT JOIN ... WHERE NULL` needs none of it, is
never stale by construction, and cannot lose an item.

Three consequences, all free:

* **New photos are not special.** Nothing enqueues them. They appear in the
  answer the moment they exist, and sort first because they are newest.
* **A dead worker costs nothing.** Its item is still owed, because it was never
  removed from anything.
* **A helper needs no protocol.** Another machine runs this same query against
  the same drive and writes results where this one already looks.

**Order is closeness to your eyes**: what is on screen now, then the rest of
this view, then newest, then the oldest debt.

## Staying out of the way without stopping

Browsing was 23 ms with chores quiet and *minutes* with them running. The first
reading of that was "chores must stop while you are using the app", and it is
the wrong lesson — it leaves the machine idle exactly when someone is sitting
in front of it, which is when there is most to do.

The right lesson is that the measurement was never about *whether* work ran. It
was about **what it shared with the request path**. Three separations, and
after them chores can run flat out:

* **Its own connection.** A chore holding a write transaction is a grid query
  waiting on a lock. The worker opens its own and never borrows the reader the
  routes use.
* **Its own process for decode.** `rawpy.postprocess` holds the GIL, so a
  thread pool adds contention without parallelism — the measured trap. Bulk
  rendering goes to a process pool; the interpreter serving the UI never blocks
  on a demosaic.
* **A slot kept free.** Chores use every core but one. The one left is what
  answers a tile the owner is looking at *right now*, which must never queue
  behind a hundred it has not asked for.

What remains of politeness is one sentence: **chores run one item at a time and
check between items.** That is enough, because an item is bounded — and it
means stopping is instant and costs nothing, since the queue is a query.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Callable, Iterable

from model import cache, photos
from model.scope import EVERYTHING, Scope, where

log = logging.getLogger(__name__)

# How long the app stays "in use" after the last thing you did. Long enough
# that a pause between keystrokes is not an invitation to start a demosaic.
QUIET_AFTER_SECONDS = 2.0

# How many owed photographs to consider before giving up on this pass. Not a
# batch -- one item is still done per step. It is the number of away or
# unreadable photographs the worker will step over to find one it can do.
CANDIDATES = 64

_last_touch = 0.0


def touched() -> None:
    """You did something. Chores make room -- they do not stop."""

    global _last_touch
    _last_touch = time.monotonic()


def busy() -> bool:
    """Is the owner doing something right now?

    Read to decide how *hard* to work, never whether to work at all. Chores
    keep going while the app is in use; they simply leave more of the machine
    alone while someone is watching.
    """

    return (time.monotonic() - _last_touch) < QUIET_AFTER_SECONDS


def workers(*, interactive: bool | None = None) -> int:
    """How many chore processes to run. Never every core, and never zero.

    Two constraints, both measured rather than guessed:

    * **Memory, priced at what a decode may actually cost.** The famous number
      here is 9 GB peak per demosaic worker — a 15 GB box sized for two workers
      wanting 17 GB was OOM-killed every four minutes. But that is the price of
      a *full-resolution* demosaic, and `render` refuses any decode over
      `DECODE_CEILING_BYTES` outright. Sizing the pool against the ceiling that
      is actually enforced rather than against the worst frame ever seen is the
      difference between 2 workers and 6 on this machine.
    * **One core stays free** so a tile the owner is looking at is rendered
      immediately instead of queueing behind a hundred they have not asked for.

    The two guards protect different things, which is why neither has to be
    conservative on the other's behalf: `render` refuses any single frame whose
    decode would exceed its ceiling, so no one item can be too big, and this
    only has to bound how many run at once. When memory cannot be measured —
    psutil is optional and is not installed here — falling back to a flat 2 on
    a sixteen-core machine left most of it idle for a risk the per-item ceiling
    had already taken care of.

    While the app is in use this halves again — not to be polite, but because
    the interactive render and the UI itself want the room.
    """

    import render

    cores = os.cpu_count() or 2
    ceiling = min(cores - 1, 6)
    try:
        import psutil

        # Half the machine's memory, divided by what one decode may cost.
        affordable = int(psutil.virtual_memory().total // 2 // render.DECODE_CEILING_BYTES)
        ceiling = min(ceiling, max(1, affordable))
    except Exception:
        pass
    slots = max(1, ceiling)
    if interactive if interactive is not None else busy():
        slots = max(1, slots // 2)
    return slots


# Whether chores should run at all is a *preference*, and a preference is a
# decision — so it is a row in the log rather than a flag in this module. That
# is not a flourish: a flag forgets itself on restart, which is exactly when
# someone who paused chores to save battery would be most annoyed to find them
# running again.
#
# It is deliberately a different question from `busy()`. "Are you using the app
# right now" and "have you told me to stop" are two things, and the old module
# conflated them into one pause flag that the politeness gate also wrote to.
CHORES = "chores"
MACHINE = "this machine"


def paused(conn) -> bool:
    """Has the owner asked for chores to stop?"""

    from model import decisions

    return decisions.latest(conn, MACHINE, CHORES) == "paused"


def set_paused(conn, stop: bool) -> bool:
    from model import decisions

    decisions.decide(conn, MACHINE, CHORES, "paused" if stop else "running")
    conn.commit()
    return stop


def owed(conn, kind: str, *, recipe: dict | None = None, on_screen: Iterable[int] = (),
         scope: Scope = EVERYTHING, limit: int = 200) -> list[dict]:
    """Photos that should have this answer and do not. The whole scheduler.

    A failed entry counts as answered: it is in `cache` with `state='failed'`,
    so the anti-join steps over it and the worker does not rediscover the same
    unreadable file on every pass. Asking again is `cache.forget`, deliberately.

    **A tail is required, and leaving it out livelocked the whole queue.** A
    photograph with no tail can never be located, so it is skipped on every
    pass — and because the worker asked for one row at a time, the same
    unlocatable row came back forever. Measured on the live catalog: 12,853 of
    156,831 owed rows have no tail, and the first of them stopped tile
    generation dead at 95 tiles. A photograph that is not in the library is not
    owed work.
    """

    ids = [int(i) for i in on_screen]
    hole = ",".join("?" for _ in ids)
    # Closeness to your eyes, spelled as an ORDER BY. The on-screen clause is
    # omitted entirely when nothing is on screen, so a background pass does not
    # pay for an empty IN list.
    nearest = f"i.id IN ({hole}) DESC, " if ids else ""
    source, args = _owed_from(kind, recipe, scope)
    return [dict(row) for row in conn.execute(
        f"""
        SELECT i.id, i.content_hash AS hash, i.tail, i.file_size
        {source}
        ORDER BY {nearest}i.date_taken DESC, i.id DESC
        LIMIT ?
        """,
        # In SQL order: the join's kind and recipe, then the scope's arguments
        # in the WHERE, then the on-screen ids in the ORDER BY. This bound the
        # ids *before* the scope for as long as it has existed, which was
        # invisible only because `EVERYTHING` carries no arguments — the first
        # caller to pass a real scope and an on-screen list at once would have
        # got them swapped.
        (*args, *ids, int(limit)),
    )]


def owing(conn, kind: str, *, recipe: dict | None = None, scope: Scope = EVERYTHING) -> int:
    """How many are owed. The same anti-join, counted instead of listed.

    A status line wanted this and had to `len()` a list of up to 100,000 rows to
    get it, which cost **731 ms and reported the clamp** — a backlog of 144,271
    read as "100,000". Counting the same predicate is **362 ms and correct**, and
    it takes the clamp with it: there is no ceiling to disclose because there is
    no ceiling. The projection and the ordering were the whole expense; the
    predicate is shared with `owed` so the two cannot drift.
    """

    source, args = _owed_from(kind, recipe, scope)
    return int(conn.execute(f"SELECT COUNT(*) {source}", args).fetchone()[0])


def _owed_from(kind: str, recipe: dict | None, scope: Scope) -> tuple[str, tuple]:
    """The anti-join itself: what is owed, before anyone says what to do with it."""

    entry = cache.kind_of(kind)
    narrowed, scope_args = where(scope)
    return (
        f"""
        FROM images i
        LEFT JOIN cache c
               ON c.hash = i.content_hash AND c.kind = ? AND c.recipe = ?
        WHERE i.content_hash IS NOT NULL
          AND i.tail IS NOT NULL
          AND i.vc_of IS NULL
          AND c.hash IS NULL
          AND ({entry.wants})
          AND ({narrowed})
        """,
        (kind, cache.canonical(kind, recipe), *scope_args),
    )


def unidentified(conn, limit: int = 200) -> list[dict]:
    """Photos with no hash yet.

    Identity is the one debt that cannot be a cache kind, because every cache
    row is keyed on the hash it would be waiting for. So it is owed first and
    separately, and everything else follows once a photo knows what it is.

    Cursor-free on purpose: hashing a photo removes it from its own candidate
    set, so the query advances itself. A bookmark would be wrong here the same
    way `id > mark` under a newest-first order was for embeddings — it skips
    everything imported before the mark.
    """

    return [dict(row) for row in conn.execute(
        f"SELECT id, tail, file_size {_UNIDENTIFIED} ORDER BY date_taken DESC, id DESC LIMIT ?",
        (int(limit),),
    )]


_UNIDENTIFIED = "FROM images WHERE content_hash IS NULL AND vc_of IS NULL AND tail IS NOT NULL"


def _unidentified_count(conn) -> int:
    return int(conn.execute(f"SELECT COUNT(*) {_UNIDENTIFIED}").fetchone()[0])


def debt(conn) -> dict[str, int]:
    """How much is owed, per kind. What a status line reads; nothing depends on it."""

    tally = {"identity": _unidentified_count(conn)}
    for name in cache.kinds():
        try:
            tally[name] = owing(conn, name)
        except Exception:  # a kind whose `wants` needs a column this catalog lacks
            log.debug("worker=debt kind=%s could not be counted", name)
    return tally


def _identify_one(conn, row) -> bool:
    path = photos.locate(conn, row["tail"], expected_size=row["file_size"])
    if path is None:
        return False
    conn.execute(
        "UPDATE images SET content_hash = ? WHERE id = ? AND content_hash IS NULL",
        (photos.content_hash(path), int(row["id"])),
    )
    return True


def step(conn, *, on_screen: Iterable[int] = (), yield_to: Callable[[], bool] = None,
         lane: int = 0, lanes: int = 1) -> dict | None:
    """Do the single most-owed thing, or nothing at all. Returns what it did.

    One item, then return. Concurrency lives *above* this function — in how
    many workers call it and where they run — rather than inside it, which is
    what keeps this readable and what makes stopping instant: there is never a
    batch half-finished, because the queue is a query.

    `yield_to` is accepted for callers that want to stand down entirely (a
    battery saver, a test). It defaults to never, because chores running is the
    normal state.

    **`lane` is how several workers share one query without coordinating.**
    Each takes every `lanes`-th candidate from the same window, so two workers
    never pick the same photograph and nothing has to claim, lock or lease a
    row. A worker that dies mid-item leaves no claim to expire — its item is
    simply owed again, which is the property the whole design turns on.
    """

    if paused(conn):
        return None

    for row in unidentified(conn, limit=1):
        if _identify_one(conn, row):
            conn.commit()
            return {"did": "identity", "photo": row["id"]}
        # Its drive is away. Nothing is owed *to this machine* about it now;
        # the next pass will find it if the drive comes back.
        return None

    for name, kind in cache.kinds().items():
        if not kind.here():
            continue
        # A window rather than one row. Being unable to locate a photograph is
        # normal -- its drive is away -- and asking for exactly one candidate
        # meant a single away photo stalled every other kind of work behind it.
        # Trying a handful costs nothing and makes progress whenever *any* of
        # them is reachable.
        for row in owed(conn, name, on_screen=on_screen, limit=CANDIDATES)[lane::lanes]:
            source = photos.locate(conn, row["tail"], expected_size=row["file_size"])
            if source is None:
                continue
            cache.make(conn, row["hash"], name, source)
            return {"did": name, "photo": row["id"]}
    return None


def sweep_cache(conn, ceiling_bytes: int) -> int:
    """Hold the cache under one ceiling and unlink what it dropped.

    Eviction is by age against one number. Not a tier policy, not a per-kind
    budget: every answer in there can be made again, so *which* one to lose is
    not a question worth a subsystem.
    """

    dropped = cache.evict(conn, ceiling_bytes)
    for kind, path in dropped:
        remove = getattr(cache.kinds().get(kind), "remove", None) or _unlink
        try:
            remove(path)
        except OSError:
            pass
    return len(dropped)


def _unlink(path: str) -> None:
    os.remove(path)


def run(open_conn, *, on_screen: Callable[[], Iterable[int]] = lambda: (),
        lane: int = 0, lanes: int = 1) -> None:
    """The chore loop. Runs until the process ends; owns everything it touches.

    Deliberately a plain `while` in a thread rather than a task on the event
    loop. A chore that blocks — a 45 MP demosaic, a cold archive read — would
    stall every request sharing that loop, and the whole point of this design
    is that it cannot.

    It opens its own connection and never borrows the reader the routes use, so
    the longest a grid query can wait on a chore is zero.
    """

    conn = open_conn()
    try:
        while True:
            try:
                did = step(conn, on_screen=on_screen(), lane=lane, lanes=lanes)
            except Exception:
                log.exception("worker=chores step failed")
                did = None
            # Nothing owed, or standing down: look again shortly rather than
            # spinning. Nothing here accumulates, so a long sleep costs only
            # latency on the next item.
            time.sleep(0.05 if did else 5.0)
    finally:
        try:
            conn.close()
        except Exception:
            pass


def start(open_conn, *, on_screen: Callable[[], Iterable[int]] = lambda: ()) -> int:
    """Run the chore loop on as many threads as this machine can afford.

    Threads rather than processes, and the reason is worth stating because the
    appendix warns the other way: `rawpy.postprocess` holds the GIL, so RAW
    decode does not parallelise here. But most of this library is JPEG and
    TIFF, and Pillow releases the GIL for both decode and encode — so the
    threads are real parallelism for the common case and merely harmless for
    the RAW one. A process pool would parallelise RAW too, at the cost of
    shipping frames over a pipe and giving each child its own catalog
    connection; that trade is worth making when RAW is the bottleneck and not
    before.

    Each thread takes its own lane of the same query, so they never collide and
    never coordinate.
    """

    import threading

    count = workers()
    for lane in range(count):
        threading.Thread(
            target=run, args=(open_conn,),
            kwargs={"on_screen": on_screen, "lane": lane, "lanes": count},
            name=f"chores-{lane}", daemon=True,
        ).start()
    return count
