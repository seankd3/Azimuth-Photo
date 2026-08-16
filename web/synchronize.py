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


def plan(conn, folder: str = "") -> dict:
    """What synchronizing this folder would change. Writes nothing.

    `new` are files on an attached drive that the catalog has never seen.
    `missing` are catalogued photographs that no attached drive holds — and it
    is empty, always, if any drive is away.
    """

    prefix = str(folder or "").replace("\\", "/").strip("/")
    scope = f"{prefix}/" if prefix else ""

    attached, away = [], []
    for row in conn.execute("SELECT * FROM drives ORDER BY is_record, id"):
        root = drives.root_of(conn, row["uuid"])
        (attached if root else away).append({"uuid": row["uuid"], "root": root,
                                             "label": row["label"] or row["uuid"][:8]})

    on_disk: set[str] = set()
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
        on_disk |= {
            scope + tail for tail in seen
            if kind.extension(tail) in PHOTOGRAPHS
        }

    catalogued = {
        row["tail"]: row["id"]
        for row in conn.execute(
            "SELECT id, tail FROM images WHERE tail IS NOT NULL"
            + (" AND substr(tail, 1, ?) = ?" if scope else ""),
            (len(scope), scope) if scope else (),
        )
    }

    new = sorted(on_disk - set(catalogued))
    missing: list[str] = []
    if not away and not unreadable:
        missing = sorted(set(catalogued) - on_disk)

    return {
        "folder": prefix or "everything",
        "drives": [d["label"] for d in attached],
        "away": [d["label"] for d in away],
        "unreadable": unreadable,
        "new": new,
        "missing": missing,
        "counts": {"new": len(new), "missing": len(missing), "on disk": len(on_disk)},
        # Said plainly, because it is the reason the numbers can be trusted.
        "note": (
            f"{len(away)} drive(s) away, so nothing is offered for removal"
            if away else
            "every drive is attached, so anything not found is genuinely gone"
        ) if (away or not unreadable) else "a drive could not be read; nothing is offered for removal",
    }


def apply(conn, folder: str = "", *, adopt: bool = True, forget: bool = False) -> dict:
    """Act on the plan. Each half is opt-in and they are not symmetrical.

    Adopting is safe — it adds rows for files that exist. Forgetting removes a
    photograph from the library, so it stays off by default and is only ever
    offered for the `missing` list, which is empty whenever a drive is away.

    Forgetting sets `trashed`; it never deletes a file. The machine does not
    remove the last copy of anything.
    """

    from model import decisions

    found = plan(conn, folder)
    adopted = forgotten = 0

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
    return {**found, "adopted": adopted, "forgotten": forgotten}
