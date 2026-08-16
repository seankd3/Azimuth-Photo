"""Where a photo's bytes are, right now.

`open()` is the whole of it: a photo is a tail, a tail lives on drives, and the
answer is the first attached drive that actually has it. The working disk wins
when both do, because it is the fast one and the expendable one.

This replaces guessing. The old resolver stripped leading components off a
recorded path and probed every attached letter for something that matched,
because the path had swallowed its drive and there was no other way back. Now
the tail is stored, so there is nothing to recover and nothing to guess.

Two properties keep the answer honest, and both are about not serving the
wrong photograph:

* a candidate must exist, and when the catalog knows the file's size it must
  match — so a same-named stranger on another drive can never stand in;
* a drive that is not attached is skipped in silence. It is *away*, which is a
  different word from *lost*, and the difference is why unplugging the archive
  can never make the library look emptied.
"""

from __future__ import annotations

import os

from model import drives


def _verified(path: str, expected_size: int | None) -> bool:
    try:
        stat = os.stat(path)
    except OSError:
        return False
    return not expected_size or stat.st_size == int(expected_size)


def _by_preference(conn) -> list[dict]:
    """Drives worth trying, working disks before record drives.

    Reads prefer the working disk for the same reason reclaim only ever deletes
    from it: it is the copy that is meant to be cheap to lose.
    """

    return [dict(row) for row in conn.execute(
        "SELECT * FROM drives ORDER BY is_record ASC, id ASC"
    )]


def locate(conn, tail: str, *, expected_size: int | None = None) -> str | None:
    """A path this machine can open for `tail`, or None if no drive has it."""

    if not tail:
        return None
    for drive in _by_preference(conn):
        path = drives.path_for(conn, drive["uuid"], tail)
        if path and _verified(path, expected_size):
            return path
    return None


def open_photo(conn, image_id: int) -> str | None:
    """The path of a catalogued photo, wherever it currently lives."""

    row = conn.execute(
        "SELECT tail, file_size FROM images WHERE id = ?", (int(image_id),)
    ).fetchone()
    if row is None or not row["tail"]:
        return None
    return locate(conn, row["tail"], expected_size=row["file_size"])


def state(conn, image_id: int) -> str:
    """One of: available, away, lost.

    The distinction the whole design turns on: a photo is only *lost* when
    every drive that could hold it is attached and none of them does. While any
    drive is away the answer is *away*, so an unplugged archive can never be
    mistaken for a deleted library — and no threshold, ratio or override switch
    is involved in saying so.
    """

    row = conn.execute(
        "SELECT tail, file_size FROM images WHERE id = ?", (int(image_id),)
    ).fetchone()
    if row is None:
        return "lost"
    if row["tail"] and locate(conn, row["tail"], expected_size=row["file_size"]):
        return "available"
    for drive in _by_preference(conn):
        if drives.root_of(conn, drive["uuid"]) is None:
            return "away"
    return "lost"
