"""Where a photo's bytes are, answered from this machine first.

The archive's own disk can be plugged straight into this computer. When it
is, every photo the catalog knows only by the archive's path — 144,413 rows
under ``/mnt/expansion`` on the owner's laptop — is readable right here, no
hub and no transfer. One rule finds it: strip leading components off the
archive path until some attached drive holds the rest, and remember the
split that worked.

Two properties keep the guess honest. A candidate is accepted only when the
actual file exists, and when the caller knows the catalog's recorded file
size, only when the size matches — so a same-named stranger on another
drive can never stand in for the photo. And a remembered mapping is dropped
only when its volume is gone, never because one file is absent: the archive
occasionally lacks a file the catalog still lists, and that must not make
every miss re-probe the world.
"""

from __future__ import annotations

import os
import string
import threading

# How many leading path components may be stripped: /mnt/expansion/... needs
# two; deeper mounts stay findable without letting the search go silly.
_MAX_STRIP = 4

_lock = threading.Lock()
_mappings: dict[str, str] = {}  # posix prefix ("/mnt/expansion") -> local root ("E:/")


def _drive_roots() -> list[str]:
    """Attached filesystem roots. Patchable seam for tests."""

    return [f"{letter}:/" for letter in string.ascii_uppercase if os.path.isdir(f"{letter}:/")]


def _verified(candidate: str, expected_size: int | None) -> bool:
    try:
        stat = os.stat(candidate)
    except OSError:
        return False
    if expected_size is not None and int(expected_size) > 0 and stat.st_size != int(expected_size):
        return False
    return True


def local_path(filepath: str, *, expected_size: int | None = None) -> str | None:
    """A path this machine can open for ``filepath``, or None.

    The row's own path wins when it exists. A POSIX archive path is tried
    against every attached drive by stripping leading components; the match
    must exist (and match ``expected_size`` when given) to be returned.
    """

    path = str(filepath or "")
    if not path:
        return None
    if not path.startswith("/"):
        return path if _verified(path, expected_size) else None
    if _verified(path, expected_size):
        return path

    parts = [part for part in path.split("/") if part]
    if len(parts) < 2:
        return None
    for depth in range(1, min(_MAX_STRIP, len(parts) - 1) + 1):
        prefix = "/" + "/".join(parts[:depth])
        tail = parts[depth:]
        with _lock:
            known = _mappings.get(prefix)
        if known is not None:
            candidate = os.path.join(known, *tail)
            if _verified(candidate, expected_size):
                return candidate
            if os.path.isdir(known):
                # The volume is attached; this one file just is not there
                # (or is not this photo). The mapping stays.
                continue
            with _lock:
                _mappings.pop(prefix, None)
        for root in _drive_roots():
            candidate = os.path.join(root, *tail)
            if _verified(candidate, expected_size):
                with _lock:
                    _mappings[prefix] = root
                return candidate
    return None
