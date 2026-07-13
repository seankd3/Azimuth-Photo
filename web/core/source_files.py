"""Filesystem-boundary checks for catalog originals."""

from __future__ import annotations

import os
import stat


def inspect_source_file(path: str, source_root: str = "") -> tuple[str, os.stat_result | None]:
    """Return a safe-to-read state without following a final symlink."""

    try:
        file_stat = os.lstat(path)
    except FileNotFoundError:
        return "missing", None
    except (OSError, ValueError):
        return "unavailable", None

    if stat.S_ISLNK(file_stat.st_mode):
        return "unsafe", None
    if not stat.S_ISREG(file_stat.st_mode):
        return "not_regular", None

    if source_root:
        try:
            real_path = os.path.realpath(path)
            real_root = os.path.realpath(source_root)
            if os.path.commonpath((real_root, real_path)) != real_root:
                return "unsafe", None
        except (OSError, ValueError):
            return "unsafe", None

    if int(file_stat.st_size or 0) <= 0:
        return "empty", file_stat
    return "available", file_stat


def source_file_is_safe(path: str, source_root: str = "") -> bool:
    state, _file_stat = inspect_source_file(path, source_root)
    return state in {"available", "empty"}
