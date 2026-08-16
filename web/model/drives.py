"""Drives: where photos live.

A drive is named by a uuid written into a marker file at its root, never by its
letter. Letters move — plug a card reader in before the archive and E: is
something else entirely — and a library that addresses 144,000 photos by letter
is one reboot away from losing itself. The marker also answers *is this really
the drive I mean?*, which a letter cannot.

Two roots today: the working disk the photos are edited on, and the archive.
Nothing here knows that, or cares. `is_record` is the only thing that
distinguishes them, and it means one thing: may this drive hold the last copy?
"""

from __future__ import annotations

import os
import time
import uuid as uuid_module

MARKER_NAME = ".azimuth-drive"


def _marker_path(root: str) -> str:
    return os.path.join(root, MARKER_NAME)


def read_marker(root: str) -> str | None:
    """The uuid this root claims, or None if it claims nothing.

    A root that is not there and a root with no marker are the same answer on
    purpose: neither is a drive we know.
    """

    try:
        with open(_marker_path(root), encoding="utf-8") as handle:
            claimed = handle.readline().strip()
    except OSError:
        return None
    return claimed or None


def write_marker(root: str, drive_uuid: str) -> None:
    """Claim a root for a drive. Never overwrites a different claim.

    Refusing is the whole point: two drives answering to one uuid would make
    every copy row ambiguous, and the failure would look like a photo that
    moved on its own.
    """

    existing = read_marker(root)
    if existing and existing != drive_uuid:
        raise ValueError(f"{root} already belongs to drive {existing}")
    with open(_marker_path(root), "w", encoding="utf-8") as handle:
        handle.write(f"{drive_uuid}\n")


def attach(conn, root: str, *, label: str = "", is_record: bool = False) -> dict:
    """Register a root as a drive, or recognise one we have seen before.

    Recognition is by marker, not by path, so a drive that came back on a
    different letter is the same drive with a new root — one row updated
    instead of every photo on it rewritten.
    """

    root = os.path.normpath(root)
    if not os.path.isdir(root):
        raise ValueError(f"not a directory: {root}")

    claimed = read_marker(root)
    if claimed:
        row = conn.execute("SELECT * FROM drives WHERE uuid = ?", (claimed,)).fetchone()
        if row is not None:
            conn.execute(
                "UPDATE drives SET root = ?, seen_at = ? WHERE uuid = ?",
                (root, time.time(), claimed),
            )
            conn.commit()
            return dict(conn.execute("SELECT * FROM drives WHERE uuid = ?", (claimed,)).fetchone())

    drive_uuid = claimed or str(uuid_module.uuid4())
    write_marker(root, drive_uuid)
    conn.execute(
        "INSERT INTO drives(uuid, root, label, is_record, seen_at) VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(uuid) DO UPDATE SET root = excluded.root, seen_at = excluded.seen_at",
        (drive_uuid, root, label or os.path.basename(root) or root, 1 if is_record else 0, time.time()),
    )
    conn.commit()
    return dict(conn.execute("SELECT * FROM drives WHERE uuid = ?", (drive_uuid,)).fetchone())


def _candidate_roots() -> list[str]:
    """Places a drive could have reappeared. Patchable seam for tests."""

    import string

    return [f"{letter}:{os.sep}" for letter in string.ascii_uppercase if os.path.isdir(f"{letter}:{os.sep}")]


def root_of(conn, drive_uuid: str) -> str | None:
    """Where this drive is right now, or None if it is not attached.

    The remembered root is checked first because that is nearly always the
    answer. When its marker no longer agrees, the letters are probed once and
    the row follows the drive — the only write a moved drive costs.
    """

    row = conn.execute("SELECT root FROM drives WHERE uuid = ?", (drive_uuid,)).fetchone()
    if row is None:
        return None

    remembered = str(row["root"])
    if read_marker(remembered) == drive_uuid:
        return remembered

    for candidate in _candidate_roots():
        # A marker sits at a library root, which is usually one level down.
        for root in (candidate, *_child_dirs(candidate)):
            if read_marker(root) == drive_uuid:
                conn.execute(
                    "UPDATE drives SET root = ?, seen_at = ? WHERE uuid = ?",
                    (os.path.normpath(root), time.time(), drive_uuid),
                )
                conn.commit()
                return os.path.normpath(root)
    return None


def _child_dirs(root: str) -> list[str]:
    try:
        with os.scandir(root) as entries:
            return [entry.path for entry in entries if not entry.name.startswith(".") and entry.is_dir()]
    except OSError:
        return []


def online(conn, drive_uuid: str) -> bool:
    """Is this drive here? Answered by looking, never by a stored flag.

    A remembered `online` column would be a value that can be stale about
    whether something is stale.
    """

    return root_of(conn, drive_uuid) is not None


def path_for(conn, drive_uuid: str, tail: str) -> str | None:
    """The absolute path of a tail on this drive, if the drive is attached.

    This is the whole point of storing tails: a path is a drive plus a tail,
    and only the drive half ever changes.
    """

    root = root_of(conn, drive_uuid)
    if root is None:
        return None
    return os.path.join(root, tail.replace("/", os.sep))


def tail_for(root: str, path: str) -> str | None:
    """The tail of `path` under `root`, or None if it does not live there.

    The inverse of `path_for`, and deliberately its neighbour: a path is a
    drive plus a tail, so the two halves of that sentence belong in one file.

    Tails are always POSIX-separated, whatever the platform wrote. They travel
    between drives — that is their entire job — and a tail carrying `\\` would
    stop matching the same photo on a drive that spells it `/`. Comparison is
    case-folded because NTFS is, but the tail keeps the case the disk actually
    uses, so it still reads like the folder it names.
    """

    root_parts = [p for p in os.path.normpath(root).replace("\\", "/").split("/") if p]
    path_parts = [p for p in os.path.normpath(path).replace("\\", "/").split("/") if p]
    if len(path_parts) <= len(root_parts):
        return None
    for mine, theirs in zip(root_parts, path_parts):
        if os.path.normcase(mine) != os.path.normcase(theirs):
            return None
    return "/".join(path_parts[len(root_parts):])
