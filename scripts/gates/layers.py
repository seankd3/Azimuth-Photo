"""Imports point down.

`model` is under `data`, `core`, `photo`, `pixels`; those are under `features`;
`features` is under `api` and `app`. An import that points the other way is a
lower layer that cannot be read, tested or deleted without the layer above it.

Every one of these is written inside a function body, not at the top of a file,
so a `^import` grep reports zero and misses all of them. Measured 2026-08-16:
**19 upward imports into `features`, 2 upward imports into `api`**.
"""

from __future__ import annotations

import re

from common import read, tracked

BELOW = ("web/data/*.py", "web/core/*.py", "web/model/*.py", "web/photo/*.py", "web/pixels/*.py")
UP = re.compile(r"^[ \t]*(?:from|import) +features\b|importlib\.import_module\(\s*[\"']features")
UP_API = re.compile(r"^[ \t]*(?:from|import) +(?:api|app)\b|importlib\.import_module\(\s*[\"'](?:api|app)")

# The old data layer, which `model/` replaces. `core/` reaching into it is the
# same backwards dependency as the rest, and the gate could not see it: for
# months `core/catalog_path.py` — whose entire job is to answer "where is the
# catalog" — answered by importing `db`, so 204 call sites that wanted a string
# had to load 1,077 lines of repositories to get one. A rule that only knew
# about `features` reported zero.
OLD = re.compile(r"^[ \t]*(?:from +(?:db|data\.repositories)\b|import +db\b)")


def _hits(root, pathspecs, pattern, skip=()):
    found = []
    for path in tracked(root, *pathspecs, tests=False):
        if path in skip:
            continue
        for number, line in enumerate(read(root, path).splitlines(), 1):
            if pattern.search(line):
                found.append(f"{path}:{number}: {line.strip()}")
    return found


def run(root) -> list[str]:
    return (
        _hits(root, BELOW, UP)
        + _hits(root, ("web/*.py",), UP_API, skip=("web/app.py", "web/server_entry.py"))
        + _hits(root, ("web/core/*.py", "web/model/*.py", "web/photo/*.py", "web/pixels/*.py"), OLD)
    )
