"""What every gate needs: the tracked files, and their text.

Only tracked files are measured. A gate that reads the working directory
measures a machine, and a build cannot fail on a file nobody else has.
"""

from __future__ import annotations

import re
import subprocess

TEST = re.compile(r"(^|/)(test_|conftest)")


def tracked(root, *pathspecs: str, tests: bool = True) -> list[str]:
    out = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z", "--", *pathspecs],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    files = [p.replace("\\", "/") for p in out.split("\0") if p]
    return files if tests else [p for p in files if not TEST.search(p)]


def read(root, path: str) -> str:
    return (root / path).read_text(encoding="utf-8", errors="replace")
