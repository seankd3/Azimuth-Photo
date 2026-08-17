"""A named group of photographs, which is not a thing to store.

Collections, keywords, saved views, stacks and the trash were five
implementations of one idea — 6,258 lines across `features/collections`,
`features/stacks`, `features/library` and `features/trash`, over eight tables:
`collections`, `collection_images`, `collection_links`, `saved_views`, `stacks`,
`stack_members`, `people`, `image_tags`. Measured on the live 157,236-photograph
catalog, **all eight hold zero rows**, while the log already carried `keyword`
(296 rows) and `collection_meta` (5). The feature was working in the one place
nobody had built a table for.

That is the whole argument. Membership is *something you decided*, and
`decisions` is where decisions go, so a set needs no storage of its own:

    add(conn, "portfolio", hashes)      one appended row per photograph
    members(conn, "portfolio")          the ones whose latest row says yes
    sets_of(conn, hash)                 every set this photograph is in
    names(conn)                         every set there is

Four consequences, each of which used to be code:

* **Removing is a row, not a delete.** So it undoes, audits and syncs like every
  other decision, and `decisions.history()` already tells you when a photograph
  left a set and when it came back.
* **An empty set does not exist.** There is nothing to garbage-collect, no
  orphaned `collection_images` rows, and no cascade to get wrong — which is what
  `test_trash_schema_deletion.py` was 297 lines of.
* **A set is a subject.** Its name, when it was made and what it is for are
  `decide(conn, name, decisions.NAME, ...)`. `decisions.py`'s own docstring
  wrote this before the module existed: *"a collection had to be some photo to
  be decided about"* — it does not, it is a subject like any other.
* **Renaming is `decisions.carry()`.** Already written, already tested.

The trash is a set called `trashed`, and stacks are sets whose name is the
group's identity. Neither needs a table, a cascade or a service.
"""

from __future__ import annotations

from model import decisions

# A set's family is its name behind this prefix, so one indexed `family = ?`
# lookup is a whole membership list and the prefix is the only reserved word.
IN = "in:"


def family(name: str) -> str:
    """The decision family that carries membership of one set."""

    name = str(name).strip()
    if not name:
        raise ValueError("a set needs a name")
    if name.startswith(IN):
        raise ValueError(f"{name!r} is already a family, not a set name")
    return IN + name


def _say(conn, name: str, subjects, yes: bool) -> int:
    kind = family(name)
    written = 0
    for subject in subjects:
        decisions.decide(conn, subject, kind, yes)
        written += 1
    return written


def add(conn, name: str, subjects) -> int:
    """Put photographs in a set. Returns how many rows were appended."""

    return _say(conn, name, subjects, True)


def remove(conn, name: str, subjects) -> int:
    """Take photographs out. A later row saying no, never a delete."""

    return _say(conn, name, subjects, False)


def members(conn, name: str) -> list[str]:
    """Everything currently in the set, by content hash.

    `decisions.current` picks the last row per subject in one pass, so this is
    one indexed query however many times the set has been edited.
    """

    return sorted(s for s, yes in decisions.current(conn, family(name)).items() if yes)


def sets_of(conn, subject: str) -> list[str]:
    """Every set one photograph is in — the reverse index, without an index."""

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
    return sorted(r["family"][len(IN):] for r in rows if decisions.loaded(r))


def names(conn) -> list[str]:
    """Every set that has ever had a member. One that has emptied still answers."""

    rows = conn.execute(
        "SELECT DISTINCT family FROM decisions WHERE family LIKE ?", (IN + "%",)
    ).fetchall()
    return sorted(r["family"][len(IN):] for r in rows)
