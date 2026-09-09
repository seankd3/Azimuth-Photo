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

**Decision columns are an index, not the truth.** `status` and `rotate` live
on `images` because a grid query cannot join 88,379 log rows per paint. They
are rebuilt from the log by `reindex()`, so a wrong write is repairable rather
than fatal — which is what makes it safe for the log to be the only thing
backed up. `elo` and `stars` are the same kind of index over the ranking,
rewritten whole by `rerank()`.

The SQL-shaped lessons in here are the expensive ones. `GLOB` never `LIKE`,
because `LIKE` is ASCII-case-insensitive and will match a folder you did not
mean. `substr()` for prefix comparisons, so a `[` in a folder name cannot turn
into a character class. And the visibility predicate's *emitted text* is
load-bearing: SQLite only applies a partial index when the query's `WHERE`
implies the index's own, so rewording this string drops ranking onto a table
scan of 157,064 rows.
"""

from __future__ import annotations

from collections.abc import Iterable

from model import cache, decisions, projection
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


Renditions = dict[str, tuple[cache.Kind, dict]]


def photos(conn, *, scope: Scope = EVERYTHING, sort: str = "newest",
           limit: int = 200, offset: int = 0, renditions: Renditions | None = None,
           reachable_on: Iterable[int] = ()) -> list[dict]:
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

    `renditions` names cached answers the row should carry -- the grid tile,
    the loupe -- as `name: (kind, recipe)`. Each becomes the answer's path when
    it is ready or NULL, plus `<name>_failed` when the answer was tried and
    could not be made, read from the cache table in the same query, so a row
    says whether its picture exists without anyone touching the disk.
    `reachable_on` is the drives that are here now; the row's `reachable` says
    whether a copy sits on one of them, which is whether a picture could be
    made at all.
    """

    if sort not in SORTS:
        raise ValueError(f"no such sort: {sort!r}; have {sorted(SORTS)}")
    clause, args = where(scope)
    return _page(conn, f"{IN_LIBRARY} AND ({clause})", args, SORTS[sort], (),
                 limit, offset, renditions, reachable_on)


def trash(conn, *, limit: int = 200, offset: int = 0,
          renditions: Renditions | None = None, reachable_on: Iterable[int] = ()) -> list[dict]:
    """One page of Trash, newest decision first.

    The same page as the grid with the complement of `IN_LIBRARY` and an order
    read from the decision log, because Trash is the library seen from the
    other side of one status decision, not a second kind of listing.
    """

    latest = (
        f"(SELECT d.at FROM decisions d WHERE d.subject = i.content_hash AND d.family = ? "
        f"ORDER BY {decisions.AUTHORITY_SQL} DESC, d.at DESC, d.id DESC LIMIT 1) DESC, i.id DESC"
    )
    return _page(conn, "i.status = 'trashed' AND i.tail IS NOT NULL", (), latest,
                 (decisions.STATUS,), limit, offset, renditions, reachable_on)


def _page(conn, condition: str, args: tuple, order: str, order_args: tuple,
          limit: int, offset: int, renditions: Renditions | None,
          reachable_on: Iterable[int] = ()) -> list[dict]:
    limit, offset = int(limit), int(offset)
    if not 1 <= limit <= 500:
        raise ValueError("a page contains between 1 and 500 photos")
    if offset < 0:
        raise ValueError("a page offset cannot be negative")

    joins, columns, bound = [], [], []
    for position, (name, (kind, recipe)) in enumerate((renditions or {}).items()):
        alias = f"r{position}"
        base = cache.canonical(kind, recipe)
        if kind.keyed is None:
            joins.append(
                f"LEFT JOIN cache {alias} ON {alias}.hash = i.content_hash AND {alias}.kind = ?"
                f" AND {alias}.recipe = ?"
            )
            bound += [kind.name, base]
        else:
            # An edited photograph's rendition is its own recipe, spelled
            # per row from the photo's develop fragment — the same splice
            # the worker's anti-join uses, so what is owed and what is
            # shown can never disagree.
            param, expression = kind.keyed
            prefix = f'{{"{param}":'
            suffix = "," + base[1:] if base != "{}" else "}"
            joins.append(
                f"LEFT JOIN cache {alias} ON {alias}.hash = i.content_hash AND {alias}.kind = ?"
                f" AND {alias}.recipe = CASE WHEN {expression} IS NULL THEN ?"
                f" ELSE ? || {expression} || ? END"
            )
            bound += [kind.name, base, prefix, suffix]
        columns.append(
            f", CASE WHEN {alias}.state = 'ready' THEN {alias}.path END AS {name}"
            f", {alias}.state = 'failed' AS {name}_failed"
        )
    here = [int(d) for d in reachable_on]
    holes = ",".join("?" for _ in here) or "NULL"
    columns.append(
        f", EXISTS (SELECT 1 FROM copies c WHERE c.photo_id = i.id AND c.drive_id IN ({holes})) AS reachable"
        ", EXISTS (SELECT 1 FROM copies c WHERE c.photo_id = i.id) AS placed"
        # A cover says how many frames wait behind it; everyone else says 0.
        ", (SELECT COUNT(*) FROM images s WHERE s.stack_of = i.id) AS stack"
    )
    # The page's ids are walked first, on the bare table where the partial
    # indexes cover the whole skip; the joins and per-row subqueries then run
    # for exactly one page. Measured at 155k rows, offset 60k: the joined
    # walk paid ~290 ms per page, the covered walk pays single digits.
    return [dict(row) for row in conn.execute(
        f"""
        SELECT i.id, i.tail, i.date_taken, i.status, i.stars, i.rotate, i.elo,
               i.content_hash AS hash, i.width, i.height, i.file_size,
               i.develop, i.stack_of{"".join(columns)}
        FROM images i {" ".join(joins)}
        WHERE i.id IN (SELECT i.id FROM images i WHERE {condition}
                       ORDER BY {order} LIMIT ? OFFSET ?)
        ORDER BY {order}
        """,
        # Bound in SQL text order: the reachability list sits in the SELECT,
        # before the rendition joins' kinds and recipes, then the inner
        # walk's condition, order, and window, then the outer order again.
        (*here, *bound, *args, *order_args, limit, offset, *order_args),
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
            SELECT rtrim(rtrim(i.tail, replace(i.tail, '/', '')), '/') AS folder,
                   COUNT(*) AS photos
            FROM images i
            WHERE {IN_LIBRARY} AND i.tail IS NOT NULL AND i.tail GLOB '*/*'
            GROUP BY folder
            ORDER BY folder
            """
        )
    ]


def position(conn, photo_id: int, sort: str, scope: Scope = EVERYTHING) -> int | None:
    """Where one photograph sits in a sort of a scope, or None if it is not
    there -- what lets a selection survive a change of sort. One ordered
    pass over the scope, the same ORDER BY the page uses, so the two cannot
    disagree."""

    if sort not in SORTS:
        raise ValueError(f"no such sort: {sort!r}; have {sorted(SORTS)}")
    clause, args = where(scope)
    row = conn.execute(
        f"SELECT at FROM (SELECT i.id, ROW_NUMBER() OVER (ORDER BY {SORTS[sort]}) - 1 AS at"
        f" FROM images i WHERE {IN_LIBRARY} AND ({clause})) WHERE id = ?",
        (*args, int(photo_id)),
    ).fetchone()
    return None if row is None else int(row["at"])


def size(conn, scope: Scope = EVERYTHING) -> int:
    """How many photographs a scope holds -- the total a page window needs."""

    clause, args = where(scope)
    return int(conn.execute(
        f"SELECT COUNT(*) FROM images i WHERE {IN_LIBRARY} AND ({clause})", args
    ).fetchone()[0])


def counts(conn) -> dict:
    """What the status line says.

    Three plain queries rather than one clever one, and that is not a
    concession — it is faster. Measured on 157,064 rows: three counts that each
    read an index take **8.3 ms**, while the single pass computing all three
    with `SUM(...)` expressions takes **188 ms**, because an aggregate over a
    condition SQLite cannot see through forces a scan of the table.
    """

    # INDEXED BY because the planner refuses a covering partial index for a
    # bare COUNT even with fresh statistics: measured 34 ms as a table scan
    # against 9.5 ms on the index it was built for. These two queries are the
    # index's reason to exist, so naming it here is the contract, not a hint.
    ask = lambda sql: int(conn.execute(sql).fetchone()[0] or 0)  # noqa: E731
    return {
        "photos": ask(
            f"SELECT COUNT(*) FROM images i INDEXED BY idx_browse_date WHERE {IN_LIBRARY}"),
        "starred": ask(
            f"SELECT COUNT(*) FROM images i INDEXED BY idx_browse_stars"
            f" WHERE {IN_LIBRARY} AND i.stars > 0"),
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
        index = int(number) - 1
        if not 0 <= index < len(_MONTHS):
            return month
        return f"{_MONTHS[index]} {year}"
    except (ValueError, IndexError):
        return month


def days(conn, scope: Scope = EVERYTHING) -> list[dict]:
    """Every day that holds photographs in a scope, newest first, with a
    count — the grid's chapter list.

    The same prefix-group trick as `months`, over the same scope the page
    query uses, so a running sum of the counts is exactly where each day
    starts in the newest-sorted grid. Undated photographs sort after every
    date, so their count arrives last under an empty day.
    """

    clause, args = where(scope)
    return [
        {"day": row["day"], "count": row["count"]}
        for row in conn.execute(
            f"""
            SELECT CASE WHEN i.date_taken IS NULL OR i.date_taken = '' THEN ''
                        ELSE substr(i.date_taken, 1, 10) END AS day,
                   COUNT(*) AS count
            FROM images i
            WHERE {IN_LIBRARY} AND ({clause})
            GROUP BY day ORDER BY day = '', day DESC
            """,
            args,
        )
    ]


# A shoot ends when the camera rests: the measured gap on the real library
# that folds 173 dated photographs into 11 legible sessions.
SESSION_REST = 3 * 3600


def sessions(conn, *, limit: int = 8) -> list[dict]:
    """The most recent shoots, named: capture-gap clustering over the dated
    library, newest first, titled by who or what was worn plus the day.

    Only the sessions returned are ever named — naming reads the members'
    worn people and labels, and a whole library holds thousands of
    sessions nobody asked about.
    """

    import json as coding

    spans: list[tuple[str, str, int]] = []   # (first, last, count) newest-first
    last = None
    for row in conn.execute(
        f"SELECT i.date_taken AS at FROM images i"
        f" WHERE {IN_LIBRARY} AND i.date_taken IS NOT NULL AND i.date_taken != ''"
        f" ORDER BY i.date_taken DESC",
    ):
        at = row["at"]
        if last is not None and _apart(last, at) <= SESSION_REST:
            first, _, count = spans[-1]
            spans[-1] = (first, at, count + 1)
        else:
            if len(spans) == int(limit):
                break
            spans.append((at, at, 1))
        last = at

    out = []
    for first, final, count in spans:
        worn: dict[str, int] = {}
        for row in conn.execute(
            "SELECT c.value FROM cache c WHERE c.kind IN ('people', 'label')"
            " AND c.state = 'ready' AND c.hash IN ("
            "   SELECT i.content_hash FROM images i"
            f"  WHERE {IN_LIBRARY} AND i.date_taken BETWEEN ? AND ?"
            "   AND i.content_hash IS NOT NULL)",
            (final, first),
        ):
            try:
                for word in coding.loads(row["value"]):
                    worn[str(word)] = worn.get(str(word), 0) + 1
            except (ValueError, TypeError):
                continue
        # A name a session earns: what most of it wears, if most of it
        # agrees; the day alone is still a true name.
        named = max(worn.items(), key=lambda pair: pair[1])[0] if worn else None
        day = f"{_MONTHS[int(first[5:7]) - 1][:3]} {int(first[8:10])}" if len(first) >= 10 else first
        out.append({
            "title": f"{day} · {named}" if named and worn[named] * 2 >= count else day,
            "from": final[:19], "to": first[:19], "count": count,
        })
    return out


def _apart(later: str, earlier: str) -> float:
    """Seconds between two catalog datestamps, largest-first."""

    from datetime import datetime

    try:
        a = datetime.fromisoformat(later[:19])
        b = datetime.fromisoformat(earlier[:19])
    except ValueError:
        return float("inf")
    return (a - b).total_seconds()


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

    plans: dict[str, dict] = {}
    for family, (_column, default, valid) in decisions.PROJECTED.items():
        plans[family] = {}
        for subject, value in decisions.current(conn, family).items():
            value = default if value is None else value
            if not valid(value):
                raise ValueError(f"invalid {family} decision: {value!r}")
            plans[family][subject] = (value,)
    return {
        family: projection.project(conn, "content_hash", (decisions.PROJECTED[family][0],), intended)
        for family, intended in plans.items()
    }


def rerank(conn, subjects=None, vectors=None) -> int:
    """Recompute the ranking from the log and write it, and its stars, into
    the sort index.

    Ranking is a derivation, so this is the only place it is stored, and it is
    stored only so that `ORDER BY elo` and `stars >= ?` are index reads.
    Losing these columns costs one recomputation; losing the log would cost
    the judgements.

    Rewritten whole, every time: what the ranking scores gets its score and
    its star, and everything else returns to base and none. A photograph that
    drops out of the ranking -- its only round taken back -- must drop out of
    the order too, which a write that only touched the scored ones left
    standing at a number nothing justified any more. With the embedding space
    the ranking reaches every photograph with a vector; without it, the ones
    you have judged.
    """

    import rank

    scores = rank.ranking(conn, subjects, vectors)
    # The shoot is the folder: a star can be earned in the world or there.
    shoots = {
        row["hash"]: row["tail"].rsplit("/", 1)[0]
        for row in conn.execute(
            "SELECT content_hash AS hash, tail FROM images"
            " WHERE content_hash IS NOT NULL AND tail IS NOT NULL")
    }
    starred = rank.stars(scores, rank.seen(conn), shoots)
    # Every identified row is intended: what the ranking scores gets its
    # score and star, everything else returns to base and none.
    intended = {
        digest: (scores.get(digest, rank.BASE), starred.get(digest, 0)) for digest in shoots
    }
    projection.project(conn, "content_hash", ("elo", "stars"), intended)
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
        try:
            if precision == "%Y":
                end = start.replace(year=start.year + 1)
            elif precision == "%Y-%m":
                end = (start.replace(year=start.year + 1, month=1) if start.month == 12
                       else start.replace(month=start.month + 1))
            else:
                end = start + _dt.timedelta(days=1)
        except (OverflowError, ValueError):
            return None
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

    # One streamed join instead of a correlated subquery per photograph —
    # measured at 155k rows: the subquery shape took thirty seconds, this
    # takes under one. Rows arrive ordered by photograph, so each one's
    # copies fold into a single held-state before its ancestors are walked.
    record_ids = {d for d, held in drive_of.items() if held["is_record"]}
    known_ids = set(drive_of)

    def fold(tail: str, on_record: bool, on_any: bool) -> None:
        parts = tail.split("/")[:-1]
        held = "safe" if on_record else ("only-here" if on_any else "unknown")
        key = parts[0]
        for depth in range(len(parts)):
            if depth:
                key = key + "/" + parts[depth]
            counts[key] = counts.get(key, 0) + 1
            where.setdefault(key, set()).add(held)

    current_id, current_tail, on_record, on_any = None, None, False, False
    for row in conn.execute(
        f"""
        SELECT i.id, i.tail, c.drive_id FROM images i
        LEFT JOIN copies c ON c.photo_id = i.id
        WHERE {IN_LIBRARY} AND i.tail GLOB '*/*' ORDER BY i.id
        """
    ):
        if row["id"] != current_id:
            if current_id is not None:
                fold(current_tail, on_record, on_any)
            current_id, current_tail = row["id"], str(row["tail"])
            on_record, on_any = False, False
        drive = row["drive_id"]
        if drive is not None and drive in known_ids:
            on_any = True
            if drive in record_ids:
                on_record = True
    if current_id is not None:
        fold(current_tail, on_record, on_any)

    children_of: dict[str, list] = {}
    for key in sorted(counts):
        if "/" in key:
            children_of.setdefault(key.rsplit("/", 1)[0], []).append(key)

    def node(path: str) -> dict:
        return {
            "path": path,
            "name": path.rsplit("/", 1)[-1],
            "total_count": counts[path],
            "safety": _safety(where.get(path) or set(), record_attached),
            "reveal_available": True,
            "children": [node(child) for child in children_of.get(path, ())],
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
