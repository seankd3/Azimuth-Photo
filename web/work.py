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

## Staying out of the way

The background path owns one connection and one worker. It does one bounded
item, then checks whether to continue. There is no pool capable of filling the
machine and no borrowed request connection capable of stalling the grid. A
caller may yield before an item for an explicit product reason such as battery
saver; otherwise debt continues to shrink. Stopping between items loses
nothing, because the queue is the query itself.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from typing import Callable, Iterable

from model import cache, decisions, photos
from model.scope import EVERYTHING, Scope, all_of, ids as only, where

log = logging.getLogger(__name__)

# How many owed photographs to consider before giving up on this pass. Not a
# batch -- one item is still done per step. It is the number of away or
# unreadable photographs the worker will step over to find one it can do.
CANDIDATES = 64


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


def owed(conn, kind: cache.Kind, *, recipe: dict | None = None, on_screen: Iterable[int] = (),
         scope: Scope = EVERYTHING, limit: int = 200, keyed: bool = False) -> list[dict]:
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
    carried = f", {kind.keyed[1]} AS keyed" if keyed else ""
    source, args = _owed_from(kind, recipe, scope, keyed=keyed)
    return [dict(row) for row in conn.execute(
        f"""
        SELECT i.id, i.content_hash AS hash, i.tail, i.file_size, i.date_taken{carried}
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


def owing(conn, kind: cache.Kind, *, recipe: dict | None = None, scope: Scope = EVERYTHING) -> int:
    """How many are owed. The same anti-join, counted instead of listed.

    A status line wanted this and had to `len()` a list of up to 100,000 rows to
    get it, which cost **731 ms and reported the clamp** — a backlog of 144,271
    read as "100,000". Counting the same predicate is **362 ms and correct**, and
    it takes the clamp with it: there is no ceiling to disclose because there is
    no ceiling. The projection and the ordering were the whole expense; the
    predicate is shared with `owed` so the two cannot drift.
    """

    source, args = _owed_from(kind, recipe, scope)
    count = int(conn.execute(f"SELECT COUNT(*) {source}", args).fetchone()[0])
    if kind.keyed is not None:
        source, args = _owed_from(kind, recipe, scope, keyed=True)
        count += int(conn.execute(f"SELECT COUNT(*) {source}", args).fetchone()[0])
    return count


def _owed_from(kind: cache.Kind, recipe: dict | None, scope: Scope,
               *, keyed: bool = False) -> tuple[str, tuple]:
    """The anti-join itself: what is owed, before anyone says what to do with it.

    A `keyed` pass owes the per-photograph variant: the recipe is the base
    recipe with the photo's own fragment spliced in as the first key, built
    in SQL so the anti-join stays one query. The base pass is unchanged —
    an edited photograph still owes its plain rendition, because derived
    kinds (the embedding, the faces) read the plain pixels.
    """

    narrowed, scope_args = where(scope)
    if not keyed:
        return (
            f"""
            FROM images i
            LEFT JOIN cache c
                   ON c.hash = i.content_hash AND c.kind = ? AND c.recipe = ?
            WHERE i.content_hash IS NOT NULL
              AND i.tail IS NOT NULL
              AND i.vc_of IS NULL
              AND c.hash IS NULL
              AND ({kind.wants})
              AND ({narrowed})
            """,
            (kind.name, cache.canonical(kind, recipe), *scope_args),
        )
    param, expression = kind.keyed
    base = cache.canonical(kind, recipe)
    prefix = f'{{"{param}":'
    suffix = "," + base[1:] if base != "{}" else "}"
    return (
        f"""
        FROM images i
        LEFT JOIN cache c
               ON c.hash = i.content_hash AND c.kind = ?
              AND c.recipe = ? || {expression} || ?
        WHERE i.content_hash IS NOT NULL
          AND i.tail IS NOT NULL
          AND i.vc_of IS NULL
          AND {expression} IS NOT NULL
          AND c.hash IS NULL
          AND ({kind.wants})
          AND ({narrowed})
        """,
        (kind.name, prefix, suffix, *scope_args),
    )


def unidentified(conn, limit: int = 200, *, scope: Scope = EVERYTHING) -> list[dict]:
    """Photos with no hash yet.

    Identity is the one debt that cannot be a cache kind, because every cache
    row is keyed on the hash it would be waiting for. So it is owed first and
    separately, and everything else follows once a photo knows what it is.

    Cursor-free on purpose: hashing a photo removes it from its own candidate
    set, so the query advances itself. A bookmark would be wrong here the same
    way `id > mark` under a newest-first order was for embeddings — it skips
    everything imported before the mark.
    """

    narrowed, args = where(scope)
    return [dict(row) for row in conn.execute(
        f"SELECT i.id, i.tail, i.file_size, i.date_taken {_UNIDENTIFIED} AND ({narrowed})"
        f" ORDER BY i.date_taken DESC, i.id DESC LIMIT ?",
        (*args, int(limit)),
    )]


_UNIDENTIFIED = "FROM images i WHERE i.content_hash IS NULL AND i.vc_of IS NULL AND i.tail IS NOT NULL"


def _unidentified_count(conn) -> int:
    return int(conn.execute(f"SELECT COUNT(*) {_UNIDENTIFIED}").fetchone()[0])


def debt(conn, kinds: Iterable[cache.Kind]) -> dict[str, int]:
    """How much is owed, per kind. What a status line reads; nothing depends on it."""

    tally = {"identity": _unidentified_count(conn)}
    for kind in kinds:
        try:
            tally[kind.name] = sum(owing(conn, kind, recipe=recipe) for recipe in kind.ahead())
        except Exception:  # a kind whose `wants` needs a column this catalog lacks
            log.debug("worker=debt kind=%s could not be counted", kind.name)
    return tally


def _identify_one(conn, row) -> bool:
    path = photos.locate(conn, row["tail"], expected_size=row["file_size"])
    if path is None:
        return False
    digest = photos.content_hash(path)
    conn.execute(
        "UPDATE images SET content_hash = ? WHERE id = ? AND content_hash IS NULL",
        (digest, int(row["id"])),
    )
    # A decision belongs to the identity, and this row may have arrived after
    # one was made -- a second copy of a picked or turned photograph. Its
    # columns take the log's current answers now, so the row is never the one
    # copy that disagrees until the next full reindex.
    for family, (column, _default, valid) in decisions.PROJECTED.items():
        value = decisions.latest(conn, digest, family)
        if valid(value):
            conn.execute(f"UPDATE images SET {column} = ? WHERE id = ?", (value, int(row["id"])))
    # A row is an address. Now that this one knows its identity, any other row
    # of the same identity that no drive holds is a dead address -- the file
    # was renamed, or moved in a way name, size and time could not pair -- and
    # leaves. Nothing is lost: decisions live under the identity, and this row
    # carries them.
    conn.execute(
        "DELETE FROM images WHERE content_hash = ? AND id != ? "
        "AND NOT EXISTS (SELECT 1 FROM copies c WHERE c.photo_id = images.id)",
        (digest, int(row["id"])),
    )
    return True


def step(conn, kinds: Iterable[cache.Kind], *, on_screen: Iterable[int] = (),
         yield_to: Callable[[], bool] | None = None,
         share: tuple[int, int] = (0, 1)) -> dict | None:
    """Do the single most-owed thing, or nothing at all. Returns what it did.

    Most-owed is decided by photograph, not by kind: what is on screen comes
    first, then the newest photograph that still owes anything, and for that
    photograph identity, then each kind in the order given. Kind-major order --
    every identity, then every date, then the first tile -- meant a fresh
    library of 157,000 photographs would not show a single picture until hours
    of hashing were done, and the proof of this surface caught it at 47: ten
    seconds of identity and metadata before the first tile.

    One item, then return. Concurrency lives *above* this function — in how
    many workers call it and where they run — rather than inside it, which is
    what keeps this readable and what makes stopping instant: there is never a
    batch half-finished, because the queue is a query. `share` is how several
    workers divide the library without a lock or a claim: `(lane, lanes)`
    keeps a worker to the photographs whose id leaves that remainder, so two
    lanes never want the same one and each is newest-first within its part.

    `yield_to` is accepted for callers that want to stand down entirely (a
    battery saver, a test). It defaults to never, because chores running is the
    normal state.

    One worker owns this loop. A worker that dies mid-item leaves no claim to
    expire — its item is simply owed again, which is the property the whole
    design turns on.
    """

    if paused(conn) or (yield_to is not None and yield_to()):
        return None

    lane, lanes = int(share[0]), max(1, int(share[1]))
    part = Scope("i.id % ? = ?", (lanes, lane)) if lanes > 1 else EVERYTHING
    looked = [int(i) for i in on_screen]
    scopes = (only(looked), EVERYTHING) if looked else (EVERYTHING,)
    for scope in scopes:
        did = _most_owed(conn, tuple(kinds), all_of(scope, part))
        if did is not None:
            return did
    return None


def _most_owed(conn, kinds: tuple[cache.Kind, ...], scope: Scope) -> dict | None:
    """Within one scope: the newest photograph's first owed thing.

    Each debt is asked for its newest few candidates rather than one, because
    being unable to locate a photograph is normal -- its drive is away -- and
    a single away photograph at the head of a kind must not stall the rest.
    The heads are merged newest-first across identity and every kind, so a
    photograph is finished (identity, metadata, tiles) before the next is
    begun, which is the order a person browsing newest-first feels.
    """

    heads: list[tuple[tuple, str, cache.Kind | None, dict | None, dict]] = []
    for row in unidentified(conn, limit=CANDIDATES, scope=scope):
        heads.append((_age(row), "identity", None, None, row))
    for kind in kinds:
        if not kind.here():
            continue
        for recipe in kind.ahead():
            for row in owed(conn, kind, recipe=recipe, scope=scope, limit=CANDIDATES):
                heads.append((_age(row), kind.name, kind, recipe, row))
            if kind.keyed is None:
                continue
            # The per-photograph variants: same kind, same base recipe, the
            # photo's own fragment as the first key.
            for row in owed(conn, kind, recipe=recipe, scope=scope,
                            limit=CANDIDATES, keyed=True):
                variant = {**(recipe or {}), kind.keyed[0]: json.loads(row["keyed"])}
                heads.append((_age(row), kind.name, kind, variant, row))
    heads.sort(key=lambda head: head[0], reverse=True)

    for _age_key, what, kind, recipe, row in heads:
        if what == "identity":
            if _identify_one(conn, row):
                conn.commit()
                return {"did": "identity", "photo": row["id"]}
            continue
        find = kind.source or (lambda conn, row: photos.locate(
            conn, row["tail"], expected_size=row["file_size"]))
        source = find(conn, row)
        if source is None:
            continue
        entry = cache.make(conn, row["hash"], kind, source, recipe)
        if entry is not None and kind.project is not None:
            cache.project(conn, row["hash"], row["id"], kind, entry, recipe)
        return {"did": kind.name, "photo": row["id"], "recipe": recipe}
    return None


def _age(row) -> tuple:
    """Sorted descending, this puts the newest first: a known date before an
    unknown one, a later date before an earlier, a higher id among equals."""

    taken = row["date_taken"]
    return (bool(taken), str(taken or ""), int(row["id"]))


def sweep_cache(conn, ceiling_bytes: int, kinds: Iterable[cache.Kind]) -> int:
    """Hold the cache under one ceiling and unlink what it dropped.

    Eviction is by age against one number. Not a tier policy, not a per-kind
    budget: every answer in there can be made again, so *which* one to lose is
    not a question worth a subsystem.
    """

    kinds = tuple(kinds)
    by_name = {kind.name: kind for kind in kinds}
    dropped = cache.evict(conn, ceiling_bytes, kinds)
    for name, path in dropped:
        remove = by_name[name].remove or _unlink
        try:
            remove(path)
        except OSError:
            pass
    return len(dropped)


def _unlink(path: str) -> None:
    os.remove(path)


class Chores:
    """One owned background worker with no process-global lifecycle.

    It may run several lanes -- threads each stepping its own share of the
    library on its own connection -- because a decode is CPU and the machine
    has cores to spare; measured on one lane, a fresh library of 157,000
    photographs would take a day to tile. Lanes share nothing but the stop
    event and the done counter.
    """

    def __init__(
        self,
        open_conn,
        kinds: Iterable[cache.Kind],
        *,
        on_screen: Callable[[], Iterable[int]] = lambda: (),
        yield_to: Callable[[], bool] = lambda: False,
        ceiling_bytes: int | None = None,
        lanes: int = 1,
    ):
        self._open_conn = open_conn
        self._kinds = tuple(kinds)
        self._on_screen = on_screen
        self._yield_to = yield_to
        self._ceiling_bytes = ceiling_bytes
        self._lanes = max(1, int(lanes))
        self._stopped = threading.Event()
        # One wake event per lane. A shared one lost wakeups: the first lane
        # to wake cleared it and the others slept out their wait, which made
        # stopping take up to the whole idle wait -- and a test's close time out.
        self._wake = [threading.Event() for _ in range(self._lanes)]
        self._threads: list[threading.Thread] = []
        # Items finished since start. A window asks for this number and re-reads
        # what it holds only when it moved -- the whole of how the grid learns
        # that identity, metadata or a tile landed behind it.
        self.done = 0

    @property
    def running(self) -> bool:
        return any(thread.is_alive() for thread in self._threads)

    def start(self) -> bool:
        """Start once. Return false when this instance is already running."""

        if self.running:
            return False
        self._stopped.clear()
        self._threads = [
            threading.Thread(target=self._run, args=(lane,), name=f"chores-{lane}", daemon=True)
            for lane in range(self._lanes)
        ]
        for thread in self._threads:
            thread.start()
        return True

    def nudge(self) -> None:
        """Something is newly owed -- a sweep admitted photographs, the window
        looked elsewhere -- so an idle lane should not sleep out its wait."""

        for wake in self._wake:
            wake.set()

    def stop(self, timeout: float = 5.0) -> bool:
        """Finish the items in hand, close the catalogs, and report success."""

        self._stopped.set()
        for wake in self._wake:
            wake.set()
        for thread in self._threads:
            thread.join(timeout)
        stopped = not self.running
        if stopped:
            self._threads = []
        return stopped

    def _run(self, lane: int = 0) -> None:
        conn = None
        try:
            conn = self._open_conn()
            while not self._stopped.is_set():
                try:
                    did = step(
                        conn,
                        self._kinds,
                        on_screen=self._on_screen(),
                        yield_to=self._yield_to,
                        share=(lane, self._lanes),
                    )
                except Exception:
                    log.exception("worker=chores step failed")
                    did = None
                if did:
                    self.done += 1

                if did is None and lane == 0 and self._ceiling_bytes is not None:
                    try:
                        freed = sweep_cache(conn, self._ceiling_bytes, self._kinds)
                        if freed:
                            log.info("worker=chores swept=%s", freed)
                    except Exception:
                        log.exception("worker=chores sweep failed")

                # Idle lanes wait to be nudged, and stop wakes them too, so
                # both new work and shutdown are immediate.
                if did:
                    self._stopped.wait(0.05)
                else:
                    self._wake[lane].wait(5.0)
                    self._wake[lane].clear()
        except Exception:
            log.exception("worker=chores stopped unexpectedly")
        finally:
            if conn is not None:
                conn.close()
