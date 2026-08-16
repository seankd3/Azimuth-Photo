"""Step 2 — give every photo a tail, and register the two drives.

A path is a drive plus a tail. This finds the drive half by evidence rather
than by a hardcoded prefix: for each row, leading components are stripped until
the remainder actually exists under one of the attached roots, size-verified.
The archive's 144k rows still carry `/mnt/expansion/Photos/...` from a machine
that is powered off, and nothing here needs to know that string.

The working disk is preferred when both hold the same tail, so a photo that
exists in both places is addressed by the copy you edit.

Dry run by default. Pass --apply, with the app stopped.
"""

import os
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web"))

from model import drives  # noqa: E402

DB = r"C:/Azimuth Photo/data/catalog/azimuth.db"
SCHEMA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web", "model", "schema.sql")
APPLY = "--apply" in sys.argv

ROOTS = [
    (r"D:\Pictures", "Working", False),
    (r"E:\Photos", "Archive", True),
]
MAX_STRIP = 4


def resolve(roots, filepath, expected_size):
    """(drive_uuid, tail) for a row, or None. Evidence only — no prefixes."""

    native = os.path.normpath(filepath)
    for uuid, root in roots:
        tail = drives.tail_for(root, native)
        if tail and _verified(os.path.join(root, tail.replace("/", os.sep)), expected_size):
            return uuid, tail

    parts = [p for p in filepath.replace("\\", "/").split("/") if p]
    for depth in range(1, min(MAX_STRIP, len(parts) - 1) + 1):
        candidate_tail = "/".join(parts[depth:])
        for uuid, root in roots:
            if _verified(os.path.join(root, candidate_tail.replace("/", os.sep)), expected_size):
                return uuid, candidate_tail
    return None


def _verified(path, expected_size):
    try:
        stat = os.stat(path)
    except OSError:
        return False
    return not expected_size or stat.st_size == int(expected_size)


conn = sqlite3.connect(DB if APPLY else f"file:{DB}?mode=ro", uri=not APPLY, timeout=30)
conn.row_factory = sqlite3.Row

if APPLY:
    with open(SCHEMA, encoding="utf-8") as handle:
        conn.executescript(handle.read())
    columns = {r["name"] for r in conn.execute("PRAGMA table_info(images)")}
    if "tail" not in columns:
        conn.execute("ALTER TABLE images ADD COLUMN tail TEXT")
    if "drive_id" not in columns:
        conn.execute("ALTER TABLE images ADD COLUMN drive_id INTEGER REFERENCES drives(id)")
    conn.commit()

roots = []
for root, label, is_record in ROOTS:
    if not os.path.isdir(root):
        print(f"  SKIP {root} (not attached)")
        continue
    if APPLY:
        drive = drives.attach(conn, root, label=label, is_record=is_record)
        roots.append((drive["uuid"], root))
        print(f"  drive {label:<8} {root}  uuid={drive['uuid'][:8]}  record={bool(is_record)}")
    else:
        roots.append((f"<{label}>", root))
        print(f"  would attach {label:<8} {root}  record={bool(is_record)}")
# Working disk first: a photo on both drives is addressed by the copy you edit.

rows = conn.execute(
    "SELECT id, filepath, file_size, source_id FROM images WHERE vc_of IS NULL"
).fetchall()
print(f"\nrows: {len(rows)}")

resolved, unresolved = [], []
for row in rows:
    hit = resolve(roots, row["filepath"], row["file_size"])
    (resolved if hit else unresolved).append((row, hit))

print(f"  tail found : {len(resolved)}")
print(f"  no tail    : {len(unresolved)}  (away or lost — a read will say which)")

by_drive = {}
for row, hit in resolved:
    by_drive[hit[0]] = by_drive.get(hit[0], 0) + 1
for uuid, n in by_drive.items():
    label = next((r for u, r in roots if u == uuid), uuid)
    print(f"    {n:>7}  {label}")

if not APPLY:
    for row, _ in unresolved[:5]:
        print(f"    e.g. no tail: {row['filepath'][:90]}")
    print("\ndry run — pass --apply (app must be stopped)")
    sys.exit(0)

uuid_to_id = {r["uuid"]: r["id"] for r in conn.execute("SELECT id, uuid FROM drives")}
cur = conn.cursor()
cur.execute("BEGIN IMMEDIATE")
cur.executemany(
    "UPDATE images SET tail = ?, drive_id = ? WHERE id = ?",
    [(tail, uuid_to_id[uuid], row["id"]) for row, (uuid, tail) in resolved],
)
conn.commit()

print(f"\ntailed {len(resolved)}")
have = conn.execute("SELECT COUNT(*) FROM images WHERE tail IS NOT NULL AND vc_of IS NULL").fetchone()[0]
print(f"  rows with a tail: {have}")
same = conn.execute(
    "SELECT COUNT(*) FROM (SELECT tail FROM images WHERE tail IS NOT NULL AND vc_of IS NULL "
    "GROUP BY tail HAVING COUNT(DISTINCT drive_id) > 1)"
).fetchone()[0]
print(f"  tails on both drives: {same}")
conn.close()
