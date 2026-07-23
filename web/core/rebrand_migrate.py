"""One-shot rename migrations for Azimuth Photo rebrand (Phase 1).

Safe rules from docs/RENAME_PLAN.md:
- rename only, never copy
- if new exists, leave alone
- if rename fails, keep using old path
"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

log = logging.getLogger(__name__)

CATALOG_NEW = "azimuth.db"
CATALOG_OLD = "photoarchive.db"
BACKUP_PREFIX_NEW = "azimuth"
BACKUP_PREFIX_OLD = "photoarchive"


def _rename_path(src: Path, dst: Path) -> bool:
    if dst.exists():
        return False
    if not src.exists():
        return False
    try:
        src.rename(dst)
        log.info("rebrand_migrate: renamed %s -> %s", src, dst)
        return True
    except OSError as exc:
        log.warning(
            "rebrand_migrate: could not rename %s -> %s (%s); keeping old path",
            src,
            dst,
            exc,
        )
        return False


def migrate_catalog_db(catalog_path: str | os.PathLike[str]) -> str:
    """Prefer azimuth.db; rename photoarchive.db (+wal/shm) when needed."""

    path = Path(catalog_path)
    name = path.name.lower()
    # If caller already asked for azimuth.db, migrate sibling old name.
    if name == CATALOG_NEW:
        new_path = path
        old_path = path.with_name(CATALOG_OLD)
    elif name == CATALOG_OLD:
        old_path = path
        new_path = path.with_name(CATALOG_NEW)
    else:
        return str(path)

    if new_path.exists():
        return str(new_path)
    if not old_path.exists():
        return str(new_path)

    # Rename main + WAL sidecars if present.
    ok = _rename_path(old_path, new_path)
    if ok:
        for suffix in ("-wal", "-shm", "-journal"):
            _rename_path(
                Path(str(old_path) + suffix),
                Path(str(new_path) + suffix),
            )
        return str(new_path)
    return str(old_path)


def migrate_user_dir(new_path: str | os.PathLike[str], old_path: str | os.PathLike[str]) -> str:
    """Rename old dir to new when only old exists."""

    new_p = Path(new_path)
    old_p = Path(old_path)
    if new_p.exists():
        return str(new_p)
    if old_p.exists() and _rename_path(old_p, new_p):
        return str(new_p)
    if old_p.exists():
        return str(old_p)
    return str(new_p)


def prefer_existing_dir(new_path: str, old_path: str) -> str:
    """Read path selection without mutating disk (for resolve_runtime_paths)."""

    if Path(new_path).exists():
        return new_path
    if Path(old_path).exists():
        return old_path
    return new_path


def migrate_pictures_dirs(home: str | os.PathLike[str]) -> None:
    home_p = Path(home)
    pairs = (
        (home_p / "Pictures" / "Azimuth Imports", home_p / "Pictures" / "photoArchive Imports"),
        (home_p / "Pictures" / "Azimuth Exports", home_p / "Pictures" / "photoArchive Exports"),
    )
    for new_p, old_p in pairs:
        migrate_user_dir(new_p, old_p)
