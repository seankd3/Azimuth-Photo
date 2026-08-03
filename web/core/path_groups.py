"""Drive-safe path grouping for Windows multi-root libraries.

``os.path.commonpath`` / ``relpath`` raise ``ValueError`` when paths sit on
different Windows drives ("Paths don't have the same drive"). Group first,
then compute per-drive roots.
"""

from __future__ import annotations

import ntpath
import os
import posixpath
import sys
from collections.abc import Iterable, Sequence
from typing import TypeVar

T = TypeVar("T")


def _platform_family(platform_name: str | None = None) -> str:
    value = str(platform_name or sys.platform or "").lower()
    if value.startswith("win"):
        return "windows"
    return "posix"


def _joiner(family: str):
    return ntpath if family == "windows" else posixpath


def drive_key(path: str, *, family: str | None = None) -> str:
    """Return a stable drive/root key for grouping (e.g. ``C:`` or ``/``)."""

    text = os.fspath(path or "")
    selected = _platform_family(family)
    paths = _joiner(selected)
    if selected == "windows":
        drive, _tail = paths.splitdrive(paths.normpath(text))
        return drive.upper() if drive else ""
    # POSIX: treat UNC-style and absolute roots as one bucket; relative stays empty.
    normalized = paths.normpath(text)
    return "/" if normalized.startswith("/") else ""


def group_paths_by_drive(
    paths: Iterable[str],
    *,
    family: str | None = None,
) -> dict[str, list[str]]:
    """Bucket paths by drive key, preserving first-seen order within each drive."""

    groups: dict[str, list[str]] = {}
    for path in paths:
        if not path:
            continue
        key = drive_key(path, family=family)
        groups.setdefault(key, []).append(path)
    return groups


def safe_commonpath(
    paths: Sequence[str],
    *,
    family: str | None = None,
) -> str | None:
    """Return ``commonpath`` when all paths share a drive; otherwise ``None``."""

    cleaned = [path for path in paths if path]
    if not cleaned:
        return None
    groups = group_paths_by_drive(cleaned, family=family)
    if len(groups) != 1:
        return None
    selected = _platform_family(family)
    paths_mod = _joiner(selected)
    try:
        return paths_mod.commonpath(cleaned)
    except ValueError:
        return None


def commonpath_per_drive(
    paths: Sequence[str],
    *,
    family: str | None = None,
) -> dict[str, str]:
    """Compute a commonpath root for each drive group that has paths."""

    selected = _platform_family(family)
    paths_mod = _joiner(selected)
    roots: dict[str, str] = {}
    for key, group in group_paths_by_drive(paths, family=family).items():
        try:
            roots[key] = paths_mod.commonpath(group)
        except ValueError:
            roots[key] = group[0]
    return roots


def safe_relpath(path: str, start: str, *, family: str | None = None) -> str | None:
    """Return ``relpath`` when path and start share a drive; otherwise ``None``."""

    if not path or not start:
        return None
    if drive_key(path, family=family) != drive_key(start, family=family):
        return None
    selected = _platform_family(family)
    paths_mod = _joiner(selected)
    try:
        return paths_mod.relpath(path, start)
    except ValueError:
        return None
