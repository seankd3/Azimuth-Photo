"""Restore drill: prove a sealed catalog snapshot is actually restorable.

Picks the newest sealed ``azimuth-*.db.gz``, restores into a throwaway
scratch directory, reuses the backup pipeline's verify helpers, spot-checks
random image rows, then deletes the scratch — success or failure.
"""

from __future__ import annotations

import gzip
import json
import logging
import os
import random
import shutil
import sqlite3
import tempfile
import urllib.error
import urllib.request
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


def assert_scratch_is_safe(scratch: Path) -> None:
    """Hard-refuse scratch dirs that already look like prod/live storage."""
    target = Path(scratch)
    if not target.exists():
        return
    if not target.is_dir():
        raise ScratchUnsafeError(f"Scratch target is not a directory: {target}")

    marker = target / backups.OWNER_MARKER_NAME
    if marker.exists():
        raise ScratchUnsafeError(
            f"Refusing scratch '{target}': contains {backups.OWNER_MARKER_NAME} "
            "(looks like a live backup root)"
        )

    live_db = target / LIVE_DB_NAME
    if live_db.exists():
        raise ScratchUnsafeError(
            f"Refusing scratch '{target}': contains {LIVE_DB_NAME} "
            "(looks like a live catalog directory)"
        )


def _decompress_snapshot(snapshot: Path, dest_db: Path) -> None:
    dest_db.parent.mkdir(parents=True, exist_ok=True)
    try:
        with gzip.open(snapshot, "rb") as gz, open(dest_db, "wb") as out:
            shutil.copyfileobj(gz, out, length=1024 * 1024)
    except (OSError, gzip.BadGzipFile, EOFError) as exc:
        raise RestoreDrillError(f"Failed to decompress snapshot '{snapshot.name}': {exc}") from exc


def _verify_restored_catalog(dest_db: Path) -> int:
    """Reuse backup verify helpers: required tables, quick_check, image count."""
    backups._validate_restored_catalog(dest_db)

    health = backups.catalog_quick_check(str(dest_db))
    if not health.get("ok"):
        raise RestoreDrillError(
            f"Restored catalog failed catalog_quick_check: {health.get('error') or health.get('state')}"
        )

    try:
        conn = sqlite3.connect(f"{dest_db.as_uri()}?mode=ro", uri=True, timeout=30.0)
    except sqlite3.Error as exc:
        raise RestoreDrillError(f"Restored catalog is not readable: {exc}") from exc
    try:
        image_count = backups._image_count(conn)
    finally:
        conn.close()

    # Self-consistency pass through the same verify used at seal time.
    backups._verify_backup_artifact(str(dest_db), str(dest_db), snapshot_images=image_count)
    return image_count


def _spot_check_rows(dest_db: Path, *, n: int = DEFAULT_SPOT_ROWS) -> int:
    """Open N random image rows and confirm referenced fields parse."""
    if n <= 0:
        return 0
    try:
        conn = sqlite3.connect(f"{dest_db.as_uri()}?mode=ro", uri=True, timeout=30.0)
        conn.row_factory = sqlite3.Row
    except sqlite3.Error as exc:
        raise RestoreDrillError(f"Could not open restored catalog for spot check: {exc}") from exc

    checked = 0
    try:
        ids = [int(row[0]) for row in conn.execute("SELECT id FROM images")]
        if not ids:
            # Empty catalogs still pass verify; spot check is a no-op.
            return 0
        sample_ids = random.sample(ids, min(int(n), len(ids)))

        has_develop = bool(
            conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='develop_settings'"
            ).fetchone()
        )

        for image_id in sample_ids:
            row = conn.execute(SPOT_IMAGE_SQL, (image_id,)).fetchone()
            if row is None:
                raise RestoreDrillError(f"Spot check failed: image {image_id} disappeared mid-drill")
            filename = row["filename"]
            filepath = row["filepath"]
            if not isinstance(filename, str) or not filename.strip():
                raise RestoreDrillError(f"Spot check failed: image {image_id} has empty filename")
            if not isinstance(filepath, str) or not filepath.strip():
                raise RestoreDrillError(f"Spot check failed: image {image_id} has empty filepath")
            elo = row["elo"]
            if elo is not None:
                try:
                    float(elo)
                except (TypeError, ValueError) as exc:
                    raise RestoreDrillError(
                        f"Spot check failed: image {image_id} elo is not numeric: {elo!r}"
                    ) from exc
            date_taken = row["date_taken"]
            if date_taken is not None and not isinstance(date_taken, str):
                raise RestoreDrillError(
                    f"Spot check failed: image {image_id} date_taken is not a string: {date_taken!r}"
                )

            if has_develop:
                settings_row = conn.execute(
                    "SELECT settings FROM develop_settings WHERE image_id = ?",
                    (image_id,),
                ).fetchone()
                if settings_row is not None and settings_row["settings"] is not None:
                    raw = settings_row["settings"]
                    if not isinstance(raw, str):
                        raise RestoreDrillError(
                            f"Spot check failed: develop_settings for image {image_id} is not text"
                        )
                    try:
                        parsed = json.loads(raw)
                    except json.JSONDecodeError as exc:
                        raise RestoreDrillError(
                            f"Spot check failed: develop_settings for image {image_id} is not JSON"
                        ) from exc
                    if not isinstance(parsed, dict):
                        raise RestoreDrillError(
                            f"Spot check failed: develop_settings for image {image_id} is not an object"
                        )
            checked += 1
    finally:
        conn.close()
    return checked


def run_restore_drill(
    *,
    backup_root: Path | None = None,
    snapshot: Path | None = None,
    scratch_parent: Path | None = None,
    scratch: Path | None = None,
    spot_rows: int = DEFAULT_SPOT_ROWS,
    seed: int | None = None,
) -> DrillResult:
    """Restore newest (or named) snapshot into scratch, verify, spot-check, clean up."""
    if seed is not None:
        random.seed(seed)

    chosen = Path(snapshot) if snapshot is not None else newest_sealed_snapshot(backup_root)
    if not chosen.is_file():
        raise RestoreDrillError(f"Snapshot not found: {chosen}")

    owned_scratch = scratch is None
    scratch_dir: Path | None = None
    cleanup = False
    if scratch is not None:
        scratch_dir = Path(scratch)
        assert_scratch_is_safe(scratch_dir)
        scratch_dir.mkdir(parents=True, exist_ok=True)
        assert_scratch_is_safe(scratch_dir)
        cleanup = True
    else:
        parent = Path(scratch_parent) if scratch_parent is not None else Path(tempfile.gettempdir())
        parent.mkdir(parents=True, exist_ok=True)
        scratch_dir = Path(tempfile.mkdtemp(prefix="azimuth-restore-drill-", dir=str(parent)))
        # Fresh empty dir should always be safe; still enforce the contract.
        assert_scratch_is_safe(scratch_dir)
        cleanup = True

    dest_db = scratch_dir / "restored.db"
    try:
        log.info("restore_drill start snapshot=%s scratch=%s", chosen, scratch_dir)
        _decompress_snapshot(chosen, dest_db)
        image_count = _verify_restored_catalog(dest_db)
        spot_checked = _spot_check_rows(dest_db, n=spot_rows)
        message = (
            f"ok snapshot={chosen.name} images={image_count} "
            f"spot_checked={spot_checked} scratch={scratch_dir}"
        )
        log.info("restore_drill %s", message)
        return DrillResult(
            ok=True,
            snapshot=chosen.name,
            scratch=str(scratch_dir),
            images=image_count,
            spot_checked=spot_checked,
            message=message,
        )
    except ScratchUnsafeError:
        # Never delete a refused target — it may be a live/prod directory.
        cleanup = False
        raise
    except backups.BackupVerificationError as exc:
        raise RestoreDrillError(str(exc)) from exc
    except backups.RestoreValidationError as exc:
        raise RestoreDrillError(str(exc)) from exc
    except RestoreDrillError:
        raise
    except Exception as exc:  # noqa: BLE001 — drill boundary; convert to honest failure
        raise RestoreDrillError(f"Restore drill failed: {exc}") from exc
    finally:
        if cleanup and scratch_dir is not None:
            shutil.rmtree(scratch_dir, ignore_errors=True)
        elif owned_scratch and scratch_dir is not None and not cleanup:
            # Safety net: auto-created scratch must never leak even on refuse-after-create.
            shutil.rmtree(scratch_dir, ignore_errors=True)


def _append_bounded_log(log_path: Path, line: str, *, keep: int = LOG_KEEP_LINES) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as handle:
        handle.write(line.rstrip() + "\n")
    try:
        text = log_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    if len(text) <= keep:
        return
    temporary = log_path.with_suffix(log_path.suffix + ".tmp")
    temporary.write_text("\n".join(text[-keep:]) + "\n", encoding="utf-8")
    os.replace(temporary, log_path)


def _read_state(state_path: Path) -> str:
    try:
        value = state_path.read_text(encoding="utf-8").strip()
    except OSError:
        return "ok"
    return value if value in {"ok", "bad"} else "ok"


def _write_state(state_path: Path, value: str) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = state_path.with_suffix(state_path.suffix + ".tmp")
    temporary.write_text(value + "\n", encoding="utf-8")
    os.replace(temporary, state_path)


def _ntfy(url: str, *, title: str, body: str, priority: str | None = None) -> None:
    headers = {"Title": title}
    if priority:
        headers["Priority"] = priority
    request = urllib.request.Request(
        url,
        data=body.encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            response.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        log.warning("restore_drill ntfy failed: %s", exc)


