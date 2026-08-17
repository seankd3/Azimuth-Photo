"""A first-party import names a file that is there.

`names` catches a symbol nothing defines. It cannot catch `import X` where `X`
is a module that no longer exists, because pyflakes does not resolve imports —
and neither does Python, until the line runs.

Four of those were live in this tree on 2026-08-17, every one left behind by a
sweep that deleted the module and not its importers:

    features/settings/routes.py   `core.intelligence_campaign`, deleted in
                                  10e29ebd. Unguarded, on the `POST /api/settings`
                                  path, so **saving settings raised
                                  ModuleNotFoundError** from that day on.
    features/ai/routes.py         `embedding_worker` and `thumbnails`, both
                                  deleted in 6fc7e31c, guarded — so pause and
                                  resume answered 503 forever instead of failing.
    test_support.py               `core.work_coordination`, deleted the same
                                  afternoon, which took 15 test modules down at
                                  once until `collects` caught it.

Guarded or not, the shape is the same and neither the compiler, the import of
the app, nor a full test run reports it. Only third-party imports may be
missing, because optional packs (torch, psutil, lensfunpy) are genuinely
optional and their absence is a supported state.
"""

from __future__ import annotations

import ast
import importlib.util

from common import read, tracked


def _first_party(root) -> set[str]:
    """Every module a `web/`-rooted import could legitimately name."""

    names: set[str] = set()
    for path in tracked(root, "web/*.py", "web/**/*.py", tests=True):
        dotted = path[len("web/"):].removesuffix(".py").replace("/", ".")
        names.add(dotted)
        if dotted.endswith(".__init__"):
            names.add(dotted.removesuffix(".__init__"))
        names.add(dotted.rsplit(".", 1)[-1])
        # a package is importable by any of its prefixes
        parts = dotted.split(".")
        for depth in range(1, len(parts)):
            names.add(".".join(parts[:depth]))
    return names


def run(root) -> list[str]:
    known = _first_party(root)
    roots = {name.split(".")[0] for name in known}
    found: list[str] = []
    for path in tracked(root, "web/*.py", "web/**/*.py", tests=True):
        try:
            tree = ast.parse(read(root, path))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                targets = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                targets = [node.module]
            else:
                continue
            for name in targets:
                if name in known or name.split(".")[0] not in roots:
                    continue  # resolvable, or third-party and allowed to be absent
                try:
                    if importlib.util.find_spec(name.split(".")[0]) is not None:
                        continue  # a real package that shares a name with ours
                except Exception:
                    pass
                found.append(f"{path}:{node.lineno}: import {name} -- no such module")
    return sorted(set(found))
