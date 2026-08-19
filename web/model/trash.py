"""Count and permanently empty photographs rejected into Trash.

Browsing Trash is a library page (`library.trash`); what lives here is the
irreversible half, which needs copy facts and byte proof."""

from __future__ import annotations

import os
import stat

from model import copies, cull, decisions, drives, photos

TRASHED = cull.TRASHED


def count(conn) -> int:
    """Photographs currently in Trash, using the projected browse index."""

    return int(
        conn.execute(
            "SELECT COUNT(*) FROM images WHERE status = ? AND tail IS NOT NULL",
            (TRASHED,),
        ).fetchone()[0]
    )


def _file_token(path: str) -> tuple[int, int, int, int]:
    try:
        entry = os.lstat(path)
    except OSError as error:
        raise ValueError(f"file is unavailable: {path}") from error
    if not stat.S_ISREG(entry.st_mode):
        raise ValueError(f"not a regular file: {path}")
    return entry.st_dev, entry.st_ino, entry.st_size, entry.st_mtime_ns


def _empty_plans(conn) -> tuple[int, list[dict]]:
    rows = [
        dict(row)
        for row in conn.execute(
            "SELECT id, content_hash AS hash, tail FROM images "
            "WHERE status = ? AND tail IS NOT NULL ORDER BY id",
            (TRASHED,),
        )
    ]
    by_hash: dict[str, list[dict]] = {}
    for row in rows:
        if not row["hash"]:
            raise ValueError(f"trashed photo {row['id']} has no identity")
        by_hash.setdefault(str(row["hash"]), []).append(row)

    attached_drives = []
    for row in conn.execute("SELECT * FROM drives ORDER BY is_record, id"):
        drive = dict(row)
        if drives.root_of(conn, drive["uuid"]) is None:
            raise ValueError(f"drive is away: {drive['label'] or drive['uuid']}")
        attached_drives.append(drive)

    plans = []
    for digest, identity_rows in by_hash.items():
        image_ids = [int(row["id"]) for row in identity_rows]
        marks = ",".join("?" * len(image_ids))
        holders = [
            dict(row)
            for row in conn.execute(
                "SELECT c.photo_id, c.drive_id, d.uuid, d.is_record, "
                "COALESCE(c.tail, i.tail) AS tail FROM copies c "
                "JOIN drives d ON d.id = c.drive_id "
                "JOIN images i ON i.id = c.photo_id "
                f"WHERE c.photo_id IN ({marks}) ORDER BY d.is_record, d.id",
                image_ids,
            )
        ]
        paths: dict[str, dict] = {}
        for holder in holders:
            try:
                path = drives.path_for(conn, holder["uuid"], holder["tail"])
            except ValueError as error:
                raise ValueError(f"invalid copy address for photo {holder['photo_id']}") from error
            if path is None:
                raise ValueError(f"drive is away for photo {holder['photo_id']}")
            key = os.path.normcase(os.path.abspath(path))
            entry = paths.setdefault(
                key,
                {
                    "path": path,
                    "is_record": bool(holder["is_record"]),
                    "hints": [],
                },
            )
            entry["hints"].append((int(holder["photo_id"]), int(holder["drive_id"])))

        # A copy row is only a hint. Inspect the canonical address on every
        # registered drive too, or a stale/missing hint could let Empty claim
        # success while a later sweep rediscovers the bytes.
        canonical_tail = str(identity_rows[0]["tail"])
        for drive in attached_drives:
            candidate = drives.path_for(conn, drive["uuid"], canonical_tail)
            key = os.path.normcase(os.path.abspath(candidate))
            if key in paths or not os.path.lexists(candidate):
                continue
            try:
                if photos.content_hash(candidate) != digest:
                    continue
            except (OSError, ValueError):
                continue
            paths[key] = {
                "path": candidate,
                "is_record": bool(drive["is_record"]),
                "hints": [],
            }

        if not paths:
            plans.append({"hash": digest, "image_ids": image_ids, "files": []})
            continue

        files = list(paths.values())
        for file in files:
            file["token"] = _file_token(file["path"])
        anchor = max(files, key=lambda file: (file["is_record"], file["path"]))
        if photos.content_hash(anchor["path"]) != digest:
            raise ValueError(f"copy changed for photo {image_ids[0]}")
        for file in files:
            if file is not anchor and not photos.same_bytes(file["path"], anchor["path"]):
                raise ValueError(f"copies differ for photo {image_ids[0]}")
        ordered = [file for file in files if file is not anchor] + [anchor]
        plans.append({"hash": digest, "image_ids": image_ids, "files": ordered})
    return len(rows), plans


def empty(conn, *, expected_count: int, dry_run: bool = False) -> dict:
    """Permanently remove every trashed identity only after complete preflight.

    The caller must repeat the visible count, preventing a stale confirmation
    from deleting photographs that entered Trash after the prompt appeared.
    Every known drive must be present, every path must still be a regular file,
    and every copy is compared byte-for-byte. Record-drive bytes are removed
    last, so an interrupted multi-copy deletion preserves the safest copy.
    """

    expected_count = int(expected_count)
    before = count(conn)
    if expected_count != before:
        raise ValueError(
            f"Trash count changed: expected {expected_count}, found {before}"
        )
    visible_count, plans = _empty_plans(conn)
    if expected_count != visible_count:
        raise ValueError(
            f"Trash count changed: expected {expected_count}, found {visible_count}"
        )
    if dry_run:
        return {
            "count": visible_count,
            "identities": len(plans),
            "files": sum(len(plan["files"]) for plan in plans),
        }

    emptied = []
    errors = []
    for plan in plans:
        failed = None
        anchor = plan["files"][-1] if plan["files"] else None
        for file in plan["files"]:
            try:
                if _file_token(file["path"]) != file["token"]:
                    raise ValueError("file changed after Empty Trash preflight")
                if file is anchor:
                    if photos.content_hash(file["path"]) != plan["hash"]:
                        raise ValueError("last copy changed before deletion")
                elif not photos.same_bytes(file["path"], anchor["path"]):
                    raise ValueError("copies changed before deletion")
                os.remove(file["path"])
                for photo_id, drive_id in file["hints"]:
                    copies.forget(conn, photo_id, drive_id)
                conn.commit()
            except (OSError, ValueError) as error:
                conn.rollback()
                failed = str(error)
                break
        if failed is not None:
            errors.append({"ids": plan["image_ids"], "reason": failed})
            continue
        marks = ",".join("?" * len(plan["image_ids"]))
        conn.execute(f"DELETE FROM copies WHERE photo_id IN ({marks})", plan["image_ids"])
        conn.execute(f"DELETE FROM images WHERE id IN ({marks})", plan["image_ids"])
        conn.commit()
        emptied.extend(plan["image_ids"])
    return {"emptied": emptied, "errors": errors, "remaining": count(conn)}
