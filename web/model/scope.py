"""Which photographs. One argument, for every surface that narrows the library.

Before this there were six spellings of the same idea and no two agreed:

    library.photos(folder=…, starred=…)      two keyword arguments
    work.owed(kind)                          none at all — it could not be asked
    cache.Kind.wants                         a bare SQL string, no arguments
    export                                   `id_filter` plus a `collection_id`
    smart collections                        a query resolved to a set of ids
    keywords / stacks / trash                each its own table and join

Adding the seventh would have been another keyword argument on `photos()`, whose
docstring is already proudly refusing three of them. So the argument is one
thing instead, and the shape was already in the tree: `Kind.wants` is documented
as *"which photos should have one, as a SQL condition over `images i`"*. That is
a scope with no parameters. This is that, with parameters.

**A scope is a WHERE clause over `images i`, and its arguments.** It is not a
list of ids — resolving to a list would drag 157,000 hashes through Python to
ask a question SQLite can answer in place, and would make "everything" the most
expensive case instead of the cheapest.

They compose with `all_of`, which is why a caller can say *this folder, in this
collection, starred* without anyone having written that combination.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from model import decisions


@dataclass(frozen=True)
class Scope:
    """A condition over `images i`, and the arguments it binds."""

    sql: str = "1"
    args: tuple = ()

    def __bool__(self) -> bool:
        """False only for the scope that narrows nothing."""

        return self.sql != "1"


EVERYTHING = Scope()
NOTHING = Scope("0")


def all_of(*scopes: Scope) -> Scope:
    """Every scope at once. Narrowing nothing is the identity, so it drops out."""

    live = [s for s in scopes if s and s.sql != "1"]
    if not live:
        return EVERYTHING
    if any(s.sql == "0" for s in live):
        return NOTHING
    return Scope(
        " AND ".join(f"({s.sql})" for s in live),
        tuple(arg for s in live for arg in s.args),
    )


def any_of(*scopes: Scope) -> Scope:
    """At least one of the scopes. The other half of composition: `all_of`
    narrows, this widens -- a parent collection is the union of its children,
    and a chip holding two cameras is either camera."""

    live = [s for s in scopes if s.sql != "0"]
    if not live:
        return NOTHING
    if any(not s for s in live):
        return EVERYTHING
    if len(live) == 1:
        return live[0]
    return Scope(
        " OR ".join(f"({s.sql})" for s in live),
        tuple(arg for s in live for arg in s.args),
    )


def not_of(scope: Scope) -> Scope:
    """Everything the scope does not match.

    NULL-safe on purpose: SQL's three-valued logic makes `NOT (camera = ?)`
    silently drop every photograph whose camera was never read, and "not shot
    on the RP" plainly includes a photograph that names no camera at all.
    """

    if not scope:
        return NOTHING
    if scope.sql == "0":
        return EVERYTHING
    return Scope(f"NOT IFNULL(({scope.sql}), 0)", scope.args)


def folder(path: str) -> Scope:
    """Everything filed under one folder, by tail prefix."""

    if not path:
        return EVERYTHING
    prefix = str(path).replace("\\", "/").rstrip("/") + "/"
    return Scope("substr(i.tail, 1, ?) = ?", (len(prefix), prefix))


def outside(path: str) -> Scope:
    """Everything not filed under one folder.

    The default-off rule: snapshots rank and refine only when you go there
    ("Everything, just not by default" -- 07-31). A scope, not a flag, so the
    surfaces that honour it honour it in the same query they already run.
    """

    if not path:
        return EVERYTHING
    prefix = str(path).replace("\\", "/").rstrip("/") + "/"
    return Scope("substr(i.tail, 1, ?) != ?", (len(prefix), prefix))


def starred(least: int) -> Scope:
    """At least this many stars."""

    least = int(least)
    if not 0 <= least <= 5:
        raise ValueError("stars are between zero and five")
    return Scope("i.stars >= ?", (least,)) if least else EVERYTHING


def in_set(set_id: str) -> Scope:
    """Members of one set, as a subquery rather than a materialised list.

    Membership is the latest decision in the set's family being true, which is
    `decisions.LATEST_IN_FAMILY` — the same definition `current()` reads, so
    there is one answer to "is it in" and not two that can drift.
    """

    from model import sets  # circular at import time: sets names the family

    return Scope(
        f"i.content_hash IN (SELECT subject FROM ({decisions.LATEST_IN_FAMILY}) WHERE value = 'true')",
        (sets.family(set_id),),
    )


def camera(models) -> Scope:
    """Shot on one of these cameras, by the exact model the file names."""

    wanted = sorted({str(m).strip() for m in models if str(m).strip()})
    if not wanted:
        return EVERYTHING
    marks = ",".join("?" for _ in wanted)
    return Scope(f"i.camera_model IN ({marks})", tuple(wanted))


def taken(start: str = "", end: str = "") -> Scope:
    """Taken within [start, end], whole days inclusive.

    Dates are stored `YYYY-MM-DD HH:MM:SS` and compare lexically, so the range
    is two string comparisons on the indexed column; the inclusive end day
    becomes an exclusive next-day bound.
    """

    import datetime as dt

    def day(value):
        value = str(value or "").strip()[:10]
        if not value:
            return None
        return dt.date.fromisoformat(value)

    first, last = day(start), day(end)
    parts = []
    if first:
        parts.append(Scope("i.date_taken >= ?", (first.isoformat(),)))
    if last:
        after = (last + dt.timedelta(days=1)).isoformat()
        parts.append(Scope("i.date_taken < ?", (after,)))
    return all_of(*parts) if parts else EVERYTHING


def status(values) -> Scope:
    """Photographs whose cull status is one of these."""

    allowed = ("unflagged", "picked")
    wanted = sorted({str(v) for v in values if str(v) in allowed})
    if not wanted:
        return EVERYTHING
    marks = ",".join("?" for _ in wanted)
    return Scope(f"i.status IN ({marks})", tuple(wanted))


def alike(terms) -> Scope:
    """Wears one of these names — a proposal, not a fact.

    Names are tags, not a partition: each photograph's row holds its
    strongest few as a JSON list, rewritten whole as the space grows, so
    this reads whatever the current answer is; a name that no longer exists
    honestly matches nothing.
    """

    wanted = sorted({str(t).strip() for t in terms if str(t).strip()})
    if not wanted:
        return EVERYTHING
    marks = ",".join("?" for _ in wanted)
    # Palette tags and people ride the same shape — a JSON list of names per
    # identity — under two kinds with two writers; a chip reads both.
    return Scope(
        "i.content_hash IN (SELECT c.hash FROM cache c, json_each(CAST(c.value AS TEXT)) j"
        f" WHERE c.kind IN ('alike', 'people') AND c.state = 'ready' AND j.value IN ({marks}))",
        tuple(wanted),
    )


def orientation(values) -> Scope:
    """Filed sideways or upright — the shape as shown, not as stored.

    A quarter-turn decision swaps the shown width and height, so the shape is
    computed through `i.rotate`. A photograph whose dimensions were never read
    matches no orientation, and NULL-safely falls into any negation.
    """

    allowed = ("landscape", "portrait", "square")
    wanted = sorted({str(v) for v in values if str(v) in allowed})
    if not wanted:
        return EVERYTHING
    shown_w = "CASE WHEN i.rotate IN (90, 270) THEN i.height ELSE i.width END"
    shown_h = "CASE WHEN i.rotate IN (90, 270) THEN i.width ELSE i.height END"
    tests = {
        "landscape": f"{shown_w} > {shown_h}",
        "portrait": f"{shown_w} < {shown_h}",
        "square": f"{shown_w} = {shown_h}",
    }
    sql = " OR ".join(f"({tests[v]})" for v in wanted)
    return Scope(f"i.width > 0 AND i.height > 0 AND ({sql})")


def ids(image_ids) -> Scope:
    """An explicit list of image ids as one JSON-bound argument.

    Used where a caller already holds a set of rows, such as an export narrowed
    by an import batch. The SQL stays one fixed shape, so selecting the whole
    library cannot exceed SQLite's parameter limit.
    """

    wanted = sorted({int(i) for i in image_ids if int(i) > 0})
    if not wanted:
        return NOTHING
    return Scope("i.id IN (SELECT value FROM json_each(?))", (json.dumps(wanted),))


def where(scope: Scope | None) -> tuple[str, tuple]:
    """The clause and arguments, for a caller assembling a query."""

    scope = scope or EVERYTHING
    return scope.sql, tuple(scope.args)
