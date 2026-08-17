"""A source moves machines: swap the prefix its photographs are recorded under.

    python scripts/repoint_source.py --source 3 --to "E:/Photos"          # dry run
    python scripts/repoint_source.py --source 3 --to "E:/Photos" --apply

Azimuth 2.0 is dropping the hub/satellite split: the laptop is the home and the
external drive is cold archive. But 144,325 of 154,842 photographs are still
registered under a `hub://` source, carrying the *other machine's* filepaths
(`/mnt/expansion/Photos/...`). This laptop cannot open any of them, which is why
5,861 tiles exist for a 154,842-photograph library and why embeddings stopped at
42,937 — the chore loop cannot read what it cannot find.

`features/imports/relocation.py` already re-points a catalog, but at a different
problem: files the owner moved *somewhere unknown*, matched one at a time by
relative path, basename+size, then content hash — and it skips `hub_remote`
rows outright. Here the mapping is known and total, so probing 144,325 files
individually would be slow and would answer a question already answered.

**What makes this safe rather than clever:**

* Dry run unless `--apply`, and the dry run does every check the apply does.
* **Collisions are found before anything is written.** Two rows landing on one
  path is the failure that cannot be undone by re-running, so it aborts.
* A sample is opened on disk first. A prefix swap that produces paths nothing
  can read is a catalog that has lost its library, and it looks fine in SQL.
* `catalog_sources.path` moves with the rows. Leaving it behind is the mistake
  this codebase has already made once — a source root rename that updated the
  images and not the source, so the next scan re-imported everything.
* One transaction, and the final state is verified by reading it back, not by
  trusting the row count the UPDATE returned.
"""

from __future__ import annotations

import argparse
import os
import random
import sqlite3
import sys
import time

SAMPLE = 500


def _rows(conn, source_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT id, filepath FROM images WHERE source_id = ? AND filepath IS NOT NULL AND filepath != ''",
        (source_id,),
    ).fetchall()


def _common_prefix(paths: list[str]) -> str:
    """The deepest directory every recorded path starts with, POSIX-spelled."""

    parts = [p.replace("\\", "/").split("/") for p in paths]
    shared: list[str] = []
    for pieces in zip(*parts):
        if len(set(pieces)) != 1:
            break
        shared.append(pieces[0])
    # never claim a whole filename as the prefix
    if shared and "." in shared[-1]:
        shared.pop()
    return "/".join(shared)


def _target(filepath: str, prefix: str, root: str) -> str:
    tail = filepath.replace("\\", "/")[len(prefix):].lstrip("/")
    return os.path.normpath(os.path.join(root, tail.replace("/", os.sep)))


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", default=r"C:\Azimuth Photo\data\catalog\azimuth.db")
    parser.add_argument("--source", type=int, required=True)
    parser.add_argument("--to", required=True, help="where those photographs live now")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)

    root = os.path.normpath(os.path.expanduser(args.to))
    if not os.path.isdir(root):
        print(f"refusing: {root} is not a directory that is attached right now")
        return 2

    conn = sqlite3.connect(args.catalog)
    conn.row_factory = sqlite3.Row
    source = conn.execute("SELECT * FROM catalog_sources WHERE id = ?", (args.source,)).fetchone()
    if source is None:
        print(f"refusing: no source {args.source}")
        return 2
    rows = _rows(conn, args.source)
    if not rows:
        print(f"nothing to do: source {args.source} has no photographs with a filepath")
        return 0

    prefix = _common_prefix([r["filepath"] for r in rows])
    if not prefix:
        print("refusing: these photographs share no common prefix, so this is not one move")
        return 2

    print(f"source {args.source}: {source['path']!r}  ->  {root}")
    print(f"  {len(rows):,} photographs, all under {prefix!r}\n")

    # 1. Collisions, before anything is written.
    taken = {
        os.path.normcase(os.path.normpath(str(r["filepath"])))
        for r in conn.execute(
            "SELECT filepath FROM images WHERE source_id != ? AND filepath IS NOT NULL", (args.source,)
        )
    }
    targets: dict[str, int] = {}
    collisions: list[str] = []
    for row in rows:
        target = _target(str(row["filepath"]), prefix, root)
        key = os.path.normcase(target)
        if key in taken:
            collisions.append(f"  id {row['id']}: {target} already belongs to another source")
        elif key in targets:
            collisions.append(f"  id {row['id']}: {target} already claimed by id {targets[key]}")
        else:
            targets[key] = int(row["id"])
    if collisions:
        print(f"REFUSING: {len(collisions):,} collisions. Nothing written.")
        for line in collisions[:10]:
            print(line)
        return 1
    print(f"  collisions: none")

    # 2. Do the new paths actually open?
    sample = random.Random(0).sample(rows, min(SAMPLE, len(rows)))
    found = sum(1 for r in sample if os.path.exists(_target(str(r["filepath"]), prefix, root)))
    print(f"  sampled {len(sample)}: {found} present on disk, {len(sample) - found} missing")
    if found < len(sample) * 0.99:
        print("REFUSING: too many sampled photographs are not where this would say they are.")
        for row in sample[:5]:
            target = _target(str(row["filepath"]), prefix, root)
            if not os.path.exists(target):
                print(f"    missing: {target}")
        return 1

    if not args.apply:
        print(f"\ndry run. Nothing written. Add --apply to move {len(rows):,} rows.")
        for row in sample[:3]:
            print(f"    {row['filepath']}\n      -> {_target(str(row['filepath']), prefix, root)}")
        return 0

    # 3. One transaction: the rows and the source that owns them.
    started = time.perf_counter()
    with conn:
        conn.executemany(
            "UPDATE images SET filepath = ? WHERE id = ?",
            [(_target(str(r["filepath"]), prefix, root), int(r["id"])) for r in rows],
        )
        conn.execute("UPDATE catalog_sources SET path = ? WHERE id = ?", (root, args.source))
    print(f"\napplied in {time.perf_counter() - started:.1f}s")

    # 4. Read the result back rather than trusting the UPDATE.
    after = _rows(conn, args.source)
    still_old = sum(1 for r in after if str(r["filepath"]).replace("\\", "/").startswith(prefix))
    check = random.Random(1).sample(after, min(SAMPLE, len(after)))
    present = sum(1 for r in check if os.path.exists(str(r["filepath"])))
    source_now = conn.execute("SELECT path FROM catalog_sources WHERE id = ?", (args.source,)).fetchone()["path"]
    print(f"  source path now: {source_now}")
    print(f"  rows still on the old prefix: {still_old}")
    print(f"  sampled {len(check)} of the moved rows: {present} open on disk")
    return 0 if still_old == 0 and present >= len(check) * 0.99 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
