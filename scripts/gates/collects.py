"""The suite can be collected.

A test module that fails to *import* does not fail — it disappears, and takes
every test in the file with it. `pytest -q` still prints a green-looking tally
of whatever survived, so the suite reports success while measuring nothing.

This tree has lived through that: **32 commits with the suite dark**, ended by
42f89a1e. Three more times in one afternoon a deletion left an import behind in
a test file and 14 or 15 modules stopped collecting at once. Every occurrence
was the same shape — a module deleted, a `from core import <gone>` left in
`test_support.py`, which every other test imports.

The other gates count things that should stay at zero. This one counts the same
way and is the cheapest of the lot to justify: without it, no other test result
means anything.

`--collect-only` imports every test module and builds the item list without
running a single assertion, so it costs about a second and cannot be affected by
a slow or flaky test.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys

COUNT = re.compile(r"(\d+) errors? during collection")


def _project_python(root) -> str:
    """The interpreter that can import this project, not whichever ran the gates.

    `check.py` is stdlib-only and runs under whatever `python` is on PATH. That
    interpreter usually cannot import fastapi or numpy, and a gate that reports
    63 collection errors because it is looking through the wrong interpreter is
    worse than no gate — it is a false alarm that teaches people to ignore it.
    """

    for candidate in (root / "web" / ".venv", root / ".venv"):
        for relative in ("Scripts/python.exe", "bin/python"):
            path = candidate / relative
            if os.path.exists(path):
                return str(path)
    return sys.executable


def run(root) -> list[str]:
    result = subprocess.run(
        [_project_python(root), "-m", "pytest", "-q", "--collect-only", "-p", "no:cacheprovider"],
        cwd=root,
        capture_output=True,
        text=True,
    )
    if "no tests ran" in result.stdout or "collected" not in result.stdout:
        if not COUNT.search(result.stdout):
            return ["collects: pytest could not run -- " + (result.stderr.strip() or result.stdout.strip())[:200]]
    return [
        line.strip()
        for line in result.stdout.splitlines()
        if line.startswith("ERROR ") or "errors during collection" in line
    ]
