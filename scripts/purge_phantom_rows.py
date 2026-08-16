"""Purge generated test-library rows from the live catalog.

`Raws/Digital/2026/2026-05-20` is a REAL shoot folder that make_test_library
also wrote into, so the folder is not the discriminator -- a folder-wide delete
would take real photographs.

A row is purged only when it is simultaneously:
  * absent from disk,
  * carrying no content hash, no dimensions, no camera,
  * not dated from EXIF,
  * and holding no judgment of any kind.

Anything that fails one of those stays. Over-keeping leaves a broken row that a
later sweep will report; over-deleting loses a photograph.

Dry run by default. Pass --apply, and only with the app stopped.
"""

import os
import sqlite3
import sys

DB = r"C:/Azimuth Photo/data/catalog/azimuth.db"
APPLY = "--apply" in sys.argv

CANDIDATES = """
SELECT id, filepath FROM images
 WHERE source_id = 5 AND vc_of IS NULL
   AND COALESCE(content_hash, '') = ''
   AND width IS NULL AND height IS NULL
   AND camera_model IS NULL
   AND COALESCE(date_source, '') != 'exif'
   AND COALESCE(stars, 0) = 0
   AND (elo IS NULL OR elo = 1200)
   AND COALESCE(comparisons, 0) = 0
   AND COALESCE(flag, '') IN ('', 'unflagged')
   AND COALESCE(status, '') IN ('', 'kept')
"""

conn = sqlite3.connect(DB if APPLY else f"file:{DB}?mode=ro", uri=not APPLY, timeout=30)
conn.row_factory = sqlite3.Row

candidates = conn.execute(CANDIDATES).fetchall()
print(f"evidence-free rows on source 5: {len(candidates)}")

ids, kept_present = [], 0
for row in candidates:
    if os.path.exists(row["filepath"]):
        kept_present += 1          # a real file we simply have not scanned yet
    else:
        ids.append(row["id"])
print(f"  ...of which exist on disk (KEPT): {kept_present}")
print(f"  ...of which are absent (PURGE)  : {len(ids)}")

if not ids:
    print("nothing to do")
    sys.exit(0)

placeholders = ",".join("?" * len(ids))

# Re-prove the precondition on the exact id set, not on the query that built it.
guard = conn.execute(
    f"SELECT COUNT(*) FROM images WHERE id IN ({placeholders}) AND ("
    "COALESCE(stars,0) > 0 OR (elo IS NOT NULL AND elo != 1200) OR COALESCE(comparisons,0) > 0 "
    "OR COALESCE(flag,'') NOT IN ('', 'unflagged') OR COALESCE(status,'') NOT IN ('', 'kept') "
    "OR COALESCE(content_hash,'') != '' OR width IS NOT NULL OR camera_model IS NOT NULL)",
    ids,
).fetchone()[0]
print(f"guard -- rows carrying anything real: {guard}")
if guard:
    print("REFUSING")
    sys.exit(1)

for table, col in (("develop_settings", "image_id"), ("comparisons", "winner_id")):
    n = conn.execute(
        f"SELECT COUNT(*) FROM {table} WHERE {col} IN ({placeholders})", ids
    ).fetchone()[0]
    if n:
        print(f"REFUSING: {n} rows in {table}")
        sys.exit(1)

CHILDREN = [
    "image_shoot_hints", "develop_settings", "develop_history", "cache_entries",
    "image_quality", "embeddings", "embeddings_by_model", "embedding_scan_images",
    "image_captions", "image_understanding", "image_tags", "caption_scan_images",
    "import_batch_images", "stack_members", "collection_images", "published_node_images",
    "share_images", "face_detections", "person_image_membership", "face_scan_images",
    "image_checksums", "propagation_updates", "image_keywords", "iptc_fields",
    "cache_image_presence", "face_scan_backlog",
]
present_tables = []
print("\nchild rows that would go:")
for table in CHILDREN:
    try:
        n = conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE image_id IN ({placeholders})", ids
        ).fetchone()[0]
    except sqlite3.OperationalError:
        continue
    present_tables.append(table)
    if n:
        print(f"   {table:<26} {n}")

if not APPLY:
    print("\ndry run -- pass --apply (app must be stopped)")
    sys.exit(0)

cur = conn.cursor()
cur.execute("BEGIN IMMEDIATE")
for table in present_tables:
    cur.execute(f"DELETE FROM {table} WHERE image_id IN ({placeholders})", ids)
cur.execute(f"DELETE FROM images WHERE id IN ({placeholders})", ids)
conn.commit()

print(f"\npurged {len(ids)}")
for label, sql in (
    ("source 5 rows", "SELECT COUNT(*) FROM images WHERE source_id = 5 AND vc_of IS NULL"),
    ("catalog rows ", "SELECT COUNT(*) FROM images WHERE vc_of IS NULL"),
    ("starred      ", "SELECT COUNT(*) FROM images WHERE COALESCE(stars,0) > 0"),
    ("elo moved    ", "SELECT COUNT(*) FROM images WHERE elo IS NOT NULL AND elo != 1200"),
    ("develop rows ", "SELECT COUNT(*) FROM develop_settings"),
    ("comparisons  ", "SELECT COUNT(*) FROM comparisons"),
):
    print(f"  {label}: {conn.execute(sql).fetchone()[0]}")
conn.close()
