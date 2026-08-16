"""Edit history, which is the decisions log filtered to one photograph.

`develop_history` was a table because history looked like a feature. It is not:
every row in it was already a decision the owner made, recorded a second time
in a second place with its own id space, its own retention rule and its own
capped queries. Two copies of the same fact is one copy and one liability.

So this is an adapter, not a store — four short functions over
`decisions.history`, and the table stops existing.

**Snapshots are the same rows with a name.** The old schema distinguished them
by `label LIKE 'Snapshot:%'` and capped the two lanes separately, which is why
a `WITH pinned AS (...) UNION ALL` was needed to read a photograph's history at
all. A named edit is still an edit; naming it only means you want to find it
again.

**Keyed on the hash, like every other decision.** So an edit history survives
the row being rebuilt, renumbered, or the photograph moving to another drive —
which the old `image_id` foreign key did not.
"""

from __future__ import annotations

from model import decisions

# What the two lanes were capped at. Kept because a history panel wants a
# bounded read, not because the log needs pruning: the log is the record and
# nothing is ever removed from it.
EDIT_LIMIT = 40
SNAPSHOT_LIMIT = 40

SNAPSHOT_PREFIX = "Snapshot:"


def _hash_of(conn, image_id: int) -> str | None:
    row = conn.execute(
        "SELECT content_hash FROM images WHERE id = ?", (int(image_id),)
    ).fetchone()
    return row["content_hash"] if row else None


def record(conn, image_id: int, settings, label: str = "") -> bool:
    """Note an edit. Returns False if the photograph has no identity yet.

    Nothing is deleted to make room. The old table pruned to a cap on every
    insert, which is how an undo stack quietly loses its oldest step; here the
    cap belongs to the *read*.
    """

    subject = _hash_of(conn, image_id)
    if not subject:
        return False
    decisions.decide(conn, subject, decisions.DEVELOP,
                     {"settings": settings, "label": label or ""})
    conn.commit()
    return True


def entries(conn, image_id: int) -> list[dict]:
    """One photograph's history, newest first, both lanes capped as before."""

    subject = _hash_of(conn, image_id)
    if not subject:
        return []

    edits, snapshots = [], []
    for row in decisions.history(conn, subject, family=decisions.DEVELOP,
                                 limit=EDIT_LIMIT + SNAPSHOT_LIMIT):
        value = row["value"] if isinstance(row["value"], dict) else {"settings": row["value"]}
        entry = {
            "id": row["id"],
            "settings": value.get("settings"),
            "label": value.get("label") or "",
            "created_at": row["at"],
        }
        lane = snapshots if entry["label"].startswith(SNAPSHOT_PREFIX) else edits
        if len(lane) < (SNAPSHOT_LIMIT if lane is snapshots else EDIT_LIMIT):
            lane.append(entry)
    return snapshots + edits


def snapshots(conn, image_id: int) -> list[dict]:
    """The named ones. A snapshot is an edit you asked to be able to find."""

    return [e for e in entries(conn, image_id) if e["label"].startswith(SNAPSHOT_PREFIX)]


def first_labelled(conn, image_id: int, label: str):
    """The oldest entry with this label — how an XMP import finds its baseline."""

    found = [e for e in entries(conn, image_id) if e["label"] == label]
    return found[-1]["settings"] if found else None


# ---------------------------------------------------------------------------
# The async half. Every caller is inside an open aiosqlite transaction, so
# these take that connection rather than opening one: a second connection
# would not see the uncommitted settings row it is recording history for, and
# on Windows would sit behind its write lock.


async def record_async(conn, image_id: int, settings_json: str, label: str = "") -> bool:
    """Note an edit on the caller's own connection and transaction.

    `at` is the moment of the decision, taken here. The old table stored a text
    timestamp its callers formatted; converting those at seventeen call sites
    would be seventeen chances to disagree about a format, and the honest value
    is simply now.
    """

    import json
    import time

    cursor = await conn.execute("SELECT content_hash FROM images WHERE id = ?", (int(image_id),))
    row = await cursor.fetchone()
    if not row or not row["content_hash"]:
        return False
    try:
        settings = json.loads(settings_json) if isinstance(settings_json, str) else settings_json
    except (TypeError, ValueError):
        settings = settings_json
    await conn.execute(
        "INSERT INTO decisions(subject, family, value, at) VALUES (?, ?, ?, ?)",
        (row["content_hash"], "develop",
         json.dumps({"settings": settings, "label": label or ""}), time.time()),
    )
    return True


async def entries_async(conn, image_id: int) -> list[dict]:
    """One photograph's history, newest first, snapshots pinned above edits."""

    import json

    cursor = await conn.execute("SELECT content_hash FROM images WHERE id = ?", (int(image_id),))
    row = await cursor.fetchone()
    if not row or not row["content_hash"]:
        return []

    cursor = await conn.execute(
        "SELECT id, value, at FROM decisions WHERE subject = ? AND family = 'develop' "
        "ORDER BY at DESC, id DESC LIMIT ?",
        (row["content_hash"], EDIT_LIMIT + SNAPSHOT_LIMIT),
    )
    edits, snaps = [], []
    for record in await cursor.fetchall():
        try:
            value = json.loads(record["value"]) or {}
        except (TypeError, ValueError):
            value = {}
        if not isinstance(value, dict):
            value = {"settings": value}
        entry = {
            "id": record["id"],
            "settings": value.get("settings"),
            "label": value.get("label") or "",
            "created_at": record["at"],
        }
        lane = snaps if entry["label"].startswith(SNAPSHOT_PREFIX) else edits
        if len(lane) < (SNAPSHOT_LIMIT if lane is snaps else EDIT_LIMIT):
            lane.append(entry)
    return snaps + edits


async def forget_snapshot(conn, image_id: int, entry_id: int) -> bool:
    """Drop a named snapshot.

    The one place the log is written to rather than appended, and it is
    deliberate: a snapshot is a bookmark the owner placed, so removing it is
    removing a bookmark rather than rewriting what happened. The edit it named
    stays in the history either way.
    """

    cursor = await conn.execute("SELECT content_hash FROM images WHERE id = ?", (int(image_id),))
    row = await cursor.fetchone()
    if not row or not row["content_hash"]:
        return False
    await conn.execute(
        "DELETE FROM decisions WHERE id = ? AND subject = ? AND family = 'develop' "
        "AND value LIKE '%\"label\": \"Snapshot:%'",
        (int(entry_id), row["content_hash"]),
    )
    return True
