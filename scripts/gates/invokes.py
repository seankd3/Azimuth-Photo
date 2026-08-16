"""A build may not name a file that is not there.

`.github/workflows/ci.yml` has run `python scripts/bench.py --check`, and
`release.yml` has run `python scripts/build_server.py`, since `0c259f93`
deleted both on 2026-08-16 — 66 commits ago as of `c9f253c9`. Neither job can
have passed once in that window, and nothing said so, because the workflow has
never run at all: `gh api repos/:owner/:repo/actions/runs --jq .total_count`
returns 0.

Every repository-relative path a workflow names, in a command or in a comment,
must be a tracked file. Measured 2026-08-16: **4** — the two scripts above and
`web/perf/standing.py` and `web/perf/baseline.json`, which the perf job's
comment describes in detail and which `9670f11f` deleted on 2026-08-14.
"""

from __future__ import annotations

import re

from common import read, tracked

TOP = ("scripts/", "web/", "clients/", "android/", "desktop/", "tools/", "docs/", "bench/")
PATH = re.compile(r"[\w][\w./-]*\.(?:py|sh|ps1|js|json|txt|sql|toml|lua)\b")


def run(root) -> list[str]:
    present = set(tracked(root))
    found = []
    for path in tracked(root, ".github/*"):
        for number, line in enumerate(read(root, path).splitlines(), 1):
            for named in PATH.findall(line):
                if named.startswith(TOP) and named not in present:
                    found.append(f"{path}:{number}: {named}")
    return found
