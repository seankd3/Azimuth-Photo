"""Restore drill: prove a sealed catalog snapshot is actually restorable.

Picks the newest sealed ``azimuth-*.db.gz``, restores into a throwaway
scratch directory, reuses the backup pipeline's verify helpers, spot-checks
random image rows, then deletes the scratch — success or failure.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from core.runtime_paths import resolve_runtime_paths
from features.system import backups

log = logging.getLogger(__name__)

DEFAULT_SPOT_ROWS = 5
LIVE_DB_NAME = "azimuth.db"
LOG_KEEP_LINES = 5000

SPOT_IMAGE_SQL = """
SELECT id, filename, filepath, elo, date_taken
FROM images
WHERE id = ?
"""


class RestoreDrillError(RuntimeError):
    """Restore drill failed (corrupt snapshot, verify failure, etc.)."""


class ScratchUnsafeError(RestoreDrillError):
    """Scratch target looks like a live/prod location — refused."""


@dataclass(frozen=True)
class DrillResult:
    ok: bool
    snapshot: str
    scratch: str
    images: int
    spot_checked: int
    message: str
    error: str | None = None


def newest_sealed_snapshot(backup_root: Path | None = None) -> Path:
    """Return the newest sealed snapshot under ``backup_root``."""
    root = Path(backup_root) if backup_root is not None else Path(resolve_runtime_paths().backup_dir)
    candidates: list[tuple[datetime, Path]] = []
    for path in backups._snapshot_paths(root):
        parsed = backups._parse_backup_name(path.name)
        if parsed is None or not path.is_file():
            continue
        candidates.append((parsed, path))
    if not candidates:
        raise RestoreDrillError(f"No sealed snapshots found under {root}")
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


