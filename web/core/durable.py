"""Making a write survive the power going out.

A rename is only atomic once the directory entry itself has reached the disk,
and forgetting that is how a catalog backup, a card import and a hub upload can
each look complete and not be there afterwards. Three modules had their own copy
of the same eight lines; a durability primitive should have exactly one.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def fsync_directory(path: Path | str) -> None:
    """Flush a directory entry, so a rename into it survives a power loss.

    Windows cannot open a directory as a file, and `os.replace` is already
    durable-atomic on NTFS, so there is nothing to do there.
    """

    if sys.platform.startswith("win"):
        return
    descriptor = os.open(os.fspath(path), os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
