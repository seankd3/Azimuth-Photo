"""Module-level names that are assigned and never read.

"Is it referenced?" and "is it read?" are different questions, and the first one
misses this whole class. `DbPathProvider` appeared eighteen times in grep and was
used nowhere: every appearance was another module declaring its own copy of an
alias for a parameter that no longer exists. The same shape hid a paging cursor
that was reset on every config change and read by nothing, an injection slot
nothing ever filled, and a private alias of a public function.

They all look alive — a name, a type annotation, a parameter slot, sometimes a
test asserting the reset happens.

    cd web && ./.venv/Scripts/python.exe scripts_dead_state.py

**Advisory, not a gate.** A name reached through `globals()`, `getattr`, or a
string cannot be seen here, so a hit is a question, not a verdict. Check each
one before deleting it — `_rss_reader` and friends are read through `global`
declarations and are not dead.
"""

from __future__ import annotations

import ast
import collections
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent
SEARCHED = ("core", "features", "data", "thumbnails", "archive", "photo", "pixels")


def _sources() -> dict[pathlib.Path, str]:
    paths: list[pathlib.Path] = []
    for name in SEARCHED:
        paths += sorted((ROOT / name).rglob("*.py"))
    paths += sorted(p for p in ROOT.glob("*.py") if not p.name.startswith("test_"))
    out = {}
    for path in paths:
        try:
            out[path] = path.read_text(encoding="utf-8")
        except OSError:
            continue
    return out


def _module_level_names(tree: ast.Module) -> set[str]:
    """Private module-level assignments — the ones injection tends to leave."""
    names = set()
    for node in tree.body:
        targets = []
        if isinstance(node, ast.Assign):
            targets = [t for t in node.targets if isinstance(t, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            targets = [node.target]
        for target in targets:
            if target.id.startswith("_") and not target.id.startswith("__"):
                names.add(target.id)
    return names


def main() -> int:
    sources = _sources()
    trees: dict[pathlib.Path, ast.Module] = {}
    for path, text in sources.items():
        try:
            trees[path] = ast.parse(text)
        except SyntaxError:
            continue

    # A read is a Load of the bare name, or of the attribute on a module object.
    reads: collections.Counter[str] = collections.Counter()
    for tree in trees.values():
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                reads[node.id] += 1
            elif isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load):
                reads[node.attr] += 1

    dead: list[tuple[str, str]] = []
    for path, tree in trees.items():
        for name in _module_level_names(tree):
            if not reads[name]:
                dead.append((path.relative_to(ROOT).as_posix(), name))

    for where, name in sorted(dead):
        print(f"{where}: {name}")
    print(f"{len(dead)} module-level name(s) assigned but never read")
    return 0  # advisory: never fails a build


if __name__ == "__main__":
    raise SystemExit(main())
