#!/usr/bin/env python3
"""The product boundary, timed on a catalog copy, so a performance row ships
with its number and the next round compares instead of rediscovering.

    web\\.venv\\Scripts\\python.exe scripts\\bench.py <catalog.db> [folder-prefix] [--budget]

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
import rank
import search as finding
import stacks
import tiles
import work
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
    store = tiles.Store(os.path.join(tempfile.gettempdir(), "azimuth-bench-tiles"))
    total = library.size(conn)
    deep = max(0, total - 200)
    named = [a for a in argv[2:] if not a.startswith("--")]
    prefix = named[0] if named else (
        conn.execute("SELECT tail FROM images WHERE tail IS NOT NULL LIMIT 1").fetchone()[0].rsplit("/", 2)[0])
    scoped = in_folder(prefix)
    first = conn.execute("SELECT id FROM images WHERE tail IS NOT NULL AND status != 'trashed' LIMIT 1").fetchone()[0]

    # The budgets, in ms on a 150k catalog with the machine at rest: a row
    # over its budget is printed OVER, and `--budget` makes that a failure.
    BUDGET = {"photos": 40, "size everything": 30, "size folder": 5, "days everything": 200, "days folder": 10,
              "position": 80, "counts": 30, "stacks.stack": 20, "stacks.unstack": 20, "cull.pick+undo (page": 150, "cull.pick+undo (folder": 3000, "facets_of": 1500,
              "stacks.project": 1500, "folder_tree": 1500, "search": 200, "rank.candidates": 150,
              "rank.uncertain": 4000, "rank.progress": 150, "work.owed": 10}
    over = []

    def row(name, fn):
        took = timed(fn)
        limit = next((ms for key, ms in BUDGET.items() if name.startswith(key)), None)
        flag = "  OVER" if limit is not None and took > limit else ""
        if flag:
            over.append(name)
        print(f"{name:<40} {took:8.1f} ms{flag}")

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
    row("folder_tree", lambda: library.folder_tree(conn))
    row("search lexical", lambda: finding.search(conn, "2016", limit=200))
    # The fit runs on the rank lane and hands Learn its uncertainty; a Learn
    # draw pays the draw alone. Both timed, each against its own budget.
    row("rank.uncertain (rounds alone)", lambda: rank.uncertain(conn))
    unsure = rank.uncertain(conn)
    row("rank.candidates learn", lambda: rank.candidates(conn, 9, mode="learn", unsure=unsure))
    row("rank.progress", lambda: rank.progress(conn))
    row("work.owed grid", lambda: work.owed(conn, store.grid, limit=64))
    row("stacks.project (whole)", lambda: stacks.project(conn))
    ids = [r[0] for r in conn.execute(
        "SELECT id FROM images WHERE tail IS NOT NULL AND content_hash IS NOT NULL ORDER BY date_taken LIMIT 3")]
    if len(ids) == 3:
        row("stacks.stack (3 frames)", lambda: stacks.stack(conn, ids))
        row("stacks.unstack (cover)", lambda: stacks.unstack(conn, ids[:1]))
    # A cull verb over a page and over a folder, and each undone: what
    # Select All then P costs, on the copy.
    from model import cull

    page = [r[0] for r in conn.execute(
        "SELECT id FROM images WHERE tail IS NOT NULL AND content_hash IS NOT NULL AND status != 'trashed' ORDER BY id LIMIT 500")]
    folder = [r[0] for r in conn.execute(
        "SELECT id FROM images WHERE tail IS NOT NULL AND content_hash IS NOT NULL AND status != 'trashed' AND tail GLOB ?",
        (prefix + "/*",))]
    for name, chosen in (("page of 500", page), (f"folder of {len(folder):,}", folder)):
        if not chosen:
            continue
        said = {}
        row(f"cull.pick+undo ({name})", lambda c=chosen, s=said: s.update(cull.pick(conn, c)) or cull.undo(conn, s["changed"]))
    conn.close()
    if "--budget" in argv and over:
        print(f"over budget: {', '.join(over)}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
