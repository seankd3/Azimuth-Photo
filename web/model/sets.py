"""A named group of photographs — and there is only one kind of those.

Collections, keywords, saved views, stacks and the trash were five
implementations of one idea: 6,258 lines across `features/collections` (2,167),
`features/stacks` (1,249), `features/library` (1,830) and `features/trash`
(1,012), over eight tables — `collections`, `collection_images`,
`collection_links`, `saved_views`, `stacks`, `stack_members`, `people`,
`image_tags`. Measured on the live 157,236-photograph catalog **all eight hold
zero rows**, while the log beside them carried `keyword` 296 and
`collection_meta` 5. Every one of them was already working in the one place
nobody had built a table for.

They differ in exactly one way, and it is not enough to be a second feature:

    a collection   you say which photographs are in it
    a keyword      you say which photographs are in it
    a saved view   a query says which photographs are in it

So a set is **a named scope**, and the axis is enumerated-or-computed. A set
with a `query` computes its members; one without remembers them. `kind` exists
only because the desktop shows the three in three places — it names a surface,
not a mechanism.

`decisions.py` wrote the design before this module existed: *"a collection had
to be some photo to be decided about"* — it does not, a set is a subject like
any other. Two kinds of row and no table:

* **the set is**, one decision holding its kind, name and query;
* **a photograph is in it**, one decision per photograph, under the set's family.

Identity is stable and the name is not, so renaming touches no membership row.
Hierarchy is not stored either: a keyword named `travel/japan` has depth 2 and
parent `travel`, because a path is a name with separators in it — which is the
same trick `library.folders` plays on tails, and it is why there is no keyword
tree table.

What falls out, each of which used to be code: removing is a row rather than a
delete, so it undoes and audits like everything else; an empty set still exists,
because it is its own descriptor; and there is no cascade to get wrong, which is
what `test_trash_schema_deletion.py` was 297 lines of.
"""

from __future__ import annotations

import uuid
from typing import Any

from model import decisions

# Members of a set live under this prefix plus the set's id, so one indexed
# `family = ?` lookup is a whole membership list. The same string is the set's
# own subject, so the log reads the same either way round.
IN = "in:"

# The descriptor: kind, name and query, in one decision. Three families would
# mean three reads to answer "what is this", and a rename that had to know
# about queries.
SET = "set"

COLLECTION, KEYWORD, VIEW = "collection", "keyword", "view"


def family(set_id: str) -> str:
    """The decision family carrying membership of one set."""

    set_id = str(set_id).strip()
    if not set_id:
        raise ValueError("a set needs an id")
    if set_id.startswith(IN):
        raise ValueError(f"{set_id!r} is a family, not a set id")
    return IN + set_id


def describe(conn, set_id: str) -> dict | None:
    """What this set is, or None if it never existed or was forgotten."""

    kind = family(set_id)
    if decisions.latest(conn, kind, decisions.FORGET):
        return None
    said = decisions.latest(conn, kind, SET)
    if not said:
        return None
    return {"id": set_id, **said}


def create(conn, name: str, *, kind: str = COLLECTION, query: Any = None,
           set_id: str | None = None) -> str:
    """Mint a set and return its id. The id never changes; the name may."""

    name = str(name).strip()
    if not name:
        raise ValueError("a set needs a name")
    set_id = str(set_id or uuid.uuid4().hex[:12])
    decisions.decide(conn, family(set_id), SET,
                     {"kind": str(kind), "name": name, "query": query})
    return set_id


def amend(conn, set_id: str, **fields) -> dict | None:
    """Change the name or the query. One more descriptor, no membership touched."""

    said = describe(conn, set_id)
    if said is None:
        return None
    said = {**said, **{k: v for k, v in fields.items() if v is not None}}
    decisions.decide(conn, family(set_id), SET,
                     {"kind": said["kind"], "name": said["name"], "query": said.get("query")})
    return said


def forget(conn, set_id: str) -> None:
    """Stop offering this set. Its members' rows stay readable in the log."""

    decisions.decide(conn, family(set_id), decisions.FORGET, True)


def all(conn, *, kind: str | None = None) -> list[dict]:
    """Every set that still exists, by name."""

    out = []
    for subject, said in decisions.current(conn, SET).items():
        if not str(subject).startswith(IN) or not said:
            continue
        if decisions.latest(conn, subject, decisions.FORGET):
            continue
        if kind is not None and said.get("kind") != kind:
            continue
        out.append({"id": str(subject)[len(IN):], **said})
    return sorted(out, key=lambda s: str(s.get("name", "")).lower())


def _say(conn, set_id: str, subjects, yes: bool) -> int:
    kind = family(set_id)
    written = 0
    for subject in subjects:
        decisions.decide(conn, subject, kind, yes)
        written += 1
    return written


def add(conn, set_id: str, subjects) -> int:
    """Put photographs in a set. Returns how many rows were appended."""

    return _say(conn, set_id, subjects, True)


def remove(conn, set_id: str, subjects) -> int:
    """Take photographs out. A later row saying no, never a delete."""

    return _say(conn, set_id, subjects, False)


def members(conn, set_id: str) -> list[str]:
    """Everything currently in the set, by content hash.

    `decisions.current` picks the last row per subject in one pass, so this is
    one indexed query however many times the set has been edited. A computed
    set has no membership rows — ask `scope.of_set` instead, which knows which
    sort it is.
    """

    return sorted(s for s, yes in decisions.current(conn, family(set_id)).items() if yes is True)


def counts(conn) -> dict[str, int]:
    """How many photographs are in every set, in one pass.

    `members()` per set is one query per set, which reads well and is an N+1 —
    a keyword panel listing three hundred keywords would issue three hundred
    queries to print three hundred numbers. The window function partitions by
    subject *and* family, so one scan answers for every set at once.
    """

    rows = conn.execute(
        """
        SELECT family, COUNT(*) AS n FROM (
            SELECT subject, family, value,
                   ROW_NUMBER() OVER (PARTITION BY subject, family ORDER BY at DESC, id DESC) AS rank
            FROM decisions WHERE family LIKE ?
        ) WHERE rank = 1 AND value = 'true'
        GROUP BY family
        """,
        (IN + "%",),
    ).fetchall()
    return {r["family"][len(IN):]: int(r["n"]) for r in rows}


def sets_of(conn, subject: str, *, kind: str | None = None) -> list[str]:
    """The ids of every set one photograph is in — a reverse index, unindexed."""

    rows = conn.execute(
        """
        SELECT family, value FROM (
            SELECT family, value,
                   ROW_NUMBER() OVER (PARTITION BY family ORDER BY at DESC, id DESC) AS rank
            FROM decisions WHERE subject = ? AND family LIKE ?
        ) WHERE rank = 1
        """,
        (str(subject), IN + "%"),
    ).fetchall()
    ids = [r["family"][len(IN):] for r in rows if decisions.loaded(r) is True]
    if kind is None:
        return sorted(ids)
    return sorted(i for i in ids if (describe(conn, i) or {}).get("kind") == kind)
