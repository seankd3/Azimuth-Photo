"""Report code files that have not passed the V2 rewrite gate."""

from __future__ import annotations

import argparse
import re
import subprocess
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "docs" / "REWRITE_LEDGER.md"
REGISTERED_ROW = re.compile(
    r"^\| `(?P<path>[^`]+)` \| (?P<status>Rebuilt|Proven|Removed) \|"
)

CODE_SUFFIXES = {
    ".css",
    ".html",
    ".js",
    ".json",
    ".kt",
    ".kts",
    ".lua",
    ".mjs",
    ".pro",
    ".ps1",
    ".py",
    ".rs",
    ".sql",
    ".toml",
    ".webmanifest",
    ".xml",
    ".yaml",
    ".yml",
}


def tracked_files() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    return [
        path
        for path in result.stdout.decode().split("\0")
        if path and (ROOT / path).is_file()
    ]


def is_code_file(path: str) -> bool:
    file = Path(path)

    if path.startswith("web/"):
        return file.suffix in CODE_SUFFIXES

    if path.startswith("android/"):
        return file.suffix in CODE_SUFFIXES

    if path.startswith("clients/"):
        return file.suffix in CODE_SUFFIXES

    if path.startswith("desktop/"):
        return file.suffix in CODE_SUFFIXES and "/icons/" not in path

    if path.startswith("scripts/"):
        return file.suffix in CODE_SUFFIXES or not file.suffix

    if path.startswith(".github/workflows/"):
        return file.suffix in CODE_SUFFIXES

    return path in {"eslint.config.js", "package.json"}


def registered_files() -> dict[str, str]:
    registered: dict[str, str] = {}
    for line in LEDGER.read_text(encoding="utf-8").splitlines():
        match = REGISTERED_ROW.match(line)
        if match:
            registered[match["path"]] = match["status"]
    return registered


def area(path: str) -> str:
    parts = Path(path).parts
    if parts[0] == "web" and len(parts) > 2:
        return "/".join(parts[:2])
    return parts[0]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--all", action="store_true", help="list every legacy file")
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail until every production file is Proven or Removed",
    )
    args = parser.parse_args()

    code = set(filter(is_code_file, tracked_files()))
    registered = registered_files()
    active = {path for path, status in registered.items() if status != "Removed"}
    removed = {path for path, status in registered.items() if status == "Removed"}
    stale = sorted(active - code)
    undeleted = sorted(removed & code)
    remaining = sorted(code - active)
    rebuilt = sorted(path for path, status in registered.items() if status == "Rebuilt")

    print(f"Code files: {len(code)}")
    print(f"Rebuilt: {len(rebuilt)}")
    print(f"Proven: {sum(status == 'Proven' for status in registered.values())}")
    print(f"Removed: {sum(status == 'Removed' for status in registered.values())}")
    print(f"Legacy: {len(remaining)}")

    if remaining:
        print("\nLegacy by area:")
        for name, count in sorted(Counter(map(area, remaining)).items()):
            print(f"  {name}: {count}")
    if args.all and remaining:
        print("\nLegacy files:")
        for path in remaining:
            print(f"  {path}")
    if stale:
        print("\nInvalid ledger entries:")
        for path in stale:
            print(f"  {path}")

    if undeleted:
        print("\nFiles marked Removed but still present:")
        for path in undeleted:
            print(f"  {path}")

    return 1 if stale or undeleted or (args.check and (remaining or rebuilt)) else 0


if __name__ == "__main__":
    raise SystemExit(main())
