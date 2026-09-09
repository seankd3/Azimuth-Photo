"""The guides an agent must read may not name a path that is not there.

`AGENTS.md` once assigned preview generation to `web/thumbnails/` for a day
after that folder was deleted. A wrong map is worse than no map.

A path is anything in backticks that starts with a tracked top-level folder,
which is how both guides already write one. Git supplies that list, so there is
nothing here to keep in sync. Other documents are not read: their dead paths are
a tidy-up, not a gate.
"""

from __future__ import annotations

import re

from common import read, tracked

GUIDES = ("AGENTS.md", ".claude/skills/azimuth-orient/SKILL.md",
          "docs/README.md", "docs/development.md", "docs/INSTALL.md", "CONTRIBUTING.md")
QUOTED = re.compile(r"`([\w][\w./-]*/[\w./-]*)`")


def run(root) -> list[str]:
    present = set(tracked(root))
    present |= {p[:i] + "/" for p in list(present) for i, c in enumerate(p) if c == "/"}
    repo = tuple(sorted({p.split("/")[0] + "/" for p in present if "/" in p}))
    return [
        f"{guide}:{number}: {named}"
        for guide in GUIDES
        for number, line in enumerate(read(root, guide).splitlines(), 1)
        for named in QUOTED.findall(line)
        if named.startswith(repo) and named not in present
    ]
