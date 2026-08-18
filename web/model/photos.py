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

import hashlib
import os
import stat as _stat
import shutil

from model import drives

# The identity digest: the first 8 MiB of the file, then its size as eight
# little-endian bytes. Cheap enough to run on a cold archive drive, and the
# size suffix is what stops two files that share an 8 MiB header — a roll of
# film scans, a burst of the same frame — from colliding for free.
HASH_PREFIX_BYTES = 8 * 1024 * 1024
HASH_DIGEST_BYTES = 16


def content_hash(path: str) -> str:
    """What photo is this, as far as a cheap read can tell.

    Names candidates and nothing more. Never identity, never permission to
    delete, and never permission to merge — a merge that drops the loser's
    decisions breaks the one promise this design makes. When the answer must be
    load-bearing, `backup.digest` reads every byte instead.
    """

    size = os.stat(path).st_size
    digest = hashlib.blake2b(digest_size=HASH_DIGEST_BYTES)
    with open(path, "rb") as handle:
        digest.update(handle.read(HASH_PREFIX_BYTES))
    digest.update(int(size).to_bytes(8, byteorder="little", signed=False))
    return digest.hexdigest()


def identify(conn, path: str) -> dict:
    """What photo is this, and do we already know it?

    Returns the hash and size always, and an `id` when some catalogued photo
    shares that hash. A caller that finds one has found a *candidate* — the
    same photo on another drive, most often, but possibly a different frame
    that shares a header. What it may do with that is add a copy row. What it
    may never do is merge two rows, because the loser's decisions are the only
    thing here that cannot be recomputed.
    """

    digest = content_hash(path)
    row = conn.execute(
        "SELECT id FROM images WHERE content_hash = ? AND vc_of IS NULL LIMIT 1", (digest,)
    ).fetchone()
    return {"hash": digest, "size": os.stat(path).st_size, "id": row["id"] if row else None}


def put(conn, source: str, drive_uuid: str, tail: str) -> dict:
    """Write a file onto a drive at a tail, then identify it and record the copy.

    Import is this function plus one pure `(date, kind, roll) -> tail`, which is
    why importing is not a subsystem.

    It never overwrites. A different file already at that tail is a refusal, not
    a resolution: choosing a `-2` suffix here would hide the fact that the
    naming rule produced a collision, and the caller is the only thing that
    knows whether that is expected.
    """

    target = drives.path_for(conn, drive_uuid, tail)
    if target is None:
        return {"outcome": "drive not attached"}

    if os.path.exists(target):
        if os.path.getsize(target) == os.path.getsize(source) and content_hash(target) == content_hash(source):
            return {"outcome": "already there", "path": target, **identify(conn, target)}
        return {"outcome": "different file at that tail", "path": target}

    os.makedirs(os.path.dirname(target), exist_ok=True)
    staging = f"{target}.importing"
    try:
        shutil.copy2(source, staging)
        # Proved before it is visible: a partial copy that took the real name
        # would be indexed as the photograph, and the original may already be
        # off the card by then.
        if content_hash(staging) != content_hash(source):
            os.remove(staging)
            return {"outcome": "verify failed"}
        os.replace(staging, target)
    except OSError as error:
        if os.path.exists(staging):
            try:
                os.remove(staging)
            except OSError:
                pass
        return {"outcome": f"copy failed: {error}"}

    return {"outcome": "written", "path": target, **identify(conn, target)}


def _verified(path: str, expected_size: int | None) -> bool:
    """Is this a real file on this drive, of the size we recorded?

    `lstat`, not `stat`, and the symlink check is the point: a catalogued tail
    must resolve to a regular file *on the drive it names*. A symlink there
    points wherever it likes — outside the library, onto another volume, at a
    file the owner never imported — and `stat` follows it silently, so the app
    would serve bytes from a path no sweep ever walked.
    """

    try:
        entry = os.lstat(path)
    except OSError:
        return False
    if not _stat.S_ISREG(entry.st_mode):
        return False
    return not expected_size or entry.st_size == int(expected_size)


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

    # A copy normally shares the photo's filed tail, but collision-safe imports
    # may give one drive a different one. The copy row is a hint, so try its
    # explicit address first and still fall back to the canonical tail across
    # every attached drive if the hint is stale or absent.
    for copy in conn.execute(
        "SELECT d.uuid, COALESCE(c.tail, ?) AS tail FROM copies c "
        "JOIN drives d ON d.id = c.drive_id WHERE c.photo_id = ? "
        "ORDER BY d.is_record ASC, d.id ASC",
        (row["tail"], int(image_id)),
    ):
        path = drives.path_for(conn, copy["uuid"], copy["tail"])
        if path and _verified(path, row["file_size"]):
            return path
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


def hashes(conn, image_ids) -> list[str]:
    """Image ids to the identities decisions are keyed on.

    The API speaks ids because a row is what a client can point at; the log
    speaks hashes because a decision has to outlive the row. This is that
    translation, in the one place that owns a photograph's identity.
    """

    ids_ = [int(i) for i in image_ids if int(i) > 0]
    if not ids_:
        return []
    marks = ",".join("?" * len(ids_))
    rows = conn.execute(
        f"SELECT content_hash FROM images WHERE id IN ({marks}) AND content_hash IS NOT NULL",
        ids_,
    ).fetchall()
    return [r["content_hash"] for r in rows]


def ids(conn, content_hashes) -> list[int]:
    """Back the other way, skipping identities whose photograph is gone."""

    wanted = [h for h in content_hashes if h]
    if not wanted:
        return []
    marks = ",".join("?" * len(wanted))
    rows = conn.execute(
        f"SELECT id FROM images WHERE content_hash IN ({marks}) ORDER BY id", wanted
    ).fetchall()
    return [int(r["id"]) for r in rows]
