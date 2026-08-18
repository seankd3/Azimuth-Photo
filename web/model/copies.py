"""Which drives hold which photos, and the sweep that keeps it roughly true.

A copy is a **hint**. Nothing precious depends on it being right, and that one
concession is what lets the sweep be four lines of policy instead of a
subsystem: a pass may drop what it did not see, because dropping a hint costs
nothing and a read would have found the file anyway.

What the sweep is *not* allowed to do is decide a photograph is gone. It never
writes a verdict, so it needs no ratio, no floor, no override switch, and no
exception class. The three things it does need are all about not lying:

* a walk that could not be completed changes nothing;
* newly observed photographs publish in bounded batches, because admitting
  real bytes is safe and lets the grid fill while a large drive is indexed;
* the marker is re-read before every published batch, and stale copy hints are
  retired only after the complete marked drive stayed present;
* everything else is just what it saw.
"""

from __future__ import annotations

import os
import stat
import time
from collections.abc import Callable

from model import drives

# Directories a sweep must never descend into. `.trash` is here for a sharp
# reason: without it, "a file at a known tail gets a copy row" would quietly
# resurrect every photo the owner ever threw away.
SKIP_DIRS = {".trash", "astrophotography", "previewcache", "__macosx", ".thumbnails", ".lrt"}
SKIP_SUFFIXES = (".lrdata", ".lrcat-data")
SWEEP_BATCH = 200


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
        "SELECT d.*, c.tail AS copy_tail FROM copies c "
        "JOIN drives d ON d.id = c.drive_id WHERE c.photo_id = ? "
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
    from model import photos

    def failed(_error: OSError) -> None:
        nonlocal complete
        complete = False

    for dirpath, dirnames, filenames in os.walk(root, onerror=failed):
        dirnames[:] = [d for d in dirnames if not _skip(d)]
        for name in filenames:
            if name.startswith("."):
                continue
            path = os.path.join(dirpath, name)
            try:
                entry = os.lstat(path)
            except OSError:
                complete = False
                continue
            if not stat.S_ISREG(entry.st_mode) or entry.st_size <= 0:
                continue
            if not photos.supported(path):
                continue
            tail = drives.tail_for(root, path)
            if tail:
                seen.add(tail)
    return seen, complete


def sweep(
    conn,
    drive_uuid: str,
    *,
    batch_size: int = SWEEP_BATCH,
    progress: Callable[[dict], None] | None = None,
) -> dict:
    """Admit observed photos progressively, then retire stale copy hints."""

    batch_size = int(batch_size)
    if batch_size < 1:
        raise ValueError("a sweep batch contains at least one file")

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
    from model import photos

    known: dict[str, list[dict]] = {}
    for row in conn.execute(
        "SELECT id, tail, file_size, file_modified_ns FROM images "
        "WHERE tail IS NOT NULL AND vc_of IS NULL ORDER BY id"
    ):
        known.setdefault(row["tail"], []).append(dict(row))
    # One drive may hold a photo under a collision-safe alternate tail. That
    # address is just as known as the photo's canonical tail; failing to add it
    # here admits the archive copy as a second photograph on the next sweep.
    for row in conn.execute(
        "SELECT i.id, c.tail, i.file_size, i.file_modified_ns FROM copies c "
        "JOIN images i ON i.id = c.photo_id "
        "WHERE c.drive_id = ? AND c.tail IS NOT NULL",
        (drive_id,),
    ):
        known.setdefault(row["tail"], []).append(dict(row))

    now = time.time()
    recorded = 0
    admitted = 0
    changed: list[str] = []
    processed = 0

    def result(*, applied: bool, reason: str = "", retired: int = 0) -> dict:
        return {
            "drive": drive_uuid,
            "applied": applied,
            **({"reason": reason} if reason else {}),
            "files_seen": len(seen),
            "copies_recorded": recorded,
            "copies_retired": retired,
            "photos_added": admitted,
            "changed": changed,
            "unknown_files": len(seen) - recorded,
        }

    def publish() -> bool:
        if drives.read_marker(root) != drive_uuid:
            conn.rollback()
            return False
        conn.commit()
        if progress is not None:
            progress(result(applied=False, reason="sweep in progress"))
        return True

    for tail in sorted(seen):
        path = drives.path_for(conn, drive_uuid, tail)
        if path is None:
            continue
        try:
            entry = os.lstat(path)
        except OSError:
            continue
        candidates = known.get(tail, [])
        match = next(
            (
                row for row in candidates
                if (row["file_size"] is None or int(row["file_size"]) == entry.st_size)
                and (
                    row["file_modified_ns"] is None
                    or int(row["file_modified_ns"]) == entry.st_mtime_ns
                )
            ),
            None,
        )
        if candidates and match is None:
            changed.append(tail)
            continue
        photo_id = int(match["id"]) if match else photos.admit(conn, path, tail)
        if photo_id is None:
            continue
        saw(conn, photo_id, drive_id, when=now)
        recorded += 1
        if match is None:
            admitted += 1
            known.setdefault(tail, []).append({
                "id": photo_id,
                "tail": tail,
                "file_size": entry.st_size,
                "file_modified_ns": entry.st_mtime_ns,
            })
        processed += 1
        if processed % batch_size == 0 and not publish():
            return result(applied=False, reason="drive changed during sweep")

    if processed % batch_size and not publish():
        return result(applied=False, reason="drive changed during sweep")

    # Admissions are durable facts about bytes we observed and may paint as
    # soon as each batch commits. Retiring a hint is different: it is deferred
    # until the complete walk and every admission batch have stayed on the
    # same marked drive.
    if drives.read_marker(root) != drive_uuid:
        return result(applied=False, reason="drive changed during sweep")

    held = {
        int(row["photo_id"]): row["tail"]
        for row in conn.execute("SELECT photo_id, tail FROM copies WHERE drive_id = ?", (drive_id,))
    }
    tail_of = {
        int(row["id"]): tail
        for tail, rows in known.items()
        for row in rows
    }
    retired = 0
    for photo_id in held:
        tail = held[photo_id] or tail_of.get(photo_id)
        if tail is None or tail not in seen:
            forget(conn, photo_id, drive_id)
            retired += 1

    conn.commit()
    return result(applied=True, retired=retired)
