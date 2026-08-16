"""Repoint rows whose roll folder was flattened.

74 film-scan rows still address `Film Scans/2026/2026-07-16/Holland Photo
Imaging/<frame>.tif` while the frames themselves now sit one level up, directly
in the dated folder. The photographs are fine; only the address is stale.

A row is repointed only when exactly one candidate exists and its size matches
the catalog's. Anything ambiguous or size-mismatched is reported and left
alone -- a wrong repoint is harder to notice than a missing photo.

Dry run by default. Pass --apply, with the app stopped.
"""

import os
import sqlite3
import sys

DB = r"C:/Azimuth Photo/data/catalog/azimuth.db"
APPLY = "--apply" in sys.argv

conn = sqlite3.connect(DB if APPLY else f"file:{DB}?mode=ro", uri=not APPLY, timeout=30)
conn.row_factory = sqlite3.Row

rows = conn.execute(
    "SELECT id, filepath, relative_path, file_size FROM images "
    "WHERE source_id = 5 AND vc_of IS NULL"
).fetchall()

moves, skipped = [], []
for row in rows:
    current = row["filepath"]
    if os.path.exists(current):
        continue
    parent = os.path.dirname(os.path.dirname(current))
    candidate = os.path.join(parent, os.path.basename(current))
    if not os.path.isfile(candidate):
        skipped.append((row["id"], current, "no candidate one level up"))
        continue
    size = row["file_size"]
    if size and os.path.getsize(candidate) != int(size):
        skipped.append((row["id"], current, "size mismatch"))
        continue
    clash = conn.execute(
        "SELECT id FROM images WHERE filepath = ? AND id != ? AND vc_of IS NULL",
        (candidate, row["id"]),
    ).fetchone()
    if clash:
        skipped.append((row["id"], current, f"target already catalogued as id {clash['id']}"))
        continue
    tail = row["relative_path"]
    new_tail = None
    if tail:
        parts = tail.replace("\\", "/").split("/")
        if len(parts) >= 2:
            new_tail = "/".join(parts[:-2] + parts[-1:])
    moves.append((candidate, new_tail, row["id"], current))

print(f"absent rows repointed : {len(moves)}")
print(f"left alone            : {len(skipped)}")
for _id, path, why in skipped[:10]:
    print(f"   {why}: {path}")

if moves[:3]:
    print("\nexample:")
    for new, tail, _id, old in moves[:3]:
        print(f"   {old}\n     -> {new}   rel -> {tail}")

if not APPLY:
    print("\ndry run -- pass --apply (app must be stopped)")
    sys.exit(0)

cur = conn.cursor()
cur.execute("BEGIN IMMEDIATE")
cur.executemany(
    "UPDATE images SET filepath = ?, relative_path = COALESCE(?, relative_path) WHERE id = ?",
    [(new, tail, image_id) for new, tail, image_id, _old in moves],
)
conn.commit()
print(f"\nrepointed {len(moves)}")

remaining = [
    r for r in conn.execute(
        "SELECT filepath FROM images WHERE source_id = 5 AND vc_of IS NULL"
    ) if not os.path.exists(r["filepath"])
]
print(f"source 5 still absent: {len(remaining)}")
conn.close()
