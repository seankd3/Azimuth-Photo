"""A named group of photographs, which is not a thing to store.

Collections, keywords, saved views, stacks and the trash are five
implementations of one idea — 6,258 lines across `features/collections` (2,167),
`features/stacks` (1,249), `features/library` (1,830) and `features/trash`
(1,012), over eight tables: `collections`, `collection_images`,
`collection_links`, `saved_views`, `stacks`, `stack_members`, `people`,
`image_tags`. Measured on the live 157,236-photograph catalog, **all eight hold
zero rows**, while the log already carried `keyword` 296 and `collection_meta` 5.

`decisions.py`'s own docstring wrote the design before this module existed: *"a
collection had to be some photo to be decided about"* — it does not, a set is a
subject like any other. So there are two kinds of row and no table:

* **The set exists**, as a decision about the set: its name, under `NAME`.
* **A photograph is in it**, as a decision about the photograph, filed under the
  set's own family.

Membership belongs in the *family*, never in the value. `judgements.membership`
put the collection inside the value under one shared `collection_member` family,
and `decisions.current()` keeps only the latest row per subject — so a
photograph added to A and then to B read back as being in B alone. Verified.
One family per set is what makes memberships independent.

**Identity is stable and the name is not.** A set is minted with an id that never
changes, and `rename` is one more `NAME` decision, so renaming touches no
membership row. That is the whole reason the old code needed integer primary
keys, and it costs one column of nothing here.

What falls out, each of which used to be code:

* **Removing is a row, not a delete** — so it undoes, audits and syncs like every
  other decision, and `decisions.history()` already says when a photograph left
  a set and when it came back.
* **There is no cascade.** No orphaned membership rows, nothing to garbage
  collect — which is what `test_trash_schema_deletion.py` was 297 lines of.
* **An empty set still exists**, because it is its own `NAME` decision. Creating
  a collection and then filling it works without a placeholder row.

The trash is a set. Stacks are sets. Keywords are sets. None needs a table, a
cascade, or a service.
"""

from __future__ import annotations

import uuid

from model import decisions

# A set's members live under this prefix plus the set's id, so one indexed
# `family = ?` lookup is a whole membership list.
IN = "in:"


def family(set_id: str) -> str:
    """The decision family carrying membership of one set."""

    set_id = str(set_id).strip()
    if not set_id:
        raise ValueError("a set needs an id")
    if set_id.startswith(IN):
        raise ValueError(f"{set_id!r} is a family, not a set id")
    return IN + set_id


def create(conn, name: str, *, set_id: str | None = None) -> str:
    """Mint a set and return its id. The id never changes; the name may."""

    name = str(name).strip()
    if not name:
        raise ValueError("a set needs a name")
    set_id = str(set_id or uuid.uuid4().hex[:12])
    decisions.decide(conn, family(set_id), decisions.NAME, name)
    return set_id


def rename(conn, set_id: str, name: str) -> None:
    """One more decision. No membership row is touched."""

    name = str(name).strip()
    if not name:
        raise ValueError("a set needs a name")
    decisions.decide(conn, family(set_id), decisions.NAME, name)


def forget(conn, set_id: str) -> None:
    """Stop offering this set. Its members' rows stay readable in the log."""

    decisions.decide(conn, family(set_id), decisions.FORGET, True)


def name_of(conn, set_id: str) -> str | None:
    """The set's current name, or None if it was never minted or was forgotten."""

    kind = family(set_id)
    if decisions.latest(conn, kind, decisions.FORGET):
        return None
    return decisions.latest(conn, kind, decisions.NAME)


def all(conn) -> list[dict]:
    """Every set that still exists, newest name first, as {id, name}."""

    out = []
    for subject, name in decisions.current(conn, decisions.NAME).items():
        if not str(subject).startswith(IN):
            continue
        set_id = str(subject)[len(IN):]
        if decisions.latest(conn, subject, decisions.FORGET):
            continue
        out.append({"id": set_id, "name": name})
    return sorted(out, key=lambda s: str(s["name"]).lower())


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
    one indexed query however many times the set has been edited.
    """

    return sorted(s for s, yes in decisions.current(conn, family(set_id)).items() if yes is True)


def sets_of(conn, subject: str) -> list[str]:
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
    return sorted(r["family"][len(IN):] for r in rows if decisions.loaded(r) is True)
