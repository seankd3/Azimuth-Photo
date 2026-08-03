"""Check every relative import in static/js resolves — file and named export.

The Python suite never loads a browser module, and ruff cannot see across
languages, so a JS import can point at a file that moved and nothing notices
until the page is open. That is how gallery_editor.js came back importing
./dom.js after dom.js had become ../lib.js.

    cd web && ./.venv/Scripts/python.exe scripts_js_imports.py

Exit code is the number of problems, so it can gate a commit.
"""

from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent / "static" / "js"

_DECL_EXPORT = re.compile(
    r"^export\s+(?:async\s+)?(?:function|const|let|var|class)\s+(\w+)", re.M
)
_LIST_EXPORT = re.compile(r"^export\s*\{([^}]*)\}", re.M)
_IMPORT = re.compile(
    r"import\s+(?:\{([^}]*)\}\s*|\*\s+as\s+\w+\s*|(\w+)\s*)?(?:from\s*)?"
    r"['\"](\.[^'\"]+)['\"]"
)


def exported_names(source: str) -> set[str]:
    names = set(_DECL_EXPORT.findall(source))
    for group in _LIST_EXPORT.findall(source):
        for piece in group.split(","):
            name = piece.split(" as ")[-1].strip()
            if name:
                names.add(name)
    return names


def main() -> int:
    sources = {p.resolve(): p.read_text(encoding="utf-8", errors="ignore")
               for p in ROOT.rglob("*.js")}
    exports = {path: exported_names(text) for path, text in sources.items()}

    problems: list[str] = []
    for path, text in sources.items():
        here = path.relative_to(ROOT).as_posix()
        for names, _default, specifier in _IMPORT.findall(text):
            target = (path.parent / specifier).resolve()
            if target not in sources:
                problems.append(f"{here} -> {specifier} (no such module)")
                continue
            for piece in names.split(","):
                name = piece.split(" as ")[0].strip()
                if name and name not in exports[target]:
                    problems.append(f"{here} imports {name!r} from {specifier} (not exported)")

    for problem in problems:
        print(problem)
    print(f"{len(problems)} unresolved import(s) across {len(sources)} modules")
    return len(problems)


if __name__ == "__main__":
    raise SystemExit(main())
