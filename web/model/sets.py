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

Collections and keywords differ only in where the desktop presents them. Both
are named sets whose membership the owner chooses. A saved query does not live
here until V2 has a real query language; the previous `query: Any` field stored
opaque values that no code could execute and therefore described a feature
that did not exist.

`decisions.py` wrote the design before this module existed: *"a collection had
to be some photo to be decided about"* — it does not, a set is a subject like
any other. Two kinds of row and no table:

* **the set is**, one decision holding its kind and name;
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

import re
import uuid

from model import decisions

# Members of a set live under this prefix plus the set's id, so one indexed
# `family = ?` lookup is a whole membership list. The same string is the set's
# own subject, so the log reads the same either way round.
IN = "in:"

# The descriptor: kind and name in one decision. Two families would mean two
# reads to answer "what is this" and a rename that could be applied halfway.
SET = "set"

# The stored spelling predates the Albums rename; the decision log is
# append-only, so the bytes keep their old word and the code speaks the new.
ALBUM, LABEL = "collection", "keyword"
KINDS = frozenset((ALBUM, LABEL))
SET_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
PHOTO_HASH = re.compile(r"^[0-9a-f]{64}$")


def family(set_id: str) -> str:
    """The decision family carrying membership of one set."""

    set_id = str(set_id).strip()
    if not SET_ID.fullmatch(set_id):
        raise ValueError("a set id uses only letters, numbers, hyphens, and underscores")
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


def create(conn, name: str, *, kind: str = ALBUM, criteria=None, set_id: str | None = None) -> str:
    """Mint a set and return its id. The id never changes; the name may.

    With `criteria` the set is smart: its members are whatever the stored
    chips match right now, and nothing is ever written per photograph.
    Without, it is fixed and `add`/`remove` say who is in.
    """

    name = str(name).strip()
    if not name:
        raise ValueError("a set needs a name")
    if kind not in KINDS:
        raise ValueError(f"a set kind is one of {sorted(KINDS)}")
    set_id = str(set_id or uuid.uuid4().hex[:12])
    family(set_id)
    if decisions.latest(conn, family(set_id), SET) is not None:
        raise ValueError(f"set id was already used: {set_id}")
    said = {"kind": kind, "name": name}
    if criteria:
        from model import criteria as chips

        said["criteria"] = chips.check(criteria)
    decisions.decide(conn, family(set_id), SET, said)
    return set_id


def redefine(conn, set_id: str, criteria) -> dict | None:
    """Change a smart set's rules — or drop them, which is how Freeze ends.

    The descriptor is rewritten whole with the same kind and name; membership
    rows are untouched, which is exactly why freezing works: the members a
    caller wrote a moment ago become the answer the instant the rules leave.
    """

    said = describe(conn, set_id)
    if said is None:
        return None
    kept = {"kind": said["kind"], "name": said["name"]}
    if criteria:
        from model import criteria as chips

        kept["criteria"] = chips.check(criteria)
    decisions.decide(conn, family(set_id), SET, kept)
    return {"id": set_id, **kept}


def rename(conn, set_id: str, name: str) -> dict | None:
    """Change the display name. Membership and identity do not move."""

    said = describe(conn, set_id)
    if said is None:
        return None
    name = str(name).strip()
    if not name:
        raise ValueError("a set needs a name")
    said = {**said, "name": name}
    decisions.decide(conn, family(set_id), SET, {k: v for k, v in said.items() if k != "id"})
    return said


def forget(conn, set_id: str) -> bool:
    """Stop offering this set. Its members' rows stay readable in the log."""

    if describe(conn, set_id) is None:
        return False
    decisions.decide(conn, family(set_id), decisions.FORGET, True)
    return True


def remember(conn, set_id: str) -> bool:
    """Offer a forgotten set again -- the way back from `forget`, one more
    row in the same log, everything it held still there."""

    kind = family(set_id)
    if not decisions.latest(conn, kind, decisions.FORGET):
        return False
    decisions.decide(conn, kind, decisions.FORGET, False)
    return True


def all(conn, *, kind: str | None = None) -> list[dict]:
    """Every set that still exists, by name."""

    if kind is not None and kind not in KINDS:
        raise ValueError(f"a set kind is one of {sorted(KINDS)}")
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
    if describe(conn, set_id) is None:
        raise ValueError(f"no such set: {set_id}")
    wanted = sorted({str(subject) for subject in subjects})
    if any(not PHOTO_HASH.fullmatch(subject) for subject in wanted):
        raise ValueError("set membership requires a BLAKE2b-256 photo identity")
    for subject in wanted:
        decisions.decide(conn, subject, kind, yes)
    return len(wanted)


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


def counts(conn) -> dict[str, int]:
    """How many photographs are in every set, in one pass.

    `members()` per set is one query per set, which reads well and is an N+1 —
    a keyword panel listing three hundred keywords would issue three hundred
    queries to print three hundred numbers. The window function partitions by
    subject *and* family, so one scan answers for every set at once.
    """

    rows = conn.execute(
        f"""
        SELECT family, COUNT(*) AS n FROM (
            SELECT subject, family, value,
                   ROW_NUMBER() OVER (PARTITION BY subject, family ORDER BY
                       {decisions.AUTHORITY_SQL} DESC, at DESC, id DESC) AS rank
            FROM decisions WHERE family LIKE ?
        ) WHERE rank = 1 AND value = 'true'
        GROUP BY family
        """,
        (IN + "%",),
    ).fetchall()
    return {r["family"][len(IN):]: int(r["n"]) for r in rows}


def sets_of(conn, subject: str, *, kind: str | None = None) -> list[str]:
    """The ids of every set one photograph is in — a reverse index, unindexed."""

    subject = str(subject)
    if not PHOTO_HASH.fullmatch(subject):
        raise ValueError("set membership requires a BLAKE2b-256 photo identity")

    rows = conn.execute(
        f"""
        SELECT family, value FROM (
            SELECT family, value,
                   ROW_NUMBER() OVER (PARTITION BY family ORDER BY
                       {decisions.AUTHORITY_SQL} DESC, at DESC, id DESC) AS rank
            FROM decisions WHERE subject = ? AND family LIKE ?
        ) WHERE rank = 1
        """,
        (subject, IN + "%"),
    ).fetchall()
    ids = [r["family"][len(IN):] for r in rows if decisions.loaded(r) is True]
    if kind is None:
        return sorted(ids)
    return sorted(i for i in ids if (describe(conn, i) or {}).get("kind") == kind)
