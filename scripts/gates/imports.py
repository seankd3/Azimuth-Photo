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
import subprocess

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


def _ours_once(root) -> set[str]:
    """Module names that have ever been a file under `web/`.

    The gap this closes: a module that is deleted *entirely* leaves no trace in
    `_first_party`, so its name is no longer a known root and `import
    thumbnails` reads exactly like `import numpy` -- third-party, allowed to be
    absent. The gate passed while `features/develop/rawproc.py` and
    `features/develop/routes.py` both imported a module deleted in d52cf6c7,
    which is to say every develop save and every RAW open on a missing original
    raised ModuleNotFoundError, and 26 tests failed on it.

    History is the instrument that distinguishes the two cases, the same way it
    distinguishes dead code from an unwired capability. If a file by that name
    was ever ours, an import of it is ours to answer for.
    """

    out = subprocess.run(
        ["git", "log", "--diff-filter=D", "--name-only", "--format=", "--", "web"],
        cwd=root, capture_output=True, text=True, check=False,
    ).stdout
    names = set()
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("web/") and line.endswith(".py"):
            dotted = line[len("web/"):].removesuffix(".py").replace("/", ".")
            names.add(dotted)
            names.add(dotted.rsplit(".", 1)[-1])
    return names


def _bare_packages(root) -> set[str]:
    """Our packages whose `__init__` re-exports nothing.

    A name imported from one of these can only be a submodule, which makes it
    checkable. A package that *does* re-export is not, without importing it —
    and importing to check is how a checker acquires side effects.
    """

    bare = set()
    for path in tracked(root, "web/**/__init__.py", tests=False):
        body = ast.parse(read(root, path)).body
        if all(isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant) for n in body):
            bare.add(path[len("web/"):].removesuffix("/__init__.py").replace("/", "."))
    return bare


def run(root) -> list[str]:
    known = _first_party(root)
    bare_packages = _bare_packages(root)
    roots = {name.split(".")[0] for name in known}
    deleted = {name.split(".")[0] for name in _ours_once(root)} - roots
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
                if name in known:
                    continue
                if name.split(".")[0] not in roots and name.split(".")[0] not in deleted:
                    continue  # third-party, and allowed to be absent
                try:
                    if importlib.util.find_spec(name.split(".")[0]) is not None:
                        continue  # a real package that shares a name with ours
                except Exception:
                    pass
                found.append(f"{path}:{node.lineno}: import {name} -- no such module")

            # `from <our package> import name` — the module resolves, the name
            # may not. `data/repositories/__init__.py` re-exports nothing, so a
            # name imported from it can only be a submodule, and
            # `from data.repositories import ratings` after ratings.py is
            # deleted names a file that is not there. This is the gap that let a
            # deletion take fourteen test modules down before `collects` caught
            # it; catching the import itself says which line to fix.
            if isinstance(node, ast.ImportFrom) and node.module in bare_packages:
                for alias in node.names:
                    if alias.name != "*" and f"{node.module}.{alias.name}" not in known:
                        found.append(
                            f"{path}:{node.lineno}: from {node.module} import {alias.name}"
                            " -- no such module in that package"
                        )
    return sorted(set(found))
