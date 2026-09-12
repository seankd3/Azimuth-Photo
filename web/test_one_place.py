"""An idea lives in one place, and the build says so.

Fifteen helper bodies were byte-identical across two or more modules when this
test was written. Copies do not stay copies: one `smoothstep` had lost its
guard against a zero-width span and produced NaN, and a second
`attachment_name` split extensions differently, so the same photo downloaded
under two names depending on which surface served it.

Advisory rules decay; a failing test does not. Deduplicating by hand is a
one-afternoon job — noticing the sixteenth copy a year later is not.
"""

import ast
import collections
import hashlib
import os
import pathlib
import unittest

WEB = pathlib.Path(__file__).parent
SKIPPED_DIRECTORIES = {".venv", "node_modules", "__pycache__", "migrations", "build", "dist"}

# A body this short is a stub or a one-line delegation, not a duplicated idea.
MEANINGFUL_STATEMENTS = 3

# Each entry needs a reason and a way out, or it is just a longer copy. Empty
# is the goal, and currently the truth: the last entry here was `configure`,
# duplicated across two sync route modules only to pass the catalog path around.
# Both are gone — see core/catalog_path.py.
ALLOWED: set[str] = set()


def _sources() -> list[pathlib.Path]:
    """The product's own modules: a pruned walk, because rglob visited the
    venv's 14,000 files before skipping them (4 s, past the suite's ten
    under load) where pruning is 74 files in 11 ms."""

    found: list[pathlib.Path] = []
    for root, dirs, files in os.walk(WEB):
        dirs[:] = [d for d in dirs if d not in SKIPPED_DIRECTORIES]
        found.extend(pathlib.Path(root) / name for name in files
                     if name.endswith(".py") and not name.startswith(("test_", "bench")))
    return found


def _duplicate_bodies() -> dict[str, set[str]]:
    """Map each repeated helper name to the modules that define it identically."""

    bodies: dict[tuple[str, str], set[str]] = collections.defaultdict(set)
    # A pruned walk: rglob visited the venv's 14,000 files before skipping
    # them (4 s, past the suite's ten under load); pruning is 74 files in
    # 11 ms.
    for path in _sources():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            statements = [
                statement
                for statement in node.body
                if not (
                    isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant)
                )
            ]
            if len(statements) < MEANINGFUL_STATEMENTS:
                continue
            source = ast.unparse(ast.Module(body=statements, type_ignores=[]))
            fingerprint = hashlib.md5(source.encode()).hexdigest()
            bodies[(node.name, fingerprint)].add(str(path.relative_to(WEB)).replace("\\", "/"))

    return {
        name: modules
        for (name, _), modules in bodies.items()
        if len(modules) > 1 and name not in ALLOWED
    }


class OnePlaceTests(unittest.TestCase):
    def test_no_helper_body_is_written_twice(self):
        duplicates = _duplicate_bodies()
        report = "\n".join(
            f"  {name}: {', '.join(sorted(modules))}" for name, modules in sorted(duplicates.items())
        )
        self.assertEqual(
            duplicates,
            {},
            f"\n{len(duplicates)} helper bodies exist in more than one module:\n{report}\n"
            "Give the idea one home and import it, or say why it must be repeated.",
        )

    def test_the_allowance_list_stays_honest(self):
        """An entry that no longer duplicates anything should be deleted."""

        bodies: dict[tuple[str, str], set[str]] = collections.defaultdict(set)
        for path in _sources():
            try:
                tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in ALLOWED:
                    statements = [
                        statement
                        for statement in node.body
                        if not (
                            isinstance(statement, ast.Expr)
                            and isinstance(statement.value, ast.Constant)
                        )
                    ]
                    source = ast.unparse(ast.Module(body=statements, type_ignores=[]))
                    bodies[(node.name, hashlib.md5(source.encode()).hexdigest())].add(str(path))

        still_duplicated = {name for (name, _), modules in bodies.items() if len(modules) > 1}
        self.assertEqual(
            ALLOWED - still_duplicated,
            set(),
            "these names no longer duplicate anything — remove them from ALLOWED",
        )


if __name__ == "__main__":
    unittest.main()
