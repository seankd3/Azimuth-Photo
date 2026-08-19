"""What you decided. The only thing here that cannot be recomputed.

Every other fact in Azimuth is a machine's opinion about bytes it can read
again: a hash, a thumbnail, a date, an embedding. Lose them and you wait. Lose
a decision — a keep, a star, a crop, a name — and it is simply gone, because
the only copy was in your head a year ago.

So decisions are an **append-only log**. A projected column can be overwritten
by a bad migration or a worker that thought it knew better; both have happened
here. A log cannot: the wrong write is one more row, and the row before it is
still readable.

Two shapes fall out of that, and both delete machinery:

* **A subject is any stable identity** — a photo's content hash, a folder's
  tail, a drive's uuid, a person's name. Deciding something about a folder or a
  person stops needing its own table, which is where the all-zeros fake content
  hash came from: a collection had to be *some* photo to be decided about.
* **Edit history is not a feature.** It is this log filtered to one subject, so
  `develop_history` is a query, not a table.

The machine's read of a file is cache. Your save is a decision. The decision
wins — and when they disagree, `latest()` is what says so.
"""

from __future__ import annotations

import json
import time
from typing import Any

# Families in use. Not a constraint — the table takes any string, because a new
# kind of decision must never need a migration — but naming them here is how a
# reader learns what the log holds.
STATUS = "status"      # unflagged / picked / trashed
# A rating made elsewhere -- Lightroom's, read from a sidecar, or V1's column
# adopted into the log. Evidence of taste, never the star column: a star here
# is computed from the ranking, and Refine is the only way to change it
# ("Stars should be applied by the photos elo", 07-31).
STAR = "star"          # 0-5
COMPARE = "compare"    # one photo beat another
DEVELOP = "develop"    # an edit
NAME = "name"          # a person, a roll, a folder
ROTATE = "rotate"      # 0/90/180/270, when the file itself is filed sideways
DATE = "date"          # a corrected capture date
FORGET = "forget"      # this subject is no longer wanted

# Decisions that are also browse columns: the family, its column on `images`,
# what a photograph has before any decision, and what counts as a value. The
# log is the truth and the column is its index; `library.reindex` rebuilds
# every column from this table, and a change to one writes both. One home, so
# a rule a writer knows is a rule the rebuild knows.
PROJECTED: dict[str, tuple[str, Any, Any]] = {
    STATUS: ("status", "unflagged", lambda v: v in ("unflagged", "picked", "trashed")),
    ROTATE: ("rotate", 0, lambda v: v in (0, 90, 180, 270)),
}

YOU = "you"
FILE = "file"
LRCAT = "lrcat"
OPLOG = "oplog"
AUTHORS = {YOU, FILE, LRCAT, OPLOG}

# Your explicit answer always wins over imported metadata. `oplog` is your
# answer arriving from another copy of Azimuth, so it has the same authority.
AUTHORITY_SQL = f"CASE by WHEN '{YOU}' THEN 2 WHEN '{OPLOG}' THEN 2 ELSE 1 END"


def decide(
    conn,
    subject: str,
    family: str,
    value: Any = None,
    *,
    at: float | None = None,
    by: str = YOU,
) -> int:
    """Append a decision. Returns its id.

    Never updates, never deletes. Changing your mind is a later row, and
    undoing is the row before it — which is why undo needs no separate store.
    """

    subject = str(subject).strip()
    if not subject:
        raise ValueError("a decision needs a subject")
    if not family:
        raise ValueError("a decision needs a family")
    by = str(by).strip().lower()
    if by not in AUTHORS:
        raise ValueError(f"unknown decision author: {by!r}")
    cursor = conn.execute(
        "INSERT INTO decisions(subject, family, value, at, by) VALUES (?, ?, ?, ?, ?)",
        (subject, str(family), json.dumps(value), at if at is not None else time.time(), by),
    )
    return int(cursor.lastrowid)


def loaded(row) -> Any:
    if row is None or row["value"] is None:
        return None
    try:
        return json.loads(row["value"])
    except (TypeError, ValueError):
        return None


def latest(conn, subject: str, family: str) -> Any:
    """What you last said about this subject, or None if you never did.

    `id DESC` breaks ties, not `at` alone: two decisions can share a timestamp
    at this clock's resolution, and the later row is the later answer.
    """

    row = conn.execute(
        f"SELECT value FROM decisions WHERE subject = ? AND family = ? "
        f"ORDER BY {AUTHORITY_SQL} DESC, at DESC, id DESC LIMIT 1",
        (str(subject), str(family)),
    ).fetchone()
    return loaded(row)


def history(conn, subject: str, *, family: str | None = None, limit: int = 200) -> list[dict]:
    """Everything you ever said about one subject, newest first.

    This is Develop's edit history, the audit trail, and the undo stack. They
    were three features because the log did not exist.
    """

    sql = "SELECT id, subject, family, value, at, by FROM decisions WHERE subject = ?"
    args: list[Any] = [str(subject)]
    if family:
        sql += " AND family = ?"
        args.append(str(family))
    sql += " ORDER BY at DESC, id DESC LIMIT ?"
    args.append(int(limit))
    return [
        {"id": row["id"], "subject": row["subject"], "family": row["family"],
         "value": loaded(row), "at": row["at"], "by": row["by"]}
        for row in conn.execute(sql, args)
    ]


# What "true now" means, written once. An append-only log answers every
# present-tense question with the same shape — last row wins, per subject — and
# anything narrowing photographs by a decision needs that shape as a *subquery*,
# not just as `current()`'s return value. One parameter: the family.
LATEST_IN_FAMILY = f"""
    SELECT subject, value FROM (
        SELECT subject, value,
               ROW_NUMBER() OVER (
                   PARTITION BY subject
                   ORDER BY {AUTHORITY_SQL} DESC, at DESC, id DESC
               ) AS rank
        FROM decisions WHERE family = ?
    ) WHERE rank = 1
"""


def current(conn, family: str) -> dict[str, Any]:
    """The latest decision in a family, for every subject that has one.

    This is how the index columns are rebuilt: one pass, not one query per
    photo. The window function picks the last row per subject inside SQLite
    rather than sorting 400,000 rows into Python.
    """

    rows = conn.execute(LATEST_IN_FAMILY, (str(family),)).fetchall()
    return {row["subject"]: loaded(row) for row in rows}


def carry(conn, subject: str, to: str) -> int:
    """Re-file what you decided about one subject under another.

    The identity digest covers the head of the file, and in a TIFF or a DNG
    that is exactly where metadata lives — so an application that writes a
    rating or an orientation into the photograph moves its digest, and every
    decision keyed on the old one is left behind. Measured: Lightroom saving
    metadata to a 40-frame roll changed all 40.

    This is not a migration and not a merge. It appends the current answer in
    each family under the new subject, which is the log doing the one thing it
    does; the rows under the old subject stay exactly where they were, so the
    record of what was decided when is never rewritten.

    The stored `value` is carried verbatim rather than decoded and re-encoded,
    because a round trip through JSON is a chance to change a number's spelling
    and there is nothing to gain by taking it.
    """

    if subject == to or not to:
        return 0
    rows = conn.execute(
        f"""
        SELECT family, value, by FROM (
            SELECT family, value, by,
                   ROW_NUMBER() OVER (
                       PARTITION BY family
                       ORDER BY {AUTHORITY_SQL} DESC, at DESC, id DESC
                   ) AS rank
            FROM decisions WHERE subject = ?
        ) WHERE rank = 1
        """,
        (str(subject),),
    ).fetchall()
    now = time.time()
    for row in rows:
        conn.execute(
            "INSERT INTO decisions(subject, family, value, at, by) VALUES (?, ?, ?, ?, ?)",
            (str(to), row["family"], row["value"], now, row["by"]),
        )
    return len(rows)


def undo(conn, subject: str, family: str) -> Any:
    """Put back what you said before, by saying it again.

    Deliberately not a delete. An undo that removed the row would make the log
    lie about what happened, and a redo would have nothing to read.
    """

    rows = conn.execute(
        f"SELECT value FROM decisions WHERE subject = ? AND family = ? "
        f"ORDER BY {AUTHORITY_SQL} DESC, at DESC, id DESC LIMIT 2",
        (str(subject), str(family)),
    ).fetchall()
    if len(rows) < 2:
        return None
    previous = loaded(rows[1])
    decide(conn, subject, family, previous)
    return previous


def amend(conn, subject: str, family: str, patch: dict, *, by: str = YOU) -> int:
    """Merge a partial object into the current answer, then append it.

    Partial edits have one safe spelling. Callers cannot accidentally replace
    masks, local adjustments, or metadata fields they did not send.
    """

    current_value = latest(conn, subject, family)
    if current_value is None:
        current_value = {}
    if not isinstance(current_value, dict) or not isinstance(patch, dict):
        raise ValueError("amend needs an object decision and an object patch")
    return decide(conn, subject, family, {**current_value, **patch}, by=by)
