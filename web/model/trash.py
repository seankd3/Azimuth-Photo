"""The reversible half of deletion: hide, restore, and exact Undo.

Trash is a decision about a photograph, keyed by its content identity. It does
not move or delete bytes. That makes the repeated culling gesture immediate,
keeps Restore independent of which drives are attached, and leaves all
irreversible filesystem work behind the separate Empty Trash boundary.
"""

from __future__ import annotations

import json
import os
import stat
from typing import Iterable

from model import copies, decisions, drives, photos

KEPT = "kept"
MAYBE = "maybe"
TRASHED = "trashed"
STATUSES = frozenset((KEPT, MAYBE, TRASHED))


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
        return KEPT
    value = decisions.loaded(rows[1])
    return value if value in STATUSES and value != TRASHED else KEPT


def _change(conn, image_ids: Iterable[int], target) -> dict:
    requested = _ids(image_ids)
    rows = _photos(conn, requested)
    missing = [requested[index] for index, row in enumerate(rows) if row["id"] is None]
    unidentified = [int(row["id"]) for row in rows if row["id"] and not row["hash"]]
    if missing or unidentified:
        raise ValueError(
            f"cannot change trash state; missing={missing}, unidentified={unidentified}"
        )

    by_hash: dict[str, dict] = {}
    for row in rows:
        by_hash.setdefault(str(row["hash"]), row)

    changes = []
    try:
        for subject, row in by_hash.items():
            latest = decisions.latest(conn, subject, decisions.STATUS)
            before = latest if latest in STATUSES else str(row["status"] or KEPT)
            after = target(conn, subject, before)
            if after not in STATUSES:
                raise ValueError(f"invalid photo status: {after!r}")
            if before == after:
                continue
            decision_id = decisions.decide(conn, subject, decisions.STATUS, after)
            conn.execute(
                "UPDATE images SET status = ? WHERE content_hash = ?",
                (after, subject),
            )
            changes.append(
                {
                    "subject": subject,
                    "before": before,
                    "after": after,
                    "decision": decision_id,
                }
            )
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return {"requested": len(requested), "changed": changes}


def put(conn, image_ids: Iterable[int]) -> dict:
    """Put complete photo identities in Trash, atomically."""

    return _change(conn, image_ids, lambda _conn, _subject, _before: TRASHED)


def restore(conn, image_ids: Iterable[int]) -> dict:
    """Restore the status each identity had immediately before Trash."""

    return _change(
        conn,
        image_ids,
        lambda inner, subject, before: (
            _previous_status(inner, subject) if before == TRASHED else before
        ),
    )


def undo(conn, changes: Iterable[dict]) -> dict:
    """Reverse exactly one returned change set, unless later intent superseded it."""

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
                raise ValueError("Trash changed after this action; Undo was not applied")

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
        raise ValueError("invalid or stale Trash Undo") from None
    except BaseException:
        conn.rollback()
        raise
    return {"changed": reversed_changes}


def count(conn) -> int:
    """Photographs currently in Trash, using the projected browse index."""

    return int(
        conn.execute(
            "SELECT COUNT(*) FROM images WHERE status = ? AND tail IS NOT NULL",
            (TRASHED,),
        ).fetchone()[0]
    )


def browse(conn, *, limit: int = 200, offset: int = 0) -> list[dict]:
    """One bounded Trash page, newest decision first."""

    limit, offset = int(limit), int(offset)
    if not 1 <= limit <= 500:
        raise ValueError("a Trash page contains between 1 and 500 photos")
    if offset < 0:
        raise ValueError("a Trash offset cannot be negative")
    return [
        dict(row)
        for row in conn.execute(
            f"""
            SELECT i.id, i.tail, i.date_taken, i.stars, i.elo,
                   i.content_hash AS hash, i.width, i.height, i.file_size,
                   (SELECT d.at FROM decisions d
                    WHERE d.subject = i.content_hash AND d.family = ?
                    ORDER BY {decisions.AUTHORITY_SQL} DESC, d.at DESC, d.id DESC
                    LIMIT 1) AS trashed_at
            FROM images i
            WHERE i.status = ? AND i.tail IS NOT NULL
            ORDER BY trashed_at DESC, i.id DESC
            LIMIT ? OFFSET ?
            """,
            (decisions.STATUS, TRASHED, limit, offset),
        )
    ]


def _file_token(path: str) -> tuple[int, int, int, int]:
    try:
        entry = os.lstat(path)
    except OSError as error:
        raise ValueError(f"file is unavailable: {path}") from error
    if not stat.S_ISREG(entry.st_mode):
        raise ValueError(f"not a regular file: {path}")
    return entry.st_dev, entry.st_ino, entry.st_size, entry.st_mtime_ns


def _empty_plans(conn) -> tuple[int, list[dict]]:
    rows = [
        dict(row)
        for row in conn.execute(
            "SELECT id, content_hash AS hash, tail FROM images "
            "WHERE status = ? AND tail IS NOT NULL ORDER BY id",
            (TRASHED,),
        )
    ]
    by_hash: dict[str, list[dict]] = {}
    for row in rows:
        if not row["hash"]:
            raise ValueError(f"trashed photo {row['id']} has no identity")
        by_hash.setdefault(str(row["hash"]), []).append(row)

    attached_drives = []
    for row in conn.execute("SELECT * FROM drives ORDER BY is_record, id"):
        drive = dict(row)
        if drives.root_of(conn, drive["uuid"]) is None:
            raise ValueError(f"drive is away: {drive['label'] or drive['uuid']}")
        attached_drives.append(drive)

    plans = []
    for digest, identity_rows in by_hash.items():
        image_ids = [int(row["id"]) for row in identity_rows]
        marks = ",".join("?" * len(image_ids))
        holders = [
            dict(row)
            for row in conn.execute(
                "SELECT c.photo_id, c.drive_id, d.uuid, d.is_record, "
                "COALESCE(c.tail, i.tail) AS tail FROM copies c "
                "JOIN drives d ON d.id = c.drive_id "
                "JOIN images i ON i.id = c.photo_id "
                f"WHERE c.photo_id IN ({marks}) ORDER BY d.is_record, d.id",
                image_ids,
            )
        ]
        paths: dict[str, dict] = {}
        for holder in holders:
            try:
                path = drives.path_for(conn, holder["uuid"], holder["tail"])
            except ValueError as error:
                raise ValueError(f"invalid copy address for photo {holder['photo_id']}") from error
            if path is None:
                raise ValueError(f"drive is away for photo {holder['photo_id']}")
            key = os.path.normcase(os.path.abspath(path))
            entry = paths.setdefault(
                key,
                {
                    "path": path,
                    "is_record": bool(holder["is_record"]),
                    "hints": [],
                },
            )
            entry["hints"].append((int(holder["photo_id"]), int(holder["drive_id"])))

        # A copy row is only a hint. Inspect the canonical address on every
        # registered drive too, or a stale/missing hint could let Empty claim
        # success while a later sweep rediscovers the bytes.
        canonical_tail = str(identity_rows[0]["tail"])
        for drive in attached_drives:
            candidate = drives.path_for(conn, drive["uuid"], canonical_tail)
            key = os.path.normcase(os.path.abspath(candidate))
            if key in paths or not os.path.lexists(candidate):
                continue
            try:
                if photos.content_hash(candidate) != digest:
                    continue
            except (OSError, ValueError):
                continue
            paths[key] = {
                "path": candidate,
                "is_record": bool(drive["is_record"]),
                "hints": [],
            }

        if not paths:
            plans.append({"hash": digest, "image_ids": image_ids, "files": []})
            continue

        files = list(paths.values())
        for file in files:
            file["token"] = _file_token(file["path"])
        anchor = max(files, key=lambda file: (file["is_record"], file["path"]))
        if photos.content_hash(anchor["path"]) != digest:
            raise ValueError(f"copy changed for photo {image_ids[0]}")
        for file in files:
            if file is not anchor and not photos.same_bytes(file["path"], anchor["path"]):
                raise ValueError(f"copies differ for photo {image_ids[0]}")
        ordered = [file for file in files if file is not anchor] + [anchor]
        plans.append({"hash": digest, "image_ids": image_ids, "files": ordered})
    return len(rows), plans


def empty(conn, *, expected_count: int, dry_run: bool = False) -> dict:
    """Permanently remove every trashed identity only after complete preflight.

    The caller must repeat the visible count, preventing a stale confirmation
    from deleting photographs that entered Trash after the prompt appeared.
    Every known drive must be present, every path must still be a regular file,
    and every copy is compared byte-for-byte. Record-drive bytes are removed
    last, so an interrupted multi-copy deletion preserves the safest copy.
    """

    expected_count = int(expected_count)
    before = count(conn)
    if expected_count != before:
        raise ValueError(
            f"Trash count changed: expected {expected_count}, found {before}"
        )
    visible_count, plans = _empty_plans(conn)
    if expected_count != visible_count:
        raise ValueError(
            f"Trash count changed: expected {expected_count}, found {visible_count}"
        )
    if dry_run:
        return {
            "count": visible_count,
            "identities": len(plans),
            "files": sum(len(plan["files"]) for plan in plans),
        }

    emptied = []
    errors = []
    for plan in plans:
        failed = None
        anchor = plan["files"][-1] if plan["files"] else None
        for file in plan["files"]:
            try:
                if _file_token(file["path"]) != file["token"]:
                    raise ValueError("file changed after Empty Trash preflight")
                if file is anchor:
                    if photos.content_hash(file["path"]) != plan["hash"]:
                        raise ValueError("last copy changed before deletion")
                elif not photos.same_bytes(file["path"], anchor["path"]):
                    raise ValueError("copies changed before deletion")
                os.remove(file["path"])
                for photo_id, drive_id in file["hints"]:
                    copies.forget(conn, photo_id, drive_id)
                conn.commit()
            except (OSError, ValueError) as error:
                conn.rollback()
                failed = str(error)
                break
        if failed is not None:
            errors.append({"ids": plan["image_ids"], "reason": failed})
            continue
        marks = ",".join("?" * len(plan["image_ids"]))
        conn.execute(f"DELETE FROM copies WHERE photo_id IN ({marks})", plan["image_ids"])
        conn.execute(f"DELETE FROM images WHERE id IN ({marks})", plan["image_ids"])
        conn.commit()
        emptied.extend(plan["image_ids"])
    return {"emptied": emptied, "errors": errors, "remaining": count(conn)}
