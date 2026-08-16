"""What the owner judged, written to the log.

Routes hold image ids and their own tables; the log is keyed on a stable
identity. This module is that translation, and it is all that is left of a
997-line operation log.

That log existed to reconcile two devices. It carried a device id, a per-origin
sequence number, a last-write-wins comparison per family, a pending queue with
retries, and an apply pipeline that read each entry back and wrote it onto the
catalog. Measured at every one of its fourteen call sites, that last step was
redundant — the caller had already written its own table and committed before
recording anything, so the log's "apply" looked the photograph back up by
content hash and set the column a second time.

`features/trash/service.py` carried the epitaph: *"a satellite's trash was
reverted by the hub's copy of the row on the next mirror refresh"*. That is a
true description of a real bug, in a hub that no longer exists. When the thing
a mechanism defends against is deleted, the mechanism is not simplified — it is
removed.

**A collection is decided about by its uuid.** The old log had to name some
photograph, so it invented an all-zeros content hash and used it as the subject
for every collection. `decisions` takes any stable identity, which is exactly
what makes that fake disappear.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Sequence

from data import connection
from model import decisions

# Families, matching what the log already holds after the 08-04 adoption:
# develop 79,512 · star 6,241 · keyword 222 · flag 161 · collection_meta 5.
FLAG = "flag"
IPTC = "iptc"
KEYWORD = "keyword"
COLLECTION = "collection_meta"
MEMBER = "collection_member"


def _hashes(conn, image_ids: Sequence[int]) -> list[str]:
    """The content hashes of these photographs, in id order.

    A photograph with no hash yet is skipped rather than recorded against an
    empty subject: the log is keyed on identity, and "" is not one.
    """

    ids = sorted({int(value) for value in image_ids if int(value) > 0})
    if not ids:
        return []
    holes = ",".join("?" for _ in ids)
    return [
        str(row["content_hash"])
        for row in conn.execute(
            f"SELECT id, content_hash FROM images"
            f" WHERE id IN ({holes}) AND content_hash IS NOT NULL ORDER BY id",
            ids,
        )
    ]


async def _off_loop(db_path: str, job) -> Any:
    """Run a write off the event loop, on a connection opened where it is used.

    Same rule as `api._writing`: a sqlite3 connection belongs to the thread that
    opened it, and a judgement must never make the interface wait.
    """

    def run():
        conn = connection.open_sync(db_path)
        try:
            answer = job(conn)
            conn.commit()
            return answer
        finally:
            connection.close_sync(conn, db_path=db_path)

    return await asyncio.to_thread(run)


async def _about(db_path: str, image_ids: Sequence[int], family: str, value: Any) -> int:
    """Record the same judgement about each of several photographs."""

    def job(conn) -> int:
        found = _hashes(conn, image_ids)
        for digest in found:
            decisions.decide(conn, digest, family, value)
        return len(found)

    return await _off_loop(db_path, job)


async def flag(db_path: str, image_ids: Sequence[int], value: str) -> int:
    """picked / unflagged / rejected."""

    return await _about(db_path, image_ids, FLAG, str(value))


async def status(db_path: str, image_ids: Sequence[int], value: str) -> int:
    """kept / maybe / trashed. Never a file change — only what you decided."""

    return await _about(db_path, image_ids, decisions.STATUS, str(value))


async def develop(db_path: str, image_id: int) -> int:
    """The edit as it now stands, read back from where the route just wrote it.

    Recorded whole rather than as a delta. `history()` is this family filtered
    to one subject, and a delta would make reading it a replay.
    """

    def job(conn) -> int:
        row = conn.execute(
            "SELECT i.content_hash AS hash, d.settings, d.origin FROM images i"
            " JOIN develop_settings d ON d.image_id = i.id WHERE i.id = ?",
            (int(image_id),),
        ).fetchone()
        if not row or not row["hash"]:
            return 0
        try:
            settings = json.loads(row["settings"] or "{}")
        except (TypeError, ValueError):
            return 0
        decisions.decide(conn, str(row["hash"]), decisions.DEVELOP,
                         {"settings": settings, "origin": str(row["origin"] or "user")})
        return 1

    return await _off_loop(db_path, job)


async def iptc(db_path: str, image_id: int) -> int:
    """Title, caption, copyright, creator."""

    def job(conn) -> int:
        row = conn.execute(
            "SELECT i.content_hash AS hash, p.title, p.caption, p.copyright, p.creator"
            " FROM images i JOIN iptc_fields p ON p.image_id = i.id WHERE i.id = ?",
            (int(image_id),),
        ).fetchone()
        if not row or not row["hash"]:
            return 0
        decisions.decide(conn, str(row["hash"]), IPTC,
                         {key: row[key] or "" for key in
                          ("title", "caption", "copyright", "creator")})
        return 1

    return await _off_loop(db_path, job)


async def keywords(db_path: str, image_ids: Sequence[int]) -> int:
    """Every keyword path now on these photographs.

    The whole set each time, not the one that changed: a set is what the owner
    means, and reconstructing it from adds and removes is a replay again.
    """

    def job(conn) -> int:
        written = 0
        for image_id in sorted({int(v) for v in image_ids if int(v) > 0}):
            row = conn.execute(
                "SELECT content_hash AS hash FROM images WHERE id = ?", (image_id,)
            ).fetchone()
            if not row or not row["hash"]:
                continue
            paths = [
                str(found["path"])
                for found in conn.execute(
                    "WITH RECURSIVE tree(id, parent_id, path) AS ("
                    " SELECT id, parent_id, name FROM keywords WHERE parent_id IS NULL"
                    " UNION ALL SELECT child.id, child.parent_id, tree.path || ' > ' || child.name"
                    " FROM keywords child JOIN tree ON child.parent_id = tree.id)"
                    " SELECT tree.path FROM image_keywords JOIN tree ON tree.id = image_keywords.keyword_id"
                    " WHERE image_keywords.image_id = ? ORDER BY tree.path COLLATE NOCASE",
                    (image_id,),
                )
            ]
            decisions.decide(conn, str(row["hash"]), KEYWORD, paths)
            written += 1
        return written

    return await _off_loop(db_path, job)


def _state(conn, collection_id: int) -> dict | None:
    """A regular collection's shareable state, or None if it is a smart one.

    A smart collection is a saved query, so it has no membership to decide
    about — its contents are recomputed, which is the whole point of it.
    """

    row = conn.execute(
        "SELECT uuid, name, query FROM collections WHERE id = ?", (int(collection_id),)
    ).fetchone()
    if row is None or row["query"] is not None or not row["uuid"]:
        return None
    parent = conn.execute(
        "SELECT parent.uuid AS uuid FROM collection_links links"
        " JOIN collections parent ON parent.id = links.parent_id"
        " WHERE links.child_id = ? ORDER BY links.added_at, links.parent_id LIMIT 1",
        (int(collection_id),),
    ).fetchone()
    return {
        "collection_uuid": str(row["uuid"]),
        "name": str(row["name"]),
        "parent_uuid": str(parent["uuid"]) if parent and parent["uuid"] else None,
    }


async def collection_state(db_path: str, collection_id: int) -> dict | None:
    """What a route needs to show about a collection. Reads only."""

    return await _off_loop(db_path, lambda conn: _state(conn, collection_id))


async def collection(db_path: str, collection_id: int) -> int:
    """This collection exists, is called this, and sits here."""

    def job(conn) -> int:
        state = _state(conn, collection_id)
        if state is None:
            return 0
        decisions.decide(conn, state["collection_uuid"], COLLECTION, state)
        return 1

    return await _off_loop(db_path, job)


async def forget_collection(db_path: str, collection_uuid: str) -> int:
    """This collection is gone.

    Takes the uuid rather than the row id, because it is said *after* the row
    has been deleted and there is nothing left to read. That is the point of a
    subject being an identity instead of a foreign key — the old log had to
    capture a payload before the delete and carry it past, and the family for
    this already existed and was not used.
    """

    if not collection_uuid:
        return 0

    def job(conn) -> int:
        decisions.decide(conn, str(collection_uuid), decisions.FORGET, True)
        return 1

    return await _off_loop(db_path, job)


async def membership(db_path: str, collection_id: int, image_ids: Sequence[int],
                     *, member: bool) -> int:
    """These photographs are in this collection, or are not.

    The subject is the *photograph*, so "which collections is this in" is the
    log filtered to one subject — the same shape as edit history, and the
    reason neither needs a table.
    """

    def job(conn) -> int:
        state = _state(conn, collection_id)
        if state is None:
            return 0
        written = 0
        for digest in _hashes(conn, image_ids):
            decisions.decide(conn, digest, MEMBER,
                             {"collection": state["collection_uuid"], "member": bool(member)})
            written += 1
        return written

    return await _off_loop(db_path, job)
