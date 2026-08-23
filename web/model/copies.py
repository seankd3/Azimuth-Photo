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

Two things it *recognises*, because the folders are the truth and people move
and rewrite files in them:

* **A move.** A file known here that is no longer at its tail, reappearing
  under a new tail with the same name, size and time, is the same photograph
  at a new address -- re-addressed at once, so picks and turns travel with
  it, and confirmed by the worker's full-byte identity later. Renaming a
  folder in Explorer is therefore instant, not five hundred new photographs
  and five hundred ghosts. When another drive still holds the old address the
  row keeps it and this drive records the alternate.
* **A rewrite.** Same tail, different bytes -- Lightroom saving metadata into
  a DNG, an edit written over the file -- is the same photograph with a new
  identity; it is re-identified now and the decisions are carried to the new
  digest, which is the log appending rather than anything being rewritten.
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


def walk_tails(root: str, under: str = "") -> tuple[set[str], bool]:
    """Every tail under `root` (or only below the folder `under`), and whether
    the walk completed.

    An incomplete walk is reported rather than papered over: the caller must
    change nothing, because "I could not read the folder" and "the folder is
    empty" are the same sight from here and opposite facts.
    """

    seen: set[str] = set()
    sidecars: set[str] = set()
    complete = True
    from model import photos

    def failed(_error: OSError) -> None:
        nonlocal complete
        complete = False

    start = os.path.join(root, *under.split("/")) if under else root
    if under and not os.path.isdir(start):
        return seen, sidecars, True  # a folder that is not there holds nothing, truthfully
    for dirpath, dirnames, filenames in os.walk(start, onerror=failed):
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
                # The walk is the one pass that sees every file, so it is
                # where Lightroom's sidecars are noticed; who reads them is
                # not this module's business.
                if name.lower().endswith(".xmp"):
                    tail = drives.tail_for(root, path)
                    if tail:
                        sidecars.add(tail)
                continue
            tail = drives.tail_for(root, path)
            if tail:
                seen.add(tail)
    return seen, sidecars, complete


def sweep(
    conn,
    drive_uuid: str,
    *,
    under: str = "",
    batch_size: int = SWEEP_BATCH,
    progress: Callable[[dict], None] | None = None,
) -> dict:
    """Admit observed photos progressively, recognise moves and rewrites, then
    retire stale copy hints. `under` narrows everything to one folder's tail."""

    batch_size = int(batch_size)
    if batch_size < 1:
        raise ValueError("a sweep batch contains at least one file")
    under = str(under or "").strip("/")

    root = drives.root_of(conn, drive_uuid)
    if root is None:
        return {"drive": drive_uuid, "applied": False, "reason": "not attached"}

    seen, sidecars, complete = walk_tails(root, under)

    # Re-read the marker *after* walking. If the drive went away mid-pass, what
    # we saw describes a moment that no longer exists.
    if drives.read_marker(root) != drive_uuid:
        return {"drive": drive_uuid, "applied": False, "reason": "drive changed during sweep"}
    if not complete:
        return {"drive": drive_uuid, "applied": False, "reason": "walk incomplete"}

    drive = conn.execute("SELECT id FROM drives WHERE uuid = ?", (drive_uuid,)).fetchone()
    drive_id = int(drive["id"])
    from model import decisions, photos

    def in_scope(tail: str) -> bool:
        return not under or tail == under or tail.startswith(under + "/")

    known: dict[str, list[dict]] = {}
    for row in conn.execute(
        "SELECT id, tail, file_size, file_modified_ns, content_hash FROM images "
        "WHERE tail IS NOT NULL AND vc_of IS NULL ORDER BY id"
    ):
        known.setdefault(row["tail"], []).append(dict(row))
    # One drive may hold a photo under a collision-safe alternate tail. That
    # address is just as known as the photo's canonical tail; failing to add it
    # here admits the archive copy as a second photograph on the next sweep.
    for row in conn.execute(
        "SELECT i.id, c.tail, i.file_size, i.file_modified_ns, i.content_hash FROM copies c "
        "JOIN images i ON i.id = c.photo_id "
        "WHERE c.drive_id = ? AND c.tail IS NOT NULL",
        (drive_id,),
    ):
        known.setdefault(row["tail"], []).append(dict(row))

    # What this drive held that is not where it was: candidates for a move,
    # indexed by the three things a move leaves unchanged.
    held = {
        int(row["photo_id"]): row["tail"]
        for row in conn.execute("SELECT photo_id, tail FROM copies WHERE drive_id = ?", (drive_id,))
    }
    tail_of = {int(row["id"]): tail for tail, rows in known.items() for row in rows}
    vanished: dict[tuple[str, int, int], list[dict]] = {}
    for photo_id, alt in held.items():
        tail = alt or tail_of.get(photo_id)
        if tail is None or not in_scope(tail) or tail in seen:
            continue
        for row in known.get(tail, ()):
            if int(row["id"]) != photo_id or row["file_size"] is None or row["file_modified_ns"] is None:
                continue
            key = (os.path.basename(tail), int(row["file_size"]), int(row["file_modified_ns"]))
            vanished.setdefault(key, []).append({**row, "tail": tail})

    def holders(photo_id: int) -> int:
        return int(conn.execute(
            "SELECT COUNT(*) FROM copies WHERE photo_id = ? AND drive_id != ?", (photo_id, drive_id)
        ).fetchone()[0])

    now = time.time()
    recorded = admitted = moved = carried = 0
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
            "photos_moved": moved,
            "photos_rewritten": carried,
            "changed": changed,
            "unknown_files": len(seen) - recorded,
            # What the walk noticed beside the photographs; the caller
            # decides whether anything reads them.
            "sidecars": sorted(sidecars),
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
            # The same address, a different size or time: read the bytes. A new
            # identity means the photograph was rewritten -- re-identify it and
            # carry what was decided to the new digest. The same identity means
            # only this drive's copy carries a different time (copied without
            # preserving it); the photograph is unchanged, and the row's time is
            # left alone unless this drive is the only one holding it.
            row = candidates[0]
            try:
                digest = photos.content_hash(path)
            except OSError:
                continue
            old = row["content_hash"]
            if old != digest:
                if old:
                    decisions.carry(conn, old, digest)
                conn.execute(
                    "UPDATE images SET content_hash = ?, file_size = ?, file_modified_ns = ? WHERE id = ?",
                    (digest, int(entry.st_size), int(entry.st_mtime_ns), int(row["id"])),
                )
                row.update(file_size=int(entry.st_size), file_modified_ns=int(entry.st_mtime_ns), content_hash=digest)
                changed.append(tail)
                carried += 1
            elif holders(int(row["id"])) == 0:
                conn.execute(
                    "UPDATE images SET file_size = ?, file_modified_ns = ? WHERE id = ?",
                    (int(entry.st_size), int(entry.st_mtime_ns), int(row["id"])),
                )
                row.update(file_size=int(entry.st_size), file_modified_ns=int(entry.st_mtime_ns))
            match = row

        if match is None:
            key = (os.path.basename(tail), int(entry.st_size), int(entry.st_mtime_ns))
            gone = vanished.get(key) or []
            if len(gone) == 1:
                # A move. Re-address the photograph when this drive was the only
                # one holding it; otherwise the canonical address stands on the
                # other drive and this drive records where its copy went.
                old_row = gone.pop()
                photo_id = int(old_row["id"])
                if holders(photo_id) == 0:
                    conn.execute("UPDATE images SET tail = ? WHERE id = ?", (tail, photo_id))
                    known.setdefault(tail, []).append({**old_row, "tail": tail})
                    known.get(old_row["tail"], [])[:] = [
                        r for r in known.get(old_row["tail"], []) if int(r["id"]) != photo_id
                    ]
                    saw(conn, photo_id, drive_id, when=now)
                else:
                    saw(conn, photo_id, drive_id, tail=tail, when=now)
                    known.setdefault(tail, []).append({**old_row, "tail": tail})
                held[photo_id] = tail if holders(photo_id) else None
                moved += 1
                recorded += 1
                processed += 1
                if processed % batch_size == 0 and not publish():
                    return result(applied=False, reason="drive changed during sweep")
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
                "content_hash": None,
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
        if tail is None or not in_scope(tail):
            continue
        if tail not in seen:
            forget(conn, photo_id, drive_id)
            retired += 1

    conn.commit()
    return result(applied=True, retired=retired)
