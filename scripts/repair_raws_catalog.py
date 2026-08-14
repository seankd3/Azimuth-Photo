"""Repair the Raws catalog debris — the files already sit in the canonical tree.

Sean, 2026-08-14: "please reogranize the raw folder once and for all please
theres still some outliers." Measured against the drive itself, every visible
outlier row's file already exists at its canonical path with the recorded
size — the physical reorganization happened long ago. What survived is
catalog debris in two shapes:

  ghost — a second row for the same file at a retired spelling
          (Photos/RAWS/…, Photos/RAWs/…, Photos/Film Scans/…) whose
          canonical row already exists. Deleted, after migrating any
          keyword / import-batch links to the canonical row.
  stale — the only row for its file, recorded under a retired spelling.
          The path is updated in place; the file is not touched.

No file is ever moved, renamed, or deleted. Rows whose file cannot be
verified on the drive, whose twin disagrees on size, that carry virtual
copies, or that are referenced by any table this script does not know
(hub Elo history, for example) are reported and left alone.

Run against the laptop catalog now; run the same script against the hub's
catalog at reconciliation, before its service scans again:

    python scripts/repair_raws_catalog.py \
        --db "C:/Azimuth Photo/data/catalog/azimuth.db" --drive "E:/Photos"
    …same command with --apply to commit the repair.
"""

from __future__ import annotations

import argparse
import re
import sqlite3
from pathlib import Path

CATALOG_ROOT = "/mnt/expansion/Photos/"

# Retired spelling -> canonical spelling, applied to the catalog path string.
REWRITES = (
    ("RAWS/", "Raws/"),
    ("RAWs/", "Raws/Digital/"),
    ("Film Scans/", "Raws/Film Scans/"),
)
BARE_YEAR = re.compile(r"^Raws/((?:19|20)\d{2})/")  # Raws/<year>/ -> Raws/Digital/<year>/

CANONICAL_TREES = (
    "Raws/Digital/", "Raws/Film Scans/", "Snapshots/", "Edits/", "Astrophotography/",
)

# Ghost references that carry user data: migrated to the twin before deletion.
MIGRATE = {"image_keywords": "keyword_id", "import_batch_images": "batch_id"}
# Ghost references that are derived state: deleted with the row.
DERIVED = {
    "sync_state", "image_shoot_hints", "cache_entries",
    "cache_image_presence", "face_scan_backlog",
}


def canonical_path(tail: str) -> str | None:
    for retired, fixed in REWRITES:
        if tail.startswith(retired):
            return fixed + tail[len(retired):]
    if match := BARE_YEAR.match(tail):
        return f"Raws/Digital/{match.group(1)}/" + tail[match.end():]
    return None


def image_ref_tables(conn: sqlite3.Connection) -> list[str]:
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    return [
        t for t in tables
        if t != "images"
        and any(c[1] == "image_id" for c in conn.execute(f"PRAGMA table_info({t})"))
    ]


def classify(conn: sqlite3.Connection, drive: Path, ref_tables: list[str]):
    ghosts, stales, reports = [], [], []
    rows = conn.execute(
        "SELECT id, filepath, file_size FROM images WHERE filepath GLOB ? "
        + "".join(" AND NOT filepath GLOB ?" for _ in CANONICAL_TREES),
        (CATALOG_ROOT + "*", *(CATALOG_ROOT + tree + "*" for tree in CANONICAL_TREES)),
    ).fetchall()
    for image_id, filepath, file_size in rows:
        tail = filepath[len(CATALOG_ROOT):]
        fixed = canonical_path(tail)
        if fixed is None:
            reports.append((image_id, tail, "no rewrite rule"))
            continue
        target = drive / fixed
        if not (target.is_file() and target.stat().st_size == (file_size or -1)):
            reports.append((image_id, tail, "file not verified at canonical path"))
            continue
        if conn.execute("SELECT 1 FROM images WHERE vc_of = ?", (image_id,)).fetchone():
            reports.append((image_id, tail, "has virtual copies"))
            continue
        twin = conn.execute(
            "SELECT id, file_size FROM images WHERE filepath = ? AND vc_of IS NULL",
            (CATALOG_ROOT + fixed,),
        ).fetchone()
        if twin is None:
            stales.append((image_id, tail, fixed))
            continue
        if twin[1] != file_size:
            reports.append((image_id, tail, f"twin #{twin[0]} size differs"))
            continue
        unknown = [
            t for t in ref_tables
            if t not in MIGRATE and t not in DERIVED
            and conn.execute(f"SELECT 1 FROM {t} WHERE image_id = ?", (image_id,)).fetchone()
        ]
        if unknown:
            reports.append((image_id, tail, f"referenced by {', '.join(unknown)}"))
            continue
        ghosts.append((image_id, tail, twin[0]))
    return ghosts, stales, reports


def apply(conn: sqlite3.Connection, ghosts, stales) -> None:
    cur = conn.cursor()
    cur.execute("BEGIN IMMEDIATE")
    for ghost_id, _tail, twin_id in ghosts:
        for table, payload in MIGRATE.items():
            cur.execute(
                f"INSERT OR IGNORE INTO {table} (image_id, {payload}) "
                f"SELECT ?, {payload} FROM {table} WHERE image_id = ?",
                (twin_id, ghost_id),
            )
            cur.execute(f"DELETE FROM {table} WHERE image_id = ?", (ghost_id,))
        for table in DERIVED:
            cur.execute(f"DELETE FROM {table} WHERE image_id = ?", (ghost_id,))
        cur.execute("DELETE FROM images WHERE id = ?", (ghost_id,))
        assert cur.rowcount == 1, f"ghost #{ghost_id} vanished mid-repair"
    for image_id, _tail, fixed in stales:
        cur.execute(
            "UPDATE images SET filepath = ?, missing_at = NULL WHERE id = ?",
            (CATALOG_ROOT + fixed, image_id),
        )
        assert cur.rowcount == 1, f"stale #{image_id} vanished mid-repair"
    conn.commit()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", required=True, help="catalog database path")
    parser.add_argument("--drive", required=True, help="archive root holding Photos/ trees, e.g. E:/Photos")
    parser.add_argument("--apply", action="store_true", help="commit the repair (default: dry run)")
    args = parser.parse_args()

    conn = sqlite3.connect(args.db, timeout=30)
    try:
        ref_tables = image_ref_tables(conn)
        # Two stale rows can name the same canonical path (a RAWS/ and a RAWs/
        # double of one file). Repairing the first turns the second into an
        # ordinary ghost, so apply runs classify-repair passes to a fixpoint
        # instead of special-casing the collision.
        for round_number in (1, 2, 3):
            ghosts, stales, reports = classify(conn, Path(args.drive), ref_tables)
            claimed: set[str] = set()
            ready, deferred = [], []
            for row in stales:
                (deferred if row[2] in claimed else ready).append(row)
                claimed.add(row[2])
            stales = ready
            for image_id, tail, twin_id in ghosts:
                print(f"ghost  #{image_id}  {tail}  (canonical row #{twin_id})")
            for image_id, tail, fixed in stales:
                print(f"stale  #{image_id}  {tail}  ->  {fixed}")
            if round_number == 1:
                for image_id, tail, reason in reports:
                    print(f"leave  #{image_id}  {tail}  [{reason}]")
            print(
                f"\npass {round_number}: {len(ghosts)} ghosts, {len(stales)} stale paths,"
                f" {len(deferred)} deferred to next pass, {len(reports)} left alone"
            )
            if not args.apply:
                print("dry run — nothing changed; pass --apply to commit")
                return 0
            if not ghosts and not stales:
                break
            apply(conn, ghosts, stales)
        ghosts, stales, _ = classify(conn, Path(args.drive), ref_tables)
        remaining = len(ghosts) + len(stales)
        print(f"applied — {remaining} actionable rows remain")
        return 0 if remaining == 0 else 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
