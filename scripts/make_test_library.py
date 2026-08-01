#!/usr/bin/env python3
"""Build a small archive on fast local disk to develop against.

Working against the real library means every check waits on a spinning disk
holding 150,000 photos, so a single verification costs most of an hour. This
copies a few hundred real photos into the same three-root shape on the SSD.
Everything the app does to the real archive it does here in seconds.

    python scripts/make_test_library.py            # build it
    python scripts/make_test_library.py --reset    # throw it away and rebuild

Source photos are copied, never moved: the originals are left untouched.
"""
from __future__ import annotations

import argparse
import os
import random
import shutil
import sys
from pathlib import Path

SOURCE = Path(r"C:\Pictures")
LIBRARY = Path(r"C:\Azimuth Test\Photos")
ROOTS = ("Edits", "Raws/Digital", "Snapshots")
RAW_EXTENSIONS = {".dng", ".cr3", ".cr2", ".nef", ".arw", ".orf", ".raf", ".rw2"}
JPEG_EXTENSIONS = {".jpg", ".jpeg", ".heic", ".png", ".tif", ".tiff"}
# Big enough to exercise grids, folders and ranking; small enough to rebuild
# in under a minute.
WANTED = {"Edits": 120, "Raws/Digital": 40, "Snapshots": 120}
MAX_BYTES = {"Edits": 40_000_000, "Raws/Digital": 60_000_000, "Snapshots": 40_000_000}


def candidates() -> tuple[list[Path], list[Path]]:
    raws: list[Path] = []
    jpegs: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(SOURCE):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for name in filenames:
            suffix = Path(name).suffix.lower()
            if suffix in RAW_EXTENSIONS:
                raws.append(Path(dirpath) / name)
            elif suffix in JPEG_EXTENSIONS:
                jpegs.append(Path(dirpath) / name)
        if len(raws) > 4000 and len(jpegs) > 8000:
            break  # plenty to sample from; do not walk half a terabyte
    return raws, jpegs


def pick(pool: list[Path], count: int, max_bytes: int, taken: set[Path]) -> list[Path]:
    chosen: list[Path] = []
    for path in pool:
        if len(chosen) >= count:
            break
        if path in taken:
            continue
        try:
            if path.stat().st_size > max_bytes:
                continue
        except OSError:
            continue
        taken.add(path)
        chosen.append(path)
    return chosen


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reset", action="store_true", help="delete the test archive first")
    args = parser.parse_args()

    if not SOURCE.is_dir():
        print(f"no source photos at {SOURCE}", file=sys.stderr)
        return 1
    if args.reset and LIBRARY.exists():
        shutil.rmtree(LIBRARY)
        print(f"removed {LIBRARY}")

    print(f"scanning {SOURCE} for photos to copy...")
    raws, jpegs = candidates()
    random.Random(7).shuffle(raws)   # fixed seed: the same fixture every time
    random.Random(7).shuffle(jpegs)
    print(f"  found {len(raws)} raws and {len(jpegs)} jpegs to choose from")

    taken: set[Path] = set()
    plan: dict[str, list[Path]] = {}
    for root in ("Edits", "Raws/Digital", "Snapshots"):
        pool = raws if root == "Raws/Digital" else jpegs
        chosen = pick(pool, WANTED[root], MAX_BYTES[root], taken)
        if len(chosen) < WANTED[root]:
            # This source is nearly all RAW, so top up from it rather than
            # leave a root empty — a phone RAW filed under Snapshots is exactly
            # the case the taxonomy has to get right anyway.
            chosen += pick(raws, WANTED[root] - len(chosen), MAX_BYTES["Raws/Digital"], taken)
        plan[root] = chosen

    copied = 0
    total_bytes = 0
    for root, files in plan.items():
        for index, source_path in enumerate(files):
            # Date folders are what the real archive looks like; derive one from
            # the file's own timestamp so browsing by date has something to show.
            stamp = source_path.stat().st_mtime
            import datetime
            day = datetime.datetime.fromtimestamp(stamp).strftime("%Y/%Y-%m-%d")
            destination = LIBRARY / root / day / source_path.name
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                continue
            shutil.copy2(source_path, destination)
            copied += 1
            total_bytes += destination.stat().st_size
        print(f"  {root}: {len(files)} photos")

    print(f"\ncopied {copied} photos ({total_bytes/1e9:.2f} GB) into {LIBRARY}")
    for root in ROOTS:
        count = sum(1 for _ in (LIBRARY / root).rglob("*") if _.is_file())
        print(f"  {root:<16} {count}")
    print("\nrun the app against it with:")
    print(r"  python scripts/run_test_app.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
