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
this view, then newest, then the oldest debt. And one gate — chores yield while
you are using the app. Measured: browsing was 23 ms with chores quiet and
*minutes* with them running, which is the whole reason a politeness rule beats
another priority number.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Callable, Iterable

from model import cache, photos

log = logging.getLogger(__name__)

# How long the app stays "in use" after the last thing you did. Long enough
# that a pause between keystrokes is not an invitation to start a demosaic.
QUIET_AFTER_SECONDS = 2.0

_last_touch = 0.0


def touched() -> None:
    """You did something. Chores stand down."""

    global _last_touch
    _last_touch = time.monotonic()


def busy() -> bool:
    return (time.monotonic() - _last_touch) < QUIET_AFTER_SECONDS


def owed(conn, kind: str, *, recipe: dict | None = None, on_screen: Iterable[int] = (),
         limit: int = 200) -> list[dict]:
    """Photos that should have this answer and do not. The whole scheduler.

    A failed entry counts as answered: it is in `cache` with `state='failed'`,
    so the anti-join steps over it and the worker does not rediscover the same
    unreadable file on every pass. Asking again is `cache.forget`, deliberately.
    """

    entry = cache.kind_of(kind)
    recipe_text = cache.canonical(kind, recipe)
    ids = [int(i) for i in on_screen]
    hole = ",".join("?" for _ in ids)
    # Closeness to your eyes, spelled as an ORDER BY. The on-screen clause is
    # omitted entirely when nothing is on screen, so a background pass does not
    # pay for an empty IN list.
    nearest = f"i.id IN ({hole}) DESC, " if ids else ""
    return [dict(row) for row in conn.execute(
        f"""
        SELECT i.id, i.content_hash AS hash, i.tail, i.file_size
        FROM images i
        LEFT JOIN cache c
               ON c.hash = i.content_hash AND c.kind = ? AND c.recipe = ?
        WHERE i.content_hash IS NOT NULL
          AND i.vc_of IS NULL
          AND c.hash IS NULL
          AND ({entry.wants})
        ORDER BY {nearest}i.date_taken DESC, i.id DESC
        LIMIT ?
        """,
        (kind, recipe_text, *ids, int(limit)),
    )]


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
        """
        SELECT id, tail, file_size FROM images
        WHERE content_hash IS NULL AND vc_of IS NULL AND tail IS NOT NULL
        ORDER BY date_taken DESC, id DESC LIMIT ?
        """,
        (int(limit),),
    )]


def debt(conn) -> dict[str, int]:
    """How much is owed, per kind. What a status line reads; nothing depends on it."""

    tally = {"identity": len(unidentified(conn, limit=100_000))}
    for name in cache.kinds():
        try:
            tally[name] = len(owed(conn, name, limit=100_000))
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


def step(conn, *, on_screen: Iterable[int] = (), yield_to: Callable[[], bool] = busy) -> dict | None:
    """Do the single most-owed thing, or nothing at all. Returns what it did.

    One worker, one item at a time. Concurrency lives above this function — in
    how often it is called — rather than inside it, which is what keeps the
    politeness gate a single `if` instead of a pool that has to be drained.
    """

    if yield_to():
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
        for row in owed(conn, name, on_screen=on_screen, limit=1):
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
    for path in dropped:
        try:
            os.remove(path)
        except OSError:
            pass
    return len(dropped)
