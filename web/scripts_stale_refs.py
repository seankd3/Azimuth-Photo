"""Find test references to names that no longer exist.

A rename passes ruff, passes import, and fails at the moment the test runs —
sometimes several commits later, looking like something else. This reads the
tests without running them and asks the real modules whether each name is there.

    cd web && ./.venv/Scripts/python.exe scripts_stale_refs.py

Two shapes, both of which bit this branch:

    patch.object(features.ai.routes, "_configured")   # gone with the guard
    library_service._resolve_library_constraints      # renamed

Exit code is the count, so it can gate a commit.
"""

from __future__ import annotations

import ast
import importlib
import pathlib
import sys
import warnings

warnings.filterwarnings("ignore")
sys.path.insert(0, ".")

import harness.env as harness_env

harness_env.apply()

_MODULES: dict[str, object] = {}


def _load(dotted: str):
    if dotted not in _MODULES:
        try:
            _MODULES[dotted] = importlib.import_module(dotted)
        except Exception:
            _MODULES[dotted] = None
    return _MODULES[dotted]


def _aliases(tree: ast.Module) -> dict[str, str]:
    found: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for name in node.names:
                found[name.asname or name.name] = f"{node.module}.{name.name}"
        elif isinstance(node, ast.Import):
            for name in node.names:
                found[name.asname or name.name] = name.name
    return found


def _resolve(dotted: str):
    """Import the longest importable prefix, then walk the rest as attributes."""
    root = dotted
    while root:
        module = _load(root)
        if module is not None:
            break
        if "." not in root:
            return None
        root = root.rpartition(".")[0]
    else:
        return None
    target = module
    for piece in dotted[len(root) :].strip(".").split("."):
        if not piece:
            continue
        target = getattr(target, piece, None)
        if target is None:
            return None
    return target


def _patch_targets(tree: ast.Module, aliases: dict[str, str]):
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "object"
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and isinstance(node.args[1].value, str)
        ):
            continue
        parts: list[str] = []
        cursor = node.args[0]
        while isinstance(cursor, ast.Attribute):
            parts.append(cursor.attr)
            cursor = cursor.value
        if not isinstance(cursor, ast.Name) or cursor.id not in aliases:
            continue
        parts.append(aliases[cursor.id])
        yield node.lineno, ".".join(reversed(parts)), node.args[1].value


def _module_attributes(tree: ast.Module, aliases: dict[str, str]):
    # A name assigned in the file shadows the import; skip those.
    assigned = {
        target.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)):
            continue
        # An assignment creates the attribute, so its absence proves nothing.
        if not isinstance(node.ctx, ast.Load):
            continue
        if not node.attr.startswith("_") or node.attr.startswith("__"):
            continue
        base = node.value.id
        if base in assigned or base not in aliases:
            continue
        yield node.lineno, aliases[base], node.attr


def main() -> int:
    stale: list[tuple[str, int, str, str]] = []
    for path in sorted(pathlib.Path(".").glob("test_*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue
        aliases = _aliases(tree)
        for finder in (_patch_targets, _module_attributes):
            for line, dotted, attribute in finder(tree, aliases):
                owner = _resolve(dotted)
                if owner is None or not hasattr(owner, "__file__"):
                    continue
                if not hasattr(owner, attribute):
                    stale.append((str(path), line, dotted, attribute))

    reported: set[tuple[str, str, str]] = set()
    for path, line, dotted, attribute in stale:
        key = (path, dotted, attribute)
        if key in reported:
            continue
        reported.add(key)
        print(f"{path}:{line}  {dotted} has no {attribute!r}")
    print(f"{len(reported)} stale reference(s)")
    return len(reported)


if __name__ == "__main__":
    raise SystemExit(main())
