#!/usr/bin/env python3
"""CLI: re-point catalog rows at archive files the owner renamed on disk.

Dry-run by default — prints what would change and touches nothing. Pass
--apply to rewrite proven matches. Photo files are never moved, renamed,
or deleted; unmatched rows are reported, never deleted.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
for candidate in (WEB / ".venv" / "bin" / "python", WEB / ".venv" / "Scripts" / "python.exe"):
    if candidate.is_file() and Path(sys.executable).resolve() != candidate.resolve():
        os.execv(str(candidate), [str(candidate), __file__, *sys.argv[1:]])

sys.path.insert(0, str(WEB))

from features.imports.relocation import relocate_catalog


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Repair catalog filepaths after a manual archive rename/reshuffle.",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=None,
        help="Catalog database (default: the runtime catalog path)",
    )
    parser.add_argument(
        "--library",
        type=Path,
        default=None,
        help="Library root holding Edits/ Raws/ Snapshots/ (default: configured library root)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Rewrite proven matches (default: dry-run, report only)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the full report as JSON",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.db is None:
        import db as catalog_db

        args.db = Path(catalog_db.DB_PATH)
    if args.library is None:
        from features.sync import hub as sync_hub

        args.library = sync_hub.default_library_root()

    report = asyncio.run(
        relocate_catalog(str(args.db), args.library, apply=args.apply)
    )
    if args.json:
        print(json.dumps(report, indent=2))
        return 0

    mode = "APPLIED" if not report["dry_run"] else "DRY-RUN (pass --apply to rewrite)"
    print(f"{mode} — library {report['library_root']}")
    print(
        f"rows: {report['rows_scanned']} scanned, "
        f"{report['rows_missing_file']} missing their file"
    )
    print(
        f"matched: {report['relocated']} relocations + {report['merged']} merges "
        f"({report['matched_by']})"
    )
    for action in report["actions"]:
        retired = (
            f" (retires duplicate row {action['retired_image_id']})"
            if action["action"] == "merge"
            else ""
        )
        print(f"  [{action['matched_by']}] #{action['image_id']} -> {action['to']}{retired}")
    if report["actions_total"] > len(report["actions"]):
        print(f"  … {report['actions_total'] - len(report['actions'])} more")
    print(f"unmatched (reported, never deleted): {report['unmatched_total']}")
    for row in report["unmatched"]:
        print(f"  #{row['id']} {row['filepath']} — {row['reason']}")
    if report["unmatched_total"] > len(report["unmatched"]):
        print(f"  … {report['unmatched_total'] - len(report['unmatched'])} more")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
