"""Write derived columns onto photograph rows, touching only rows that differ.

Every derived column on ``images`` is rebuilt the same way: status and
rotate from the log, the metadata columns from the cache, elo and stars
from the ranking, ``stack_of`` from capture-time cadence. Compute what each
row should say, then write the rows that say otherwise. The comparison is
one sequential read (measured 0.6 s over 150,000 rows) instead of one
UPDATE per row (24 s, every row dirtied, the write lock held throughout),
and a rebuild that changes nothing writes nothing — which is the normal case
at every start.

A whole-table rebuild lands in slices with a commit between, so the lock is
never held for more than a few hundred milliseconds at a time; a cull key
pressed during a full rerank waits that long, not the whole pass. An act's
own projection (`only`) is the act's transaction: it commits nothing, so
the caller's commit lands the decisions and their projection together, or
its rollback undoes both.
"""

from __future__ import annotations

from typing import Iterable

# Rows written between commits. Measured at 160 µs a row, one slice holds
# the write lock for about a third of a second.
SLICE = 2000


def project(conn, key: str, columns: Iterable[str], intended: dict, *,
            only: bool = False, slice_rows: int = SLICE) -> int:
    """Make ``images.<columns>`` say ``intended[key]`` for every keyed row.

    ``intended`` maps a key (a content hash, or an id) to the tuple of values
    its columns should hold. Rows whose key is not in ``intended`` are left
    alone: a caller that means "and everything else returns to its default"
    says so by including those rows. Returns how many rows were written.
    """

    columns = tuple(columns)
    listed = ", ".join(columns)
    differing: dict = {}
    # `only`: read just the keyed rows instead of the whole table -- the
    # shape for a handful of rows changed by one act.
    if only:
        keys = list(intended)
        reads = (
            conn.execute(
                f"SELECT {key}, {listed} FROM images WHERE {key} IN ({','.join('?' for _ in chunk)})", chunk)
            for chunk in (keys[at:at + 500] for at in range(0, len(keys), 500))
        )
    else:
        reads = (conn.execute(f"SELECT {key}, {listed} FROM images WHERE {key} IS NOT NULL"),)
    in_order: list[tuple] = []   # (key, wanted) as read: the rows' own order on disk
    seen: set = set()            # one statement per key: two rows of one identity share it
    for read in reads:
        for row in read:
            wanted = intended.get(row[0])
            if wanted is not None and tuple(row)[1:] != tuple(wanted) and row[0] not in seen:
                seen.add(row[0])
                differing.setdefault(tuple(wanted), []).append(row[0])
                in_order.append((row[0], tuple(wanted)))
    # Rows that want the same values land in one statement per five
    # hundred, not one per row: a cull verb writes one word onto thousands
    # of rows, and the per-row form spent its time re-entering the engine
    # (9,319 rows: 2.83 s per row, 0.30 s in chunks; 150,000: 20 s to
    # 5.3 s). A rerank writes tens of thousands of distinct values over a
    # few rows each; those keep the one bound statement (executemany) in
    # the rows' own order -- a statement per small group was three times
    # slower, and the same rows updated out of order were three times
    # slower again.
    assignments = ", ".join(f"{column} = ?" for column in columns)
    written = 0
    since_commit = 0
    singles = [(*wanted, k) for k, wanted in in_order if len(differing[wanted]) < 16]
    for wanted, keys in differing.items():
        if len(keys) < 16:
            continue
        for start in range(0, len(keys), 500):
            chunk = keys[start:start + 500]
            cursor = conn.execute(
                f"UPDATE images SET {assignments} WHERE {key} IN ({','.join('?' * len(chunk))})", (*wanted, *chunk))
            written += cursor.rowcount
            since_commit += len(chunk)
            if not only and since_commit >= slice_rows:
                conn.commit()
                since_commit = 0
    for start in range(0, len(singles), slice_rows):
        cursor = conn.executemany(f"UPDATE images SET {assignments} WHERE {key} = ?", singles[start:start + slice_rows])
        written += cursor.rowcount
        if not only:
            conn.commit()
    if not only:
        conn.commit()
    return written
