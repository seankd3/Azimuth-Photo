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


def _latest_row(conn, subject: str, family: str = decisions.STATUS):
    return conn.execute(
        f"SELECT id, value FROM decisions WHERE subject = ? AND family = ? "
        f"ORDER BY {decisions.AUTHORITY_SQL} DESC, at DESC, id DESC LIMIT 1",
        (subject, family),
    ).fetchone()


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

    changes = []
    try:
        for subject, row in by_hash.items():
            latest = decisions.latest(conn, subject, family)
            before = latest if valid(latest) else (row["current"] if valid(row["current"]) else default)
            after = target(conn, subject, before)
            if not valid(after):
                raise ValueError(f"invalid {family}: {after!r}")
            if before == after:
                continue
            decision_id = decisions.decide(conn, subject, family, after)
            updated = conn.execute(
                f"UPDATE images SET {column} = ? WHERE content_hash = ?",
                (after, subject),
            )
            changes.append(
                {
                    "subject": subject,
                    "family": family,
                    "before": before,
                    "after": after,
                    "decision": decision_id,
                    "photos": updated.rowcount,
                }
            )
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
        for change in requested:
            subject = str(change["subject"])
            family = str(change.get("family", decisions.STATUS))
            _column, _default, valid = decisions.PROJECTED[family]
            before = change["before"]
            after = change["after"]
            decision_id = int(change["decision"])
            if not valid(before) or not valid(after):
                raise ValueError("invalid Undo value")
            latest = _latest_row(conn, subject, family)
            if (
                latest is None
                or int(latest["id"]) != decision_id
                or decisions.loaded(latest) != after
            ):
                raise ValueError(f"{family} changed after this action")

        reversed_changes = []
        for change in requested:
            subject = str(change["subject"])
            family = str(change.get("family", decisions.STATUS))
            column = decisions.PROJECTED[family][0]
            restored = change["before"]
            decision_id = decisions.decide(conn, subject, family, restored)
            conn.execute(
                f"UPDATE images SET {column} = ? WHERE content_hash = ?",
                (restored, subject),
            )
            reversed_changes.append(
                {
                    "subject": subject,
                    "family": family,
                    "before": change["after"],
                    "after": restored,
                    "decision": decision_id,
                }
            )
        conn.commit()
    except (KeyError, TypeError, ValueError):
        conn.rollback()
        raise ValueError("invalid or stale Undo") from None
    except BaseException:
        conn.rollback()
        raise
    return {"changed": reversed_changes}
