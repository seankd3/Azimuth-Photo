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
import uuid

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

    try:
        target = drives.path_for(conn, drive_uuid, tail)
    except ValueError:
        return {"outcome": "invalid tail"}
    if target is None:
        return {"outcome": "drive not attached"}

    from model import backup

    if os.path.lexists(target):
        if _verified(target, os.path.getsize(source)) and backup.digest(target) == backup.digest(source):
            return {"outcome": "already there", "path": target, **identify(conn, target)}
        return {"outcome": "different file at that tail", "path": target}

    os.makedirs(os.path.dirname(target), exist_ok=True)
    staging = f"{target}.importing-{uuid.uuid4().hex}"
    try:
        shutil.copy2(source, staging)
        if backup.digest(staging) != backup.digest(source):
            return {"outcome": "verify failed"}
        _publish_without_overwrite(staging, target)
    except OSError as error:
        return {"outcome": f"copy failed: {error}"}
    finally:
        if os.path.exists(staging):
            try:
                os.remove(staging)
            except OSError:
                pass

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
    try:
        tail = drives.safe_tail(tail)
    except ValueError:
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
        try:
            path = drives.path_for(conn, copy["uuid"], copy["tail"])
        except ValueError:
            continue
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


def move(conn, photo_id: int, drive_uuid: str, tail: str) -> str:
    """Relocate one photograph without ever overwriting or risking its last copy.

    The destination becomes visible only after a full-byte verification. The
    catalog changes next. The source is removed last, when both the bytes and
    the durable addresses are already safe.
    """

    from model import backup, copies

    photo = conn.execute(
        "SELECT tail FROM images WHERE id = ?", (int(photo_id),)
    ).fetchone()
    if photo is None or not photo["tail"]:
        return "no photo"
    source = open_photo(conn, photo_id)
    if source is None:
        return "source unreadable"
    target_root = drives.root_of(conn, drive_uuid)
    if target_root is None:
        return "drive not attached"
    try:
        tail = drives.safe_tail(tail)
        target = drives.path_for(conn, drive_uuid, tail)
    except ValueError:
        return "invalid tail"
    if target is None:
        return "drive not attached"
    if os.path.normcase(os.path.abspath(source)) == os.path.normcase(os.path.abspath(target)):
        return "already there"
    if os.path.lexists(target):
        return "destination exists"

    drive = conn.execute("SELECT id FROM drives WHERE uuid = ?", (drive_uuid,)).fetchone()
    if drive is None:
        return "unknown drive"
    source_drive_id = _drive_holding_path(conn, source)
    old_tail = str(photo["tail"])

    os.makedirs(os.path.dirname(target), exist_ok=True)
    staging = f"{target}.moving-{uuid.uuid4().hex}"
    published = False
    try:
        shutil.copy2(source, staging)
        source_digest = backup.digest(source)
        if backup.digest(staging) != source_digest:
            return "verify failed"
        if drives.read_marker(target_root) != drive_uuid:
            return "drive changed while verifying"
        _publish_without_overwrite(staging, target)
        published = True

        conn.execute(
            "UPDATE copies SET tail = ? WHERE photo_id = ? AND tail IS NULL",
            (old_tail, int(photo_id)),
        )
        conn.execute("UPDATE images SET tail = ? WHERE id = ?", (tail, int(photo_id)))
        copies.saw(conn, photo_id, int(drive["id"]), tail=None)
        if source_drive_id is not None and source_drive_id != int(drive["id"]):
            copies.forget(conn, photo_id, source_drive_id)
        conn.commit()
    except Exception as error:
        conn.rollback()
        if published and os.path.exists(target):
            try:
                os.remove(target)
            except OSError:
                pass
        return f"move failed: {error}"
    finally:
        if os.path.exists(staging):
            try:
                os.remove(staging)
            except OSError:
                pass

    try:
        if backup.digest(source) != backup.digest(target):
            return "moved; source changed after verification"
        os.remove(source)
    except OSError as error:
        return f"moved; source cleanup failed: {error}"
    return "moved"


def _publish_without_overwrite(staging: str, target: str) -> None:
    """Give verified bytes their final name while refusing a collision.

    A hardlink is the clean atomic operation on NTFS and ordinary Linux
    filesystems. FAT-family camera media cannot hardlink, so the fallback
    atomically claims the unused name with an exclusive create and replaces
    only that placeholder. It can expose an empty claim after a machine crash,
    but it can never overwrite somebody else's photograph.
    """

    try:
        os.link(staging, target)
    except OSError:
        descriptor = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(descriptor)
        try:
            os.replace(staging, target)
        except BaseException:
            try:
                os.remove(target)
            except OSError:
                pass
            raise
    else:
        os.remove(staging)


def _drive_holding_path(conn, path: str) -> int | None:
    for drive in conn.execute("SELECT id, uuid FROM drives ORDER BY id"):
        root = drives.root_of(conn, drive["uuid"])
        if root and drives.tail_for(root, path):
            return int(drive["id"])
    return None


def group(conn, photo_id: int) -> list[int]:
    """The original and every version descended from it, once each."""

    root = int(photo_id)
    seen: set[int] = set()
    while root not in seen:
        seen.add(root)
        row = conn.execute("SELECT version_of FROM images WHERE id = ?", (root,)).fetchone()
        if row is None:
            return []
        if row["version_of"] is None:
            break
        root = int(row["version_of"])

    return [
        int(row["id"])
        for row in conn.execute(
            "WITH RECURSIVE family(id) AS ("
            " SELECT ? UNION SELECT i.id FROM images i JOIN family f ON i.version_of = f.id"
            ") SELECT id FROM family ORDER BY id",
            (root,),
        )
    ]
