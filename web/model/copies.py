"""Which drives hold which photos, and the sweep that keeps it roughly true.

A copy is a **hint**. Nothing precious depends on it being right, and that one
concession is what lets the sweep be four lines of policy instead of a
subsystem: a pass may drop what it did not see, because dropping a hint costs
nothing and a read would have found the file anyway.

What the sweep is *not* allowed to do is decide a photograph is gone. It never
writes a verdict, so it needs no ratio, no floor, no override switch, and no
exception class. The three things it does need are all about not lying:

* the marker is re-read afterwards — a pass whose drive vanished mid-walk
  changes nothing at all;
* a walk that could not be completed changes nothing, for the same reason;
* everything else is just what it saw.
"""

from __future__ import annotations

import os
import time

from model import drives

# Directories a sweep must never descend into. `.trash` is here for a sharp
# reason: without it, "a file at a known tail gets a copy row" would quietly
# resurrect every photo the owner ever threw away.
SKIP_DIRS = {".trash", "astrophotography", "previewcache", "__macosx", ".thumbnails", ".lrt"}
SKIP_SUFFIXES = (".lrdata", ".lrcat-data")


def _skip(name: str) -> bool:
    lowered = name.lower()
    return lowered in SKIP_DIRS or lowered.endswith(SKIP_SUFFIXES) or lowered.startswith(".")


def saw(conn, photo_id: int, drive_id: int, *, tail: str | None = None, when: float | None = None) -> None:
    """Record that this drive holds this photo."""

    conn.execute(
        "INSERT INTO copies(photo_id, drive_id, tail, seen_at) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(photo_id, drive_id) DO UPDATE SET tail = excluded.tail, seen_at = excluded.seen_at",
        (int(photo_id), int(drive_id), tail, when if when is not None else time.time()),
    )


def forget(conn, photo_id: int, drive_id: int) -> None:
    conn.execute(
        "DELETE FROM copies WHERE photo_id = ? AND drive_id = ?", (int(photo_id), int(drive_id))
    )


def drives_holding(conn, photo_id: int) -> list[dict]:
    return [dict(row) for row in conn.execute(
        "SELECT d.* FROM copies c JOIN drives d ON d.id = c.drive_id WHERE c.photo_id = ? "
        "ORDER BY d.is_record ASC, d.id ASC",
        (int(photo_id),),
    )]


def is_backed_up(conn, photo_id: int) -> bool:
    """Has a copy on a drive allowed to be the last one.

    Enough to *report* on. Never enough to delete on — that asks for a
    full-file digest at the moment of deletion, not a row written some time ago.
    """

    row = conn.execute(
        "SELECT 1 FROM copies c JOIN drives d ON d.id = c.drive_id "
        "WHERE c.photo_id = ? AND d.is_record = 1 LIMIT 1",
        (int(photo_id),),
    ).fetchone()
    return row is not None


def walk_tails(root: str) -> tuple[set[str], bool]:
    """Every tail under `root`, and whether the walk completed.

    An incomplete walk is reported rather than papered over: the caller must
    change nothing, because "I could not read the folder" and "the folder is
    empty" are the same sight from here and opposite facts.
    """

    seen: set[str] = set()
    complete = True

    def failed(_error: OSError) -> None:
        nonlocal complete
        complete = False

    for dirpath, dirnames, filenames in os.walk(root, onerror=failed):
        dirnames[:] = [d for d in dirnames if not _skip(d)]
        for name in filenames:
            if name.startswith("."):
                continue
            tail = drives.tail_for(root, os.path.join(dirpath, name))
            if tail:
                seen.add(tail)
    return seen, complete


def sweep(conn, drive_uuid: str) -> dict:
    """Bring one drive's copy rows in line with what is actually on it."""

    root = drives.root_of(conn, drive_uuid)
    if root is None:
        return {"drive": drive_uuid, "applied": False, "reason": "not attached"}

    seen, complete = walk_tails(root)

    # Re-read the marker *after* walking. If the drive went away mid-pass, what
    # we saw describes a moment that no longer exists.
    if drives.read_marker(root) != drive_uuid:
        return {"drive": drive_uuid, "applied": False, "reason": "drive changed during sweep"}
    if not complete:
        return {"drive": drive_uuid, "applied": False, "reason": "walk incomplete"}

    drive = conn.execute("SELECT id FROM drives WHERE uuid = ?", (drive_uuid,)).fetchone()
    drive_id = int(drive["id"])
    known = {
        row["tail"]: int(row["id"])
        for row in conn.execute("SELECT id, tail FROM images WHERE tail IS NOT NULL AND vc_of IS NULL")
    }

    now = time.time()
    added = 0
    for tail in seen:
        photo_id = known.get(tail)
        if photo_id is not None:
            saw(conn, photo_id, drive_id, when=now)
            added += 1

    held = {
        int(row["photo_id"]): row["tail"]
        for row in conn.execute("SELECT photo_id, tail FROM copies WHERE drive_id = ?", (drive_id,))
    }
    tail_of = {photo_id: tail for tail, photo_id in known.items()}
    retired = 0
    for photo_id in held:
        tail = held[photo_id] or tail_of.get(photo_id)
        if tail is None or tail not in seen:
            forget(conn, photo_id, drive_id)
            retired += 1

    conn.commit()
    return {
        "drive": drive_uuid,
        "applied": True,
        "files_seen": len(seen),
        "copies_recorded": added,
        "copies_retired": retired,
        "unknown_files": len(seen) - added,
    }
