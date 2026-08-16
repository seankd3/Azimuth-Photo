"""Getting a second copy of every photograph, and never removing the last one.

Two verbs, and the asymmetry between them is the whole safety model.

`back_up` only ever **adds**. It copies a photo to a drive allowed to hold the
last copy, proves the copy byte for byte, and records it. If anything is wrong
it leaves both sides untouched — the worst case is that a photo stays
unprotected, which is where it already was.

`reclaim` **removes**, and therefore proves far more before it acts. It re-reads
both files at the moment of deletion and compares full digests. A copy row is a
hint written some time ago; it is enough to *report* that a photo is backed up
and never enough to delete on. The prefix hash that identifies photos elsewhere
is not permission either: a roll of film scans can share both a size and a first
8 MiB.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import time

from model import copies, drives, photos

CHUNK = 1024 * 1024


def digest(path: str) -> str:
    """A full-file digest. Not the prefix hash — this one authorises deletion."""

    out = hashlib.blake2b(digest_size=16)
    with open(path, "rb") as handle:
        while chunk := handle.read(CHUNK):
            out.update(chunk)
    return out.hexdigest()


def unprotected(conn, limit: int | None = None) -> list[dict]:
    """Photos with no copy on any record drive. The backup queue."""

    sql = (
        "SELECT i.id, i.tail, i.file_size FROM images i "
        "JOIN copies c ON c.photo_id = i.id "
        "JOIN drives d ON d.id = c.drive_id AND d.is_record = 0 "
        "WHERE i.vc_of IS NULL AND i.tail IS NOT NULL AND NOT EXISTS ("
        "  SELECT 1 FROM copies c2 JOIN drives d2 ON d2.id = c2.drive_id "
        "  WHERE c2.photo_id = i.id AND d2.is_record = 1) "
        "GROUP BY i.id ORDER BY i.id"
    )
    if limit:
        sql += f" LIMIT {int(limit)}"
    return [dict(row) for row in conn.execute(sql)]


def record_drive(conn) -> dict | None:
    """The attached drive allowed to hold a last copy."""

    for row in conn.execute("SELECT * FROM drives WHERE is_record = 1 ORDER BY id"):
        if drives.root_of(conn, row["uuid"]) is not None:
            return dict(row)
    return None


def back_up(conn, photo_id: int, *, dry_run: bool = False) -> str:
    """Copy one photo to the record drive. Adds only; never removes anything."""

    row = conn.execute(
        "SELECT tail, file_size FROM images WHERE id = ?", (int(photo_id),)
    ).fetchone()
    if row is None or not row["tail"]:
        return "no tail"

    source = photos.open_photo(conn, photo_id)
    if source is None:
        return "source unreadable"

    drive = record_drive(conn)
    if drive is None:
        return "no record drive attached"

    target = drives.path_for(conn, drive["uuid"], row["tail"])
    if target is None:
        return "no record drive attached"
    if os.path.exists(target):
        # Already there under the same tail. Confirm it really is this photo
        # before claiming the photograph is protected.
        if digest(target) == digest(source):
            if not dry_run:
                copies.saw(conn, photo_id, int(drive["id"]))
                conn.commit()
            return "already there"
        return "different file at that tail"

    if dry_run:
        return "would copy"

    os.makedirs(os.path.dirname(target), exist_ok=True)
    staging = f"{target}.copying"
    try:
        shutil.copy2(source, staging)
        if digest(staging) != digest(source):
            os.remove(staging)
            return "verify failed"
        os.replace(staging, target)
    except OSError as exc:
        if os.path.exists(staging):
            try:
                os.remove(staging)
            except OSError:
                pass
        return f"copy failed: {exc}"

    copies.saw(conn, photo_id, int(drive["id"]))
    conn.commit()
    return "copied"


def back_up_all(conn, *, limit: int | None = None, dry_run: bool = False, on_step=None) -> dict:
    """Work the backup queue. Stops early only if the record drive goes away."""

    queue = unprotected(conn, limit=limit)
    tally: dict[str, int] = {}
    started = time.time()
    for index, photo in enumerate(queue, 1):
        outcome = back_up(conn, photo["id"], dry_run=dry_run)
        tally[outcome] = tally.get(outcome, 0) + 1
        if on_step:
            on_step(index, len(queue), photo, outcome)
        if outcome == "no record drive attached":
            break
    return {"queued": len(queue), "outcomes": tally, "seconds": round(time.time() - started, 1)}
