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
    # Recently added *is* id order -- ids are handed out in insertion order --
    # so this sort needs neither a column nor an index. Reading `created_at`
    # instead cost 233 ms against 0.2 ms, for the same answer.
    "added": "i.id DESC",
}


def photos(conn, *, folder: str | None = None, sort: str = "newest",
           starred: int | None = None, limit: int = 200, offset: int = 0) -> list[dict]:
    """One page of the grid.

    Deliberately has no `include_missing`, no `source`, no `online_only`. Those
    arguments existed because the query had to know where the bytes were; it
    does not, and a photograph on an unplugged drive is listed exactly like any
    other. What it looks like when painted is `render`'s problem and `state()`'s
    answer.
    """

    if sort not in SORTS:
        raise ValueError(f"no such sort: {sort!r}; have {sorted(SORTS)}")

    where = [IN_LIBRARY]
    args: list = []
    if folder:
        prefix = folder.replace("\\", "/").rstrip("/") + "/"
        where.append("substr(i.tail, 1, ?) = ?")
        args += [len(prefix), prefix]
    if starred:
        where.append("i.stars >= ?")
        args.append(int(starred))

    args += [int(limit), int(offset)]
    return [dict(row) for row in conn.execute(
        f"""
        SELECT i.id, i.tail, i.date_taken, i.stars, i.elo, i.content_hash AS hash,
               i.width, i.height, i.file_size
        FROM images i
        WHERE {' AND '.join(where)}
        ORDER BY {SORTS[sort]}
        LIMIT ? OFFSET ?
        """,
        args,
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
