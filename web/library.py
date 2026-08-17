"""The queries the grid reads.

Three ideas, and each one deletes a subsystem.

**A photo is in the library. Full stop.** The old visibility rule was a
conjunction of five things — not missing, source not excluded, not remote, not
trashed, owns a file — copied into two hundred queries, and any one of them
disagreeing hid photographs at random. Here the only thing that removes a
photograph from view is the owner throwing it away. *Where its bytes are does
not enter into it*, which is the entire reason unplugging the archive cannot
empty the library: `away` is something a tile says, never something a query
filters on.

**A folder is a prefix of a tail.** No folder table, no scan state, no
`folder_scan_state` with 39 rows nobody could explain. Folder browsing is
`substr(tail, 1, ?) = ?` — and because tails are stored rather than absolute
paths, the same query answers for photos on the working disk and on the
archive, together, without knowing either exists.

**Decision columns are an index, not the truth.** `status` and `stars` live on
`images` because a grid query cannot join 88,379 log rows per paint. They are
rebuilt from the log by `reindex()`, so a wrong write is repairable rather than
fatal — which is what makes it safe for the log to be the only thing backed up.

The SQL-shaped lessons in here are the expensive ones. `GLOB` never `LIKE`,
because `LIKE` is ASCII-case-insensitive and will match a folder you did not
mean. `substr()` for prefix comparisons, so a `[` in a folder name cannot turn
into a character class. And the visibility predicate's *emitted text* is
load-bearing: SQLite only applies a partial index when the query's `WHERE`
implies the index's own, so rewording this string drops ranking onto a table
scan of 157,064 rows.
"""

from __future__ import annotations

from model import decisions
from model.scope import EVERYTHING, Scope, where

# The one predicate. Spelled once, imported everywhere, never inlined.
#
# Two clauses, and the second was missing until an adversarial read caught it.
# `catalog_sources` row 4 (`C:\Pictures`) carries `included = 0, removed_at =
# 1785542505` — the owner deliberately removed that source and kept its rows —
# and 10,689 photographs came back into the grid the moment the source join
# went away. They are all duplicates (measured: zero unique hashes, every one
# also on a tailed row), so showing them is doubly wrong.
#
# The fix is not to restore the source join. **A photograph with no tail is not
# in the library yet**, because a tail is how anything is ever opened, and all
# 12,793 tail-less rows are either those duplicates or rows awaiting adoption.
# One clause, no join, and it says something true about the core rather than
# about a table that is going away.
IN_LIBRARY = "i.status != 'trashed' AND i.tail IS NOT NULL"

# Sorts the grid may ask for. A sort not in here is refused -- never silently
# swapped for another, which is how "sort by date" quietly became "sort by Elo"
# and nobody could tell because both produce a plausible order.
SORTS = {
    "newest": "i.date_taken DESC, i.id DESC",
    "oldest": "i.date_taken ASC, i.id ASC",
    "best": "i.elo DESC, i.id DESC",
    "stars": "i.stars DESC, i.date_taken DESC",
    "folder": "i.tail ASC",
    # The grid has offered these two since it was written and neither had an
    # entry, so both fell through the caller's `.get(..., "newest")` and served
    # date order under a control reading "Camera" or "Size". 121,755 rows carry
    # a camera and every row carries a size, so both are real orders; what was
    # missing was the row in this table.
    "camera": "i.camera_model IS NULL, i.camera_model ASC, i.date_taken DESC",
    "file_size": "i.file_size DESC, i.id DESC",
    # "filename" was mapped to `folder`, which sorts by the whole path -- so a
    # library organised by date sorted by date under a control reading
    # "Filename". A filename sort sorts by filename.
    "filename": "i.filename ASC, i.id ASC",
    # Recently added *is* id order -- ids are handed out in insertion order --
    # so this sort needs neither a column nor an index. Reading `created_at`
    # instead cost 233 ms against 0.2 ms, for the same answer.
    "added": "i.id DESC",
}


def photos(conn, *, scope: Scope = EVERYTHING, sort: str = "newest",
           limit: int = 200, offset: int = 0) -> list[dict]:
    """One page of the grid.

    Deliberately has no `include_missing`, no `source`, no `online_only`. Those
    arguments existed because the query had to know where the bytes were; it
    does not, and a photograph on an unplugged drive is listed exactly like any
    other. What it looks like when painted is `render`'s problem and `state()`'s
    answer.

    `folder` and `starred` were the next two of those arguments and they are
    gone the same way: **which photographs** is one argument, so a collection, a
    folder, a rating and anything later are the same kind of thing and compose
    without this function learning about any of them.
    """

    if sort not in SORTS:
        raise ValueError(f"no such sort: {sort!r}; have {sorted(SORTS)}")

    clause, args = where(scope)
    return [dict(row) for row in conn.execute(
        f"""
        SELECT i.id, i.tail, i.date_taken, i.stars, i.elo, i.content_hash AS hash,
               i.width, i.height, i.file_size
        FROM images i
        WHERE {IN_LIBRARY} AND ({clause})
        ORDER BY {SORTS[sort]}
        LIMIT ? OFFSET ?
        """,
        (*args, int(limit), int(offset)),
    )]


def folders(conn) -> list[dict]:
    """Every folder that holds photographs, with its count.

    Derived from the tails themselves, so it cannot disagree with what is in
    the library and there is nothing to keep up to date. A folder renamed in
    Explorer shows up here after the next sweep, because the tails changed and
    a tail is all a folder ever was.

    Costs ~470 ms over 157,064 rows and cannot use an index, because the folder
    is an expression over the tail rather than a stored value. That is fine and
    deliberately not optimised: the folder tree is not on the first-paint path,
    so it is computed after the grid is up. Storing it would buy 470 ms once
    and cost a table that can disagree with the tails forever.
    """

    return [
        {"folder": row["folder"], "photos": row["photos"]}
        for row in conn.execute(
            f"""
            SELECT rtrim(i.tail, replace(i.tail, '/', '')) AS folder, COUNT(*) AS photos
            FROM images i
            WHERE {IN_LIBRARY} AND i.tail IS NOT NULL AND i.tail GLOB '*/*'
            GROUP BY folder
            ORDER BY folder
            """
        )
    ]


def counts(conn) -> dict:
    """What the status line says.

    Three plain queries rather than one clever one, and that is not a
    concession — it is faster. Measured on 157,064 rows: three counts that each
    read an index take **8.3 ms**, while the single pass computing all three
    with `SUM(...)` expressions takes **188 ms**, because an aggregate over a
    condition SQLite cannot see through forces a scan of the table.
    """

    ask = lambda sql: int(conn.execute(sql).fetchone()[0] or 0)  # noqa: E731
    return {
        "photos": ask(f"SELECT COUNT(*) FROM images i WHERE {IN_LIBRARY}"),
        "starred": ask(f"SELECT COUNT(*) FROM images i WHERE {IN_LIBRARY} AND i.stars > 0"),
        "unidentified": ask("SELECT COUNT(*) FROM images WHERE content_hash IS NULL"),
    }


_MONTHS = ("January", "February", "March", "April", "May", "June",
           "July", "August", "September", "October", "November", "December")


def months(conn, *, cover: bool = False) -> list[dict]:
    """Every month that holds photographs, newest first, with a count.

    `date_taken` is stored as `YYYY-MM-DD HH:MM:SS`, so the month is
    `substr(date_taken, 1, 7)` — a prefix of a text column, which SQLite can
    group without a function over every row. This is the one shape behind both
    the date histogram and the date-group headers; they were two queries in two
    modules returning the same numbers under different key names.
    """

    picked = ", MIN(i.id) AS cover_id" if cover else ""
    return [
        {"month": row["month"], "count": row["count"],
         **({"cover_id": row["cover_id"]} if cover else {})}
        for row in conn.execute(
            f"""
            SELECT substr(i.date_taken, 1, 7) AS month, COUNT(*) AS count{picked}
            FROM images i
            WHERE {IN_LIBRARY} AND i.date_taken IS NOT NULL AND i.date_taken != ''
            GROUP BY month ORDER BY month DESC
            """
        )
    ]


def month_label(month: str) -> str:
    """`2026-08` as `August 2026`. The only place a date is spelled for a human."""

    try:
        year, number = month.split("-")
        return f"{_MONTHS[int(number) - 1]} {year}"
    except (ValueError, IndexError):
        return month


def facets(conn) -> dict:
    """What you can filter by, and how many of each there are.

    One pass per facet, each grouping a column. They are separate queries for
    the same reason the counts are: a single pass computing all of them with
    conditional aggregates cannot use an index and reads every row.
    """

    def tally(column: str, key: str) -> list[dict]:
        return [
            {key: row[0], "count": row[1]}
            for row in conn.execute(
                f"SELECT {column}, COUNT(*) FROM images i"
                f" WHERE {IN_LIBRARY} AND {column} IS NOT NULL AND {column} != ''"
                f" GROUP BY {column} ORDER BY COUNT(*) DESC"
            )
        ]

    return {
        "years": [
            {"year": row[0], "count": row[1]}
            for row in conn.execute(
                f"SELECT substr(i.date_taken, 1, 4) AS y, COUNT(*) FROM images i"
                f" WHERE {IN_LIBRARY} AND i.date_taken IS NOT NULL AND i.date_taken != ''"
                f" GROUP BY y ORDER BY y DESC"
            )
        ],
        "file_types": [
            {"ext": str(row[0] or "").lstrip(".").lower(), "count": row[1]}
            for row in conn.execute(
                f"SELECT i.file_ext, COUNT(*) FROM images i"
                f" WHERE {IN_LIBRARY} AND i.file_ext IS NOT NULL AND i.file_ext != ''"
                f" GROUP BY lower(i.file_ext) ORDER BY COUNT(*) DESC"
            )
        ],
        "cameras": [
            {"camera": row[0], "count": row[1]}
            for row in conn.execute(
                f"SELECT TRIM(COALESCE(i.camera_make,'') || ' ' || COALESCE(i.camera_model,'')) AS c,"
                f" COUNT(*) FROM images i WHERE {IN_LIBRARY} AND c != ''"
                f" GROUP BY c ORDER BY COUNT(*) DESC"
            )
        ],
        "lenses": tally("i.lens", "lens"),
        "undated": int(conn.execute(
            f"SELECT COUNT(*) FROM images i WHERE {IN_LIBRARY}"
            f" AND (i.date_taken IS NULL OR i.date_taken = '')"
        ).fetchone()[0]),
        "people": [],
    }


def reindex(conn) -> dict[str, int]:
    """Rebuild the decision columns from the log.

    The log is the truth; these columns are an index over it that exists purely
    so a grid paint is one query. Running this can only ever restore what the
    owner said — which is why a bad write to `images` is an inconvenience here
    and used to be a loss.

    **A decision belongs to the photograph, not to the row.** Subjects are
    content hashes, so where two rows carry the same hash they share every
    decision, and this writes the log's latest answer to both. That is the
    correct reading — the same bytes are the same photograph — but it means a
    duplicate row is not harmless: measured on the live catalog, 2 status and
    38 star values disagree between rows that are the same photograph. The
    repair is to stop having duplicate rows, not to key decisions on `id`.
    """

    counts: dict[str, int] = {}
    subject_id = {
        row["content_hash"]: row["id"]
        for row in conn.execute("SELECT id, content_hash FROM images WHERE content_hash IS NOT NULL")
    }

    for family, column, default in (
        (decisions.STATUS, "status", "kept"),
        (decisions.STAR, "stars", 0),
    ):
        latest = decisions.current(conn, family)
        applied = 0
        for subject, value in latest.items():
            image_id = subject_id.get(subject)
            if image_id is None:
                continue
            conn.execute(f"UPDATE images SET {column} = ? WHERE id = ?", (value or default, image_id))
            applied += 1
        counts[family] = applied

    conn.commit()
    return counts


def rerank(conn, subjects=None, vectors=None) -> int:
    """Recompute the ranking from the log and write it into the sort index.

    Ranking is a derivation, so this is the only place it is stored, and it is
    stored only so that `ORDER BY elo` is an index read. Losing this column
    costs one recomputation; losing the log would cost the judgements.

    **It refuses to run without vectors, and that refusal is the point.**
    Ranking without propagation is not a smaller version of the ranking, it is
    a different and much worse one: 2,532 comparisons cover 665 photographs,
    and propagation is what carries them to the other 156,000. Writing plain
    Elo over the index would quietly replace a whole-library order with an
    order over 0.4% of it, and nothing would look broken — the grid would just
    be wrong in a way no error message could describe.

    So: pass the embedding space, or do not rerank. Reading with a stale
    ranking is strictly better than writing a lesser one.
    """

    import rank

    if vectors is None or not subjects:
        raise ValueError(
            "rerank needs the embedding space; plain Elo covers 665 of 157,064 photos"
        )

    scores = rank.ranking(conn, subjects, vectors)
    if not scores:
        return 0
    conn.executemany(
        "UPDATE images SET elo = ? WHERE content_hash = ?",
        [(score, subject) for subject, score in scores.items()],
    )
    conn.commit()
    return len(scores)


_DATE_PRECISIONS = ("%Y", "%Y-%m", "%Y-%m-%d")


def date_range(date_taken: str) -> tuple[str, str] | None:
    """The half-open range a date prefix covers, or None if it is not a date.

    A `>= / <` range rather than a prefix match, so the `date_taken` index still
    applies — `LIKE '2026-08%'` cannot use it.

    The day form was missing once, and it mattered more than it looks: clicking
    a day in the timeline sends `YYYY-MM-DD`, this returned None, the caller
    then added no date condition at all, and the grid showed the whole library
    while the chip read the date the owner had clicked. A filter that cannot be
    read must match nothing, never everything.
    """

    import datetime as _dt

    value = (date_taken or "").strip()
    for precision in _DATE_PRECISIONS:
        try:
            start = _dt.datetime.strptime(value, precision)
        except ValueError:
            continue
        if precision == "%Y":
            end = start.replace(year=start.year + 1)
        elif precision == "%Y-%m":
            end = (start.replace(year=start.year + 1, month=1) if start.month == 12
                   else start.replace(month=start.month + 1))
        else:
            end = start + _dt.timedelta(days=1)
        stamp = "%Y-%m-%d %H:%M:%S"
        return start.strftime(stamp), end.strftime(stamp)
    return None


def folder_tree(conn) -> list[dict]:
    """One tree over every drive, because a folder is a prefix of a tail.

    Lightroom shows a tree per source, so the same shoot filed on two disks
    appears twice and the owner has to know which copy they are clicking. Here
    the tail *is* the folder, and the drive is a property of the photographs
    underneath it — so `Raws/Digital/2026` is one node whether it lives on the
    working disk, the archive, or both.

    Which drives hold it comes back on the node as a quiet fact rather than as
    the thing the tree is organised by. That is the whole difference, and it is
    free: the tails already say it.

    **No depth limit, because the limit was never buying anything.** Measured
    on 144,271 tails: stopping at three levels costs 964 ms and shows 222
    folders; building the whole tree costs 1,157 ms and shows 1,359. The cap
    hid the day and roll folders -- the ones actually worth browsing to -- to
    save 193 ms on a panel that loads after first paint.
    """

    counts: dict[str, int] = {}
    where: dict[str, set] = {}
    # Each drive as the three things the indicator needs: what to call it,
    # whether it is the fast disk or the archive, and whether it answered.
    from model import drives as drive_model

    drive_of = {}
    record_attached = False
    for row in conn.execute("SELECT id, uuid, label, root, is_record FROM drives"):
        online = drive_model.root_of(conn, row["uuid"]) is not None
        drive_of[int(row["id"])] = {
            "label": row["label"] or row["root"],
            "is_record": bool(row["is_record"]),
            "online": online,
        }
        if row["is_record"] and online:
            record_attached = True

    for row in conn.execute(
        f"""
        SELECT i.tail, (SELECT GROUP_CONCAT(c.drive_id) FROM copies c WHERE c.photo_id = i.id) AS drives
        FROM images i WHERE {IN_LIBRARY} AND i.tail GLOB '*/*'
        """
    ):
        parts = str(row["tail"]).split("/")[:-1]
        on = [drive_of[int(d)] for d in str(row["drives"] or "").split(",")
              if d.strip().isdigit() and int(d) in drive_of]
        # One question per photograph: is there a copy on a drive allowed to
        # hold the last one? Everything else about where it lives is trivia.
        held = "safe" if any(d["is_record"] for d in on) else ("only-here" if on else "unknown")
        for depth in range(1, len(parts) + 1):
            key = "/".join(parts[:depth])
            counts[key] = counts.get(key, 0) + 1
            where.setdefault(key, set()).add(held)

    def node(path: str) -> dict:
        children = sorted(
            k for k in counts
            if k.startswith(path + "/") and k.count("/") == path.count("/") + 1
        )
        return {
            "path": path,
            "name": path.rsplit("/", 1)[-1],
            "total_count": counts[path],
            "safety": _safety(where.get(path) or set(), record_attached),
            "reveal_available": True,
            "children": [node(child) for child in children],
        }

    return [node(k) for k in sorted(counts) if "/" not in k]


def _safety(states: set, record_attached: bool) -> str:
    """One word for a folder: is everything here backed up?

    Green when every photograph has a copy on a drive allowed to hold the last
    one. Amber when any of them exists only on the working disk -- the disk
    that is meant to be cheap to lose. Hollow when the record drive is not
    plugged in, because then the honest answer is that we cannot say, and a
    green dot we are not entitled to is worse than no dot at all.

    Only amber asks for anything. That is the point of there being one mark:
    a row you scan past unless it is telling you something.
    """

    if not record_attached:
        return "unknown"
    if "only-here" in states or "unknown" in states:
        return "at-risk"
    return "safe"
