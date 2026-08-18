"""Getting a second copy of every photograph, and never removing the last one.

Two verbs, and the asymmetry between them is the whole safety model.

`back_up` only ever **adds**. It copies a photo to a drive allowed to hold the
last copy, proves the copy byte for byte, and records it. If anything is wrong
it leaves both sides untouched — the worst case is that a photo stays
unprotected, which is where it already was.

`reclaim` **removes**, and therefore proves far more before it acts. It compares
the actual byte streams at the moment of deletion. A copy row and even a full
content hash are enough to *report* that a photo is backed up and never enough
to authorize deleting one.
"""

from __future__ import annotations

import os
import shutil
import time
import uuid

from model import copies, drives, photos

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

    try:
        target = drives.path_for(conn, drive["uuid"], row["tail"])
    except ValueError:
        return "invalid tail"
    if target is None:
        return "no record drive attached"
    if os.path.exists(target):
        # Already there under the same tail. Confirm it really is this photo
        # before claiming the photograph is protected.
        if photos.same_bytes(target, source):
            if not dry_run:
                copies.saw(conn, photo_id, int(drive["id"]))
                conn.commit()
            return "already there"
        return "different file at that tail"

    if dry_run:
        return "would copy"

    os.makedirs(os.path.dirname(target), exist_ok=True)
    staging = f"{target}.copying-{uuid.uuid4().hex}"
    try:
        shutil.copy2(source, staging)
        if not photos.same_bytes(staging, source):
            return "verify failed"
        photos.publish_without_overwrite(staging, target)
    except OSError as exc:
        return f"copy failed: {exc}"
    finally:
        if os.path.exists(staging):
            try:
                os.remove(staging)
            except OSError:
                pass

    copies.saw(conn, photo_id, int(drive["id"]))
    conn.commit()
    return "copied"


def reclaim(conn, photo_id: int, *, dry_run: bool = False) -> str:
    """Free the working disk's copy, having just proved the archive's is identical.

    Everything expensive about this function is deliberate. It re-opens both
    files *now* and compares their bytes, because a copy row is a hint written
    some time ago and a hint may not authorise a deletion. It deletes only from
    a drive whose `is_record` is 0, so the archive's copy is unreachable from
    here no matter what a caller passes. And it re-reads the record drive's
    marker after the comparison, so a drive pulled mid-read cannot be the one
    that vouched for what we are about to remove.

    A guess may be wrong. A consequence may not.
    """

    row = conn.execute("SELECT tail FROM images WHERE id = ?", (int(photo_id),)).fetchone()
    if row is None or not row["tail"]:
        return "no tail"

    drive = record_drive(conn)
    if drive is None:
        return "no record drive attached"
    recorded = conn.execute(
        "SELECT tail FROM copies WHERE photo_id = ? AND drive_id = ?",
        (int(photo_id), int(drive["id"])),
    ).fetchone()
    archived_tail = recorded["tail"] if recorded and recorded["tail"] else row["tail"]
    try:
        archived = drives.path_for(conn, drive["uuid"], archived_tail)
    except ValueError:
        return "invalid tail"
    if archived is None or not os.path.exists(archived):
        return "not archived"

    freed = []
    for holder in copies.drives_holding(conn, photo_id):
        if int(holder["is_record"]):
            continue
        try:
            here = drives.path_for(conn, holder["uuid"], holder["copy_tail"] or row["tail"])
        except ValueError:
            return "invalid tail"
        if not here or not os.path.exists(here) or os.path.normcase(here) == os.path.normcase(archived):
            continue

        if not photos.same_bytes(here, archived):
            return "copies differ"
        if drives.read_marker(drives.root_of(conn, drive["uuid"]) or "") != drive["uuid"]:
            return "record drive changed while verifying"
        if dry_run:
            return "would free"
        os.remove(here)
        copies.forget(conn, photo_id, int(holder["id"]))
        freed.append(here)

    if not freed:
        return "nothing to free"
    conn.commit()
    return "freed"


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
