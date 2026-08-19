"""Pick, Reject, clear, restore, and exact Undo.

One status answers one direct culling question: unflagged, picked, or trashed.
Stars are computed quality and never enter this vocabulary. Every change is an
append-only content-identity decision; the column is only its browse index.
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


def _photos(conn, image_ids: list[int]) -> list[dict]:
    if not image_ids:
        return []
    payload = json.dumps(image_ids, separators=(",", ":"))
    return [
        dict(row)
        for row in conn.execute(
            """
            SELECT i.id, i.content_hash AS hash, i.status
            FROM json_each(?) wanted
            LEFT JOIN images i ON i.id = CAST(wanted.value AS INTEGER)
            ORDER BY CAST(wanted.value AS INTEGER)
            """,
            (payload,),
        )
    ]


def _latest_row(conn, subject: str):
    return conn.execute(
        f"SELECT id, value FROM decisions WHERE subject = ? AND family = ? "
        f"ORDER BY {decisions.AUTHORITY_SQL} DESC, at DESC, id DESC LIMIT 1",
        (subject, decisions.STATUS),
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


def _change(
    conn,
    image_ids: Iterable[int],
    target: Callable[[object, str, str], str],
) -> dict:
    requested = _ids(image_ids)
    rows = _photos(conn, requested)
    missing = [requested[index] for index, row in enumerate(rows) if row["id"] is None]
    unidentified = [int(row["id"]) for row in rows if row["id"] and not row["hash"]]
    if missing or unidentified:
        raise ValueError(
            f"cannot change cull state; missing={missing}, unidentified={unidentified}"
        )

    by_hash: dict[str, dict] = {}
    for row in rows:
        by_hash.setdefault(str(row["hash"]), row)

    changes = []
    try:
        for subject, row in by_hash.items():
            latest = decisions.latest(conn, subject, decisions.STATUS)
            before = latest if latest in STATUSES else str(row["status"] or UNFLAGGED)
            after = target(conn, subject, before)
            if after not in STATUSES:
                raise ValueError(f"invalid cull status: {after!r}")
            if before == after:
                continue
            decision_id = decisions.decide(conn, subject, decisions.STATUS, after)
            updated = conn.execute(
                "UPDATE images SET status = ? WHERE content_hash = ?",
                (after, subject),
            )
            changes.append(
                {
                    "subject": subject,
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
    return {"requested": len(requested), "changed": changes}


def pick(conn, image_ids: Iterable[int]) -> dict:
    return _change(conn, image_ids, lambda _conn, _subject, _before: PICKED)


def clear(conn, image_ids: Iterable[int]) -> dict:
    return _change(conn, image_ids, lambda _conn, _subject, _before: UNFLAGGED)


def reject(conn, image_ids: Iterable[int]) -> dict:
    return _change(conn, image_ids, lambda _conn, _subject, _before: TRASHED)


def restore(conn, image_ids: Iterable[int]) -> dict:
    """Restore the status each identity had immediately before Reject."""

    return _change(
        conn,
        image_ids,
        lambda inner, subject, before: (
            _previous_status(inner, subject) if before == TRASHED else before
        ),
    )


def undo(conn, changes: Iterable[dict]) -> dict:
    """Reverse exactly one returned change set unless newer intent superseded it."""

    requested = list(changes)
    try:
        subjects = [str(change["subject"]) for change in requested]
        if len(subjects) != len(set(subjects)):
            raise ValueError("one Undo may change each identity once")
        for change in requested:
            subject = str(change["subject"])
            before = change["before"]
            after = change["after"]
            decision_id = int(change["decision"])
            if before not in STATUSES or after not in STATUSES:
                raise ValueError("invalid Undo status")
            latest = _latest_row(conn, subject)
            if (
                latest is None
                or int(latest["id"]) != decision_id
                or decisions.loaded(latest) != after
            ):
                raise ValueError("cull state changed after this action")

        reversed_changes = []
        for change in requested:
            subject = str(change["subject"])
            restored = change["before"]
            decision_id = decisions.decide(conn, subject, decisions.STATUS, restored)
            conn.execute(
                "UPDATE images SET status = ? WHERE content_hash = ?",
                (restored, subject),
            )
            reversed_changes.append(
                {
                    "subject": subject,
                    "before": change["after"],
                    "after": restored,
                    "decision": decision_id,
                }
            )
        conn.commit()
    except (KeyError, TypeError, ValueError):
        conn.rollback()
        raise ValueError("invalid or stale cull Undo") from None
    except BaseException:
        conn.rollback()
        raise
    return {"changed": reversed_changes}
