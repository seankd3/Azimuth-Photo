"""The UI may not call a path the backend does not serve.

Served paths come from the route decorators; called paths come from the `/api/`
literals in the desktop JS and templates. A literal that ends in `/`, or that
ends in an interpolation, is a prefix and is matched as one — the gate errs
silent rather than loud.

Measured 2026-08-16: 170 served paths, 157 distinct literals over 183 sites,
**16 called and not served**.
"""

from __future__ import annotations

import fnmatch
import re

from common import read, tracked

DECORATOR = re.compile(r"@[A-Za-z_]+\.(?:get|post|put|delete|patch)\(\s*[\"']([^\"']+)[\"']")
START = re.compile(r"[\"'`]/api/")


def _literal(text: str, at: int) -> str:
    """Read one JS string from its opening quote. `${...}` becomes `*`."""

    quote, i, out = text[at], at + 1, ""
    while i < len(text) and text[i] not in (quote, "\n"):
        if text[i] == "\\":
            i += 2
            continue
        if text[i : i + 2] == "${":
            depth, j = 0, i + 1
            while j < len(text):
                depth += (text[j] == "{") - (text[j] == "}")
                if depth == 0:
                    break
                j += 1
            out, i = out + "*", j + 1
            continue
        out, i = out + text[i], i + 1
    return out


def _served(root):
    paths = set()
    for path in tracked(root, "*.py", tests=False):
        paths.update(DECORATOR.findall(read(root, path)))
    return [["*" if s.startswith("{") else s for s in p.strip("/").split("/")] for p in paths]


def _compatible(literal: str, served) -> bool:
    path = literal.split("?")[0].split("#")[0]
    segments = path.strip("/").split("/")
    prefix = path.endswith("/") or segments[-1].endswith("*")
    width = len(segments) - 1 if path.endswith("/") else len(segments)
    for route in served:
        if len(route) < width or (not prefix and len(route) != width):
            continue
        if all(route[i] == "*" or fnmatch.fnmatch(route[i], segments[i]) for i in range(width)):
            return True
    return False


def run(root) -> list[str]:
    served = _served(root)
    called: dict[str, list[str]] = {}
    for path in tracked(root, "web/static/*", "web/templates/*"):
        if not path.endswith((".js", ".html")):
            continue
        for number, line in enumerate(read(root, path).splitlines(), 1):
            for match in START.finditer(line):
                called.setdefault(_literal(line, match.start()), []).append(f"{path}:{number}")
    return [
        f"{literal}  <- {', '.join(sites)}"
        for literal, sites in sorted(called.items())
        if not _compatible(literal, served)
    ]
