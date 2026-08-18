"""What we computed. Everything here is disposable, and that is the point.

A thumbnail, a Develop base, an embedding, a caption, a face vector and an EXIF
read are one idea: *the answer to a question about some bytes*. They were six
subsystems with six schedulers, six backlogs and six ways of being stale. Here
they are rows keyed on `(hash, kind, recipe)` — what we looked at, what we
asked, and with which settings.

**Keyed on the hash, never the path.** Every re-render wave this project has
suffered came from a key that folded in a location: move a folder and 144,000
thumbnails are strangers. Bytes do not change when they move, so their answers
do not either.

**`recipe` is a pure function of its inputs, never a timestamp.** That is not
advice here, it is enforced: a kind declares its parameters and anything else
is refused. Feeding `updated_at` in is how reset-then-redo re-rendered
identical pixels and two machines never shared an entry, and it is the kind of
bug that comes back as a one-word keyword argument.

**A failure is an answer.** Recorded once, with why. The alternative is a
worker that rediscovers the same unreadable file every pass, forever.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from collections.abc import Iterable
from typing import Any, Callable

READY = "ready"
FAILED = "failed"


@dataclass(frozen=True)
class Kind:
    """How to compute one sort of answer, and what it costs to.

    Small on purpose. Four questions is all the work layer ever asks about a
    kind: how do I make it, what does it cost, can this machine make it at all,
    and may I throw it away when the disk fills.
    """

    name: str
    # compute(source, hash, **recipe). The hash is passed because a kind
    # that writes a file has to name it after what it shows, and re-deriving
    # it would mean reading 8 MiB of the photograph again.
    compute: Callable[..., "Made"]
    # Roughly what one costs, in seconds. Used to order work and to refuse a
    # frame this machine cannot afford; never for a progress bar.
    cost: float = 1.0
    # The recipe's whole vocabulary. Anything outside it is refused, which is
    # the mechanical half of "recipe is never a timestamp".
    params: tuple[str, ...] = ()
    # Which recipes are worth making before anyone asks for them. `wants` says
    # *which photographs* should have this kind; this says *which versions*.
    #
    # A kind with no parameters has exactly one version, spelled `{}` — which is
    # why the work loop could get away with passing no recipe at all, right up
    # until a kind had sizes. `tile` does, and the loop was recording every tile
    # it made under `{}` while `/api/thumb` looked for
    # `{"edits":null,"rotate":0,"size":400}`: 5,340 rows nothing would ever
    # read, the grid remaking on demand what the loop had just made, and 1920
    # and 3840 never made ahead at all.
    #
    # Asked, never remembered, for the same reason `here` is: an embedding's
    # recipe names the model in use, and the model is a setting the owner can
    # change while the app is running. A tuple fixed at import would keep
    # making vectors for the model they switched away from.
    ahead: Callable[[], tuple[dict[str, Any], ...]] = lambda: ({},)
    evictable: bool = True
    # Which photos should have one, as a SQL condition over `images i`. This is
    # the half of "owed is a query" that a kind owns: the work layer asks
    # *should exist* minus *is cached* and needs to be told the first half
    # exactly once, here, rather than in a scheduler per kind.
    wants: str = "1"
    # Can this machine make it right now? A missing model, no GPU, a helper's
    # job. Asked, never remembered.
    here: Callable[[], bool] = lambda: True
    # How to remove one. Almost always "unlink the path", but a Develop base is
    # three files sharing a stem -- the pixels, the metadata and a preview --
    # and an eviction that took one of them would leave a base that looks
    # present and cannot be read. The registry already answers what a kind
    # costs and whether it can be made; how to throw one away belongs beside
    # them rather than as a special case inside the evictor.
    remove: Callable[[str], None] | None = None


@dataclass(frozen=True)
class Made:
    """What a compute returned: a file on disk, or a value, or neither."""

    path: str | None = None
    value: Any = None
    bytes: int = 0


def canonical(kind: Kind, recipe: dict[str, Any] | None) -> str:
    """The recipe, spelled one way.

    Sorted keys so `{a,b}` and `{b,a}` are one entry, and restricted to the
    kind's declared parameters so nothing incidental — a timestamp, a path, a
    row version — can get in and split the cache per machine and per run.
    """

    recipe = dict(recipe or {})
    declared = set(kind.params)
    unknown = sorted(set(recipe) - declared)
    if unknown:
        raise ValueError(f"{kind.name} takes {sorted(declared)}; refused {unknown}")
    return json.dumps({k: recipe[k] for k in sorted(recipe)}, separators=(",", ":"), sort_keys=True)


def get(conn, hash: str, kind: Kind, recipe: dict[str, Any] | None = None) -> dict | None:
    """The stored answer, ready or failed, or None if we never asked."""

    row = conn.execute(
        "SELECT * FROM cache WHERE hash = ? AND kind = ? AND recipe = ?",
        (str(hash), kind.name, canonical(kind, recipe)),
    ).fetchone()
    return dict(row) if row is not None else None


def put(conn, hash: str, kind: Kind, made: Made, recipe: dict[str, Any] | None = None) -> None:
    _write(conn, hash, kind.name, canonical(kind, recipe), READY, made.path, made.value, made.bytes, None)


def failed(conn, hash: str, kind: Kind, note: str, recipe: dict[str, Any] | None = None) -> None:
    """Remember that this could not be made, and why.

    Once. A pass that rediscovers the same unreadable file is a pass that never
    reaches the readable ones behind it.
    """

    _write(conn, hash, kind.name, canonical(kind, recipe), FAILED, None, None, 0, str(note)[:500])


def _write(conn, hash, kind, recipe, state, path, value, size, note) -> None:
    conn.execute(
        "INSERT INTO cache(hash, kind, recipe, state, path, value, bytes, note, at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(hash, kind, recipe) DO UPDATE SET "
        "state=excluded.state, path=excluded.path, value=excluded.value, "
        "bytes=excluded.bytes, note=excluded.note, at=excluded.at",
        (str(hash), str(kind), recipe, state, path, value, int(size or 0), note, time.time()),
    )


def make(conn, hash: str, kind: Kind, source: str, recipe: dict[str, Any] | None = None,
         *, remake: bool = False) -> dict | None:
    """The answer, from cache or by computing it. None if it cannot be made.

    `source` is the file to look at — whatever `open()` said. A kind this
    machine cannot serve returns None *without* recording a failure, because
    "not here" is about the machine and would otherwise be remembered as a
    fact about the photo, poisoning the entry for the helper that can.
    """

    recipe_text = canonical(kind, recipe)
    if not remake:
        row = conn.execute(
            "SELECT * FROM cache WHERE hash = ? AND kind = ? AND recipe = ?",
            (str(hash), kind.name, recipe_text),
        ).fetchone()
        if row is not None:
            return dict(row) if row["state"] == READY else None

    if not kind.here():
        return None

    try:
        made = kind.compute(source, hash, **(recipe or {}))
    except Exception as error:  # noqa: BLE001 - the note is the whole point
        failed(conn, hash, kind, f"{type(error).__name__}: {error}", recipe)
        conn.commit()
        return None

    put(conn, hash, kind, made, recipe)
    conn.commit()
    return get(conn, hash, kind, recipe)


def forget(conn, hash: str, *, kind: Kind | None = None) -> int:
    """Drop cached answers so they are made again. Never touches decisions."""

    if kind:
        cursor = conn.execute("DELETE FROM cache WHERE hash = ? AND kind = ?", (str(hash), kind.name))
    else:
        cursor = conn.execute("DELETE FROM cache WHERE hash = ?", (str(hash),))
    return cursor.rowcount


def size(conn, *, kind: Kind | None = None) -> int:
    sql = "SELECT COALESCE(SUM(bytes), 0) AS total FROM cache"
    args: tuple = ()
    if kind:
        sql += " WHERE kind = ?"
        args = (kind.name,)
    return int(conn.execute(sql, args).fetchone()["total"])


def evict(
    conn,
    ceiling_bytes: int,
    kinds: Iterable[Kind],
) -> list[tuple[str, str]]:
    """Bring the cache under one ceiling, oldest first. Returns (kind, path).

    One number, one order. Not a tier policy, not a per-kind budget, not a
    least-recently-used accounting table — those exist to decide *which* answer
    to lose, and every answer here can be made again.

    Never-evict kinds are excluded before anything is chosen, so a run that
    frees nothing is a run that frees nothing rather than one that quietly
    deletes the embeddings it takes hours to remake.
    """

    keepers = [kind.name for kind in kinds if not kind.evictable]
    hole = ",".join("?" for _ in keepers)
    where = f"WHERE kind NOT IN ({hole})" if keepers else ""
    total = int(conn.execute(
        f"SELECT COALESCE(SUM(bytes), 0) AS total FROM cache {where}", keepers
    ).fetchone()["total"])
    if total <= ceiling_bytes:
        return []

    dropped: list[tuple[str, str]] = []
    for row in conn.execute(
        f"SELECT hash, kind, recipe, path, bytes FROM cache {where} ORDER BY at ASC", keepers
    ).fetchall():
        if total <= ceiling_bytes:
            break
        conn.execute(
            "DELETE FROM cache WHERE hash = ? AND kind = ? AND recipe = ?",
            (row["hash"], row["kind"], row["recipe"]),
        )
        total -= int(row["bytes"] or 0)
        if row["path"]:
            dropped.append((row["kind"], row["path"]))
    conn.commit()
    return dropped
