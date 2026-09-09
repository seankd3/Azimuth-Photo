"""Embedded facts used to browse a photograph.

The answer is keyed only by the photograph's content identity, so it contains
only facts embedded in those bytes. Folder names, mtimes, JSON companions and
XMP sidecars are deliberately absent: each can change while the content hash
stays the same and therefore cannot honestly share this cache key.
"""

from __future__ import annotations

import json

from model import cache, decisions, projection
from photo import tags


def make(path: str, _digest: str) -> cache.Made:
    value = json.dumps(tags.read(path), separators=(",", ":"), sort_keys=True)
    return cache.Made(value=value, bytes=len(value.encode("utf-8")))


def project(conn, photo_id: int, entry: dict) -> None:
    """Maintain the query columns derived from one ready metadata answer."""

    answer = decoded(entry)
    row = conn.execute(
        "SELECT content_hash FROM images WHERE id = ?", (int(photo_id),)
    ).fetchone()
    if row is None:
        return
    values = _values(answer, decisions.latest(conn, row["content_hash"], decisions.DATE))
    conn.execute(
        "UPDATE images SET date_taken = ?, camera_make = ?, camera_model = ?, "
        "lens = ?, width = ?, height = ? WHERE content_hash = ?",
        (*values, row["content_hash"]),
    )


def reindex(conn) -> dict[str, int]:
    """Rebuild query columns from ready metadata rows after opening a catalog.

    The chosen dates are read once for the whole pass: asking the log per
    photograph was 150,000 queries, and the whole of a sixteen-second start.
    """

    chosen = decisions.current(conn, decisions.DATE)
    intended = {}
    discarded = 0
    for row in conn.execute(
        "SELECT c.hash, c.value, c.state FROM cache c"
        " WHERE c.kind = ? AND c.recipe = '{}' AND c.state = ?"
        " AND EXISTS (SELECT 1 FROM images i WHERE i.content_hash = c.hash)",
        (KIND.name, cache.READY),
    ).fetchall():
        entry = {"state": row["state"], "value": row["value"]}
        try:
            answer = decoded(entry)
        except ValueError:
            # A cache row that cannot be read is dropped and re-owed; a date
            # decision that cannot be read is a different fault and raises.
            conn.execute(
                "DELETE FROM cache WHERE hash = ? AND kind = ? AND recipe = '{}'",
                (row["hash"], KIND.name),
            )
            discarded += 1
            continue
        intended[row["hash"]] = _values(answer, chosen.get(row["hash"]))
    projected = projection.project(
        conn, "content_hash",
        ("date_taken", "camera_make", "camera_model", "lens", "width", "height"), intended)
    return {"projected": projected, "discarded": discarded}


def _values(answer: dict, chosen_date) -> tuple:
    date_taken = answer.get("date_taken")
    if chosen_date is not None:
        date_taken = tags.normalize_date(chosen_date)
        if date_taken is None:
            raise ValueError(f"invalid date decision: {chosen_date!r}")
    return (
        date_taken,
        answer.get("camera_make"),
        answer.get("camera_model"),
        answer.get("lens"),
        answer["width"],
        answer["height"],
    )


def decoded(entry: dict) -> dict:
    if not entry or entry.get("state") != cache.READY:
        raise ValueError("metadata projection requires a ready cache entry")
    try:
        answer = json.loads(entry["value"])
    except (KeyError, TypeError, json.JSONDecodeError) as error:
        raise ValueError("invalid cached metadata") from error
    if not isinstance(answer, dict) or not all(
        isinstance(answer.get(name), int) and answer[name] > 0
        for name in ("width", "height")
    ):
        raise ValueError("cached metadata has no valid dimensions")
    return answer


KIND = cache.Kind(
    name="metadata",
    compute=make,
    cost=0.02,
    project=project,
)
