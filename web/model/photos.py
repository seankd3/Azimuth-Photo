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

* a path must be a real file and, when the catalog knows its size, it must
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

READ_CHUNK_BYTES = 1024 * 1024
HASH_DIGEST_BYTES = 32
SUPPORTED_EXTENSIONS = frozenset({
    ".arw", ".cr2", ".cr3", ".dng", ".heic", ".heif", ".jpeg", ".jpg",
    ".nef", ".orf", ".png", ".raf", ".rw2", ".tif", ".tiff", ".webp",
})


def supported(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in SUPPORTED_EXTENSIONS


def admit(conn, path: str, tail: str) -> int | None:
    """Add one real photograph to the catalog without reading its pixels yet."""

    try:
        entry = os.lstat(path)
        tail = drives.safe_tail(tail)
    except (OSError, ValueError):
        return None
    if not _stat.S_ISREG(entry.st_mode) or entry.st_size <= 0 or not supported(path):
        return None
    cursor = conn.execute(
        "INSERT INTO images(filename, tail, file_size, file_modified_ns, file_ext) "
        "VALUES (?, ?, ?, ?, ?)",
        (
            os.path.basename(path),
            tail,
            int(entry.st_size),
            int(entry.st_mtime_ns),
            os.path.splitext(path)[1].lower(),
        ),
    )
    return int(cursor.lastrowid)


def content_hash(path: str) -> str:
    """Stable identity derived from every byte, or a refusal if bytes change."""

    digest = hashlib.blake2b(digest_size=HASH_DIGEST_BYTES)
    with open(path, "rb") as handle:
        before = os.fstat(handle.fileno())
        while chunk := handle.read(READ_CHUNK_BYTES):
            digest.update(chunk)
        after = os.fstat(handle.fileno())
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise OSError(f"file changed while hashing: {path}")
    return digest.hexdigest()


def copy_verified(source: str, staging: str) -> str | None:
    """Copy `source` to `staging`, hashing the bytes as they pass, then hash
    the copy: the source read once, the copy once, and one digest that both
    agree on -- or None when they do not, or the source changed underneath.

    An import and a backup both copy and verify; this is the one shape of
    it. Before, a copy was made and then both files were read again to
    compare them, and the import hashed the copy a third time to record it.
    """

    digest = hashlib.blake2b(digest_size=HASH_DIGEST_BYTES)
    with open(source, "rb") as reading, open(staging, "wb") as writing:
        before = os.fstat(reading.fileno())
        while chunk := reading.read(READ_CHUNK_BYTES):
            digest.update(chunk)
            writing.write(chunk)
        after = os.fstat(reading.fileno())
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        return None
    shutil.copystat(source, staging)
    made = digest.hexdigest()
    return made if content_hash(staging) == made else None


def same_bytes(left: str, right: str) -> bool:
    """Whether two files contain exactly the same bytes.

    Destructive work asks this instead of trusting any digest, cheap or full.
    """

    with open(left, "rb") as first, open(right, "rb") as second:
        before_first = os.fstat(first.fileno())
        before_second = os.fstat(second.fileno())
        if before_first.st_size != before_second.st_size:
            return False
        while True:
            a = first.read(READ_CHUNK_BYTES)
            b = second.read(READ_CHUNK_BYTES)
            if a != b:
                return False
            if not a:
                after_first = os.fstat(first.fileno())
                after_second = os.fstat(second.fileno())
                return _fingerprint(before_first) == _fingerprint(after_first) and (
                    _fingerprint(before_second) == _fingerprint(after_second)
                )


def _fingerprint(entry) -> tuple[int, int, int]:
    return entry.st_size, entry.st_mtime_ns, entry.st_ctime_ns


def identify(conn, path: str, digest: str | None = None) -> dict:
    """What photo is this, and do we already know it?

    Returns the full-content hash and size always, and an `id` when a catalogued
    photo shares that identity. Matching bytes may add a copy; rows are never
    silently merged because each may already carry irreplaceable decisions.
    `digest` is the hash when the caller has just made the file and knows it.
    """

    digest = digest or content_hash(path)
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
        source_entry = os.lstat(source)
        if not _stat.S_ISREG(source_entry.st_mode) or source_entry.st_size <= 0:
            return {"outcome": "source unreadable"}
        target = drives.path_for(conn, drive_uuid, tail)
    except OSError:
        return {"outcome": "source unreadable"}
    except ValueError:
        return {"outcome": "invalid tail"}
    if target is None:
        return {"outcome": "drive not attached"}
    if not supported(target):
        return {"outcome": "unsupported photo"}

    if os.path.lexists(target):
        if is_file(target, source_entry.st_size) and same_bytes(target, source):
            return {
                "outcome": "already there",
                "path": target,
                **_record(conn, target, drive_uuid, tail),
            }
        return {"outcome": "different file at that tail", "path": target}

    os.makedirs(os.path.dirname(target), exist_ok=True)
    staging = f"{target}.importing-{uuid.uuid4().hex}"
    try:
        made = copy_verified(source, staging)
        if made is None:
            return {"outcome": "verify failed"}
        publish_without_overwrite(staging, target)
    except OSError as error:
        return {"outcome": f"copy failed: {error}"}
    finally:
        if os.path.exists(staging):
            try:
                os.remove(staging)
            except OSError:
                pass

    return {
        "outcome": "written",
        "path": target,
        **_record(conn, target, drive_uuid, tail, digest=made),
    }


def _record(conn, path: str, drive_uuid: str, tail: str, digest: str | None = None) -> dict:
    """Make a verified file a photo and a copy fact in one transaction."""

    from model import copies

    known = identify(conn, path, digest=digest)
    photo_id = known["id"]
    if photo_id is None:
        photo_id = admit(conn, path, tail)
        if photo_id is None:
            raise ValueError(f"not an admissible photo: {path}")
        conn.execute(
            "UPDATE images SET content_hash = ? WHERE id = ?",
            (known["hash"], photo_id),
        )
        copy_tail = None
    else:
        row = conn.execute("SELECT tail FROM images WHERE id = ?", (photo_id,)).fetchone()
        if row["tail"] != tail and not copies.drives_holding(conn, photo_id):
            # Nothing held this photograph any more -- it was missing -- so the
            # address it is brought back to becomes its address.
            conn.execute("UPDATE images SET tail = ? WHERE id = ?", (tail, photo_id))
            copy_tail = None
        else:
            copy_tail = None if row["tail"] == tail else tail

    drive = conn.execute("SELECT id FROM drives WHERE uuid = ?", (drive_uuid,)).fetchone()
    if drive is None:
        raise ValueError(f"unknown drive: {drive_uuid}")
    copies.saw(conn, photo_id, int(drive["id"]), tail=copy_tail)
    conn.commit()
    return {"hash": known["hash"], "size": known["size"], "id": photo_id}


def is_file(
    path: str,
    expected_size: int | None,
    expected_modified_ns: int | None = None,
) -> bool:
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
    if expected_size is not None and entry.st_size != int(expected_size):
        return False
    return expected_modified_ns is None or entry.st_mtime_ns == int(expected_modified_ns)


def _by_preference(conn) -> list[dict]:
    """Drives worth trying, working disks before record drives.

    Reads prefer the working disk for the same reason reclaim only ever deletes
    from it: it is the copy that is meant to be cheap to lose.
    """

    return [dict(row) for row in conn.execute(
        "SELECT * FROM drives ORDER BY is_record ASC, id ASC"
    )]


def locate(
    conn,
    tail: str,
    *,
    expected_size: int | None = None,
    expected_modified_ns: int | None = None,
    roots: dict[int, str] | None = None,
) -> str | None:
    """A path this machine can open for `tail`, or None if no drive has it.

    `roots` is the attached list already looked at (drive id to root); with
    it no marker is read here. Without it every drive's marker is."""

    if not tail:
        return None
    try:
        tail = drives.safe_tail(tail)
    except ValueError:
        return None
    for drive in _by_preference(conn):
        if roots is None:
            path = drives.path_for(conn, drive["uuid"], tail)
        else:
            root = roots.get(int(drive["id"]))
            path = os.path.join(root, tail.replace("/", os.sep)) if root else None
        if path and is_file(path, expected_size, expected_modified_ns):
            return path
    return None


def open_photo(conn, image_id: int) -> str | None:
    """The path of a catalogued photo, wherever it currently lives."""

    row = conn.execute(
        "SELECT tail, file_size, file_modified_ns FROM images WHERE id = ?", (int(image_id),)
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
        if path and is_file(path, row["file_size"], row["file_modified_ns"]):
            return path
    return locate(
        conn,
        row["tail"],
        expected_size=row["file_size"],
        expected_modified_ns=row["file_modified_ns"],
    )


def state(conn, image_id: int) -> str:
    """One of: available, away, lost.

    The distinction the whole design turns on: a photo is only *lost* when
    every drive that could hold it is attached and none of them does. While any
    drive is away the answer is *away*, so an unplugged archive can never be
    mistaken for a deleted library — and no threshold, ratio or override switch
    is involved in saying so.
    """

    row = conn.execute("SELECT id FROM images WHERE id = ?", (int(image_id),)).fetchone()
    if row is None:
        return "lost"
    if open_photo(conn, image_id) is not None:
        return "available"
    for holder in conn.execute(
        "SELECT d.uuid FROM copies c JOIN drives d ON d.id = c.drive_id "
        "WHERE c.photo_id = ?",
        (int(image_id),),
    ):
        if drives.root_of(conn, holder["uuid"]) is None:
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

    from model import copies

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
        if not same_bytes(staging, source):
            return "verify failed"
        if drives.read_marker(target_root) != drive_uuid:
            return "drive changed while verifying"
        publish_without_overwrite(staging, target)
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
        if not same_bytes(source, target):
            return "moved; source changed after verification"
        os.remove(source)
    except OSError as error:
        return f"moved; source cleanup failed: {error}"
    return "moved"


def publish_without_overwrite(staging: str, target: str) -> None:
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
