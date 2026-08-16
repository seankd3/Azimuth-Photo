"""Sweep the attached drives and record which of them hold what.

This is `model.copies.sweep` run against the real catalog. It writes copy rows
and nothing else: no photo is created, moved, marked or removed by a sweep.

    python scripts/sweep_drives.py            # every attached drive
    python scripts/sweep_drives.py D:\\Pictures
"""

import os
import sqlite3
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web"))

from model import copies, drives  # noqa: E402

DB = r"C:/Azimuth Photo/data/catalog/azimuth.db"

conn = sqlite3.connect(DB, timeout=60)
conn.row_factory = sqlite3.Row

# The core's tables arrive with the step that needs them, and every statement
# in here is CREATE ... IF NOT EXISTS, so this is idempotent.
with open(os.path.join(os.path.dirname(drives.__file__), "schema.sql"), encoding="utf-8") as handle:
    conn.executescript(handle.read())
conn.commit()

wanted = [os.path.normpath(a) for a in sys.argv[1:] if not a.startswith("-")]
rows = conn.execute("SELECT uuid, root, label, is_record FROM drives ORDER BY is_record, id").fetchall()
if wanted:
    rows = [r for r in rows if os.path.normpath(r["root"]) in wanted]

for row in rows:
    started = time.time()
    print(f"{row['label']} ({row['root']}) ...", flush=True)
    result = copies.sweep(conn, row["uuid"])
    elapsed = time.time() - started
    if not result["applied"]:
        print(f"   no change: {result['reason']}  [{elapsed:.1f}s]")
        continue
    print(
        f"   files {result['files_seen']:>7}   copies +{result['copies_recorded']} "
        f"-{result['copies_retired']}   unknown {result['unknown_files']}   [{elapsed:.1f}s]"
    )

print("\nphotos by where they live:")
for label, sql in (
    ("on the working disk", "SELECT COUNT(DISTINCT c.photo_id) FROM copies c JOIN drives d ON d.id=c.drive_id WHERE d.is_record=0"),
    ("on a record drive  ", "SELECT COUNT(DISTINCT c.photo_id) FROM copies c JOIN drives d ON d.id=c.drive_id WHERE d.is_record=1"),
    ("on both            ", "SELECT COUNT(*) FROM (SELECT c.photo_id FROM copies c JOIN drives d ON d.id=c.drive_id GROUP BY c.photo_id HAVING COUNT(DISTINCT d.is_record)>1)"),
    ("no copy anywhere   ", "SELECT COUNT(*) FROM images i WHERE i.vc_of IS NULL AND NOT EXISTS (SELECT 1 FROM copies c WHERE c.photo_id=i.id)"),
):
    print(f"   {label}: {conn.execute(sql).fetchone()[0]}")

only_here = conn.execute(
    "SELECT COUNT(DISTINCT c.photo_id) FROM copies c JOIN drives d ON d.id = c.drive_id "
    "WHERE d.is_record = 0 AND c.photo_id NOT IN ("
    "  SELECT c2.photo_id FROM copies c2 JOIN drives d2 ON d2.id = c2.drive_id WHERE d2.is_record = 1)"
).fetchone()[0]
print(f"\n   ONLY HERE (no archive copy): {only_here}")
conn.close()
