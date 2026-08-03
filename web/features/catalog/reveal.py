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

from core.path_groups import safe_commonpath


Runner = Callable[[Sequence[str]], None]
_NONLOCAL_SOURCE_PREFIXES = ("hub:",)


def source_has_local_folders(path: str) -> bool:
    """Whether this source denotes folders on this machine.

    Hub-mirrored catalog rows deliberately have no original file on a satellite.
    Their ``hub://`` source is a library namespace, not a path an OS file manager
    can open.
    """

    return not str(path or "").strip().lower().startswith(_NONLOCAL_SOURCE_PREFIXES)


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
        if safe_commonpath([root_norm, candidate]) == root_norm:
            return True
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

    if not source_has_local_folders(path):
        return {"ok": False, "error": "Reveal is only available for local folders"}

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
