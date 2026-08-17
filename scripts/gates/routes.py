"""A route is served once, and reachable.

`web/app.py` is 119 lines and the worst-coupled file in the tree: 334 files
change with it, 158 commits, 218 repairs per thousand lines. Most of that is
unavoidable — it is the assembly point, so everyone's work passes through it.
But its *own* bugs are only ever two, and both are the failure modes of a
hand-maintained list:

    fix(develop): resolve export presets before image routes   -> wrong order
    fix(settings): configure devices routes                    -> never registered

Discovery would end the second and cause the first, because FastAPI matches in
registration order and 14 develop paths are order-sensitive: `/api/develop/presets`
must be registered before `/api/develop/{image_id}`, or the literal is swallowed
by the parameter. So the list stays — it carries real information — and the two
ways it goes wrong stop being possible instead.

Three counts, all of which should be zero:

* **twice** — a path served by two modules. Three existed and never ran, which is
  worse than dead code because it reads as live. Deleting them is what made
  registration order stop mattering for correctness.
* **stranded** — a module defining `router` that `app.py` never includes. That is
  a surface written, shipped and unreachable.
* **shadowed** — a literal path registered *after* a parameter that swallows it.
  This is the one a person cannot see by reading, because the two routes live in
  different files and only their registration order decides.
"""

from __future__ import annotations

import re

from common import read, tracked

DECORATOR = re.compile(r'@router\.(get|post|put|delete|patch)\(\s*"([^"]+)"')
INCLUDED = re.compile(r"^\s*([a-zA-Z_][\w]*)\.router,", re.M)
IMPORTED = re.compile(r"^\s*(?:import (\S+) as (\w+)|from ([\w.]+) import (.+))$", re.M)


def _served(root):
    """Every route, as (verb, path, module)."""

    out = []
    for path in tracked(root, "web/*.py", tests=False):
        for verb, route in DECORATOR.findall(read(root, path)):
            out.append((verb.upper(), route, path))
    return out


def _registered(root) -> dict[str, int]:
    """Module path -> the position `app.py` includes it at.

    Keyed on the *module*, not the alias, because they differ where it matters:
    `features/develop/routes.py` is included as `develop_routes`. A first
    version of this gate ranked by filename stem, so `routes` was never found in
    the order, every comparison read as "not earlier", and the gate passed while
    reproducing the exact historical bug. A gate that cannot fail is worse than
    no gate — it certifies.
    """

    text = read(root, "web/app.py")
    order = {alias: index for index, alias in enumerate(INCLUDED.findall(text))}
    modules: dict[str, int] = {}
    for whole, whole_as, source, names in IMPORTED.findall(text):
        if whole and whole_as in order:
            modules["web/" + whole.replace(".", "/") + ".py"] = order[whole_as]
        for name in (names or "").split(","):
            actual, _, alias = name.strip().partition(" as ")
            actual, alias = actual.strip(), alias.strip()
            if not source or (alias or actual) not in order:
                continue
            at = order[alias or actual]
            modules[f"web/{source.replace('.', '/')}/{actual}.py"] = at
            modules[f"web/{source.replace('.', '/')}.py"] = at
    return modules


def run(root) -> list[str]:
    routes = _served(root)
    found: list[str] = []

    seen: dict[tuple, list[str]] = {}
    for verb, route, path in routes:
        seen.setdefault((verb, route), []).append(path)
    for (verb, route), modules in sorted(seen.items()):
        if len(modules) > 1:
            found.append(f"twice: {verb} {route} -- {', '.join(sorted(set(modules)))}")

    registered = _registered(root)
    defines = {path for _, _, path in routes}
    for path in sorted(defines - set(registered)):
        found.append(f"stranded: {path} defines routes app.py never includes")

    # A literal segment is shadowed by a parameter registered before it.
    def rank(path: str) -> int:
        return registered.get(path, len(registered))

    for verb, route, path in routes:
        segments = route.strip("/").split("/")
        for other_verb, other, other_path in routes:
            if other_path == path or other_verb != verb:
                continue
            theirs = other.strip("/").split("/")
            if len(theirs) != len(segments):
                continue
            if not all(a == b or b.startswith("{") for a, b in zip(segments, theirs)):
                continue
            if any(not a.startswith("{") and b.startswith("{") for a, b in zip(segments, theirs)):
                if rank(other_path) < rank(path):
                    found.append(f"shadowed: {verb} {route} ({path}) registered after {other} ({other_path})")
    return sorted(set(found))
