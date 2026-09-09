"""Write derived columns onto photograph rows, touching only rows that differ.

Every derived column on ``images`` is rebuilt the same way: status and
rotate from the log, the metadata columns from the cache, elo and stars
from the ranking, ``stack_of`` from capture-time cadence. Compute what each
row should say, then write the rows that say otherwise. The comparison is
one sequential read (measured 0.6 s over 150,000 rows) instead of one
UPDATE per row (24 s, every row dirtied, the write lock held throughout),
and a rebuild that changes nothing writes nothing — which is the normal case
at every start.

Writes land in slices with a commit between, so the lock is never held for
more than a few hundred milliseconds at a time; a cull key pressed during a
full rerank waits that long, not the whole pass.
"""

from __future__ import annotations

from typing import Iterable

# Rows written between commits. Measured at 160 µs a row, one slice holds
# the write lock for about a third of a second.
SLICE = 2000


def project(conn, key: str, columns: Iterable[str], intended: dict) -> int:
    """Make ``images.<columns>`` say ``intended[key]`` for every keyed row.

    ``intended`` maps a key (a content hash, or an id) to the tuple of values
    its columns should hold. Rows whose key is not in ``intended`` are left
    alone: a caller that means "and everything else returns to its default"
    says so by including those rows. Returns how many rows were written.
    """

    columns = tuple(columns)
    listed = ", ".join(columns)
    differing: list[tuple] = []
    for row in conn.execute(f"SELECT {key}, {listed} FROM images WHERE {key} IS NOT NULL"):
        wanted = intended.get(row[0])
        if wanted is not None and tuple(row)[1:] != tuple(wanted):
            differing.append((*wanted, row[0]))
    assignments = ", ".join(f"{column} = ?" for column in columns)
    written = 0
    for start in range(0, len(differing), SLICE):
        cursor = conn.executemany(
            f"UPDATE images SET {assignments} WHERE {key} = ?", differing[start:start + SLICE])
        written += cursor.rowcount
        conn.commit()
    conn.commit()
    return written
