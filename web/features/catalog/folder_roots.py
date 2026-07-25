"""Customer-friendly starting points for the photo-folder browser."""

from __future__ import annotations

import ntpath
import os
import posixpath
import string
import sys
from collections.abc import Callable, Mapping


IsDirectory = Callable[[str], bool]


def quick_browse_roots(
    *,
    platform_name: str | None = None,
    home: str | None = None,
    environ: Mapping[str, str] | None = None,
    is_directory: IsDirectory = os.path.isdir,
) -> list[dict[str, str]]:
    """Return useful local, removable, and mapped-drive entry points."""

    platform = (platform_name or sys.platform).lower()
    windows = platform.startswith("win")
    paths = ntpath if windows else posixpath
    environment = os.environ if environ is None else environ
    resolved_home = home or environment.get("USERPROFILE") or os.path.expanduser("~")
    candidates: list[tuple[str, str]] = [
        ("Home", resolved_home),
        ("Pictures", paths.join(resolved_home, "Pictures")),
    ]

    if windows:
        one_drive = environment.get("OneDrive") or environment.get("OneDriveConsumer")
        if one_drive:
            candidates.append(("OneDrive", one_drive))
        # Windows assigns letters to local, removable, and mapped network drives.
        candidates.extend((f"{letter}:", f"{letter}:\\") for letter in string.ascii_uppercase)
    else:
        candidates.extend(
            [
                ("Media", "/media"),
                ("Mounts", "/mnt"),
                ("Run Media", paths.join("/run/media", environment.get("USER", ""))),
                ("Volumes", "/Volumes"),
            ]
        )

    roots: list[dict[str, str]] = []
    seen: set[str] = set()
    for label, path in candidates:
        normalized = paths.normpath(path)
        identity = paths.normcase(normalized)
        if identity in seen or not is_directory(normalized):
            continue
        seen.add(identity)
        roots.append({"label": label, "path": normalized})
    return roots
