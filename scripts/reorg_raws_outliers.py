"""File the Raws outliers into the canonical tree — hub-side, atomically.

Sean, 2026-08-14: "please reorganize the raw folder once and for all please
theres still some outliers." Measured that day, the outliers are eleven
originals, all on the archive drive, all referenced by the hub's catalog:

  /mnt/expansion/Photos/Raws/2025/<date>/*        (2)  -> Raws/Digital/2025/<date>/
  /mnt/expansion/Photos/RAWS/Digital/<tail>       (9)  -> Raws/Digital/<tail>

They cannot be moved while the hub is offline: the hub's catalog lives on its
boot disk, so the file move and the catalog repoint cannot happen atomically,
and a half-done reorganization becomes a missing-file storm on reboot. RUN
THIS ON THE HUB (omarchy), with the archive mounted and azimuth-photo.service
stopped:

    python3 scripts/reorg_raws_outliers.py                # dry run, prints the plan
    python3 scripts/reorg_raws_outliers.py --apply        # move + repoint, hash-gated

Per file: destination collision refuses; size+BLAKE2b of the moved copy must
match the source before the old path is released (copy, verify, repoint,
unlink — never a bare rename across checks); each file's move and its
catalog UPDATE commit together. Satellites follow via the ordinary mirror
refresh — filepath is mirrored state, identity (content_hash) never changes.
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import sqlite3
import sys
from pathlib import Path

HUB_DB = Path.home() / "Projects" / "photo-archive" / "web" / "azimuth.db"
ARCHIVE = Path("/mnt/expansion/Photos")

OUTLIER_QUERIES = (
    # Bare date folders directly under Raws/ — belong under Raws/Digital/.
    (
        "SELECT id, filepath FROM images WHERE filepath GLOB '/mnt/expansion/Photos/Raws/2025/*' "
        "AND filepath NOT GLOB '/mnt/expansion/Photos/Raws/Digital/*' "
        "AND filepath NOT GLOB '/mnt/expansion/Photos/Raws/Film Scans/*' AND missing_at IS NULL",
        lambda tail: "Raws/Digital/" + tail.removeprefix("Raws/"),
    ),
    # Legacy uppercase RAWS shelf — same tail under the canonical casing.
    (
        "SELECT id, filepath FROM images WHERE filepath GLOB '/mnt/expansion/Photos/RAWS/Digital/*' "
        "AND missing_at IS NULL",
        lambda tail: "Raws/" + tail.removeprefix("RAWS/"),
    ),
)


def _digest(path: Path) -> str:
    h = hashlib.blake2b(digest_size=16)
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="move files and repoint the catalog")
    parser.add_argument("--db", type=Path, default=HUB_DB)
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    plan: list[tuple[int, Path, Path]] = []
    for query, retarget in OUTLIER_QUERIES:
        for row in conn.execute(query).fetchall():
            source = Path(row["filepath"])
            tail = str(source.relative_to(ARCHIVE))
            plan.append((int(row["id"]), source, ARCHIVE / retarget(tail)))

    if not plan:
        print("No outliers — the tree is canonical.")
        return 0
    for image_id, source, destination in plan:
        marker = "" if source.is_file() else "  [SOURCE MISSING]"
        print(f"  {source}\n    -> {destination}{marker}")
    if not args.apply:
        print(f"\nDry run: {len(plan)} file(s). Re-run with --apply on the hub, service stopped.")
        return 0

    moved = 0
    for image_id, source, destination in plan:
        if not source.is_file():
            print(f"skip (missing): {source}")
            continue
        if destination.exists():
            print(f"REFUSED (collision): {destination}")
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        if _digest(destination) != _digest(source):
            destination.unlink(missing_ok=True)
            print(f"REFUSED (verify failed): {source}")
            continue
        conn.execute(
            "UPDATE images SET filepath = ? WHERE id = ? AND filepath = ?",
            (str(destination), image_id, str(source)),
        )
        conn.commit()
        source.unlink()
        moved += 1
        print(f"moved: {source.name} -> {destination.parent}")
    print(f"\n{moved}/{len(plan)} moved. Restart the service; satellites follow on mirror refresh.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
