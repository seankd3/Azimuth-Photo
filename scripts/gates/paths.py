"""An instruction may not name a path that is not there.

A CI workflow and an agent guide are the same kind of document: both tell
somebody where to work. When one names a file or folder that was deleted, it
does not fail loudly -- it sends the next reader to a place that is not there,
and a wrong map is worse than no map.

So every repository-relative path an instruction names, in a command, a link or
a sentence, must be tracked. Folders count: `web/thumbnails/` had been gone for
a day while `AGENTS.md` still assigned preview generation to it.

**A record is not an instruction.** `MASTER_PLAN.md` preserves the owner's asks
verbatim, `SIMPLIFY_LOG.md` and `REWRITE_LEDGER.md` say what was deleted, and
`docs/archive/` is kept precisely because it is out of date. A dead path in
those is history, and history is allowed to name what no longer exists. They are
excluded by name below, and that exclusion is the gate knowing what it reads --
widen it only for another document that records the past rather than directing
the present.
"""

from __future__ import annotations

import re

from common import read, tracked

# Where product code lives. A path outside these is a URL, a dependency, or
# prose that happens to contain a slash.
TOP = ("scripts/", "web/", "clients/", "android/", "desktop/", "tools/", "docs/", "bench/", ".github/")

INSTRUCTIONS = (
    ".github/*",
    ".claude/skills/*",
    ".claude/workflows/*",
    "AGENTS.md",
    "CLAUDE.md",
    "README.md",
    "CONTRIBUTING.md",
    "docs/*.md",
)

RECORDS = ("MASTER_PLAN.md", "docs/SIMPLIFY_LOG.md", "docs/REWRITE_LEDGER.md", "docs/archive/")

FILE = re.compile(r"[\w][\w./-]*[.](?:py|sh|ps1|js|json|txt|sql|toml|lua|md|css|html|yml|yaml)\b")
FOLDER = re.compile(r"[\w][\w./-]*/")


def run(root) -> list[str]:
    files = set(tracked(root))
    # A folder exists when something tracked lives under it.
    present = set(files)
    for path in files:
        parts = path.split("/")
        for depth in range(1, len(parts)):
            present.add("/".join(parts[:depth]) + "/")

    found = []
    for path in tracked(root, *INSTRUCTIONS):
        if path.startswith(RECORDS):
            continue
        for number, line in enumerate(read(root, path).splitlines(), 1):
            named = set(FILE.findall(line)) | set(FOLDER.findall(line))
            for target in sorted(named):
                if target.startswith(TOP) and target not in present:
                    found.append(f"{path}:{number}: {target}")
    return found
