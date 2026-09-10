#!/usr/bin/env python3
"""The product boundary, timed on a catalog copy, so a performance row ships
with its number and the next round compares instead of rediscovering.

    web\\.venv\\Scripts\\python.exe scripts\\bench.py <catalog.db> [folder-prefix]

The catalog is copied to a scratch file first; nothing is written to the
one named. Prints one line per verb: the median of three runs in ms, and
the query plan's first line where a scan would be the news. Numbers taken
while other jobs run are inflated -- compare within a run.
"""

from __future__ import annotations

import os
import shutil
import statistics
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "web"))

import library
import model
import stacks
from boot import facets_of
from model.scope import covers_only
from model.scope import folder as in_folder


def timed(fn, runs: int = 3) -> float:
    took = []
    for _ in range(runs):
        start = time.perf_counter()
        fn()
        took.append((time.perf_counter() - start) * 1000)
    return statistics.median(took)


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    scratch = os.path.join(tempfile.gettempdir(), "azimuth-bench.db")
    for ext in ("", "-wal", "-shm"):
        if os.path.exists(scratch + ext):
            os.remove(scratch + ext)
    shutil.copy(argv[1], scratch)
    conn = model.connect(scratch)
    total = library.size(conn)
    deep = max(0, total - 200)
    prefix = argv[2] if len(argv) > 2 else (
        conn.execute("SELECT tail FROM images WHERE tail IS NOT NULL LIMIT 1").fetchone()[0].rsplit("/", 2)[0])
    scoped = in_folder(prefix)
    first = conn.execute("SELECT id FROM images WHERE tail IS NOT NULL AND status != 'trashed' LIMIT 1").fetchone()[0]

    def row(name, fn):
        print(f"{name:<40} {timed(fn):8.1f} ms")

    print(f"catalog {argv[1]}  rows {total:,}  folder {prefix}")
    for sort in ("newest", "best", "stars", "filename"):
        row(f"photos {sort} @0", lambda s=sort: library.photos(conn, sort=s, limit=200, offset=0))
        row(f"photos {sort} @{deep}", lambda s=sort: library.photos(conn, sort=s, limit=200, offset=deep))
    row("photos newest folder @0", lambda: library.photos(conn, sort="newest", limit=200, offset=0, scope=scoped))
    row("photos best folder @0", lambda: library.photos(conn, sort="best", limit=200, offset=0, scope=scoped))
    row("photos newest collapsed @0", lambda: library.photos(conn, sort="newest", limit=200, offset=0, scope=covers_only()))
    row("size everything", lambda: library.size(conn))
    row("size folder", lambda: library.size(conn, scoped))
    row("days everything", lambda: library.days(conn))
    row("days folder", lambda: library.days(conn, scoped))
    for sort in ("newest", "best", "stars", "added", "filename"):
        row(f"position {sort}", lambda s=sort: library.position(conn, first, s))
    row("counts", lambda: library.counts(conn))
    row("facets_of", lambda: facets_of(conn))
    row("stacks.project (whole)", lambda: stacks.project(conn))
    ids = [r[0] for r in conn.execute(
        "SELECT id FROM images WHERE tail IS NOT NULL AND content_hash IS NOT NULL ORDER BY date_taken LIMIT 3")]
    if len(ids) == 3:
        row("stacks.stack (3 frames)", lambda: stacks.stack(conn, ids))
        row("stacks.unstack (cover)", lambda: stacks.unstack(conn, ids[:1]))
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
