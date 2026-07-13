"""Open a catalog folder in the OS file manager (Reveal in Explorer/Finder).

Security: only paths under a known included catalog source root are allowed.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Sequence
from typing import Any


Runner = Callable[[Sequence[str]], None]


def normalize_path(path: str) -> str:
    return os.path.realpath(os.path.abspath(os.path.expanduser(path or "")))


def path_is_under_roots(path: str, roots: Sequence[str]) -> bool:
    """Return True when *path* resolves inside one of the catalog source roots."""

    candidate = normalize_path(path)
    if not candidate:
        return False
    for root in roots:
        root_norm = normalize_path(root)
        if not root_norm:
            continue
        try:
            if os.path.commonpath([root_norm, candidate]) == root_norm:
                return True
        except ValueError:
            continue
    return False


def reveal_argv(path: str, platform_name: str | None = None) -> list[str]:
    """Build the argv list that opens a folder in the platform file manager."""

    target = normalize_path(path)
    family = (platform_name or sys.platform or "").lower()
    if family.startswith("win"):
        return ["explorer", target]
    if family == "darwin":
        return ["open", target]
    return ["xdg-open", target]


def graphical_session_available(platform_name: str | None = None, environ: dict | None = None) -> bool:
    family = (platform_name or sys.platform or "").lower()
    env = environ if environ is not None else os.environ
    if family.startswith("win") or family == "darwin":
        return True
    return bool(env.get("DISPLAY") or env.get("WAYLAND_DISPLAY"))


def reveal_label(platform_name: str | None = None) -> str:
    family = (platform_name or sys.platform or "").lower()
    if family.startswith("win"):
        return "Reveal in Explorer"
    if family == "darwin":
        return "Reveal in Finder"
    return "Open in file manager"


def _default_runner(argv: Sequence[str]) -> None:
    subprocess.Popen(
        list(argv),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def reveal_folder(
    path: str,
    roots: Sequence[str],
    *,
    platform_name: str | None = None,
    environ: dict | None = None,
    runner: Runner | None = None,
    which: Callable[[str], str | None] | None = None,
) -> dict[str, Any]:
    """Validate *path* against catalog roots and open it in the OS file manager."""

    if not str(path or "").strip():
        return {"ok": False, "error": "Path is required"}

    candidate = normalize_path(path)
    if not path_is_under_roots(candidate, roots):
        return {"ok": False, "error": "Path is outside catalog sources"}

    if not os.path.isdir(candidate):
        return {"ok": False, "error": "Not a folder"}

    if not graphical_session_available(platform_name, environ):
        return {
            "ok": False,
            "error": "No graphical session available to open a file manager",
        }

    argv = reveal_argv(candidate, platform_name)
    lookup = which or shutil.which
    if lookup(argv[0]) is None:
        return {"ok": False, "error": f"{argv[0]} is not available"}

    try:
        (runner or _default_runner)(argv)
    except OSError as exc:
        return {"ok": False, "error": str(exc) or "Could not open file manager"}

    return {"ok": True, "path": candidate, "argv": list(argv)}
