"""A build may not name a file that is not there.

Every repository-relative path a workflow names, in a command or comment, must
be tracked. The V2 release workflow satisfies this rule; the inherited CI
benchmark references remain counted debt until that workflow is rebuilt.
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
