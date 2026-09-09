"""Imports point down.

The V2 tree has one dependency direction and `docs/ARCHITECTURE.md` states it:
the window calls `desktop`, which calls `boot`, which calls the surfaces, which
call `model` and `pixels`, which call nothing of the product. In the UI the same
arrow runs `kit <- net <- store <- lens <- shell`. An import that points the
other way is a lower layer that cannot be read, tested or deleted without the
layer above it -- and it is exactly how the V1 tree stopped being one thing.

Three rules, each a counted line:

* `web/model/`, `web/pixels/` and `web/photo/` import only the standard
  library, third-party packages, and their own siblings -- except that `model`
  may read a photograph through `photo`, which is below it. `pixels` in
  particular may not know where a file lives or what a catalog is; it is
  mathematics over arrays.
* No V2 module imports `desktop` (the native edge is the top) and only
  `desktop` imports `boot`.
* A UI module under `web/static/v2/<layer>/` imports only its own layer or one
  below it in `kit, net, store, lens, shell`.
"""

from __future__ import annotations

import ast
import re

from common import read, tracked

PURE = {"web/model/": "model", "web/pixels/": "pixels", "web/photo/": "photo"}
BELOW = {"model": {"photo"}}
UI_ORDER = ("kit", "net", "store", "lens", "shell")
JS_IMPORT = re.compile(r"""^\s*import\b[^'"]*['"]([^'"]+)['"]""", re.M)


def _first_party(root) -> set[str]:
    names = set()
    for path in tracked(root, "web/*.py", "web/**/*.py", tests=False):
        dotted = path[len("web/"):].removesuffix(".py").replace("/", ".")
        names.add(dotted.removesuffix(".__init__"))
        names.add(dotted.split(".")[0])
    return names


def _python(root) -> list[str]:
    ours = _first_party(root)
    found = []
    for path in tracked(root, "web/*.py", "web/**/*.py", tests=False):
        try:
            tree = ast.parse(read(root, path))
        except SyntaxError:
            continue
        package = next((name for prefix, name in PURE.items() if path.startswith(prefix)), None)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                targets = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                targets = [node.module]
            else:
                continue
            for name in targets:
                head = name.split(".")[0]
                if head not in ours:
                    continue
                if package and head != package and head not in BELOW.get(package, ()):
                    found.append(f"{path}:{node.lineno}: {package} imports {name}")
                elif head == "desktop":
                    found.append(f"{path}:{node.lineno}: imports the native edge")
                elif head == "boot" and path != "web/desktop.py":
                    found.append(f"{path}:{node.lineno}: imports the product boundary")
    return found


def _ui(root) -> list[str]:
    found = []
    for path in tracked(root, "web/static/v2/**/*.js"):
        parts = path[len("web/static/v2/"):].split("/")
        if len(parts) < 2 or parts[0] not in UI_ORDER:
            continue
        layer = UI_ORDER.index(parts[0])
        for number, line in enumerate(read(root, path).splitlines(), 1):
            match = JS_IMPORT.match(line)
            if not match:
                continue
            target = match.group(1)
            if target.startswith("../"):
                above = target[3:].split("/")[0]
                if above in UI_ORDER and UI_ORDER.index(above) > layer:
                    found.append(f"{path}:{number}: {parts[0]} imports {above}")
    return found


def run(root) -> list[str]:
    return _python(root) + _ui(root)
