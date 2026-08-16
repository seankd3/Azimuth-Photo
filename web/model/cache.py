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
    evictable: bool = True
    # Which photos should have one, as a SQL condition over `images i`. This is
    # the half of "owed is a query" that a kind owns: the work layer asks
    # *should exist* minus *is cached* and needs to be told the first half
    # exactly once, here, rather than in a scheduler per kind.
    wants: str = "1"
    # Can this machine make it right now? A missing model, no GPU, a helper's
    # job. Asked, never remembered.
    here: Callable[[], bool] = lambda: True


@dataclass(frozen=True)
class Made:
    """What a compute returned: a file on disk, or a value, or neither."""

    path: str | None = None
    value: Any = None
    bytes: int = 0


_KINDS: dict[str, Kind] = {}


def register(kind: Kind) -> Kind:
    """Declare a kind. One registry, so an unknown kind is an error, not a guess."""

    _KINDS[kind.name] = kind
    return kind


def unregister(name: str) -> None:
    _KINDS.pop(str(name), None)


def kinds() -> dict[str, Kind]:
    """A copy, so iterating cannot be disturbed by a kind registering late."""

    return dict(_KINDS)


def kind_of(name: str) -> Kind:
    try:
        return _KINDS[str(name)]
    except KeyError:
        raise KeyError(f"no such cache kind: {name!r}") from None


def canonical(kind: str, recipe: dict[str, Any] | None) -> str:
    """The recipe, spelled one way.

    Sorted keys so `{a,b}` and `{b,a}` are one entry, and restricted to the
    kind's declared parameters so nothing incidental — a timestamp, a path, a
    row version — can get in and split the cache per machine and per run.
    """

    recipe = dict(recipe or {})
    declared = set(kind_of(kind).params)
    unknown = sorted(set(recipe) - declared)
    if unknown:
        raise ValueError(f"{kind} takes {sorted(declared)}; refused {unknown}")
    return json.dumps({k: recipe[k] for k in sorted(recipe)}, separators=(",", ":"), sort_keys=True)


def get(conn, hash: str, kind: str, recipe: dict[str, Any] | None = None) -> dict | None:
    """The stored answer, ready or failed, or None if we never asked."""

    row = conn.execute(
        "SELECT * FROM cache WHERE hash = ? AND kind = ? AND recipe = ?",
        (str(hash), str(kind), canonical(kind, recipe)),
    ).fetchone()
    return dict(row) if row is not None else None


def put(conn, hash: str, kind: str, made: Made, recipe: dict[str, Any] | None = None) -> None:
    _write(conn, hash, kind, canonical(kind, recipe), READY, made.path, made.value, made.bytes, None)


def failed(conn, hash: str, kind: str, note: str, recipe: dict[str, Any] | None = None) -> None:
    """Remember that this could not be made, and why.

    Once. A pass that rediscovers the same unreadable file is a pass that never
    reaches the readable ones behind it.
    """

    _write(conn, hash, kind, canonical(kind, recipe), FAILED, None, None, 0, str(note)[:500])


def _write(conn, hash, kind, recipe, state, path, value, size, note) -> None:
    conn.execute(
        "INSERT INTO cache(hash, kind, recipe, state, path, value, bytes, note, at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(hash, kind, recipe) DO UPDATE SET "
        "state=excluded.state, path=excluded.path, value=excluded.value, "
        "bytes=excluded.bytes, note=excluded.note, at=excluded.at",
        (str(hash), str(kind), recipe, state, path, value, int(size or 0), note, time.time()),
    )


def make(conn, hash: str, kind: str, source: str, recipe: dict[str, Any] | None = None,
         *, remake: bool = False) -> dict | None:
    """The answer, from cache or by computing it. None if it cannot be made.

    `source` is the file to look at — whatever `open()` said. A kind this
    machine cannot serve returns None *without* recording a failure, because
    "not here" is about the machine and would otherwise be remembered as a
    fact about the photo, poisoning the entry for the helper that can.
    """

    entry = kind_of(kind)
    recipe_text = canonical(kind, recipe)
    if not remake:
        row = conn.execute(
            "SELECT * FROM cache WHERE hash = ? AND kind = ? AND recipe = ?",
            (str(hash), kind, recipe_text),
        ).fetchone()
        if row is not None:
            return dict(row) if row["state"] == READY else None

    if not entry.here():
        return None

    try:
        made = entry.compute(source, hash, **(recipe or {}))
    except Exception as error:  # noqa: BLE001 - the note is the whole point
        failed(conn, hash, kind, f"{type(error).__name__}: {error}", recipe)
        conn.commit()
        return None

    put(conn, hash, kind, made, recipe)
    conn.commit()
    return get(conn, hash, kind, recipe)


def forget(conn, hash: str, *, kind: str | None = None) -> int:
    """Drop cached answers so they are made again. Never touches decisions."""

    if kind:
        cursor = conn.execute("DELETE FROM cache WHERE hash = ? AND kind = ?", (str(hash), str(kind)))
    else:
        cursor = conn.execute("DELETE FROM cache WHERE hash = ?", (str(hash),))
    return cursor.rowcount


def size(conn, *, kind: str | None = None) -> int:
    sql = "SELECT COALESCE(SUM(bytes), 0) AS total FROM cache"
    args: tuple = ()
    if kind:
        sql += " WHERE kind = ?"
        args = (str(kind),)
    return int(conn.execute(sql, args).fetchone()["total"])


def evict(conn, ceiling_bytes: int) -> list[str]:
    """Bring the cache under one ceiling, oldest first. Returns paths to unlink.

    One number, one order. Not a tier policy, not a per-kind budget, not a
    least-recently-used accounting table — those exist to decide *which* answer
    to lose, and every answer here can be made again.

    Never-evict kinds are excluded before anything is chosen, so a run that
    frees nothing is a run that frees nothing rather than one that quietly
    deletes the embeddings it takes hours to remake.
    """

    keepers = [name for name, kind in _KINDS.items() if not kind.evictable]
    hole = ",".join("?" for _ in keepers)
    where = f"WHERE kind NOT IN ({hole})" if keepers else ""
    total = int(conn.execute(
        f"SELECT COALESCE(SUM(bytes), 0) AS total FROM cache {where}", keepers
    ).fetchone()["total"])
    if total <= ceiling_bytes:
        return []

    dropped: list[str] = []
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
            dropped.append(row["path"])
    conn.commit()
    return dropped
