"""A name that is called is a name that exists.

Python resolves a name when the line runs, not when the file loads. So a call
to something that was never imported compiles, imports, and passes a test suite
that does not happen to take that branch — and then raises `NameError` in front
of a user. Every one of these was found by booting the app against a real
catalog, never by the 758-test suite:

    background.py     `_catalog_path` and `app_catalog_path`, 7 sites, defined
                      nowhere. All inside lambdas handed to background daemons,
                      so nothing resolved them until 15 s after boot, inside a
                      task whose exception is logged rather than raised. The
                      daily catalog backup, the nightly cloud backup, the
                      watched-folder poller, the reconcile worker, collection
                      suggestions and the stored-star refresh were all dead.
    export/routes.py  `cache_entries`, deleted with thumbnails/ in 6fc7e31c.
    imports/staging   `generation`, same commit — every import-canvas preview.
    develop/routes.py `cursor`, in the snapshot response.
    db.py             `people_repository`, whose module went in 4b1151c9 and
                      left nine functions calling it.

The comment in `imports/staging.py` had *twice* warned that this route breaks
on stale calls into moved helpers. It broke anyway. Prose does not hold an
invariant; this does.

pyflakes rather than a hand-rolled scope walk: getting Python's scoping right
(comprehensions, class bodies, walrus, globals, star imports) is a project, and
a checker that is subtly wrong is worse than none — it certifies. Only
git-tracked sources are scanned, so no virtualenv is ever in the count.
"""

from __future__ import annotations

import subprocess
import sys

from common import tracked


def run(root) -> list[str]:
    paths = list(tracked(root, "web/*.py", "web/**/*.py", "scripts/**/*.py", tests=False))
    if not paths:
        return []
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pyflakes", *paths],
            cwd=root,
            capture_output=True,
            text=True,
        )
    except OSError as error:  # pyflakes absent: say so, never pass quietly
        return [f"names: cannot run pyflakes ({error})"]
    if result.returncode and not result.stdout:
        return [f"names: pyflakes failed -- {result.stderr.strip()[:200]}"]
    return [line for line in result.stdout.splitlines() if "undefined name '" in line]
