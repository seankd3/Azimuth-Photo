"""Synchronize a folder: what changed on disk, and what to do about it.

Lightroom's Synchronize Folder shows you a count, asks, and then acts. The
asking is the feature — a scan that acts on its own is one you cannot trust
with a drive that was briefly unplugged.

Azimuth already had both halves and neither was ever connected to a question:
`copies.walk_tails` says what is on a drive, and `photos.state` says whether a
photograph is available, away or lost. This is the conversation between them.

> **A photograph is only offered for removal when every drive that could hold
> it is attached and none of them has it.**

That is the same sentence the whole design turns on, and here it is what makes
the feature safe: with the archive unplugged, `missing` is empty. Not "empty
because we checked a ratio" — empty because *away* and *lost* are different
words and only one of them is ever actionable. Lightroom cannot make that
distinction, which is why its dialog can offer to remove photographs that are
simply on a disk you did not plug in.

**Three words, not two.** `new` and `missing` describe a file appearing or
disappearing; neither describes the far more common event, which is a file
*staying put and becoming different*. `changed` is that. It is also the whole
of the Lightroom integration: Save Metadata to Files rewrites the photograph,
so the orientation you set in Lightroom arrives here the same way a phone's
does — in the file, read by the same decoder, with nothing in between. There
is deliberately no `.lrcat` reader. Adobe's schema is private and serves one
application; the photograph is a published format and serves all of them, and
the version that reads the file is the version that also works for darktable,
Bridge, Capture One, a restored backup and a re-scan.

Nothing here writes unless asked. `plan()` is the dialog; `apply()` is the
button.
"""

from __future__ import annotations

import os

from model import copies, drives, photos
from photo import kind

# What counts as a photograph on disk. A folder holds sidecars, catalogs,
# previews and lockfiles beside the pictures, and a synchronize that offered
# to adopt an .xmp as a photograph would fill the library with metadata --
# 75 of them in the first folder this was pointed at.
PHOTOGRAPHS = kind.RAW_FORMATS | kind.DISPLAY_FORMATS


def _size(path: str) -> int | None:
    """The file's size, or None if it cannot be read right now."""

    try:
        return os.stat(path).st_size
    except OSError:
        return None


def plan(conn, folder: str = "") -> dict:
    """What synchronizing this folder would change. Writes nothing.

    `new` are files on an attached drive that the catalog has never seen.
    `missing` are catalogued photographs that no attached drive holds — and it
    is empty, always, if any drive is away.
    `changed` are catalogued photographs whose file is present but is no longer
    the file we read. Size is the test: it costs the `stat` the walk is doing
    anyway, and it is recorded for every one of the 144,271 catalogued
    photographs, so it works today without a migration. A metadata write moves
    it — measured, on all 40 frames of the roll this was built for.
    """

    prefix = str(folder or "").replace("\\", "/").strip("/")
    scope = f"{prefix}/" if prefix else ""

    attached, away = [], []
    for row in conn.execute("SELECT * FROM drives ORDER BY is_record, id"):
        root = drives.root_of(conn, row["uuid"])
        (attached if root else away).append({"uuid": row["uuid"], "root": root,
                                             "label": row["label"] or row["uuid"][:8]})

    # Tail -> where it was actually found, so the size test needs no second
    # search for the drive that answered.
    on_disk: dict[str, str] = {}
    unreadable: list[str] = []
    for drive in attached:
        start = os.path.join(drive["root"], scope.replace("/", os.sep)) if scope else drive["root"]
        if not os.path.isdir(start):
            continue
        # Walk only the folder asked about. Walking the whole drive and
        # filtering afterwards is the same answer and, on a 144,000-file
        # archive, the difference between a dialog and a hang.
        seen, complete = copies.walk_tails(start)
        if not complete:
            # A walk that could not be completed says nothing. "I could not read
            # the folder" and "the folder is empty" look identical from here and
            # are opposite facts.
            unreadable.append(drive["label"])
            continue
        for tail in seen:
            if kind.extension(tail) in PHOTOGRAPHS:
                on_disk.setdefault(scope + tail, os.path.join(start, tail.replace("/", os.sep)))

    # A tail should name one photograph. 110 in this archive name two — the
    # same file catalogued once per drive, from before `copies` existed — and
    # the two rows carry the two drives' sizes, which now differ because one
    # drive has the Lightroom-updated file and the other has the old one.
    # Both sides pick the lowest id so `plan` and `apply` are talking about the
    # same row; without that the file is reported changed, refreshed, and
    # reported changed again forever.
    catalogued: dict[str, int] = {}
    for row in conn.execute(
        "SELECT tail, file_size FROM images WHERE tail IS NOT NULL"
        + (" AND substr(tail, 1, ?) = ?" if scope else "")
        + " ORDER BY id",
        (len(scope), scope) if scope else (),
    ):
        catalogued.setdefault(row["tail"], row["file_size"])

    new = sorted(set(on_disk) - set(catalogued))
    missing: list[str] = []
    if not away and not unreadable:
        missing = sorted(set(catalogued) - set(on_disk))
    changed = sorted(
        tail for tail in set(catalogued) & set(on_disk)
        # A size we cannot read is not a size that differs. An unreadable file
        # is the `unreadable` case above, not a changed one.
        if (_size(on_disk[tail]) or catalogued[tail]) != catalogued[tail]
    )

    return {
        "folder": prefix or "everything",
        "drives": [d["label"] for d in attached],
        "away": [d["label"] for d in away],
        "unreadable": unreadable,
        "new": new,
        "missing": missing,
        "changed": changed,
        "counts": {"new": len(new), "missing": len(missing),
                   "changed": len(changed), "on disk": len(on_disk)},
        # Said plainly, because it is the reason the numbers can be trusted.
        "note": (
            f"{len(away)} drive(s) away, so nothing is offered for removal"
            if away else
            "every drive is attached, so anything not found is genuinely gone"
        ) if (away or not unreadable) else "a drive could not be read; nothing is offered for removal",
    }


def _found_at(conn, tail: str) -> str | None:
    """Where an attached drive actually holds this tail, if one does."""

    for drive in conn.execute("SELECT uuid FROM drives ORDER BY is_record, id"):
        path = drives.path_for(conn, drive["uuid"], tail)
        if path and os.path.exists(path):
            return path
    return None


def apply(conn, folder: str = "", *, adopt: bool = True, refresh: bool = True,
          forget: bool = False) -> dict:
    """Act on the plan. Each part is opt-in and they are not symmetrical.

    Adopting and refreshing are safe — one adds rows for files that exist, the
    other re-reads facts the machine worked out and can work out again. Both
    are on. Forgetting removes a photograph from the library, so it stays off
    by default and is only ever offered for the `missing` list, which is empty
    whenever a drive is away.

    Forgetting sets `trashed`; it never deletes a file. The machine does not
    remove the last copy of anything.
    """

    import render
    import tiles
    from model import decisions

    found = plan(conn, folder)
    adopted = refreshed = forgotten = 0

    if adopt:
        for drive in conn.execute("SELECT * FROM drives ORDER BY is_record, id"):
            root = drives.root_of(conn, drive["uuid"])
            if not root:
                continue
            for tail in found["new"]:
                path = drives.path_for(conn, drive["uuid"], tail)
                if not path or not os.path.exists(path):
                    continue
                identity = photos.identify(conn, path)
                if identity["id"]:
                    # Known bytes at a new tail: a copy, not a new photograph.
                    copies.saw(conn, identity["id"], int(drive["id"]), tail=tail)
                else:
                    conn.execute(
                        "INSERT INTO images(tail, file_size, content_hash, status)"
                        " VALUES (?, ?, ?, 'kept')",
                        (tail, identity["size"], identity["hash"]),
                    )
                adopted += 1

    if refresh:
        for tail in found["changed"]:
            row = conn.execute(
                "SELECT id, content_hash AS hash FROM images WHERE tail = ?"
                " ORDER BY id LIMIT 1", (tail,)
            ).fetchone()
            path = _found_at(conn, tail)
            if row is None or not path:
                continue
            identity = photos.identify(conn, path)
            if row["hash"] and identity["hash"] != row["hash"]:
                # The photograph is the same photograph; only its bytes moved.
                # Its decisions follow it, and its renditions do not -- they
                # were computed from bytes that no longer exist.
                decisions.carry(conn, row["hash"], identity["hash"])
                tiles.purge(conn, row["hash"])
            try:
                width, height = render.dimensions(path)
            except Exception:
                width = height = None
            conn.execute(
                "UPDATE images SET content_hash = ?, file_size = ?, file_modified_at = ?,"
                " width = COALESCE(?, width), height = COALESCE(?, height) WHERE id = ?",
                (identity["hash"], identity["size"], os.path.getmtime(path),
                 width, height, row["id"]),
            )
            refreshed += 1

    if forget and found["missing"]:
        for tail in found["missing"]:
            row = conn.execute(
                "SELECT id, content_hash AS hash FROM images WHERE tail = ?", (tail,)
            ).fetchone()
            if row is None:
                continue
            if row["hash"]:
                decisions.decide(conn, row["hash"], decisions.STATUS, "trashed")
            conn.execute("UPDATE images SET status = 'trashed' WHERE id = ?", (row["id"],))
            forgotten += 1

    conn.commit()
    return {**found, "adopted": adopted, "refreshed": refreshed, "forgotten": forgotten}
