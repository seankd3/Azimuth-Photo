"""Which photographs, in a form a person edits and the catalog can keep.

A smart collection needs its rules stored, and CORE.md forbids the easy
mistake: *"V2 does not store an opaque query payload that nothing can
execute."* SQL text in a row is exactly that payload — unreadable to the
person, unrewritable by the code, and a security hole besides. So the stored
form is **chips**: a list of small typed objects with a closed vocabulary,
each one sentence a person could say.

    [{"is": "stars",  "least": 3},
     {"is": "camera", "values": ["EOS R5", "EOS RP"]},
     {"is": "in",     "values": ["a1b2c3"], "not": true}]

Three rules, learned from the V1 filter language this replaces (13 facets,
one value each, no ranges, no negation — the gap Sean named on 07-09):

* **Chips AND across.** Every chip must hold. Adding a chip always narrows.
* **Values OR within.** A chip naming two cameras means either camera. This
  is where almost all of boolean algebra people actually reach for lives,
  without a query builder.
* **Any chip negates.** `"not": true` inverts the whole chip, NULL-safely —
  "not the RP" includes photographs that name no camera.

The same chips are the filter bar: a filter is an unsaved smart collection,
and saving the chips is what creates one. Intersections need no machinery of
their own — two `in` chips are "Landscapes ∩ Costa Rica" — which is why a
collection can live in exactly one place on the shelf while a photograph
belongs to many.

`compile()` turns chips into one `Scope`; the constructors it calls are the
same ones every other surface narrows with, so a smart collection cannot
drift from what the grid, Refine, search, and export mean by the same words.
"""

from __future__ import annotations

from model import scope as scopes
from model.scope import EVERYTHING, Scope, all_of, any_of, not_of

# field -> the keys a chip of that field may carry (beyond "is" and "not").
FIELDS = {
    "folder": ("values",),
    "in": ("values",),
    "camera": ("values",),
    "status": ("values",),
    "orientation": ("values",),
    "person": ("values",),
    "label": ("values",),
    # Legacy spelling from the clusters era; compiles as person-or-label so a
    # saved chip keeps answering. New chips never write it.
    "alike": ("values",),
    "stars": ("least",),
    "taken": ("from", "to"),
    # Inside one stack: the value is the cover frame's id. The chip is how
    # a collapsed run opens — the browse's resting state hides members.
    "stack": ("values",),
}

# fields whose values are a closed set rather than whatever the files say
CLOSED = {
    "status": ("unflagged", "picked"),
    "orientation": ("landscape", "portrait", "square"),
}


def check(chips) -> list[dict]:
    """The chips, validated and in canonical form — or a ValueError that says
    which chip is malformed. What `sets.create` stores is what this returns."""

    if not isinstance(chips, (list, tuple)):
        raise ValueError("criteria are a list of chips")
    out = []
    for chip in chips:
        if not isinstance(chip, dict):
            raise ValueError(f"a chip is an object, not {type(chip).__name__}")
        field = chip.get("is")
        if field not in FIELDS:
            raise ValueError(f"no such filter: {field!r}; have {sorted(FIELDS)}")
        allowed = FIELDS[field]
        unknown = sorted(set(chip) - {"is", "not"} - set(allowed))
        if unknown:
            raise ValueError(f"a {field} chip takes {sorted(allowed)}; refused {unknown}")
        clean: dict = {"is": field}
        if "values" in allowed:
            values = [str(v).strip() for v in (chip.get("values") or []) if str(v).strip()]
            if not values:
                raise ValueError(f"a {field} chip needs at least one value")
            if field in CLOSED:
                bad = sorted(set(values) - set(CLOSED[field]))
                if bad:
                    raise ValueError(f"a {field} chip takes {list(CLOSED[field])}; refused {bad}")
            clean["values"] = sorted(set(values))
        if field == "stars":
            least = int(chip.get("least", 0))
            if not 1 <= least <= 5:
                raise ValueError("a stars chip asks for one to five")
            clean["least"] = least
        if field == "taken":
            clean["from"] = str(chip.get("from") or "").strip()[:10]
            clean["to"] = str(chip.get("to") or "").strip()[:10]
            if not clean["from"] and not clean["to"]:
                raise ValueError("a taken chip needs a from or a to")
            scopes.taken(clean["from"], clean["to"])  # dates must parse
        if chip.get("not"):
            clean["not"] = True
        out.append(clean)
    return out


def compile(conn, chips, _seen: frozenset = frozenset()) -> Scope:
    """One scope from the chips: values ORed within, chips ANDed across,
    negation NULL-safe. `conn` resolves `in` chips, whose sets may themselves
    be smart — a saved intersection composes like anything else."""

    parts = []
    for chip in check(chips):
        field = chip["is"]
        if field == "folder":
            built = any_of(*(scopes.folder(v) for v in chip["values"]))
        elif field == "in":
            built = any_of(*(resolve(conn, v, _seen) for v in chip["values"]))
        elif field == "camera":
            built = scopes.camera(chip["values"])
        elif field == "status":
            built = scopes.status(chip["values"])
        elif field == "orientation":
            built = scopes.orientation(chip["values"])
        elif field == "person":
            built = scopes.person(chip["values"])
        elif field == "label":
            built = scopes.label(chip["values"])
        elif field == "alike":
            built = any_of(scopes.person(chip["values"]), scopes.label(chip["values"]))
        elif field == "stars":
            built = scopes.starred(chip["least"])
        elif field == "stack":
            built = any_of(*(scopes.stacked_under(int(v)) for v in chip["values"]))
        else:
            built = scopes.taken(chip["from"], chip["to"])
        parts.append(not_of(built) if chip.get("not") else built)
    return all_of(*parts)


def resolve(conn, set_id: str, _seen: frozenset = frozenset()) -> Scope:
    """The scope one set answers to: its stored rules if it is smart, its
    membership if it is fixed, and nothing if it no longer exists.

    A smart collection may name another set in an `in` chip; a cycle of such
    names would recurse forever, so a set already being resolved answers as
    empty — the honest reading of "this set, in terms of itself".
    """

    from model import sets

    if set_id in _seen:
        return scopes.NOTHING
    said = sets.describe(conn, set_id)
    if said is None:
        return scopes.NOTHING
    rules = said.get("criteria")
    if not rules:
        return scopes.in_set(set_id)
    # A smart album takes exceptions: what was dragged in stays in, what was
    # removed stays out — (rules ∪ pinned) ∖ denied, from the same membership
    # rows a plain album already stores. No tool in the pro tier has this;
    # their users fake it with marker keywords.
    return all_of(
        any_of(compile(conn, rules, _seen | {set_id}), scopes.in_set(set_id)),
        not_of(scopes.out_of_set(set_id)),
    )
