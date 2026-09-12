"""Pick, Reject, clear, restore, turn, and exact Undo.

One status answers one direct culling question: unflagged, picked, or trashed.
Stars are computed quality and never enter this vocabulary. Every change is an
append-only content-identity decision; the column is only its browse index.

`change` and `undo` are the shape every projected decision shares -- decide
for each identity, write the column, report what moved so one Undo can put
it back exactly -- and the status verbs are its first callers. A turn is the
same shape with another family (`decisions.PROJECTED` says which column and
what counts), so it lives here rather than growing a second copy.
"""

from __future__ import annotations

import json
import time
from typing import Callable, Iterable

from model import decisions

UNFLAGGED = "unflagged"
PICKED = "picked"
TRASHED = "trashed"
STATUSES = frozenset((UNFLAGGED, PICKED, TRASHED))


def _ids(values: Iterable[int]) -> list[int]:
    return sorted({int(value) for value in values if int(value) > 0})


def _photos(conn, image_ids: list[int], column: str) -> list[dict]:
    if not image_ids:
        return []
    payload = json.dumps(image_ids, separators=(",", ":"))
    return [
        dict(row)
        for row in conn.execute(
            f"""
            SELECT i.id, i.content_hash AS hash, i.{column} AS current
            FROM json_each(?) wanted
            LEFT JOIN images i ON i.id = CAST(wanted.value AS INTEGER)
            ORDER BY CAST(wanted.value AS INTEGER)
            """,
            (payload,),
        )
    ]


def _latest_all(conn, subjects: Iterable[str], family: str) -> dict[str, tuple[int, object]]:
    """The latest decision per subject -- its id and value -- by the rule
    `decisions.latest` reads by, in one read for the whole selection."""

    payload = json.dumps(sorted({str(s) for s in subjects}), separators=(",", ":"))
    out: dict[str, tuple[int, object]] = {}
    for row in conn.execute(
        f"SELECT id, subject, value FROM decisions WHERE family = ?"
        f" AND subject IN (SELECT value FROM json_each(?))"
        f" ORDER BY {decisions.AUTHORITY_SQL} ASC, at ASC, id ASC",
        (family, payload),
    ):
        out[str(row["subject"])] = (int(row["id"]), decisions.loaded(row))   # the last in this order wins
    return out


def _decide_all(conn, family: str, said: list[tuple[str, object]]) -> list[int]:
    """One decision per subject, appended in one statement; their ids in
    order. Select All then P on a whole library is one insert, not a
    hundred and fifty thousand."""

    if not said:
        return []
    first = int(conn.execute("SELECT COALESCE(MAX(id), 0) FROM decisions").fetchone()[0])
    now = time.time()
    conn.executemany(
        "INSERT INTO decisions(subject, family, value, at, by) VALUES (?, ?, ?, ?, ?)",
        [(str(subject), str(family), json.dumps(value), now, decisions.YOU) for subject, value in said],
    )
    ids = [int(row[0]) for row in conn.execute("SELECT id FROM decisions WHERE id > ? ORDER BY id", (first,))]
    if len(ids) != len(said):
        raise RuntimeError("the decisions written do not match the decisions made")
    return ids


def _project_all(conn, column: str, said: list[tuple[str, object]]) -> dict[str, int]:
    """The column written for every subject, one statement per distinct
    value; how many rows each subject moved."""

    by_value: dict[str, list[str]] = {}
    for subject, value in said:
        by_value.setdefault(json.dumps(value), []).append(str(subject))
    for key, subjects in by_value.items():
        conn.execute(
            f"UPDATE images SET {column} = ? WHERE content_hash IN (SELECT value FROM json_each(?))",
            (json.loads(key), json.dumps(subjects, separators=(",", ":"))),
        )
    return {
        str(row["hash"]): int(row["n"]) for row in conn.execute(
            "SELECT content_hash AS hash, COUNT(*) AS n FROM images"
            " WHERE content_hash IN (SELECT value FROM json_each(?)) GROUP BY content_hash",
            (json.dumps([str(subject) for subject, _ in said], separators=(",", ":")),))
    }


def _previous_status(conn, subject: str) -> str:
    rows = conn.execute(
        f"SELECT value FROM decisions WHERE subject = ? AND family = ? "
        f"ORDER BY {decisions.AUTHORITY_SQL} DESC, at DESC, id DESC LIMIT 2",
        (subject, decisions.STATUS),
    ).fetchall()
    if len(rows) < 2:
        return UNFLAGGED
    value = decisions.loaded(rows[1])
    return value if value in STATUSES and value != TRASHED else UNFLAGGED


def change(
    conn,
    image_ids: Iterable[int],
    target: Callable[[object, str, object], object],
    *,
    family: str = decisions.STATUS,
) -> dict:
    """Decide `family` for each identity behind `image_ids` and project it.

    `target(conn, subject, before)` names the value each identity should now
    have. Identities already there are left alone; the rest get one decision
    row and their column written, and the returned changes carry enough --
    subject, family, before, after, the decision id, how many rows moved -- for
    `undo` to put exactly this back or refuse if something newer intervened.
    """

    column, default, valid = decisions.PROJECTED[family]
    requested = _ids(image_ids)
    rows = _photos(conn, requested, column)
    # A decision belongs to an identity, and identity arrives shortly after
    # a sweep; a photograph that has none yet is passed over and counted,
    # never a reason to refuse the rest -- Select All then P must pick what
    # it can. A row that left under the window is passed over the same way.
    missing = sum(1 for row in rows if row["id"] is None)
    unidentified = sum(1 for row in rows if row["id"] and not row["hash"])

    by_hash: dict[str, dict] = {}
    for row in rows:
        if row["id"] and row["hash"]:
            by_hash.setdefault(str(row["hash"]), row)

    try:
        # Every subject's last word in one read; the target decided in
        # Python; then one insert and one write per distinct value. A loop
        # of one decision per row was thirty seconds on a whole library.
        latest_all = _latest_all(conn, by_hash, family)
        todo: list[tuple[str, object, object]] = []
        for subject, row in by_hash.items():
            latest = latest_all.get(subject, (None, None))[1]
            before = latest if valid(latest) else (row["current"] if valid(row["current"]) else default)
            after = target(conn, subject, before)
            if not valid(after):
                raise ValueError(f"invalid {family}: {after!r}")
            if before != after:
                todo.append((subject, before, after))
        ids = _decide_all(conn, family, [(subject, after) for subject, _, after in todo])
        counts = _project_all(conn, column, [(subject, after) for subject, _, after in todo])
        changes = [
            {"subject": subject, "family": family, "before": before, "after": after,
             "decision": decision_id, "photos": counts.get(subject, 0)}
            for (subject, before, after), decision_id in zip(todo, ids)
        ]
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return {"requested": len(requested), "changed": changes,
            "unidentified": unidentified, "missing": missing}


def pick(conn, image_ids: Iterable[int]) -> dict:
    return change(conn, image_ids, lambda _conn, _subject, _before: PICKED)


def clear(conn, image_ids: Iterable[int]) -> dict:
    return change(conn, image_ids, lambda _conn, _subject, _before: UNFLAGGED)


def reject(conn, image_ids: Iterable[int]) -> dict:
    return change(conn, image_ids, lambda _conn, _subject, _before: TRASHED)


def restore(conn, image_ids: Iterable[int]) -> dict:
    """Restore the status each identity had immediately before Reject."""

    return change(
        conn,
        image_ids,
        lambda inner, subject, before: (
            _previous_status(inner, subject) if before == TRASHED else before
        ),
    )


def turn(conn, image_ids: Iterable[int], by: int = 90) -> dict:
    """Turn each identity `by` degrees clockwise from how it is shown now."""

    if int(by) % 90:
        raise ValueError("a turn is a quarter of a circle")
    return change(
        conn,
        image_ids,
        lambda _conn, _subject, before: (int(before or 0) + int(by)) % 360,
        family=decisions.ROTATE,
    )


def undo(conn, changes: Iterable[dict]) -> dict:
    """Reverse exactly one returned change set unless newer intent superseded it."""

    requested = list(changes)
    try:
        subjects = [(str(change["subject"]), str(change.get("family", decisions.STATUS))) for change in requested]
        if len(subjects) != len(set(subjects)):
            raise ValueError("one Undo may change each identity once")
        families = {family for _, family in subjects}
        latest_by_family = {
            family: _latest_all(conn, [subject for subject, held in subjects if held == family], family)
            for family in families
        }
        for change in requested:
            subject = str(change["subject"])
            family = str(change.get("family", decisions.STATUS))
            _column, _default, valid = decisions.PROJECTED[family]
            before = change["before"]
            after = change["after"]
            decision_id = int(change["decision"])
            if not valid(before) or not valid(after):
                raise ValueError("invalid Undo value")
            latest = latest_by_family[family].get(subject)
            if latest is None or latest[0] != decision_id or latest[1] != after:
                raise ValueError(f"{family} changed after this action")

        reversed_changes = []
        for family in families:
            column = decisions.PROJECTED[family][0]
            mine = [change for change in requested if str(change.get("family", decisions.STATUS)) == family]
            said = [(str(change["subject"]), change["before"]) for change in mine]
            ids = _decide_all(conn, family, said)
            _project_all(conn, column, said)
            for change, decision_id in zip(mine, ids):
                reversed_changes.append(
                    {"subject": str(change["subject"]), "family": family,
                     "before": change["after"], "after": change["before"], "decision": decision_id}
                )
        conn.commit()
    except (KeyError, TypeError, ValueError):
        conn.rollback()
        raise ValueError("invalid or stale Undo") from None
    except BaseException:
        conn.rollback()
        raise
    return {"changed": reversed_changes}
