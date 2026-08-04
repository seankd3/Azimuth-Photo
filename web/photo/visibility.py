"""The one answer to "is this photo in the library".

The rule was hand-written in roughly two hundred places across twenty files, in
three different strengths. A rule written in two hundred places is two hundred
chances to disagree, and it cannot be repaired or reasoned about.

It lives here rather than in `data/repositories/catalog.py` because it is a fact
about a photo, not about the catalog repository — and because putting it there
made `image_deletion` and `catalog` import each other.

**The emitted text matters.** `data/schema.py` carries the same predicate in
partial indexes (`ON images(elo DESC) WHERE status IN ('kept', 'maybe')`), and
SQLite will only use those indexes for a query whose WHERE clause implies
theirs. These functions therefore emit exactly the string the queries used to
spell out, and `test_visibility_rule_is_one_rule.py` asserts it character for
character. Change the wording here and the ranking queries quietly fall back to
a table scan on 147k rows.
"""

from __future__ import annotations


def source_join(image_alias: str = "i", source_alias: str = "s") -> str:
    return f"JOIN catalog_sources {source_alias} ON {source_alias}.id = {image_alias}.source_id"


def source_included(source_alias: str = "s") -> str:
    return f"{source_alias}.included = 1"


def visible_image_condition(image_alias: str = "i") -> str:
    """The photo half of the rule, for queries already scoped to a source.

    Pass an empty alias for a single-table query that names columns bare.
    """

    prefix = f"{image_alias}." if image_alias else ""
    return f"{prefix}status IN ('kept', 'maybe') AND {prefix}missing_at IS NULL"


def active_image_condition(image_alias: str = "i", source_alias: str = "s") -> str:
    """The whole rule: the source is included, and the photo is visible.

    A photo is in the library when its source is included, the owner has not
    trashed it, and the file is where the catalog says it is.
    """

    return f"{source_included(source_alias)} AND {visible_image_condition(image_alias)}"
