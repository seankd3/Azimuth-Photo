"""Copy every photo that exists in only one place onto the archive.

Adds only. Never deletes, never moves, never touches the source. A copy is
proved byte for byte before it counts, and anything unexpected is reported and
skipped rather than forced.

    python scripts/back_up_photos.py              # dry run
    python scripts/back_up_photos.py --apply
    python scripts/back_up_photos.py --apply --limit 50
"""

import os
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web"))

from model import backup, drives

DB = r"C:/Azimuth Photo/data/catalog/azimuth.db"
APPLY = "--apply" in sys.argv
LIMIT = None
for index, arg in enumerate(sys.argv):
    if arg == "--limit" and index + 1 < len(sys.argv):
        LIMIT = int(sys.argv[index + 1])

conn = sqlite3.connect(DB, timeout=60)
conn.row_factory = sqlite3.Row
with open(os.path.join(os.path.dirname(drives.__file__), "schema.sql"), encoding="utf-8") as handle:
    conn.executescript(handle.read())
conn.commit()

drive = backup.record_drive(conn)
if drive is None:
    print("no record drive attached — plug the archive in")
    sys.exit(1)
print(f"archive: {drive['label']} at {drives.root_of(conn, drive['uuid'])}")

queue = backup.unprotected(conn, limit=LIMIT)
total_bytes = sum(int(p["file_size"] or 0) for p in queue)
print(f"photos with no archive copy: {len(queue)}  ({total_bytes / 1024**3:.1f} GB)")
if not queue:
    sys.exit(0)


def step(index, total, photo, outcome):
    if outcome not in ("copied", "would copy") or index % 100 == 0 or index == total:
        print(f"   [{index}/{total}] {photo['tail'][:70]}  {outcome}", flush=True)


result = backup.back_up_all(conn, limit=LIMIT, dry_run=not APPLY, on_step=step)
print(f"\n{result['outcomes']}   in {result['seconds']}s")

left = len(backup.unprotected(conn))
print(f"still only in one place: {left}")
conn.close()

if not APPLY:
    print("\ndry run — pass --apply")
